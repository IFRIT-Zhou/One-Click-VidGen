"""Conservative text normalization for speech engines.

The returned text is only used for synthesis.  OCV keeps the user's original
text for subtitles and editing so typographic choices are never lost.
"""

from __future__ import annotations

import re
import unicodedata


_CHAR_TRANSLATION = str.maketrans({
    "\u2010": "-",  # hyphen
    "\u2011": "-",  # non-breaking hyphen
    "\u2012": "-",  # figure dash
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2015": "-",  # horizontal bar
    "\u2212": "-",  # mathematical minus
    "\ufe63": "-",
    "\uff0d": "-",
    "\u00a0": " ",  # no-break space
    "\u202f": " ",  # narrow no-break space
    "\u2007": " ",  # figure space
    "\u200b": "",   # zero-width space
    "\u2060": "",   # word joiner
    "\ufeff": "",   # zero-width no-break space / BOM
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u201f": '"',
})


def normalize_tts_text(text: str) -> str:
    """Return an engine-safe reading copy without changing visible subtitles."""
    normalized = unicodedata.normalize("NFKC", str(text or "")).translate(_CHAR_TRANSLATION)
    # Preserve real line breaks but eliminate exotic horizontal whitespace and
    # control characters which some Windows-native tokenizers cannot print.
    normalized = re.sub(r"[\t\v\f\r ]+", " ", normalized)
    normalized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", normalized)
    return normalized.strip()
