import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import module4_video_render as visual


class VisualConstraintsTest(unittest.TestCase):
    def test_repeated_visual_anchor_detector_finds_consecutive_table_shots(self) -> None:
        mapping = [
            {"image_prompt": "出租屋餐桌旁，两人隔着账单沉默"},
            {"image_prompt": "餐桌近景，账单压在冷掉的饭菜旁"},
            {"image_prompt": "低机位拍摄餐桌和账单"},
            {"image_prompt": "早高峰地铁车厢中的通勤者"},
        ]
        runs = visual._repeated_visual_anchor_runs(
            mapping,
            {"locations": [{"name": "出租屋餐桌"}]},
        )

        self.assertIn(("餐桌", 0, 2), runs)

    def test_agent2_prompt_requires_source_backed_broll(self) -> None:
        prompt = visual.build_visual_prompt_system(content_mode=visual.CONTENT_MODE_GENERAL)

        self.assertIn("说明性 B-roll", prompt)
        self.assertIn("通勤、工作、家务", prompt)

    def test_cloud_image_submit_includes_stable_client_job_id(self) -> None:
        captured_payloads = []

        class Response:
            ok = True
            status_code = 200

            @staticmethod
            def json():
                return {"code": 0, "data": {"taskId": "cloud-task-1"}}

        def request(_session, _method, _url, **kwargs):
            captured_payloads.append(kwargs["json"])
            return Response()

        macro = {"macro_scene_id": "poster_001", "image_prompt": "雨夜中的城市街道"}
        config = {
            "api_key": "cloud-access-token",
            "endpoint": "https://example.test/image-pool/generate",
            "ratio": "16:9",
            "resolution": "1k",
            "cloud_pool": "1",
        }
        with (
            patch.object(visual, "_request_with_cloud_refresh", side_effect=request),
            patch.dict(os.environ, {"VOICE_OVER_VIDEO_JOB_ID": "job-test-123"}, clear=False),
        ):
            first = visual._submit_poster_request(macro, config, object())
            second = visual._submit_poster_request(macro, config, object())

        self.assertEqual(first, "cloud-task-1")
        self.assertEqual(second, "cloud-task-1")
        self.assertEqual(captured_payloads[0]["clientJobId"], captured_payloads[1]["clientJobId"])
        self.assertRegex(
            captured_payloads[0]["clientJobId"],
            r"^ocv-job-test-123-poster_001-[0-9a-f]{16}$",
        )

    def test_cloud_terminal_failure_uses_new_retry_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "poster_002.jpg"
            macro = {
                "macro_scene_id": "poster_002",
                "image_prompt": "雨夜中的城市街道",
                "_output_path": str(output),
            }
            config = {"api_key": "cloud", "cloud_pool": "1", "account_label": "号池"}
            pool = visual.RunningHubAccountPool([config], per_key_concurrency=1)
            client_ids = []

            def submit(current_macro, _config):
                client_ids.append(visual._cloud_client_job_id(current_macro, {"prompt": current_macro["image_prompt"]}))
                return visual.PosterTask(current_macro, output, f"task-{len(client_ids)}")

            terminal = visual.RunningHubResultRetryableError(
                "cloud failed",
                confirmed_terminal=True,
                status="FAILED",
                error_code=1516,
                error_message="result file missing",
            )
            with (
                patch.object(visual, "CLOUD_RETRY_STATE_PATH", root / "retry-state.json"),
                patch.object(visual, "_submit_poster", side_effect=submit),
                patch.object(visual, "_wait_for_poster", side_effect=[terminal, output]),
                patch.object(visual, "_poster_output_path", return_value=output),
                patch.object(visual, "_retry_delay_seconds", return_value=0),
                patch.object(visual.time, "sleep"),
                patch.dict(os.environ, {"VOICE_OVER_VIDEO_JOB_ID": "job-terminal-test"}, clear=False),
            ):
                self.assertEqual(visual._render_poster_with_retry(macro, pool), output)

            self.assertEqual(len(client_ids), 2)
            self.assertNotEqual(client_ids[0], client_ids[1])
            self.assertRegex(client_ids[0], r"-[0-9a-f]{16}$")
            self.assertTrue(client_ids[1].endswith("-retry-1"))

    def test_cloud_unknown_result_reuses_original_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "poster_003.jpg"
            macro = {
                "macro_scene_id": "poster_003",
                "image_prompt": "清晨的河岸",
                "_output_path": str(output),
            }
            config = {"api_key": "cloud", "cloud_pool": "1", "account_label": "号池"}
            pool = visual.RunningHubAccountPool([config], per_key_concurrency=1)
            client_ids = []

            def submit(current_macro, _config):
                client_ids.append(visual._cloud_client_job_id(current_macro, {"prompt": current_macro["image_prompt"]}))
                return visual.PosterTask(current_macro, output, "same-server-task")

            unknown = visual.RunningHubResultRetryableError(
                "query timed out",
                confirmed_terminal=False,
                status="UNKNOWN",
            )
            with (
                patch.object(visual, "CLOUD_RETRY_STATE_PATH", root / "retry-state.json"),
                patch.object(visual, "_submit_poster", side_effect=submit),
                patch.object(visual, "_wait_for_poster", side_effect=[unknown, output]),
                patch.object(visual, "_poster_output_path", return_value=output),
                patch.object(visual, "_retry_delay_seconds", return_value=0),
                patch.object(visual.time, "sleep"),
                patch.dict(os.environ, {"VOICE_OVER_VIDEO_JOB_ID": "job-unknown-test"}, clear=False),
            ):
                self.assertEqual(visual._render_poster_with_retry(macro, pool), output)

            self.assertEqual(client_ids[0], client_ids[1])
            self.assertNotIn("-retry-", client_ids[0])

    def test_generic_cloud_terminal_failure_stops_after_one_new_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "poster_004.jpg"
            macro = {
                "macro_scene_id": "poster_004",
                "image_prompt": "普通场景",
                "_output_path": str(output),
            }
            config = {"api_key": "cloud", "cloud_pool": "1", "account_label": "号池"}
            pool = visual.RunningHubAccountPool([config], per_key_concurrency=1)
            client_ids = []

            def submit(current_macro, _config):
                client_ids.append(
                    visual._cloud_client_job_id(
                        current_macro, {"prompt": current_macro["image_prompt"]}
                    )
                )
                return visual.PosterTask(current_macro, output, f"task-{len(client_ids)}")

            terminal = visual.RunningHubResultRetryableError(
                "cloud failed",
                confirmed_terminal=True,
                status="FAILED",
                error_message="generic workflow failure",
            )
            with (
                patch.object(visual, "CLOUD_RETRY_STATE_PATH", root / "retry-state.json"),
                patch.object(visual, "_submit_poster", side_effect=submit),
                patch.object(visual, "_wait_for_poster", side_effect=[terminal, terminal]),
                patch.object(visual, "_poster_output_path", return_value=output),
                patch.object(visual, "_retry_delay_seconds", return_value=0),
                patch.object(visual.time, "sleep"),
                patch.dict(os.environ, {"VOICE_OVER_VIDEO_JOB_ID": "job-generic-failure"}, clear=False),
            ):
                with self.assertRaisesRegex(RuntimeError, "避免.*重复扣费"):
                    visual._render_poster_with_retry(macro, pool)

            self.assertEqual(len(client_ids), 2)
            self.assertNotEqual(client_ids[0], client_ids[1])
            self.assertTrue(client_ids[1].endswith("-retry-1"))

    def test_cloud_moderation_rewrite_uses_new_retry_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "poster_005.jpg"
            macro = {
                "macro_scene_id": "poster_005",
                "image_prompt": "危险场景",
                "_output_path": str(output),
            }
            config = {"api_key": "cloud", "cloud_pool": "1", "account_label": "号池"}
            pool = visual.RunningHubAccountPool([config], per_key_concurrency=1)
            identities_and_prompts = []

            def submit(current_macro, _config):
                identities_and_prompts.append((
                    visual._cloud_client_job_id(current_macro, {"prompt": current_macro["image_prompt"]}),
                    current_macro["image_prompt"],
                ))
                return visual.PosterTask(current_macro, output, f"task-{len(identities_and_prompts)}")

            blocked = visual.RunningHubModerationError(
                "blocked",
                confirmed_terminal=True,
                status="BLOCKED",
                error_code=1501,
                error_message="content policy",
            )
            with (
                patch.object(visual, "CLOUD_RETRY_STATE_PATH", root / "retry-state.json"),
                patch.object(visual, "_submit_poster", side_effect=submit),
                patch.object(visual, "_wait_for_poster", side_effect=[blocked, output]),
                patch.object(visual, "_retry_delay_seconds", return_value=0),
                patch.object(visual.time, "sleep"),
                patch.dict(os.environ, {"VOICE_OVER_VIDEO_JOB_ID": "job-moderation-test"}, clear=False),
            ):
                self.assertEqual(visual._render_poster_with_retry(macro, pool), output)

            self.assertNotEqual(identities_and_prompts[0][0], identities_and_prompts[1][0])
            self.assertTrue(identities_and_prompts[1][0].endswith("-retry-1"))
            self.assertNotEqual(identities_and_prompts[0][1], identities_and_prompts[1][1])

    def test_cloud_retry_generation_survives_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "retry-state.json"
            macro = {"macro_scene_id": "poster_006", "image_prompt": "山间公路"}
            with (
                patch.object(visual, "CLOUD_RETRY_STATE_PATH", state_path),
                patch.dict(os.environ, {"VOICE_OVER_VIDEO_JOB_ID": "job-resume-test"}, clear=False),
            ):
                visual._advance_cloud_retry_generation(
                    macro,
                    status="FAILED",
                    error_code=5001,
                    error_message="workflow failed",
                )
                resumed = visual._hydrate_cloud_retry_context({
                    "macro_scene_id": "poster_006",
                    "image_prompt": "山间公路",
                })
                client_id = visual._cloud_client_job_id(resumed, {"prompt": "山间公路"})

            self.assertEqual(resumed["_cloud_retry_generation"], 1)
            self.assertTrue(client_id.endswith("-retry-1"))

    def test_nested_cloud_failure_details_are_preserved(self) -> None:
        payload = {
            "status": "FAILED",
            "data": {"failure": {"error_code": 7301, "failureReason": "model workflow crashed"}},
        }
        self.assertEqual(visual._runninghub_result_error_code(payload), 7301)
        self.assertEqual(visual._runninghub_error_message(payload), "model workflow crashed")

    def test_wait_for_poster_marks_generic_failed_as_confirmed_terminal(self) -> None:
        task = visual.PosterTask(
            {"macro_scene_id": "poster_006"},
            Path("poster_006.jpg"),
            "img-failed-1",
        )
        result = {
            "status": "FAILED",
            "data": {"failure": {"error_code": 7301, "failureReason": "model workflow crashed"}},
        }
        config = {"api_key": "cloud", "query_url": "https://cloud.test/query", "cloud_pool": "1"}
        with (
            patch.object(visual, "_new_session", return_value=object()),
            patch.object(visual, "_request_json", return_value=result),
        ):
            with self.assertRaises(visual.RunningHubResultRetryableError) as raised:
                visual._wait_for_poster(task, config)

        self.assertTrue(raised.exception.confirmed_terminal)
        self.assertEqual(raised.exception.status, "FAILED")
        self.assertEqual(raised.exception.error_code, 7301)
        self.assertIn("model workflow crashed", str(raised.exception))

    def test_cloud_reference_upload_replays_bytes_after_token_refresh(self) -> None:
        class Response:
            def __init__(self, status_code: int, payload: dict):
                self.status_code = status_code
                self.ok = status_code < 400
                self._payload = payload

            def json(self):
                return self._payload

            def close(self):
                return None

            def raise_for_status(self):
                if not self.ok:
                    raise RuntimeError(f"HTTP {self.status_code}")

        class Session:
            def __init__(self):
                self.calls = []

            def request(self, method, url, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    return Response(401, {})
                return Response(200, {"code": 0, "data": {"download_url": "https://cdn.test/ref.png"}})

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        config = {
            "api_key": "expired-token",
            "refresh_token": "refresh-token",
            "cloud_base_url": "https://cloud.test/api/v1",
            "upload_url": "https://cloud.test/api/v1/image-pool/media/upload",
            "cloud_pool": "1",
        }
        session = Session()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.png"
            content = b"fake-image-bytes"
            path.write_bytes(content)
            with (
                patch.object(visual, "_new_session", return_value=session),
                patch.object(visual.requests, "post", return_value=Response(200, {"access_token": "fresh-token", "refresh_token": "fresh-refresh"})),
            ):
                self.assertEqual(visual._reference_image_url(config, str(path)), "https://cdn.test/ref.png")

        uploaded = session.calls[1]["files"]["file"][1]
        self.assertEqual(uploaded, content)
        self.assertEqual(config["api_key"], "fresh-token")

    def test_cloud_reference_upload_resolves_relative_media_url(self) -> None:
        class Response:
            status_code = 200
            ok = True

            def json(self):
                return {"code": 0, "data": {"download_url": "/api/v1/image-pool/media/asset-1"}}

            def close(self):
                return None

        class Session:
            def request(self, method, url, **kwargs):
                return Response()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        config = {
            "api_key": "cloud-token",
            "cloud_base_url": "https://cloud.test/api/v1",
            "upload_url": "https://cloud.test/api/v1/image-pool/media/upload",
            "cloud_pool": "1",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.png"
            path.write_bytes(b"fake-image-bytes")
            with patch.object(visual, "_new_session", return_value=Session()):
                self.assertEqual(
                    visual._reference_image_url(config, str(path)),
                    "https://cloud.test/api/v1/image-pool/media/asset-1",
                )

    def test_cloud_reference_upload_falls_back_to_data_uri_on_proxy_500(self) -> None:
        class Response:
            status_code = 500
            ok = False

            def json(self):
                return {"code": "INTERNAL_ERROR", "message": "upstream failed"}

            def close(self):
                return None

        class Session:
            def request(self, method, url, **kwargs):
                return Response()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        config = {
            "api_key": "cloud-token",
            "cloud_base_url": "https://cloud.test/api/v1",
            "upload_url": "https://cloud.test/api/v1/image-pool/media/upload",
            "cloud_pool": "1",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.png"
            content = b"fake-image-bytes"
            path.write_bytes(content)
            with patch.object(visual, "_new_session", return_value=Session()):
                result = visual._reference_image_url(config, str(path))
        self.assertTrue(result.startswith("data:image/png;base64,"))
        self.assertIn("ZmFrZS1pbWFnZS1ieXRlcw==", result)

    def test_poster_submit_uses_reference_data_uri_after_upload_failure(self) -> None:
        captured = {}

        class Response:
            ok = True
            status_code = 200

            @staticmethod
            def json():
                return {"taskId": "image-task-1"}

        def request(_session, _method, url, **kwargs):
            captured["url"] = url
            captured["payload"] = kwargs["json"]
            return Response()

        macro = {
            "macro_scene_id": "poster_001",
            "image_prompt": "林晚站在厨房，角色形象参考图1",
            "character_ids": ["lin_wan"],
            "reference_image_ids": ["图1"],
        }
        config = {
            "api_key": "cloud-token",
            "endpoint": "https://cloud.test/image-pool/generate",
            "ratio": "2:1",
            "resolution": "1k",
            "cloud_pool": "1",
        }
        with (
            patch.dict(os.environ, {"USER_REFERENCE_IMAGE_PATHS_JSON": "[\"/tmp/lin.png\"]"}, clear=False),
            patch.object(visual, "_reference_image_url", return_value="data:image/png;base64,AAA"),
            patch.object(visual, "_request_with_cloud_refresh", side_effect=request),
        ):
            task_id = visual._submit_poster_request(macro, config, object())

        self.assertEqual(task_id, "image-task-1")
        self.assertEqual(captured["payload"]["imageUrls"], ["data:image/png;base64,AAA"])
        self.assertEqual(captured["url"], config["endpoint"])

    def test_direct_runninghub_submit_does_not_include_client_job_id(self) -> None:
        captured_payload = {}

        class Response:
            ok = True
            status_code = 200

            @staticmethod
            def json():
                return {"taskId": "runninghub-task-1"}

        def request(_session, _method, _url, **kwargs):
            captured_payload.update(kwargs["json"])
            return Response()

        config = {
            "api_key": "runninghub-key",
            "endpoint": "text-to-image",
            "ratio": "16:9",
            "resolution": "1k",
        }
        with patch.object(visual, "_request_with_cloud_refresh", side_effect=request):
            visual._submit_poster_request(
                {"macro_scene_id": "poster_001", "image_prompt": "城市街道"},
                config,
                object(),
            )

        self.assertNotIn("clientJobId", captured_payload)

    def test_direct_runninghub_urls_use_global_configurable_base(self) -> None:
        config = {"endpoint": "/rhart-image-g-2/text-to-image"}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RUNNINGHUB_BASE_URL", None)
            self.assertEqual(
                visual._runninghub_generate_url(config),
                "https://www.runninghub.ai/openapi/v2/rhart-image-g-2/text-to-image",
            )
            self.assertEqual(
                visual._runninghub_url("/openapi/v2/query"),
                "https://www.runninghub.ai/openapi/v2/query",
            )
        with patch.dict(
            os.environ,
            {"RUNNINGHUB_BASE_URL": "https://images.example.test/root/"},
            clear=False,
        ):
            self.assertEqual(
                visual._runninghub_generate_url(config),
                "https://images.example.test/root/openapi/v2/rhart-image-g-2/text-to-image",
            )
            self.assertEqual(
                visual._runninghub_url("/uc/openapi/accountStatus"),
                "https://images.example.test/root/uc/openapi/accountStatus",
            )
            self.assertEqual(
                visual._runninghub_url("/openapi/v2/media/upload/binary"),
                "https://images.example.test/root/openapi/v2/media/upload/binary",
            )

        cloud_url = "https://cloud.example.test/api/v1/image-pool/generate"
        self.assertEqual(visual._runninghub_generate_url({"endpoint": cloud_url}), cloud_url)

    def test_runninghub_documented_errors_use_bounded_categories(self) -> None:
        for code in (416, 812):
            with self.subTest(code=code), self.assertRaises(visual.RunningHubPowerInsufficient):
                visual._handle_runninghub_submit_error({"errorCode": code})
        with self.assertRaises(visual.RunningHubAccessDenied):
            visual._handle_runninghub_submit_error({"errorCode": 40310})
        with self.assertRaises(visual.RunningHubModerationError):
            visual._handle_runninghub_submit_error({"errorCode": 1501})
        with self.assertRaises(visual.RunningHubResultRetryableError):
            visual._handle_runninghub_submit_error({"errorCode": 1504})

    def test_strict_agent2_failure_aborts_without_local_prompt_fallback(self) -> None:
        scenes = [{
            "slide_id": "scene_001",
            "start": 0,
            "end": 6,
            "text_content": "她走进空荡的房间。",
            "visual_summary": "她走进空荡的房间。",
        }]
        with (
            patch.object(visual, "gemini_configured", return_value=True),
            patch.object(
                visual,
                "generate_gemini_text",
                side_effect=visual.GeminiError("HTTP 502 upstream timed out"),
            ),
            patch.object(visual, "_fallback_mapping") as fallback,
            patch.dict(os.environ, {"REQUIRE_AI_AGENT_SUCCESS": "1"}, clear=False),
        ):
            with self.assertRaisesRegex(RuntimeError, "Agent 2.*提交 Image2 前安全终止.*HTTP 502"):
                visual.build_macro_mapping(scenes)
        fallback.assert_not_called()

    def test_strict_agent2_requires_configured_language_model(self) -> None:
        with (
            patch.object(visual, "gemini_configured", return_value=False),
            patch.dict(os.environ, {"REQUIRE_AI_AGENT_SUCCESS": "1"}, clear=False),
        ):
            with self.assertRaisesRegex(RuntimeError, "语言模型未配置.*提交 Image2 前安全终止"):
                visual.build_macro_mapping([])

    def test_one_failed_image_reuses_neighbor_without_stopping_batch(self) -> None:
        mapping = [
            {"macro_scene_id": f"poster_{index:03d}", "image_prompt": f"画面 {index}"}
            for index in range(1, 4)
        ]

        def render_one(macro, _pool):
            if macro["macro_scene_id"] == "poster_002":
                raise RuntimeError("模拟单图持续失败")
            return Path(f"{macro['macro_scene_id']}.jpg")

        with (
            patch.object(visual, "_render_poster_with_retry", side_effect=render_one),
            patch.dict(
                os.environ,
                {"RUNNINGHUB_ACTIVE_TASK_CONCURRENCY": "2", "RUNNINGHUB_ALLOW_NEIGHBOR_FALLBACK": "1"},
                clear=False,
            ),
        ):
            results = visual.render_posters_concurrently(mapping, [{}])
        self.assertEqual(len(results), 3)
        self.assertIn(results[1], {results[0], results[2]})

    def test_cloud_pool_always_enqueues_the_whole_image_batch(self) -> None:
        with patch.dict(os.environ, {"RUNNINGHUB_ACTIVE_TASK_CONCURRENCY": "1"}):
            self.assertEqual(
                visual._poster_worker_count([{"cloud_pool": "1"}], 12),
                12,
            )
            self.assertEqual(visual._poster_worker_count([{}], 12), 1)
        pool = visual.RunningHubAccountPool(
            [{"api_key": "cloud-token", "cloud_pool": "1", "account_label": "云端号池"}],
            per_key_concurrency=12,
        )
        leases = [pool.acquire() for _ in range(12)]
        for lease in leases:
            pool.release(lease)

    def test_cloud_balance_failure_checkpoints_completed_images_for_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            visual_dir = root / "visual"
            assets_dir = visual_dir / "assets"
            mapping_path = visual_dir / "poster_mapping.json"
            plan_path = visual_dir / "visual_prompt_plan.json"
            checkpoint = root / "job" / "visual_runtime"
            assets_dir.mkdir(parents=True)
            mapping = [
                {"macro_scene_id": "poster_001", "image_prompt": "第一张"},
                {"macro_scene_id": "poster_002", "image_prompt": "第二张"},
            ]
            mapping_path.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
            plan_path.write_text(json.dumps({"mapping": mapping}, ensure_ascii=False), encoding="utf-8")
            first_completed = threading.Event()

            def render_one(macro, _pool):
                if macro["macro_scene_id"] == "poster_001":
                    output = assets_dir / "poster_001_stable.jpg"
                    output.write_bytes(b"valid-image")
                    first_completed.set()
                    return output
                self.assertTrue(first_completed.wait(timeout=2))
                raise visual.RunningHubAllAccountsPowerInsufficient("HTTP 402")

            with (
                patch.object(visual, "VISUAL_DIR", visual_dir),
                patch.object(visual, "ASSETS_DIR", assets_dir),
                patch.object(visual, "POSTER_MAPPING_PATH", mapping_path),
                patch.object(visual, "VISUAL_PROMPT_PLAN_PATH", plan_path),
                patch.object(visual, "_render_poster_with_retry", side_effect=render_one),
                patch.dict(os.environ, {"VISUAL_CHECKPOINT_DIR": str(checkpoint)}, clear=False),
            ):
                with self.assertRaisesRegex(RuntimeError, "断点续跑.*已完成图片"):
                    visual.render_posters_concurrently(
                        mapping,
                        [{"api_key": "cloud", "cloud_pool": "1"}],
                    )

            saved_mapping = json.loads((checkpoint / "poster_mapping.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_mapping[0]["asset_filename"], "poster_001_stable.jpg")
            self.assertTrue((checkpoint / "assets" / "poster_001_stable.jpg").is_file())

    def test_visual_checkpoint_restores_mapping_plan_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            visual_dir = root / "visual"
            assets_dir = visual_dir / "assets"
            checkpoint = root / "checkpoint"
            (checkpoint / "assets").mkdir(parents=True)
            (checkpoint / "poster_mapping.json").write_text("[]", encoding="utf-8")
            (checkpoint / "visual_prompt_plan.json").write_text("{}", encoding="utf-8")
            (checkpoint / "assets" / "poster_001_hash.jpg").write_bytes(b"image")
            with (
                patch.object(visual, "VISUAL_DIR", visual_dir),
                patch.object(visual, "ASSETS_DIR", assets_dir),
                patch.object(visual, "POSTER_MAPPING_PATH", visual_dir / "poster_mapping.json"),
                patch.object(visual, "VISUAL_PROMPT_PLAN_PATH", visual_dir / "visual_prompt_plan.json"),
                patch.dict(os.environ, {"VISUAL_CHECKPOINT_DIR": str(checkpoint)}, clear=False),
            ):
                self.assertTrue(visual._restore_visual_checkpoint())
                self.assertTrue((visual_dir / "poster_mapping.json").is_file())
                self.assertTrue((visual_dir / "visual_prompt_plan.json").is_file())
                self.assertTrue((assets_dir / "poster_001_hash.jpg").is_file())

    def test_auto_image_concurrency_uses_all_configured_key_capacity(self) -> None:
        configs = [{"api_key": f"key-{index}"} for index in range(7)]
        with patch.dict(os.environ, {
            "RUNNINGHUB_CONCURRENCY_MODE": "auto",
            "RUNNINGHUB_PER_KEY_CONCURRENCY": "1",
            "RUNNINGHUB_ACTIVE_TASK_CONCURRENCY": "3",
        }, clear=False):
            self.assertEqual(visual._poster_worker_count(configs, 20), 7)

    def test_manual_image_concurrency_respects_account_and_total_limits(self) -> None:
        configs = [{"api_key": "key-1"}, {"api_key": "key-2"}]
        with patch.dict(os.environ, {
            "RUNNINGHUB_CONCURRENCY_MODE": "manual",
            "RUNNINGHUB_PER_KEY_CONCURRENCY": "2",
            "RUNNINGHUB_ACTIVE_TASK_CONCURRENCY": "3",
        }, clear=False):
            self.assertEqual(visual._poster_worker_count(configs, 20), 3)

    def test_moderation_failure_is_rewritten_for_single_image_retry(self) -> None:
        self.assertTrue(visual._looks_like_moderation_failure("content safety blocked"))
        with self.assertRaises(visual.RunningHubModerationError):
            visual._handle_runninghub_submit_error(
                {"code": 400, "message": "提示词触发内容审核"},
                400,
            )
        rewritten = visual._rewrite_prompt_after_moderation(
            "走廊中出现腐烂尸体和满地鲜血。",
            1,
        )
        self.assertNotIn("腐烂尸体", rewritten)
        self.assertNotIn("满地鲜血", rewritten)
        self.assertIn("安全重绘", rewritten)
        self.assertIn("遮挡", rewritten)

    def test_rate_limit_is_treated_as_account_concurrency_backoff(self) -> None:
        with self.assertRaises(visual.RunningHubQueueFull):
            visual._handle_runninghub_submit_error({"code": 429, "message": "too many requests"}, 429)

    def test_science_mode_restores_red_scarf_girl_and_science_agent(self) -> None:
        self.assertNotIn("黑色短发", visual.SCIENCE_VISUAL_STYLE)
        self.assertIn("黑色短发", visual.SCIENCE_GLOBAL_CHARACTER_PROMPT)
        self.assertIn("红色围巾", visual.SCIENCE_GLOBAL_CHARACTER_PROMPT)
        system_prompt = visual.build_visual_prompt_system(
            content_mode=visual.CONTENT_MODE_SCIENCE,
        )
        self.assertIn("科普科技口播视频", system_prompt)
        self.assertIn("知识点", system_prompt)
        self.assertIn("设备交互", system_prompt)
        self.assertIn("屏幕背向镜头、虚化或不可读", system_prompt)
        self.assertIn("设备正面屏幕插入特写", system_prompt)
        self.assertNotIn("设备使用者第一视角", system_prompt)

    def test_default_style_and_object_closeup_rule_match_story_profile(self) -> None:
        self.assertIn("伊藤润二式惊悚漫画", visual.DEFAULT_VISUAL_STYLE)
        self.assertIn("高反差电影光影", visual.DEFAULT_VISUAL_STYLE)
        self.assertIn("薄雾与局部轮廓光", visual.DEFAULT_VISUAL_STYLE)
        self.assertIn("夸张血腥", visual.DEFAULT_VISUAL_STYLE)
        system_prompt = visual.build_visual_prompt_system()
        self.assertIn("手机、平板、电脑显示器", system_prompt)
        self.assertIn("屏幕背向镜头、虚化或不可读", system_prompt)
        self.assertIn("设备正面屏幕插入特写", system_prompt)
        self.assertNotIn("红色鸭舌帽", visual.DEFAULT_VISUAL_STYLE)
        self.assertIn("红色鸭舌帽", visual.DEFAULT_GLOBAL_CHARACTER_PROMPT)

    def test_device_creative_guidance_is_present_in_every_default_mode(self) -> None:
        for content_mode in (
            visual.CONTENT_MODE_STORY,
            visual.CONTENT_MODE_SCIENCE,
            visual.CONTENT_MODE_PURE_SCIENCE,
            visual.CONTENT_MODE_GENERAL,
        ):
            with self.subTest(content_mode=content_mode):
                system_prompt = visual.build_visual_prompt_system(content_mode=content_mode)
                self.assertIn("仅有查看、拿取、操作或接听动作", system_prompt)
                self.assertIn("屏幕背向镜头、虚化或不可读", system_prompt)
                self.assertIn("只有原文明示具体文字、照片、监控、网页或文件内容", system_prompt)
                self.assertIn("不并列人物脸部特写", system_prompt)

    def test_pure_science_mode_allows_scientific_labels_without_default_character(self) -> None:
        system_prompt = visual.build_visual_prompt_system(
            content_mode=visual.CONTENT_MODE_PURE_SCIENCE,
        )
        self.assertIn("严肃科普", system_prompt)
        self.assertIn("禁止默认套用生物学", system_prompt)
        self.assertIn("物理可用受力图", system_prompt)
        self.assertIn("历史、地理与社会知识", system_prompt)
        self.assertIn("ATP、ADP、Pi", system_prompt)
        self.assertIn("公式", system_prompt)
        self.assertIn("不设置机械的 20 字上限", system_prompt)
        self.assertIn("默认没有固定主持人物", system_prompt)
        self.assertNotIn("红色围巾", system_prompt)
        self.assertNotIn("黑色短发", system_prompt)

    def test_pure_science_fallback_uses_structures_instead_of_host(self) -> None:
        scenes = [{
            "slide_id": "scene_001", "start": 0, "end": 8,
            "text_content": "ATP 水解为 ADP 和 Pi，并释放能量。",
            "visual_summary": "ATP 水解过程",
        }]
        with patch.dict(
            os.environ,
            {"CONTENT_MODE": visual.CONTENT_MODE_PURE_SCIENCE},
            clear=False,
        ):
            mapping = visual._fallback_mapping(scenes)
        self.assertEqual(len(mapping), 1)
        prompt = mapping[0]["image_prompt"]
        self.assertIn("跨学科", prompt)
        self.assertNotIn("科普少女", prompt)
        self.assertNotIn("红色围巾", prompt)

    def test_explicit_screen_content_becomes_device_only_and_clears_character_reference(self) -> None:
        scenes = [{
            "slide_id": "scene_001", "start": 0, "end": 6,
            "text_content": "她举起手机，屏幕上写着“今晚别回家”。",
        }]
        mapping = [{
            "includes_slides": ["scene_001"],
            "image_prompt": "林晚举着手机贴近脸部，角色形象参考图1，惊恐地看向镜头。",
            "character_ids": ["lin_wan"],
            "reference_image_ids": ["图1"],
        }]
        story_plan = {
            "characters": [{
                "character_id": "lin_wan", "name": "林晚", "role": "主角",
                "appearance": "35岁黑色长发女性",
            }],
            "semantic_units": [{
                "start_slide_id": "scene_001", "end_slide_id": "scene_001",
                "character_ids": [], "device_shot_mode": "screen_insert",
                "device_type": "手机", "screen_content": "今晚别回家",
            }],
        }
        with patch.dict(os.environ, {"USER_REFERENCE_IMAGE_PATHS_JSON": '["lin.png"]'}, clear=False):
            result = visual._finalize_mapping(mapping, scenes, story_plan)[0]
        self.assertEqual(result["device_shot_mode"], "screen_insert")
        self.assertEqual(result["character_ids"], [])
        self.assertEqual(result["reference_image_ids"], [])
        self.assertIn("只展示手机正面屏幕", result["image_prompt"])
        self.assertIn("今晚别回家", result["image_prompt"])
        self.assertIn("可根据常见应用形态设计合理的状态栏", result["image_prompt"])
        self.assertNotIn("屏幕内容严格依据原文", result["image_prompt"])
        self.assertNotIn("林晚举着手机贴近脸部", result["image_prompt"])
        self.assertNotIn("本镜头唯一角色卡", result["image_prompt"])

    def test_unspecified_screen_content_keeps_person_but_forbids_readable_ui(self) -> None:
        scenes = [{
            "slide_id": "scene_001", "start": 0, "end": 6,
            "text_content": "林晚坐在床边，低头看了很久手机。",
        }]
        mapping = [{
            "includes_slides": ["scene_001"],
            "image_prompt": "林晚坐在床边低头查看手机，神情疲惫。",
            "character_ids": ["lin_wan"],
            "reference_image_ids": ["图1"],
        }]
        story_plan = {
            "characters": [{
                "character_id": "lin_wan", "name": "林晚", "role": "主角",
                "appearance": "35岁黑色长发女性",
            }],
            "semantic_units": [{
                "start_slide_id": "scene_001", "end_slide_id": "scene_001",
                "character_ids": ["lin_wan"], "device_shot_mode": "device_interaction",
                "device_type": "手机", "screen_content": "不应保留",
            }],
        }
        with patch.dict(os.environ, {"USER_REFERENCE_IMAGE_PATHS_JSON": '["lin.png"]'}, clear=False):
            result = visual._finalize_mapping(mapping, scenes, story_plan)[0]
        self.assertEqual(result["device_shot_mode"], "device_interaction")
        self.assertEqual(result["reference_image_ids"], ["图1"])
        self.assertEqual(result["screen_content"], "")
        self.assertIn("屏幕必须背向镜头、虚化或不可读", result["image_prompt"])
        self.assertIn("林晚", result["image_prompt"])

    def test_long_device_unit_is_scoped_to_each_child_poster(self) -> None:
        scenes = [
            {"slide_id": "scene_017", "start": 0, "end": 3, "text_content": "面前摆着三样东西：二胎孕检报告单，"},
            {"slide_id": "scene_020", "start": 3, "end": 6, "text_content": "小号里仅一人可见的密友动态，"},
            {"slide_id": "scene_023", "start": 6, "end": 9, "text_content": "老城区民宿的入住时间账单。"},
        ]
        mapping = [
            {"includes_slides": [scene["slide_id"]], "image_prompt": f"独有镜头 {index}"}
            for index, scene in enumerate(scenes, 1)
        ]
        story_plan = {"semantic_units": [{
            "start_slide_id": "scene_017", "end_slide_id": "scene_023",
            "character_ids": [], "device_shot_mode": "screen_insert",
            "device_type": "纸质报告/手机/打印账单",
            "screen_content": "二胎孕检报告单、小号密友动态、老城区民宿入住账单",
        }]}

        result = visual._finalize_mapping(mapping, scenes, story_plan)

        self.assertEqual([item["screen_content"] for item in result], [
            "二胎孕检报告单", "小号密友动态", "老城区民宿入住账单",
        ])
        self.assertEqual([item["device_type"] for item in result], ["纸质报告", "手机", "打印账单"])
        self.assertEqual(len({item["image_prompt"] for item in result}), 3)

    def test_agent0_information_registry_disambiguates_adjacent_evidence_posters(self) -> None:
        scenes = [
            {"slide_id": "scene_020", "start": 0, "end": 2, "text_content": "一人可见的密友动态，"},
            {"slide_id": "scene_021", "start": 2, "end": 4, "text_content": "还有一张打印出来的账单——"},
            {"slide_id": "scene_022", "start": 4, "end": 6, "text_content": "那间开在老城区、"},
            {"slide_id": "scene_023", "start": 6, "end": 8, "text_content": "打着咖啡旗号的民宿，"},
            {"slide_id": "scene_024", "start": 8, "end": 10, "text_content": "每一笔入住时间"},
            {"slide_id": "scene_025", "start": 10, "end": 12, "text_content": "都对应着她声称带孩子补习的时间。"},
        ]
        mapping = [
            {"includes_slides": ["scene_020", "scene_021", "scene_022"], "image_prompt": "证据镜头一"},
            {"includes_slides": ["scene_023", "scene_024", "scene_025"], "image_prompt": "证据镜头二"},
        ]
        story_plan = {
            "key_information_objects": [
                {"object_id": "social", "device_type": "手机", "content": "小号里上百条仅一人可见的密友动态"},
                {"object_id": "hotel", "device_type": "打印账单", "content": "老城区民宿的入住时间记录，对应她声称带孩子补习的时间"},
            ],
            # Agent 1 intentionally omitted the social-media item, reproducing
            # the real failure that previously made both posters inherit the bill.
            "semantic_units": [{
                "start_slide_id": "scene_020", "end_slide_id": "scene_025",
                "character_ids": [], "device_shot_mode": "screen_insert",
                "device_type": "纸质报告与打印账单",
                "screen_content": "老城区民宿入住时间记录",
            }],
        }

        result = visual._finalize_mapping(mapping, scenes, story_plan)

        self.assertEqual(result[0]["device_type"], "手机")
        self.assertIn("密友动态", result[0]["screen_content"])
        self.assertEqual(result[1]["device_type"], "打印账单")
        self.assertIn("民宿的入住时间记录", result[1]["screen_content"])
        self.assertNotEqual(result[0]["image_prompt"], result[1]["image_prompt"])
        self.assertIn("信息载体特写硬约束", result[1]["image_prompt"])
        self.assertNotIn("正面屏幕", result[1]["image_prompt"])

    def test_screen_insert_does_not_leak_into_setup_or_consequence_groups(self) -> None:
        scenes = [
            {"slide_id": "scene_179", "start": 0, "end": 3, "text_content": "丈夫没有给你任何协商空间。"},
            {"slide_id": "scene_182", "start": 3, "end": 6, "text_content": "调查报告列出住房记录和消费明细。"},
            {"slide_id": "scene_185", "start": 6, "end": 9, "text_content": "还有咖啡馆后巷拥抱的照片。"},
            {"slide_id": "scene_188", "start": 9, "end": 12, "text_content": "法院判决后，她被要求搬出家。"},
        ]
        mapping = [
            {"includes_slides": [scene["slide_id"]], "image_prompt": f"独有场景 {index}"}
            for index, scene in enumerate(scenes, 1)
        ]
        story_plan = {"semantic_units": [{
            "start_slide_id": "scene_179", "end_slide_id": "scene_188",
            "character_ids": [], "device_shot_mode": "screen_insert",
            "device_type": "纸质文件",
            "screen_content": "住房记录、消费明细、咖啡馆后巷拥抱照片",
        }]}

        result = visual._finalize_mapping(mapping, scenes, story_plan)

        self.assertEqual([item["device_shot_mode"] for item in result], [
            "none", "screen_insert", "screen_insert", "none",
        ])
        self.assertIn("独有场景 1", result[0]["image_prompt"])
        self.assertIn("独有场景 4", result[3]["image_prompt"])
        self.assertEqual(result[1]["screen_content"], "住房记录")
        self.assertEqual(result[2]["screen_content"], "咖啡馆后巷拥抱照片")

    def test_pacing_groups_use_agent_one_recommendation_and_real_timestamps(self) -> None:
        scenes = [
            {"slide_id": f"scene_{index:03d}", "start": (index - 1) * 3, "end": index * 3}
            for index in range(1, 7)
        ]
        plan = {"story_beats": [
            {"slide_ids": ["scene_001", "scene_002"], "visual_pacing": "hold"},
            {"slide_ids": ["scene_003", "scene_004"], "visual_pacing": "fast"},
            {"slide_ids": ["scene_005", "scene_006"], "visual_pacing": "normal"},
        ]}
        groups = visual._visual_groups(scenes, plan)
        self.assertEqual([[item["slide_id"] for item in group] for group in groups], [
            ["scene_001", "scene_002"], ["scene_003", "scene_004"], ["scene_005", "scene_006"],
        ])

    def test_character_name_is_expanded_and_style_meta_is_not_sent_to_image_model(self) -> None:
        scenes = [
            {"slide_id": "scene_001", "start": 0, "end": 5},
            {"slide_id": "scene_002", "start": 5, "end": 10},
        ]
        mapping = [{
            "macro_scene_id": "poster_001",
            "includes_slides": ["scene_001"],
            "image_prompt": "阿凯在萱萱妈妈身旁骑行，萱萱妈妈神情恍惚。",
        }]
        story_plan = {
            "characters": [{
                "name": "萱萱妈妈",
                "role": "主角",
                "appearance": "30岁左右、扎马尾的女性",
                "wardrobe": "前期居家服，后期骑行服或运动装",
                "wardrobe_states": [{
                    "state_id": "ride",
                    "start_slide_id": "scene_001",
                    "end_slide_id": "scene_002",
                    "wardrobe": "磨旧的深灰色骑行服",
                    "headwear": "白色骑行头盔",
                    "carried_items": "旧自行车",
                }],
                "signature_item": "头盔",
            }],
        }
        style = "都市悬疑漫画；主角为35岁中年女性，黑色长发，随时都戴着红色鸭舌帽。"
        with patch.dict(os.environ, {"VISUAL_STYLE_PROMPT": style}, clear=False):
            result = visual._finalize_mapping(mapping, scenes, story_plan)
        prompt = result[0]["image_prompt"]
        self.assertIn("萱萱妈妈：35岁中年女性", prompt)
        self.assertEqual(prompt.count("35岁中年女性，黑色长发，随时都戴着红色鸭舌帽"), 1)
        self.assertIn("本镜头服装=磨旧的深灰色骑行服", prompt)
        self.assertNotIn("前期居家服，后期骑行服或运动装", prompt)
        self.assertNotIn("本镜头头部状态=白色骑行头盔", prompt)
        self.assertNotIn("同一角色的脸型、发型、年龄、服装和标志性物件", prompt)
        self.assertIn("本镜头唯一角色卡", prompt)
        self.assertIn("【统一画面风格】", prompt)
        self.assertIn("【视觉媒介锁】", prompt)

    def test_duration_and_character_style_are_enforced(self) -> None:
        scenes = [
            {
                "slide_id": f"scene_{index:03d}",
                "start": (index - 1) * 5.0,
                "end": index * 5.0,
                "text_content": f"第 {index} 句",
                "visual_summary": f"第 {index} 句",
            }
            for index in range(1, 9)
        ]
        style = "黑色短发带红色围巾的可爱少女，科教手绘漫画风。"
        with (
            patch.object(visual, "gemini_configured", return_value=False),
            patch.dict(os.environ, {"VISUAL_STYLE_PROMPT": style}, clear=False),
        ):
            mapping = visual.build_macro_mapping(scenes)

        scenes_by_id = {scene["slide_id"]: scene for scene in scenes}
        covered = []
        for item in mapping:
            included = [scenes_by_id[slide_id] for slide_id in item["includes_slides"]]
            duration = max(scene["end"] for scene in included) - min(scene["start"] for scene in included)
            self.assertLessEqual(duration, 15.0)
            self.assertIn(style.rstrip("。"), item["image_prompt"])
            self.assertIn("保留所选画风需要的线稿", item["image_prompt"])
            self.assertEqual(item["image_prompt"].count(style.rstrip("。")), 1)
            self.assertEqual(item["image_prompt"].count("保留所选画风需要的线稿"), 1)
            covered.extend(item["includes_slides"])
        self.assertEqual(covered, list(scenes_by_id))

    def test_existing_exact_style_and_quality_are_deduplicated(self) -> None:
        style = "黑色短发带红色围巾的可爱少女，科教手绘漫画风。"
        quality = "去除燥波燥点，去除涂抹感，色彩平滑，画面严格执行干净质感。"
        scenes = [{"slide_id": "scene_001", "start": 0, "end": 5}]
        mapping = [{
            "macro_scene_id": "poster_001",
            "includes_slides": ["scene_001"],
            "image_prompt": f"{style}\n少女站在讲台前。\n{quality}",
        }]
        with patch.dict(os.environ, {"VISUAL_STYLE_PROMPT": style}, clear=False):
            result = visual._finalize_mapping(mapping, scenes)
        prompt = result[0]["image_prompt"]
        self.assertEqual(prompt.count(style.rstrip("。")), 1)
        self.assertNotIn("去除燥波燥点", prompt)
        self.assertEqual(prompt.count("保留所选画风需要的线稿"), 1)
        self.assertIn("少女站在讲台前", prompt)

    def test_quality_is_not_duplicated_when_forced_style_already_contains_it(self) -> None:
        quality = "去除燥波燥点，去除涂抹感，色彩平滑，画面严格执行干净质感。"
        style = f"中式阴森漫画，红色鸭舌帽。{quality}"
        scenes = [{"slide_id": "scene_001", "start": 0, "end": 5}]
        mapping = [{
            "macro_scene_id": "poster_001",
            "includes_slides": ["scene_001"],
            "image_prompt": "女人查看手机。",
        }]
        with patch.dict(os.environ, {"VISUAL_STYLE_PROMPT": style}, clear=False):
            result = visual._finalize_mapping(mapping, scenes)
        self.assertNotIn("去除燥波燥点", result[0]["image_prompt"])
        self.assertEqual(result[0]["image_prompt"].count("保留所选画风需要的线稿"), 1)

    def test_explicit_imagery_is_rewritten_before_submission(self) -> None:
        prompt = "走廊里出现腐烂尸体，满地鲜血，画面血肉模糊。"
        guarded = visual._apply_visual_safety_guard(prompt)
        self.assertNotIn("腐烂尸体", guarded)
        self.assertNotIn("满地鲜血", guarded)
        self.assertNotIn("血肉模糊", guarded)
        self.assertIn("遮挡", guarded)
        self.assertIn("远景", guarded)

    def test_agent_two_receives_agent_one_context(self) -> None:
        scenes = [{
            "slide_id": "scene_001",
            "start": 0,
            "end": 5,
            "text_content": "她推开走廊尽头的门。",
            "visual_summary": "女人推门",
        }]
        response = '[{"includes_slides":["scene_001"],"image_prompt":"女人推开旧门"}]'
        with patch.object(visual, "generate_gemini_text", return_value=response) as generate:
            mapping = visual._plan_mapping_batch(
                scenes,
                "系统提示",
                "测试批次",
                {"characters": [{"name": "林晚", "appearance": "黑色短发"}]},
            )
        self.assertIsNotNone(mapping)
        call = generate.call_args.kwargs
        self.assertIn("Agent 1 提供的全文故事上下文", call["system_prompt"])
        self.assertIn("林晚", call["system_prompt"])
        self.assertIn("黑色短发", call["system_prompt"])

    def test_agent_two_rewrites_three_consecutive_identical_location_shots(self) -> None:
        scenes = [
            {"slide_id": f"scene_{index:03d}", "start": index - 1, "end": index, "text_content": text}
            for index, text in enumerate(("讨论通勤", "讨论家务", "讨论医疗"), 1)
        ]
        repeated = json.dumps([
            {"includes_slides": [scene["slide_id"]], "image_prompt": f"餐桌旁讨论{index}"}
            for index, scene in enumerate(scenes, 1)
        ], ensure_ascii=False)
        diversified = json.dumps([
            {"includes_slides": ["scene_001"], "image_prompt": "早高峰地铁车厢"},
            {"includes_slides": ["scene_002"], "image_prompt": "厨房中的家务场景"},
            {"includes_slides": ["scene_003"], "image_prompt": "医院候诊区陪伴老人"},
        ], ensure_ascii=False)
        with patch.object(
            visual,
            "generate_gemini_text",
            side_effect=[repeated, diversified],
        ) as generate:
            mapping = visual._plan_mapping_batch(
                scenes,
                "系统提示",
                "测试批次",
                {"locations": [{"name": "出租屋餐桌"}]},
                required_groups=[[scene] for scene in scenes],
            )

        self.assertEqual(generate.call_count, 2)
        self.assertEqual(mapping[0]["image_prompt"], "早高峰地铁车厢")

    def test_agent_two_retries_an_incomplete_json_batch_before_failing(self) -> None:
        scenes = [{
            "slide_id": "scene_001", "start": 0, "end": 5,
            "text_content": "她推开走廊尽头的门。",
        }]
        complete = '[{"includes_slides":["scene_001"],"image_prompt":"女人推开旧门"}]'
        with (
            patch.object(visual, "generate_gemini_text", side_effect=["[]", complete]) as generate,
            patch.dict(os.environ, {
                "REQUIRE_AI_AGENT_SUCCESS": "1",
                "AGENT2_PLAN_MAX_ATTEMPTS": "3",
            }, clear=False),
        ):
            mapping = visual._plan_mapping_batch(scenes, "系统提示", "长文批次 5/7")
        self.assertEqual(len(mapping or []), 1)
        self.assertEqual(generate.call_count, 2)

    def test_agent_two_splits_a_persistently_truncated_batch_on_group_boundaries(self) -> None:
        groups = [
            [{"slide_id": "scene_001", "start": 0, "end": 5, "text_content": "第一组"}],
            [{"slide_id": "scene_002", "start": 5, "end": 10, "text_content": "第二组"}],
        ]
        first = '[{"includes_slides":["scene_001"],"image_prompt":"第一个独立镜头"}]'
        second = '[{"includes_slides":["scene_002"],"image_prompt":"第二个独立镜头"}]'
        with (
            patch.object(visual, "generate_gemini_text", side_effect=["[]", "[]", "[]", first, second]),
            patch.dict(os.environ, {
                "REQUIRE_AI_AGENT_SUCCESS": "1",
                "AGENT2_PLAN_MAX_ATTEMPTS": "3",
            }, clear=False),
        ):
            mapping = visual._plan_mapping_groups_resilient(groups, "系统提示", "长文批次 5/7", {})
        self.assertEqual([item["includes_slides"] for item in mapping or []], [
            ["scene_001"], ["scene_002"],
        ])

    def test_repeated_model_terms_do_not_duplicate_one_screen_insert(self) -> None:
        scenes = [
            {"slide_id": "scene_001", "start": 0, "end": 3, "text_content": "H3与Seedance的生产成本需要客观比较。"},
            {"slide_id": "scene_002", "start": 3, "end": 6, "text_content": "电脑屏幕上显示RunningHub的H3工作流页面。"},
            {"slide_id": "scene_003", "start": 6, "end": 9, "text_content": "H3的int8参数更适合家用显卡。"},
            {"slide_id": "scene_004", "start": 9, "end": 12, "text_content": "之后会把这套工作流同步给用户。"},
        ]
        mapping = [
            {"includes_slides": [scene["slide_id"]], "image_prompt": f"独立创意镜头 {index}"}
            for index, scene in enumerate(scenes, 1)
        ]
        story_plan = {
            "key_information_objects": [{
                "object_id": "workflow", "device_type": "电脑显示器",
                "content": "RunningHub平台上的MiniMax H3工作流页面",
            }],
            "semantic_units": [{
                "start_slide_id": "scene_001", "end_slide_id": "scene_004",
                "device_shot_mode": "screen_insert", "device_type": "电脑显示器",
                "screen_content": "RunningHub平台上的MiniMax H3工作流页面",
            }],
        }
        result = visual._finalize_mapping(mapping, scenes, story_plan)
        self.assertEqual([item["device_shot_mode"] for item in result], [
            "none", "screen_insert", "none", "none",
        ])
        self.assertIn("独立创意镜头 1", result[0]["image_prompt"])
        self.assertIn("独立创意镜头 3", result[2]["image_prompt"])
        self.assertIn("独立创意镜头 4", result[3]["image_prompt"])

    def test_character_reference_marker_is_explicit_and_safe_by_default(self) -> None:
        scenes = [{"slide_id": "scene_001", "start": 0, "end": 5}]
        without_marker = visual._normalize_mapping(
            [{"includes_slides": ["scene_001"], "image_prompt": "空走廊"}], scenes,
        )
        with_marker = visual._normalize_mapping(
            [{"includes_slides": ["scene_001"], "image_prompt": "男主角：角色形象参考图1", "reference_image_ids": ["图1", "图2", "无效编号"]}], scenes,
        )
        self.assertEqual(without_marker[0]["reference_image_ids"], [])
        self.assertEqual(with_marker[0]["reference_image_ids"], ["图1", "图2"])

    def test_reference_catalog_preserves_uploaded_order(self) -> None:
        with patch.dict(
            os.environ,
            {"USER_REFERENCE_IMAGE_PATHS_JSON": '["male.png", "female.png", "second.png"]'},
            clear=False,
        ):
            self.assertEqual(
                visual._reference_image_catalog(),
                {"图1": "male.png", "图2": "female.png", "图3": "second.png"},
            )
            prompt = visual.build_visual_prompt_system(content_mode=visual.CONTENT_MODE_GENERAL)
        self.assertIn("角色形象参考图N", prompt)
        self.assertIn("图1、图2、图3", prompt)

    def test_stale_reference_state_is_removed_from_saved_expert_prompt(self) -> None:
        prompt = (
            "只输出严格 JSON。\n"
            "- 本次未上传角色形象参考图；reference_image_ids 必须输出 []。\n"
            "保留这一条创作规则。"
        )
        cleaned = visual._strip_dynamic_reference_image_instructions(prompt)
        self.assertNotIn("本次未上传", cleaned)
        self.assertIn("保留这一条创作规则", cleaned)

    def test_named_characters_recover_reference_ids_and_keep_clear_boundaries(self) -> None:
        scenes = [{"slide_id": "scene_001", "start": 0, "end": 5}]
        mapping = [{
            "macro_scene_id": "poster_001",
            "includes_slides": ["scene_001"],
            "image_prompt": "莱恩正向艾德里安严肃地讲述情况",
            "reference_image_ids": [],
        }]
        story_plan = {"characters": [
            {"name": "艾德里安", "role": "王国骑士", "appearance": "年轻，身着银色胸甲"},
            {"name": "莱恩", "role": "精灵弓手", "appearance": "金色长发束在脑后"},
        ]}
        with patch.dict(os.environ, {
            "GLOBAL_CHARACTER_PROMPT": "艾德里安：图1，深蓝披风\n莱恩：图3，白银铠甲",
            "USER_REFERENCE_IMAGE_PATHS_JSON": '["knight.png", "witch.png", "elf.png"]',
            "VISUAL_STYLE_PROMPT": "",
        }, clear=False):
            result = visual._finalize_mapping(mapping, scenes, story_plan)
        prompt = result[0]["image_prompt"]
        self.assertIn("莱恩：金色长发束在脑后；角色形象参考图3", prompt)
        self.assertIn("艾德里安：年轻，身着银色胸甲；角色形象参考图1", prompt)
        self.assertIn("莱恩正向艾德里安严肃地讲述情况", prompt)
        self.assertEqual(result[0]["reference_image_ids"], ["图1", "图3"])

    def test_natural_character_reference_wording_is_supported(self) -> None:
        with patch.dict(os.environ, {
            "GLOBAL_CHARACTER_PROMPT": "林晚形象参考图1\n男主角参考图2\n女主角角色形象参考图3",
            "USER_REFERENCE_IMAGE_PATHS_JSON": '["lin.png", "male.png", "female.png"]',
        }, clear=False):
            self.assertEqual(visual._character_reference_label("林晚"), "图1")
            self.assertEqual(visual._character_reference_label("男主角"), "图2")
            self.assertEqual(visual._character_reference_label("女主角"), "图3")

    def test_agent_one_context_does_not_bind_reference_to_environment_shot(self) -> None:
        scenes = [{"slide_id": "scene_001", "start": 0, "end": 5}]
        mapping = [{
            "macro_scene_id": "poster_001",
            "includes_slides": ["scene_001"],
            "character_ids": [],
            "image_prompt": "阳台上一条洗得发白的旧毛巾特写",
            "reference_image_ids": [],
        }]
        story_plan = {
            "characters": [{
                "character_id": "lin_wan",
                "name": "林晚",
                "role": "主角",
                "appearance": "三十岁，黑色中长发",
            }],
            "semantic_units": [{
                "start_slide_id": "scene_001",
                "end_slide_id": "scene_001",
                "character_ids": ["lin_wan"],
            }],
        }
        with patch.dict(os.environ, {
            "GLOBAL_CHARACTER_PROMPT": "林晚形象参考图1",
            "USER_REFERENCE_IMAGE_PATHS_JSON": '["lin.png"]',
            "VISUAL_STYLE_PROMPT": "",
        }, clear=False):
            result = visual._finalize_mapping(mapping, scenes, story_plan)
        self.assertEqual(result[0]["reference_image_ids"], [])

    def test_named_visible_character_recovers_reference_from_natural_wording(self) -> None:
        scenes = [{"slide_id": "scene_001", "start": 0, "end": 5}]
        mapping = [{
            "macro_scene_id": "poster_001",
            "includes_slides": ["scene_001"],
            "character_ids": [],
            "image_prompt": "林晚站在水槽前安静地洗碗",
            "reference_image_ids": [],
        }]
        story_plan = {"characters": [{
            "character_id": "lin_wan",
            "name": "林晚",
            "role": "主角",
            "appearance": "三十岁，黑色中长发",
        }]}
        with patch.dict(os.environ, {
            "GLOBAL_CHARACTER_PROMPT": "林晚形象参考图1",
            "USER_REFERENCE_IMAGE_PATHS_JSON": '["lin.png"]',
            "VISUAL_STYLE_PROMPT": "",
        }, clear=False):
            result = visual._finalize_mapping(mapping, scenes, story_plan)
        self.assertEqual(result[0]["reference_image_ids"], ["图1"])
        self.assertIn("角色形象参考图1", result[0]["image_prompt"])

    def test_shared_role_characters_get_one_unique_card_and_no_duplicate_appearance(self) -> None:
        scenes = [
            {"slide_id": "scene_001", "start": 0, "end": 5},
            {"slide_id": "scene_002", "start": 5, "end": 10},
        ]
        wife_appearance = "31岁左右，黑色中长发，气质温和但略显疲惫"
        husband_appearance = "33岁左右，短黑发，面容朴实，工作后略显疲惫"
        mapping = [{
            "macro_scene_id": "poster_001",
            "includes_slides": ["scene_001", "scene_002"],
            "character_ids": ["wife", "husband"],
            "image_prompt": (
                f"家庭经营者妻子（{wife_appearance}）站在窗边，"
                f"家庭经营者丈夫（{husband_appearance}）坐在沙发上。"
            ),
        }]
        story_plan = {
            "characters": [
                {"character_id": "wife", "name": "妻子", "role": "家庭经营者", "appearance": wife_appearance, "wardrobe": "米白针织衫"},
                {"character_id": "husband", "name": "丈夫", "role": "家庭经营者", "appearance": husband_appearance, "wardrobe": "深蓝衬衫"},
            ],
            "semantic_units": [{
                "start_slide_id": "scene_001", "end_slide_id": "scene_002",
                "character_ids": ["wife", "husband"],
            }],
        }
        with patch.dict(os.environ, {"VISUAL_STYLE_PROMPT": "温暖治愈的都市情感口播插画风"}, clear=False):
            result = visual._finalize_mapping(mapping, scenes, story_plan)
        prompt = result[0]["image_prompt"]
        self.assertIn(f"妻子：{wife_appearance}", prompt)
        self.assertIn(f"丈夫：{husband_appearance}", prompt)
        self.assertEqual(prompt.count(wife_appearance), 1)
        self.assertEqual(prompt.count(husband_appearance), 1)
        self.assertNotIn("家庭经营者：", prompt)
        self.assertIn("妻子站在窗边", prompt)
        self.assertIn("丈夫坐在沙发上", prompt)
        self.assertIn("【视觉媒介锁】", prompt)

    def test_character_expansion_is_idempotent_and_deduplicates_role_and_reference(self) -> None:
        story_plan = {"characters": [{
            "name": "艾德里安",
            "role": "王国骑士",
            "appearance": "年轻，身着银色胸甲",
        }]}
        with patch.dict(os.environ, {
            "GLOBAL_CHARACTER_PROMPT": "艾德里安：图1，深蓝色旧披风",
            "USER_REFERENCE_IMAGE_PATHS_JSON": '["knight.png"]',
        }, clear=False):
            once, _ = visual._expand_character_names(
                "年轻的王国骑士艾德里安（角色形象参考图1，深蓝色旧披风）站在城门前",
                story_plan,
            )
            twice, _ = visual._expand_character_names(once, story_plan)
        self.assertEqual(once, twice)
        self.assertNotIn("王国骑士王国骑士", once)
        self.assertEqual(once.count("角色形象参考图1"), 1)
        self.assertIn("王国骑士艾德里安（年轻，身着银色胸甲，角色形象参考图1，深蓝色旧披风）", once)

    def test_visual_editor_redraws_share_one_round_robin_account_pool(self) -> None:
        configs = [
            {"api_key": "redraw-key-1", "endpoint": "/generate", "ratio": "2:1", "resolution": "1k", "account_label": "账号 1"},
            {"api_key": "redraw-key-2", "endpoint": "/generate", "ratio": "2:1", "resolution": "1k", "account_label": "账号 2"},
        ]
        namespace = f"test-redraw-{id(self)}"
        first_call_pool = visual.shared_runninghub_account_pool(configs, namespace=namespace)
        second_call_pool = visual.shared_runninghub_account_pool(list(configs), namespace=namespace)
        self.assertIs(first_call_pool, second_call_pool)
        self.assertEqual(first_call_pool.acquire()["account_label"], "账号 1")
        self.assertEqual(second_call_pool.acquire()["account_label"], "账号 2")

    def test_account_pool_assigns_distinct_single_capacity_keys(self) -> None:
        configs = [
            {"api_key": "capacity-key-1", "account_label": "账号 1"},
            {"api_key": "capacity-key-2", "account_label": "账号 2"},
        ]
        pool = visual.RunningHubAccountPool(configs, per_key_concurrency=1)
        first = pool.acquire()
        second = pool.acquire()
        try:
            self.assertNotEqual(first["api_key"], second["api_key"])
        finally:
            pool.release(first)
            pool.release(second)

    def test_power_exhausted_account_is_skipped_by_new_pool(self) -> None:
        configs = [
            {"api_key": "quota-key-1", "endpoint": "/generate", "ratio": "2:1", "resolution": "1k", "account_label": "账号 1"},
            {"api_key": "quota-key-2", "endpoint": "/generate", "ratio": "2:1", "resolution": "1k", "account_label": "账号 2"},
        ]
        try:
            first = visual.RunningHubAccountPool(configs)
            first.mark_power_exhausted(configs[0])
            next_batch = visual.RunningHubAccountPool(configs)
            self.assertEqual(next_batch.acquire()["account_label"], "账号 2")
            self.assertTrue(visual._looks_like_power_insufficient(None, "账户余额不足"))
        finally:
            with visual._ACCOUNT_STATE_LOCK:
                visual._POWER_EXHAUSTED_ACCOUNT_KEYS.difference_update({"quota-key-1", "quota-key-2"})

    def test_multi_moment_prompt_gets_single_scene_guard_but_comparison_is_allowed(self) -> None:
        risky = visual._single_scene_guard("村民从窗后窥视，随后男人走出浓雾并忘记名字")
        comparison = visual._single_scene_guard("同一器材使用前后效果对比")
        self.assertIn("单镜头构图硬约束", risky)
        self.assertIn("不使用多格漫画", risky)
        self.assertEqual(comparison, "同一器材使用前后效果对比")


if __name__ == "__main__":
    unittest.main()
