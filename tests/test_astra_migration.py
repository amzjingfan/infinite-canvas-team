import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class AstraMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_config_prefers_configured_default_over_legacy_model(self):
        with patch.object(main, "CHAT_MODEL", "gpt-6-astra"), patch.object(
            main, "CHAT_MODELS", ["gpt-5.5", "gpt-6-astra"]
        ), patch.object(main, "public_api_providers", return_value=[]):
            config = await main.ai_config()
        self.assertEqual(config["chat_model"], "gpt-6-astra")

    async def test_explicit_model_list_is_respected(self):
        with patch.object(main, "CHAT_MODELS", ["gpt-4o-mini"]), patch.object(
            main, "public_api_providers", return_value=[]
        ):
            config = await main.ai_config()
        self.assertEqual(config["chat_model"], "gpt-4o-mini")

    async def test_canvas_sends_astra_and_preserves_text_response(self):
        upstream = main.httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Astra reply"}}]},
            request=main.httpx.Request("POST", "https://example.test/v1/chat/completions"),
        )
        client = AsyncMock()
        client.post.return_value = upstream
        provider = {"id": "test", "protocol": "openai", "chat_models": ["gpt-6-astra"]}
        with patch.object(main, "get_api_provider", return_value=provider), patch.object(
            main, "resolve_chat_provider", return_value=("https://example.test/v1", {}, "gpt-6-astra")
        ), patch.object(main.httpx, "AsyncClient") as factory:
            factory.return_value.__aenter__.return_value = client
            result = await main.canvas_llm(main.CanvasLLMRequest(message="Hello", provider="test"))
        body = client.post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "gpt-6-astra")
        self.assertFalse({"temperature", "top_p", "logprobs", "top_logprobs", "tools"} & body.keys())
        self.assertEqual(result["text"], "Astra reply")


if __name__ == "__main__":
    unittest.main()
