"""`mindmap llm-check` — 키 출처·계열·인증·모델·작은 호출. 네트워크는 가짜 opener 로, 키 값은 어디에도 안 보인다."""

from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from unittest import mock

from helpers import D0  # noqa: F401  (sys.path 설정)
from kstock.cli import main as cli_main
from kstock.generate.llm_fill import key_family, key_hint, llm_check

GOOD = "sk-ant-api03-" + "A" * 95
USR = "sk-ant-usr-" + "B" * 95
OAT = "sk-ant-oat01-" + "C" * 95


class _Resp:
    def __init__(self, status: int, body: dict):
        self.status = status
        self._b = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(auth_ok: bool, models: list[str], call_ok: bool = True):
    seen: list[str] = []

    def open_(req, timeout=0):
        seen.append(req.full_url)
        assert req.get_header("X-api-key"), "키가 헤더로 가야 한다"
        if not auth_ok:
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                         io.BytesIO(json.dumps({"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}).encode()))
        if req.full_url.endswith("/v1/models?limit=100"):
            return _Resp(200, {"data": [{"id": m} for m in models]})
        if not call_ok:
            raise urllib.error.HTTPError(req.full_url, 400, "Bad", {}, io.BytesIO(b'{"error":{"type":"invalid_request_error","message":"bad model"}}'))
        return _Resp(200, {"content": [{"type": "text", "text": "pong"}], "usage": {"input_tokens": 12, "output_tokens": 2}})

    open_.seen = seen  # type: ignore[attr-defined]
    return open_


class TestLlmCheck(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": GOOD, "KSTOCK_LLM_MODEL": "claude-sonnet-4-5"}, clear=False)
        self._env.start()
        os.environ.pop("KSTOCK_OFFLINE", None)   # helpers 가 켠 오프라인은 이 테스트에서만 끈다 (가짜 opener 라 네트워크 없음)

    def tearDown(self):
        self._env.stop()
        os.environ.setdefault("KSTOCK_OFFLINE", "1")

    def test_ok_path_and_no_key_leak(self):
        op = _opener(True, ["claude-haiku-4-5", "claude-sonnet-4-5"])
        r = llm_check(opener=op)
        self.assertTrue(r.auth_ok and r.call_ok and r.model_available and r.ok)
        self.assertEqual(r.call_text, "pong")
        self.assertEqual(r.usage, (12, 2))
        text = "\n".join(r.lines())
        self.assertNotIn("A" * 20, text)                       # 키 값 유출 금지
        self.assertIn("sk-ant-api03-", text)                   # 계열은 보여도 된다
        self.assertEqual(len(op.seen), 2)

    def test_auth_fail_with_family_hint(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": USR}):
            self.assertEqual(key_family(), "sk-ant-usr-")
            self.assertIsNone(key_hint())                          # Console 이 발급하는 사용자 연결 키 — 경고 없음
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": OAT}):
            self.assertIn("sk-ant-usr-", key_hint() or "")
            r = llm_check(opener=_opener(False, []))
        self.assertFalse(r.auth_ok)
        self.assertFalse(r.ok)
        self.assertIn("authentication_error", r.error or "")
        self.assertTrue(any("API 키" in n for n in r.notes))
        self.assertNotIn("C" * 20, "\n".join(r.lines()))

    def test_model_missing_suggests_alternative_and_skips_call(self):
        op = _opener(True, ["claude-haiku-4-5", "claude-sonnet-4-7"])
        r = llm_check(opener=op)
        self.assertTrue(r.auth_ok)
        self.assertFalse(r.model_available)
        self.assertIsNone(r.call_ok)                            # 없는 모델로는 호출하지 않는다
        self.assertTrue(any("KSTOCK_LLM_MODEL=claude-sonnet-4-7" in n for n in r.notes), r.notes)
        self.assertFalse(r.ok)

    def test_auto_model_when_unset(self):
        from kstock.llm.models import pick_model

        self.assertEqual(pick_model(["claude-haiku-5-5", "claude-sonnet-4-6", "claude-sonnet-5-5", "claude-opus-5"]), "claude-sonnet-5-5")
        self.assertEqual(pick_model(["claude-haiku-5-5", "claude-sonnet-7"]), "claude-sonnet-7")     # 선호 목록 밖이어도 sonnet
        self.assertEqual(pick_model(["claude-sonnet-5-5"], preferred="claude-sonnet-4-5"), None)    # 명시한 모델이 없으면 None
        with mock.patch.dict(os.environ, {"KSTOCK_LLM_MODEL": ""}):
            r = llm_check(opener=_opener(True, ["claude-haiku-5-5", "claude-sonnet-4-6", "claude-sonnet-5-5"]))
        self.assertFalse(r.model_explicit)
        self.assertEqual(r.model, "claude-sonnet-5-5")
        self.assertTrue(r.ok and r.call_ok)
        self.assertTrue(any("KSTOCK_LLM_MODEL=claude-sonnet-5-5" in n for n in r.notes))

    def test_offline_and_missing_key(self):
        with mock.patch.dict(os.environ, {"KSTOCK_OFFLINE": "1"}):
            r = llm_check(opener=_opener(True, ["x"]))
            self.assertIsNone(r.auth_ok)
            self.assertIn("OFFLINE", r.error or "")
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            r = llm_check(opener=_opener(True, ["x"]))
            self.assertIn("없음", r.error or "")

    def test_workspace_header_and_org_scope_guidance(self):
        # 조직 범위 키: 헤더 없이 보내면 400 + 안내, ANTHROPIC_WORKSPACE_ID 가 있으면 헤더가 붙는다
        calls: list[dict[str, str]] = []

        def open_(req, timeout=0):
            calls.append(dict(req.headers))
            if not req.get_header("Anthropic-workspace-id"):
                raise urllib.error.HTTPError(req.full_url, 400, "Bad", {}, io.BytesIO(json.dumps({"type": "error", "error": {
                    "type": "invalid_request_error",
                    "message": "This API key is not scoped to a workspace, so this request must include the anthropic-workspace-id header"}}).encode()))
            if req.full_url.endswith("/v1/models?limit=100"):
                return _Resp(200, {"data": [{"id": "claude-sonnet-4-5"}]})
            return _Resp(200, {"content": [{"type": "text", "text": "pong"}], "usage": {"input_tokens": 1, "output_tokens": 1}})

        with mock.patch.dict(os.environ, {"ANTHROPIC_WORKSPACE_ID": ""}):
            r = llm_check(opener=open_)
        self.assertFalse(r.auth_ok)
        self.assertTrue(any("ANTHROPIC_WORKSPACE_ID" in n for n in r.notes), r.notes)
        with mock.patch.dict(os.environ, {"ANTHROPIC_WORKSPACE_ID": "wrkspc_01ABCDEFGHIJKLMNOP"}):
            r = llm_check(opener=open_)
        self.assertTrue(r.ok, r.lines())
        self.assertEqual(calls[-1].get("Anthropic-workspace-id"), "wrkspc_01ABCDEFGHIJKLMNOP")
        self.assertIn("헤더 설정됨", "\n".join(r.lines()))

    def test_cli_exit_codes(self):
        with mock.patch("urllib.request.urlopen", _opener(True, ["claude-sonnet-4-5"])):
            self.assertEqual(cli_main(["llm-check"]), 0)
        with mock.patch("urllib.request.urlopen", _opener(False, [])):
            self.assertEqual(cli_main(["llm-check", "--no-call"]), 1)


if __name__ == "__main__":
    unittest.main()


class TestLocalFiller(unittest.TestCase):
    def test_openai_compat_and_complete_json(self):
        from kstock.generate import OpenAICompatFiller, complete_json, make_filler

        calls: list[dict] = []

        def open_(req, timeout=0):
            body = json.loads(req.data.decode("utf-8"))
            calls.append({"url": req.full_url, "auth": req.get_header("Authorization"), "body": body})
            n = len(calls)
            text = "응답: ```json\n{\"a\": 1}\n```" if n == 1 else ("깨진 json {" if n == 2 else '{"b": 2}')
            return _Resp(200, {"choices": [{"message": {"content": text}}]})

        with mock.patch.dict(os.environ, {"LLM_BASE_URL": "http://spark:8000/v1", "LLM_API_KEY": "k", "LLM_MODEL": "qwen"}):
            f = OpenAICompatFiller(opener=open_)
            self.assertEqual(complete_json(f, "s", "u"), {"a": 1})                         # 코드펜스 제거
            self.assertEqual(calls[0]["url"], "http://spark:8000/v1/chat/completions")
            self.assertEqual(calls[0]["auth"], "Bearer k")
            self.assertEqual(calls[0]["body"]["model"], "qwen")
            self.assertEqual(calls[0]["body"]["response_format"], {"type": "json_object"})
            self.assertEqual(complete_json(f, "s", "u", retries=1), {"b": 2})               # 깨진 JSON → 재시도 1회
            self.assertIn("JSON 만 다시", calls[2]["body"]["messages"][1]["content"])
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
                self.assertIsInstance(make_filler("auto"), OpenAICompatFiller)             # Claude 키 없음 → 로컬
        with mock.patch.dict(os.environ, {"LLM_BASE_URL": "", "LLM_MODEL": "", "ANTHROPIC_API_KEY": ""}):
            with self.assertRaises(RuntimeError):
                make_filler("local")
            with self.assertRaises(RuntimeError):
                make_filler("auto")
