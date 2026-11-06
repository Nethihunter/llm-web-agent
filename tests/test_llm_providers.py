"""Providers must only ever use their own credentials."""
import json
import unittest
from unittest import mock

from llm import create_client
from llm.base import LLMConfigError
from llm.grok import GrokClient
from llm.openai import OpenAIClient

GOOD = {"is_goal_reached": False, "thought": "t", "selector": "#a", "action": "click",
        "value": None, "confidence": 0.9, "limitations": ""}

# Same shape as key.json.example once the user filled in BOTH providers.
CFG = {
    "OPENAI_API_KEY": "sk-openai-real", "OPENAI_MODEL": "gpt-4o-mini",
    "GROK_API_KEY": "xai-real", "GROK_MODEL": "grok-x",
}


def ok_response():
    r = mock.Mock(status_code=200)
    r.json.return_value = {"choices": [{"message": {"content": json.dumps(GOOD)},
                                        "finish_reason": "stop"}], "usage": {}}
    return r


class Grok(unittest.TestCase):
    @mock.patch("llm.base.requests.post")
    def test_grok_sends_grok_key_url_and_model_only(self, post):
        post.return_value = ok_response()
        create_client(CFG, auto_confirm=True, provider="grok").plan_next_step("<a>", "g", [])
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.x.ai/v1/chat/completions")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer xai-real")
        self.assertEqual(kwargs["json"]["model"], "grok-x")
        self.assertNotIn("sk-openai-real", json.dumps(kwargs["headers"]))

    def test_grok_never_falls_back_to_the_openai_key(self):
        with self.assertRaises(LLMConfigError) as ctx:
            GrokClient({"OPENAI_API_KEY": "sk-openai-real"})
        self.assertIn("GROK_API_KEY", str(ctx.exception))

    def test_placeholder_key_from_the_example_file_is_rejected(self):
        with self.assertRaises(LLMConfigError):
            GrokClient({"GROK_API_KEY": "xai-your-grok-key"})
        with self.assertRaises(LLMConfigError):
            OpenAIClient({"OPENAI_API_KEY": "sk-your-openai-key"})

    def test_model_override_reaches_grok(self):
        c = create_client(CFG, provider="grok", model="grok-y")
        self.assertEqual(c.model, "grok-y")


class OpenAI(unittest.TestCase):
    @mock.patch("llm.base.requests.post")
    def test_openai_defaults(self, post):
        post.return_value = ok_response()
        c = create_client(CFG, auto_confirm=True, provider="openai")
        c.plan_next_step("<a>", "g", [])
        self.assertEqual(post.call_args.args[0], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer sk-openai-real")


class Anthropic(unittest.TestCase):
    @mock.patch("llm.base.requests.post")
    def test_request_shape_and_text_extraction(self, post):
        r = mock.Mock(status_code=200)
        r.json.return_value = {"content": [{"type": "text", "text": json.dumps(GOOD)}],
                               "usage": {"input_tokens": 5, "output_tokens": 3},
                               "stop_reason": "end_turn"}
        post.return_value = r
        c = create_client({"ANTHROPIC_API_KEY": "sk-ant-real"}, auto_confirm=True,
                          provider="anthropic")
        res = c.plan_next_step("<a>", "g", [])
        body = post.call_args.kwargs["json"]
        self.assertIn("system", body)
        self.assertEqual(body["model"], "claude-haiku-5-5")
        self.assertEqual(post.call_args.kwargs["headers"]["x-api-key"], "sk-ant-real")
        self.assertEqual(res.data["selector"], "#a")


if __name__ == "__main__":
    unittest.main()
