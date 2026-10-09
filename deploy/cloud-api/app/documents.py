from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree


MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_DOCX_ENTRIES = 500
MAX_DOCX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
MAX_DOCX_XML_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_CHARACTERS = 200_000

WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WORD_DOCUMENT_PATH = "word/document.xml"


@dataclass(frozen=True)
class DocumentParseError(Exception):
    status_code: int
    message: str

    def __str__(self) -> str:
        return self.message


def _validate_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise DocumentParseError(422, "Document contains no readable text")
    if len(normalized) > MAX_DOCUMENT_CHARACTERS:
        raise DocumentParseError(
            413,
            f"Document text exceeds {MAX_DOCUMENT_CHARACTERS} characters",
        )
    return normalized


def _parse_txt(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise DocumentParseError(422, "TXT must use UTF-8 or GB18030 encoding")

    if "\x00" in text:
        raise DocumentParseError(422, "TXT appears to contain binary data")
    control_count = sum(ord(char) < 32 and char not in "\n\r\t\f" for char in text)
    if control_count > max(4, len(text) // 100):
        raise DocumentParseError(422, "TXT appears to contain binary data")
    return _validate_text(text)


def _read_docx_xml(payload: bytes) -> bytes:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except (OSError, zipfile.BadZipFile) as exc:
        raise DocumentParseError(422, "DOCX file is damaged or invalid") from exc

    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_DOCX_ENTRIES:
            raise DocumentParseError(413, "DOCX contains too many archive entries")
        if any(info.flag_bits & 0x1 for info in entries):
            raise DocumentParseError(422, "Encrypted DOCX files are not supported")
        if sum(info.file_size for info in entries) > MAX_DOCX_UNCOMPRESSED_BYTES:
            raise DocumentParseError(413, "DOCX uncompressed content is too large")

        by_name = {info.filename: info for info in entries}
        document = by_name.get(WORD_DOCUMENT_PATH)
        if document is None or "[Content_Types].xml" not in by_name:
            raise DocumentParseError(422, "DOCX file is damaged or invalid")
        if document.file_size > MAX_DOCX_XML_BYTES:
            raise DocumentParseError(413, "DOCX document XML is too large")
        try:
            xml_payload = archive.read(document)
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise DocumentParseError(422, "DOCX file is damaged or invalid") from exc

    if len(xml_payload) > MAX_DOCX_XML_BYTES:
        raise DocumentParseError(413, "DOCX document XML is too large")
    if b"<!DOCTYPE" in xml_payload.upper() or b"<!ENTITY" in xml_payload.upper():
        raise DocumentParseError(422, "DOCX document XML contains unsupported declarations")
    return xml_payload


def _parse_docx(payload: bytes) -> str:
    xml_payload = _read_docx_xml(payload)
    try:
        root = ElementTree.fromstring(xml_payload)
    except ElementTree.ParseError as exc:
        raise DocumentParseError(422, "DOCX document XML is invalid") from exc

    paragraph_tag = f"{{{WORD_NAMESPACE}}}p"
    text_tag = f"{{{WORD_NAMESPACE}}}t"
    tab_tag = f"{{{WORD_NAMESPACE}}}tab"
    break_tags = {f"{{{WORD_NAMESPACE}}}br", f"{{{WORD_NAMESPACE}}}cr"}
    paragraphs: list[str] = []
    character_count = 0
    for paragraph in root.iter(paragraph_tag):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == text_tag and node.text:
                parts.append(node.text)
            elif node.tag == tab_tag:
                parts.append("\t")
            elif node.tag in break_tags:
                parts.append("\n")
        value = "".join(parts)
        if value:
            character_count += len(value) + 1
            if character_count > MAX_DOCUMENT_CHARACTERS:
                raise DocumentParseError(
                    413,
                    f"Document text exceeds {MAX_DOCUMENT_CHARACTERS} characters",
                )
            paragraphs.append(value)
    return _validate_text("\n".join(paragraphs))


def parse_document(filename: str | None, payload: bytes) -> dict[str, str | int]:
    safe_filename = Path((filename or "").replace("\\", "/")).name
    suffix = Path(safe_filename).suffix.lower()
    if suffix == ".doc":
        raise DocumentParseError(
            415,
            "Legacy .doc files are not supported; save the document as .docx or .txt",
        )
    if suffix not in {".txt", ".docx"}:
        raise DocumentParseError(415, "Only TXT and DOCX documents are supported")
    if not payload:
        raise DocumentParseError(422, "Document file is empty")
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise DocumentParseError(413, "Document file exceeds the 5 MiB limit")

    document_type = suffix[1:]
    text = _parse_txt(payload) if document_type == "txt" else _parse_docx(payload)
    return {
        "text": text,
        "filename": safe_filename,
        "type": document_type,
        "size": len(payload),
    }
