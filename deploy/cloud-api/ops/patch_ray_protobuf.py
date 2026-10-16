"""Repair Ray Serve's descriptor conversion for protobuf 7.

Run using the Python interpreter inside the TTS worker container. The patch is
idempotent, preserves the original next to config.py, and verifies real Serve
configuration serialization. It survives container restarts; run it again after
recreating a container from an unpatched image, before starting Ray workers.
"""

from __future__ import annotations

import datetime
import importlib
import pathlib
import shutil


LEGACY = "if field.label == FieldDescriptor.LABEL_REPEATED:"
INTERIM = "if field.is_repeated:"
COMPATIBLE = (
    "if (field.is_repeated if hasattr(field, 'is_repeated') "
    "else field.label == FieldDescriptor.LABEL_REPEATED):"
)


def patched_source(source: str) -> str:
    if source.count(COMPATIBLE) == 1:
        return source
    matches = [needle for needle in (LEGACY, INTERIM) if needle in source]
    if len(matches) != 1 or source.count(matches[0]) != 1:
        raise RuntimeError("Unexpected Ray Serve source; refusing to patch")
    return source.replace(matches[0], COMPATIBLE)


def verify(module) -> None:
    from google.protobuf.descriptor_pb2 import FileDescriptorProto

    config = module.DeploymentConfig
    for original in (
        config(num_replicas=2),
        config(num_replicas=2, user_config={"text": "test", "list": [1, 2]}, max_ongoing_requests=7),
        config(num_replicas=1, user_config=b"raw-bytes"),
    ):
        restored = config.from_proto_bytes(original.to_proto_bytes())
        for field in ("num_replicas", "user_config", "max_ongoing_requests"):
            if getattr(restored, field) != getattr(original, field):
                raise RuntimeError(f"DeploymentConfig round trip failed: {field}")
    proto = FileDescriptorProto(name="test.proto", dependency=["a.proto", "b.proto"])
    proto.message_type.add(name="Nested")
    result = module._proto_to_dict(proto)
    if not (
        result["name"] == "test.proto"
        and result["dependency"] == ["a.proto", "b.proto"]
        and result["message_type"][0]["name"] == "Nested"
    ):
        raise RuntimeError("Scalar/repeated/nested protobuf conversion failed")


def main() -> None:
    import ray.serve._private.config as module

    path = pathlib.Path(module.__file__)
    original = path.read_text(encoding="utf-8")
    updated = patched_source(original)
    compile(updated, str(path), "exec")
    backup = None
    if updated != original:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = path.with_name(f"{path.name}.backup-protobuf7-{stamp}")
        shutil.copy2(path, backup)
        try:
            path.write_text(updated, encoding="utf-8")
            module = importlib.reload(module)
            verify(module)
        except BaseException:
            shutil.copy2(backup, path)
            raise
    else:
        verify(module)
    print(f"PATCH_OK changed={updated != original} path={path} backup={backup}")
    print("VERIFY_OK DeploymentConfig and scalar/repeated/nested protobuf serialization")


if __name__ == "__main__":
    main()
