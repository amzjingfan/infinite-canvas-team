import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class AutoDLTests(unittest.IsolatedAsyncioTestCase):
    def test_platform_is_available_without_changing_existing_provider(self):
        tugo = {"id": "custom-api", "name": "TUGO", "protocol": "openai", "video_models": ["veo3"], "primary": True}
        providers = main.merge_default_api_providers([tugo], inject_missing=False)
        self.assertEqual(providers[0], tugo)
        autodl = next((p for p in providers if p["id"] == "autodl"), None)
        self.assertIsNotNone(autodl)
        normalized = main.normalize_provider(autodl)
        self.assertEqual(normalized["protocol"], "autodl")
        self.assertEqual(normalized["video_models"], ["minimax-h3"])
        self.assertEqual(normalized["image_models"], [])

    def test_platform_key_used_for_requests_and_masked_in_settings(self):
        with patch.dict(main.os.environ, {"API_PROVIDER_AUTODL_KEY": "platform-secret", "AUTODL_API_KEY": "old-secret"}):
            self.assertEqual(main.autodl_headers()["Authorization"], "platform-secret")
            public = main.public_provider({"id": "autodl"})
            self.assertTrue(public["has_key"])
            self.assertNotIn("platform-secret", str(public))

    def test_cleared_platform_key_does_not_reactivate_old_key(self):
        with patch.dict(main.os.environ, {"API_PROVIDER_AUTODL_KEY": "", "AUTODL_API_KEY": "old-secret"}):
            with self.assertRaises(main.HTTPException):
                main.autodl_headers()

    async def test_submission_preserves_order_and_seed_zero(self):
        request = main.AutoDLVideoRequest(prompt="test", images=["one", "two"], audios=["voice"], seed=0)
        async def upload(value, kind):
            return "https://example.com/" + value
        with patch.object(main, "autodl_headers", return_value={}), patch.object(
            main, "autodl_reference_url", side_effect=upload
        ), patch.object(main, "autodl_request", new_callable=AsyncMock, return_value={"task_id":"test"}) as send:
            result = await main.autodl_submit(request)
        body = send.call_args.args[2]
        self.assertEqual(body["ref_image_0"], "https://example.com/one")
        self.assertEqual(body["ref_image_1"], "https://example.com/two")
        self.assertEqual(body["ref_audio_0"], "https://example.com/voice")
        self.assertEqual(body["seed"], 0)
        self.assertEqual(result["task_id"], "test")

    def test_rejects_invalid_limits(self):
        for fields in ({"duration":0}, {"duration":16}, {"duration":1.5}, {"images":["x"]*10}, {"audios":["x"]*4}, {"prompt":""}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                main.AutoDLVideoRequest(**{"prompt":"test", **fields})

    async def test_both_documented_success_states(self):
        for status in ("SUCCESS", "completed"):
            with patch.object(main, "autodl_request", new_callable=AsyncMock, return_value={
                "status":status, "results":[{"type":"video", "url":"https://example.com/video.mp4"}]
            }):
                result = await main.autodl_result("test-id")
                self.assertEqual(len(result["urls"]), 1)

    async def test_failed_and_queued_do_not_download(self):
        for status in ("FAILED", "QUEUED", "RUNNING"):
            with patch.object(main, "autodl_request", new_callable=AsyncMock, return_value={"status":status}):
                result = await main.autodl_result("test-id")
                self.assertEqual(result["status"], status)
                self.assertEqual(result["urls"], [])

    async def test_download_saves_local_result(self):
        with patch.object(main, "autodl_result", new_callable=AsyncMock, return_value={"urls":["https://example.com/v.mp4"]}), patch.object(
            main, "save_remote_video_to_output", new_callable=AsyncMock, return_value="/assets/output/v.mp4"
        ):
            result = await main.autodl_download(main.AutoDLDownloadRequest(task_id="test"))
        self.assertEqual(result["urls"], ["/assets/output/v.mp4"])

    async def test_private_url_rejected(self):
        with self.assertRaises(main.HTTPException):
            await main.autodl_reference_url("http://127.0.0.1/assets/input/a.png", "image")

    async def test_raw_authorization_and_business_error(self):
        response = main.httpx.Response(200, json={"code":"NoBalance", "msg":"余额不足"}, request=main.httpx.Request("POST", "https://example.com"))
        with patch.dict(main.os.environ, {"AUTODL_API_KEY":"test-token"}), patch.object(main.httpx, "AsyncClient") as factory:
            client = factory.return_value.__aenter__.return_value
            client.request = AsyncMock(return_value=response)
            with self.assertRaises(main.HTTPException):
                await main.autodl_request("POST", "/test", {})
            self.assertEqual(client.request.call_args.kwargs["headers"]["Authorization"], "test-token")


if __name__ == "__main__":
    unittest.main()
