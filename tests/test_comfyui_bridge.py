import requests
from unittest.mock import patch

from backend.app.comfyui_bridge import (Binding, WorkflowMappings, _api_graph, _duration_value,
                                        _find_output_file, _history_payload, _patch_graph,
                                        _video_dimensions, video_task_state)


def graph():
    return {
        "1": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "old"}},
        "2": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
        "6": {"class_type": "LoadAudio", "inputs": {"audio": "old.wav"}},
        "3": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {"width": ["9", 0], "height": ["9", 1], "length": 124}},
        "4": {"class_type": "RandomNoise", "inputs": {"noise_seed": 1}},
        "5": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["8", 0]}},
    }


def mappings():
    return WorkflowMappings(
        prompt=Binding(node_id="1", input_name="value"),
        image=Binding(node_id="2", input_name="image"),
        duration=Binding(node_id="3", input_name="length"),
        width=Binding(node_id="3", input_name="width"),
        height=Binding(node_id="3", input_name="height"),
        seed=Binding(node_id="4", input_name="noise_seed"),
        output_node_id="5", fps=24, frame_divisor=17, frame_remainder=5,
    )


def test_h3_duration_is_rounded_up_to_allowed_frame_shape():
    assert _duration_value(5, mappings()) == 124
    assert _duration_value(5.2, mappings()) == 141


def test_video_resolution_preset_follows_project_orientation():
    profile = {"resolution_preset": "720p", "default_width": 1344, "default_height": 768}
    assert _video_dimensions(profile, "16:9") == (1280, 720)
    assert _video_dimensions(profile, "9:16") == (720, 1280)


def test_custom_and_legacy_video_resolution_keeps_saved_dimensions():
    profile = {"resolution_preset": "custom", "default_width": 1344, "default_height": 768}
    assert _video_dimensions(profile, "16:9") == (1344, 768)
    assert _video_dimensions(profile, "9:16") == (768, 1344)


def test_graph_patch_replaces_links_when_the_user_maps_core_dimensions():
    patched = _patch_graph(graph(), mappings(), prompt="hello", duration=5, width=1280, height=720, seed=42, image_name="ocv/ref.png")
    assert patched["1"]["inputs"]["value"] == "hello"
    assert patched["2"]["inputs"]["image"] == "ocv/ref.png"
    assert patched["3"]["inputs"] == {"width": 1280, "height": 720, "length": 124}
    assert patched["4"]["inputs"]["noise_seed"] == 42


def test_graph_patch_injects_reference_audio_when_mapped():
    configured = mappings()
    configured.audio = Binding(node_id="6", input_name="audio")
    patched = _patch_graph(graph(), configured, prompt="hello", duration=5, width=1280,
                           height=720, seed=42, image_name="ocv/ref.png",
                           audio_name="ocv/voice.wav")
    assert patched["6"]["inputs"]["audio"] == "ocv/voice.wav"


def test_graph_patch_disconnects_mapped_reference_audio_when_not_supplied():
    source = graph()
    source["3"]["inputs"]["ref_video_audios.ref_video_audio_0"] = ["6", 0]
    configured = mappings()
    configured.audio = Binding(node_id="6", input_name="audio")
    patched = _patch_graph(source, configured, prompt="hello", duration=5, width=1280,
                           height=720, seed=42, image_name="ocv/ref.png")
    assert "6" not in patched
    assert "ref_video_audios.ref_video_audio_0" not in patched["3"]["inputs"]


def test_output_supports_video_helper_gifs_bucket():
    output = _find_output_file({"outputs": {"5": {"gifs": [{"filename": "clip.mp4", "subfolder": "selflift", "type": "output"}]}}}, "5")
    assert output == {"filename": "clip.mp4", "subfolder": "selflift", "type": "output"}


def test_canvas_workflow_is_rejected_with_clear_message():
    try:
        _api_graph({"nodes": []})
    except ValueError as exc:
        assert "画布工作流" in str(exc)
    else:
        raise AssertionError("canvas workflow should not be executable")


def test_busy_comfyui_history_timeout_is_retryable_not_failure():
    connection = {"base_url": "http://127.0.0.1:8188", "history_path": "/history/{prompt_id}"}
    with patch("backend.app.comfyui_bridge.requests.get", side_effect=requests.ReadTimeout("busy")):
        assert _history_payload(connection, "task-1") is None


def test_video_task_is_missing_only_after_history_queue_history_confirmation():
    responses = []
    for payload in ({}, {"queue_running": [], "queue_pending": []}, {}):
        response = requests.Response()
        response.status_code = 200
        response._content = __import__('json').dumps(payload).encode()
        responses.append(response)
    connection = {**{"base_url": "http://127.0.0.1:8188", "history_path": "/history/{prompt_id}"}}
    with patch("backend.app.comfyui_bridge._connection", return_value=connection), \
         patch("backend.app.comfyui_bridge.requests.get", side_effect=responses):
        assert video_task_state(1, "old-task") == "missing"


def test_video_task_in_running_queue_is_not_missing():
    history = requests.Response()
    history.status_code = 200
    history._content = b"{}"
    queue = requests.Response()
    queue.status_code = 200
    queue._content = b'{"queue_running":[[1,"old-task",{},{}]],"queue_pending":[]}'
    connection = {"base_url": "http://127.0.0.1:8188", "history_path": "/history/{prompt_id}"}
    with patch("backend.app.comfyui_bridge._connection", return_value=connection), \
         patch("backend.app.comfyui_bridge.requests.get", side_effect=[history, queue]):
        assert video_task_state(1, "old-task") == "running"
