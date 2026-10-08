"""Local integration check; does not submit a generation task or alter user presets."""
import json
import argparse
from pathlib import Path
import sys
import time
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import managed_comfyui as engine
import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, help="Explicitly submit a 2-second local generation smoke test")
    parser.add_argument("--candidate", type=Path, help="Validate a staged release without changing active.json")
    parser.add_argument("--profile-id", help="Workflow to use for the optional generation smoke test")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=384)
    parser.add_argument("--duration", type=float, default=2)
    args = parser.parse_args()
    stack = ExitStack()
    if args.candidate:
        release = args.candidate.resolve()
        if release.parent != (engine.RUNTIME / "releases").resolve():
            raise ValueError("Candidate must be inside the OCV release directory")
        manifest = engine.read_json(release / "component.json", {})
        if manifest.get("version") != release.name:
            raise ValueError("Invalid candidate manifest")
        stack.enter_context(patch.object(engine, "runtime_info", return_value=(release, manifest)))
        # Keep candidate caches, database, input and output separate from active engine.
        original_settings = engine.read_json(engine.DATA / "settings.json", {})
        stack.enter_context(patch.object(engine, "DATA", engine.DATA / "validation" / release.name))
        engine.write_json(engine.DATA / "settings.json", original_settings)
    try:
        url = engine.ensure_ready()
        release, manifest = engine.runtime_info()
        profiles = engine.builtin_profiles()
        if not profiles:
            raise RuntimeError("No builtin workflows")
        profile = next((p for p in profiles if p["id"] == args.profile_id), None) if args.profile_id else profiles[0]
        if profile is None:
            raise ValueError("Unknown profile id")
        session = requests.Session()
        session.trust_env = False
        response = session.get(url + "/object_info", timeout=30)
        response.raise_for_status()
        nodes = response.json()
        required_classes = {node["class_type"] for p in profiles for node in p["workflow"].values()}
        missing = sorted(required_classes - nodes.keys())
        if missing:
            raise RuntimeError("Missing nodes: " + ", ".join(missing))
        print(json.dumps({"status": "startup_and_nodes_passed", "version": manifest["version"], "node_count": len(nodes), "profiles": [p["id"] for p in profiles], "required_classes": sorted(required_classes), "models_ready": engine.status()["models_ready"]}), flush=True)
        if args.reference:
            from backend.app import comfyui_bridge as bridge
            output = engine.DATA / f"{profile['id']}-{args.width}x{args.height}-{args.duration:g}s-smoke-test.mp4"
            started = time.monotonic()
            prompt = ('subject_definitions: <Subject 1> is the main subject in <Picture 1>.\n'
                      f'summary: A {args.duration:g}-second subtle animation faithfully preserving the reference composition, appearance and lighting.\n'
                      f'timeline: 0.0-{args.duration:g} seconds: <Subject 1> remains in place with very slight natural movement. Fixed camera.\n'
                      'audio: Quiet ambient environmental sound only. No speech, singing or music. No added text or subtitles.')
            with patch.object(bridge, "video_profile", return_value=profile), patch.object(bridge, "_saved_connection", return_value={"mode": "managed"}):
                bridge.run_video_profile(0, profile["id"], prompt=prompt, image_path=args.reference.resolve(),
                    duration=args.duration, ratio="16:9", output_path=output, resolution="custom", dimensions=(args.width, args.height),
                    progress=lambda value: print(value, flush=True), on_submitted=lambda value: print("Submitted: " + str(value), flush=True))
            print("GENERATION_PASSED: " + str(output), flush=True)
            engine.write_json(output.with_suffix(".validation.json"), {"profile_id": profile["id"], "width": args.width,
                "height": args.height, "requested_duration": args.duration, "elapsed_seconds": round(time.monotonic() - started, 2),
                "version": manifest["version"], "generation_passed": True})
    finally:
        try:
            engine.stop(shutdown=True)
        finally:
            stack.close()


if __name__ == "__main__":
    main()
