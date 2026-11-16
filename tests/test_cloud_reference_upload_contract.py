import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import module4_video_render as visual


class CloudReferenceUploadContractTests(unittest.TestCase):
    def test_pool_upload_contract(self):
        cases = [
            ({"code": 0, "data": {"media_id": "imgasset_test"}}, 200,
             "https://pool.test/api/v1/image-pool/media/imgasset_test"),
            ({"code": 0, "data": {}}, 200, None),
            ({"code": 0, "data": {"media_id": "../unsafe"}}, 200, None),
            ({"error": "unavailable"}, 503, None),
        ]
        for payload, status, expected in cases:
            with self.subTest(status=status, payload=payload), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "ref.png"
                source.write_bytes(b"test image")
                response = MagicMock(ok=status == 200, status_code=status)
                response.json.return_value = payload
                config = dict(api_key="test", cloud_pool="1", method="running",
                              cloud_base_url="https://pool.test/api/v1",
                              upload_url="https://pool.test/api/v1/image-pool/media/upload")
                with patch.object(visual, "_request_with_cloud_refresh", return_value=response) as request:
                    if expected:
                        self.assertEqual(visual._reference_image_url(config, str(source)), expected)
                    else:
                        with self.assertRaises(visual.RunningHubReferenceUploadError):
                            visual._reference_image_url(config, str(source))
                        self.assertFalse(any(key[-1] == str(source.resolve()) for key in visual._REFERENCE_IMAGE_URLS))
                    self.assertEqual(request.call_count, 1)
                    response.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
