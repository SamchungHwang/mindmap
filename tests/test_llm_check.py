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

    def test_think_off_and_fallbacks(self):
        """사고 끄기(reasoning_effort=none) 기본 · LLM_THINK/LLM_MAX_TOKENS · 서버가 모르면 빼고 재시도 · <think> 제거 · 사고만 하고 답이 없을 때."""
        from kstock.generate import OpenAICompatFiller
        from kstock.generate.llm_fill import strip_think

        self.assertEqual(strip_think("<think>음…</think>\n{\"a\": 1}"), '{"a": 1}')
        self.assertEqual(strip_think("{\"a\": 1}<think>잘린 사고"), '{"a": 1}')
        self.assertEqual(strip_think("그냥 답"), "그냥 답")

        calls: list[dict] = []

        def open_(req, timeout=0):
            body = json.loads(req.data.decode("utf-8"))
            calls.append(body)
            if "reasoning_effort" in body and body["model"] == "vllm-old":
                raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(b'{"error":"extra fields: reasoning_effort"}'))
            return _Resp(200, {"choices": [{"message": {"content": "<think>생각</think>{\"ok\": true}", "reasoning": "생각"}}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

        env = {"LLM_BASE_URL": "http://spark:11435/v1", "LLM_API_KEY": "k", "LLM_MODEL": "nemotron-3-nano:latest"}
        with mock.patch.dict(os.environ, env):
            for k in ("LLM_THINK", "LLM_MAX_TOKENS"):
                os.environ.pop(k, None)
            f = OpenAICompatFiller(opener=open_)
            self.assertEqual(f.think, "none")                                                  # 기본: 사고 끈다
            self.assertEqual(f.max_tokens, 8000)
            self.assertEqual(f.complete("s", "u"), '{"ok": true}')                            # <think> 제거
            self.assertEqual(calls[-1]["reasoning_effort"], "none")
            self.assertEqual(calls[-1]["max_tokens"], 8000)
            self.assertEqual(f.last_usage["completion_tokens"], 5)
        with mock.patch.dict(os.environ, {**env, "LLM_THINK": "off", "LLM_MAX_TOKENS": "12000"}):
            f = OpenAICompatFiller(opener=open_)
            self.assertEqual((f.think, f.max_tokens), ("none", 12000))                         # off 는 none 의 별칭
        with mock.patch.dict(os.environ, {**env, "LLM_THINK": "high"}):
            self.assertEqual(OpenAICompatFiller(opener=open_).body("s", "u")["reasoning_effort"], "high")
        with mock.patch.dict(os.environ, {**env, "LLM_THINK": "auto"}):
            self.assertNotIn("reasoning_effort", OpenAICompatFiller(opener=open_).body("s", "u"))  # auto: 서버 기본값
        with mock.patch.dict(os.environ, {**env, "LLM_THINK": "lots"}):
            with self.assertRaises(RuntimeError):
                OpenAICompatFiller(opener=open_)
        with mock.patch.dict(os.environ, {**env, "LLM_MODEL": "vllm-old", "LLM_THINK": "none"}):
            f = OpenAICompatFiller(opener=open_)
            n = len(calls)
            self.assertEqual(f.complete("s", "u"), '{"ok": true}')                            # 400 → reasoning_effort 빼고 재시도
            self.assertEqual(len(calls), n + 2)
            self.assertIn("reasoning_effort", calls[-2])
            self.assertNotIn("reasoning_effort", calls[-1])
            self.assertEqual(f.think, "auto")

        with mock.patch.dict(os.environ, {**env, "LLM_API_KEY": "", "NEMOCLAW_LOCAL_INFERENCE_TOKEN": "openshell:resolve:env:v42_NEMOCLAW_LOCAL_INFERENCE_TOKEN"}):
            self.assertEqual(OpenAICompatFiller(opener=open_).api_key, "openshell:resolve:env:v42_NEMOCLAW_LOCAL_INFERENCE_TOKEN")  # 샌드박스 핸들 폴백

        def only_think(req, timeout=0):
            return _Resp(200, {"choices": [{"message": {"content": "", "reasoning": "생각만 길게 " * 50}}]})

        with mock.patch.dict(os.environ, env):
            with self.assertRaises(RuntimeError) as cm:
                OpenAICompatFiller(opener=only_think).complete("s", "u")
            self.assertIn("사고(reasoning)만", str(cm.exception))


class TestSdkTolerance(unittest.TestCase):
    """설치된 anthropic SDK 가 모르는 인자(예: temperature) 를 TypeError 로 거부하면 빼고 다시 부른다."""

    def test_drops_unexpected_kwarg_and_retries(self):
        from kstock.llm.filler import ClaudeFiller, _create_tolerant

        calls: list[dict] = []

        class _Msgs:
            def create(self, **kw):  # noqa: ANN003
                calls.append(dict(kw))
                if "temperature" in kw:
                    raise TypeError("Messages.create() got an unexpected keyword argument 'temperature'")
                return mock.Mock(content=[mock.Mock(text='{"ok": 1}')])

        client = mock.Mock(messages=_Msgs())
        dropped: set[str] = set()
        msg = _create_tolerant(client, {"model": "m", "max_tokens": 5, "temperature": 0.0, "messages": []}, dropped)
        self.assertEqual(msg.content[0].text, '{"ok": 1}')
        self.assertEqual(dropped, {"temperature"})
        self.assertEqual(len(calls), 2)
        self.assertNotIn("temperature", calls[1])
        # 두 번째 요청부터는 처음부터 빼고 부른다
        f = ClaudeFiller(model="m", api_key="x")
        f._client = client
        f._dropped_kwargs = dropped
        calls.clear()
        self.assertEqual(f._complete("s", "u"), '{"ok": 1}')
        self.assertEqual(len(calls), 1)
        self.assertNotIn("temperature", calls[0])

    def test_required_kwarg_error_is_raised(self):
        from kstock.llm.filler import _create_tolerant

        class _Msgs:
            def create(self, **kw):  # noqa: ANN003
                raise TypeError("Messages.create() got an unexpected keyword argument 'model'")

        with self.assertRaises(TypeError):
            _create_tolerant(mock.Mock(messages=_Msgs()), {"model": "m", "max_tokens": 5, "messages": []}, set())


class TestThinkingOff(unittest.TestCase):
    """Claude 5 계열은 사고가 기본으로 켜져 텍스트가 빈다 → 끄는 값 cascade · 잘림 재시도 · 빈 텍스트 오류."""

    def setUp(self):
        from kstock.llm import filler as fl

        fl._THINKING_MODE_OK.clear()
        os.environ.pop("KSTOCK_LLM_THINKING", None)
        os.environ.pop("KSTOCK_OFFLINE", None)   # 가짜 opener 라 네트워크 없음

    def tearDown(self):
        os.environ.setdefault("KSTOCK_OFFLINE", "1")

    @staticmethod
    def _filler(msgs_cls):
        from kstock.llm.filler import ClaudeFiller

        f = ClaudeFiller(model="claude-sonnet-5-5", api_key="x", max_tokens=100)
        f._client = mock.Mock(messages=msgs_cls())
        f._dropped_kwargs = set()
        return f

    @staticmethod
    def _bad_request(text: str):
        e = type("BadRequestError", (Exception,), {})(text)
        e.status_code = 400
        return e

    def test_cascade_between_tools_rejected_then_disabled_accepted(self):
        from kstock.llm import filler as fl

        calls: list[dict] = []

        class _Msgs:
            def create(self, **kw):  # noqa: ANN003
                calls.append(dict(kw))
                th = kw.get("thinking")
                if th == {"type": "between_tools"}:
                    raise TestThinkingOff._bad_request("thinking.type: between_tools is not supported")
                return mock.Mock(content=[mock.Mock(type="text", text="{}")], stop_reason="end_turn")

        f = self._filler(_Msgs)
        self.assertEqual(f._complete("s", "u"), "{}")
        self.assertEqual([c.get("thinking") for c in calls], [{"type": "between_tools"}, {"type": "disabled"}])
        self.assertEqual(fl._THINKING_MODE_OK["claude-sonnet-5-5"], {"type": "disabled"})
        # 기억 — 다음 호출은 바로 disabled
        calls.clear()
        f._complete("s", "u")
        self.assertEqual([c.get("thinking") for c in calls], [{"type": "disabled"}])

    def test_truncated_response_retries_with_bigger_budget(self):
        budgets: list[int] = []

        class _Msgs:
            def create(self, **kw):  # noqa: ANN003
                budgets.append(kw["max_tokens"])
                if kw["max_tokens"] < 400:
                    return mock.Mock(content=[mock.Mock(type="text", text='{"a":')], stop_reason="max_tokens")
                return mock.Mock(content=[mock.Mock(type="text", text='{"a": 1}')], stop_reason="end_turn")

        f = self._filler(_Msgs)
        self.assertEqual(f._complete("s", "u"), '{"a": 1}')
        self.assertEqual(budgets, [100, 400])

    def test_thinking_only_response_is_a_clear_error(self):
        class _Msgs:
            def create(self, **kw):  # noqa: ANN003
                return mock.Mock(content=[mock.Mock(type="thinking", text=None)], stop_reason="end_turn")

        f = self._filler(_Msgs)
        with self.assertRaises(RuntimeError) as cm:
            f._complete("s", "u")
        self.assertIn("thinking", str(cm.exception))
        self.assertIn("텍스트를 주지 않았다", str(cm.exception))

    def test_adaptive_env_sends_no_thinking_field(self):
        calls: list[dict] = []

        class _Msgs:
            def create(self, **kw):  # noqa: ANN003
                calls.append(dict(kw))
                return mock.Mock(content=[mock.Mock(type="text", text="ok")], stop_reason="end_turn")

        os.environ["KSTOCK_LLM_THINKING"] = "adaptive"
        try:
            f = self._filler(_Msgs)
            self.assertEqual(f._complete("s", "u"), "ok")
            self.assertNotIn("thinking", calls[0])
        finally:
            os.environ.pop("KSTOCK_LLM_THINKING", None)

    def test_llm_check_cascades_thinking_on_400(self):
        """urllib 경로(llm_check) 도 같은 cascade: 첫 값 400(thinking) → 다음 값으로 성공."""
        seen: list[dict | None] = []

        def opener(req, timeout=0):  # noqa: ANN001
            if req.get_method() == "GET":
                return _Resp(200, {"data": [{"id": "claude-sonnet-5-5"}]})
            body = json.loads(req.data.decode("utf-8"))
            seen.append(body.get("thinking"))
            if body.get("thinking") == {"type": "between_tools"}:
                raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(
                    json.dumps({"error": {"type": "invalid_request_error", "message": "thinking.type between_tools unsupported"}}).encode()))
            return _Resp(200, {"content": [{"type": "text", "text": "pong"}], "usage": {"input_tokens": 5, "output_tokens": 1}, "stop_reason": "end_turn"})

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-usr-" + "a" * 90}, clear=False):
            r = llm_check(opener=opener)
        self.assertTrue(r.call_ok, r.error)
        self.assertEqual(seen, [{"type": "between_tools"}, {"type": "disabled"}])
        self.assertTrue(any("disabled 로 끔" in n for n in r.notes))
