"""LLM 그룹 채움 — 규칙 채움 뒤에, 노드의 question 에 답하는 부분을 LLM 이 쓴다 (자동 생성 계획 §3b).

- `GroupFiller`      : `complete(system, user) -> str` 하나면 된다.
- `MockGroupFiller`  : 테스트용. 그룹 이름 → 미리 준 dict.
- `ClaudeGroupFiller`: anthropic SDK (`pip install kstock[llm]`, ANTHROPIC_API_KEY).
- `fill_groups()`    : 그룹 순서대로 묻고, 답을 검증해 맵에 적용한다. 숫자 환각 방어:
    * 드라이버 current/consensus 는 evidence 문장이 자료 안에 있어야 받는다. 규칙 채움 드라이버(keep_id) 의 숫자는 LLM 이 못 바꾼다.
    * 시나리오 목표가는 앵커 ±25% 로 자른다. 확률은 합 1 로 정규화.
    * Kill 조건의 driver_id 는 맵에 있는 드라이버만, threshold 는 숫자만.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date
from typing import Any, Protocol

from ..models import (
    Action,
    Driver,
    Event,
    MapKind,
    Measurement,
    NewMap,
    Node,
    NodeStatus,
    Rule,
    Scenario,
    Source,
)
from ..reports.digest import ReportDigest
from .prompts import GROUPS, SYSTEM_GROUP, group_prompt, parse_json
from .rules import DRAFT, mark_draft

Progress = Callable[[str, int, int], None]


class GroupFiller(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class MockGroupFiller:
    """answers: {group: dict}. 그룹은 프롬프트 첫 줄 '## 그룹: xxx' 로 읽는다."""

    def __init__(self, answers: dict[str, dict[str, Any]] | None = None):
        self.answers = answers or {}
        self.calls: list[str] = []

    def complete(self, system: str, user: str) -> str:
        m = re.search(r"## 그룹: (\w+)", user)
        group = m[1] if m else "?"
        self.calls.append(group)
        return json.dumps(self.answers.get(group, {}), ensure_ascii=False)


class ClaudeGroupFiller:
    """그룹/Claim 추출용 Claude 호출. max_tokens 기본 8000 — Claim JSON 은 리포트 한 건에 수천 토큰이고,
    사고가 켜진 모델이면 사고 토큰까지 이 예산에서 나간다 (ClaudeFiller 가 사고를 끄고, 잘리면 늘려 다시 묻는다)."""

    def __init__(self, model: str | None = None, *, api_key: str | None = None, max_tokens: int = 8000):
        from ..llm.filler import ClaudeFiller

        self._inner = ClaudeFiller(model, api_key=api_key, max_tokens=max_tokens)

    def complete(self, system: str, user: str) -> str:
        return self._inner._complete(system, user)


class OpenAICompatFiller:
    """OpenAI 호환 서버(vLLM·Ollama·LM Studio…)용 — `.env` 의 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL. urllib 만 쓴다.
    `/v1/chat/completions` 에 system+user 를 보내고 첫 choice 의 content 를 돌려준다. JSON 모드는 서버가 지원할 때만 켠다."""

    def __init__(self, base_url: str | None = None, *, api_key: str | None = None, model: str | None = None,
                 max_tokens: int = 4000, temperature: float = 0.0, timeout: float = 120.0, json_mode: bool = True,
                 opener: Callable[..., Any] | None = None):
        import os

        from ..env import load_dotenv

        load_dotenv()
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "") or "none"
        self.model = model or os.environ.get("LLM_MODEL", "")
        self.max_tokens, self.temperature, self.timeout, self.json_mode = max_tokens, temperature, timeout, json_mode
        self._open = opener
        if not self.base_url:
            raise RuntimeError("LLM_BASE_URL 이 없다 — .env 에 LLM_BASE_URL=http://host:port/v1, LLM_MODEL=… 을 적을 것")

    def complete(self, system: str, user: str) -> str:
        import urllib.error
        import urllib.request

        url = self.base_url + ("" if self.base_url.endswith("/v1") else "/v1") + "/chat/completions"
        body: dict[str, Any] = {"model": self.model, "max_tokens": self.max_tokens, "temperature": self.temperature,
                                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"content-type": "application/json", "authorization": f"Bearer {self.api_key}"})
        open_ = self._open or urllib.request.urlopen
        try:
            with open_(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")[:300]
            if self.json_mode and e.code == 400 and "response_format" in text:
                self.json_mode = False          # 서버가 JSON 모드를 모르면 끄고 한 번 더
                return self.complete(system, user)
            raise RuntimeError(f"LLM 서버 HTTP {e.code}: {text}") from e
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"LLM 서버 응답 모양이 다르다: {str(data)[:200]}") from e


def local_llm_available() -> bool:
    from ..env import env

    return bool(env("LLM_BASE_URL")) and bool(env("LLM_MODEL"))


def make_filler(kind: str = "auto", **kw: Any) -> GroupFiller:
    """kind: claude | local | auto(Claude 키가 쓸 만하면 Claude, 아니면 .env 의 로컬 서버). 못 만들면 RuntimeError."""
    k = (kind or "auto").lower()
    if k in ("claude", "auto") and llm_available() and not key_problem():
        return ClaudeGroupFiller(**kw)
    if k in ("local", "auto") and local_llm_available():
        return OpenAICompatFiller(**{x: v for x, v in kw.items() if x in ("model", "max_tokens", "temperature")})
    if k == "claude":
        raise RuntimeError("Claude 를 쓸 수 없다 — " + llm_status())
    if k == "local":
        raise RuntimeError("로컬 LLM 을 쓸 수 없다 — .env 에 LLM_BASE_URL · LLM_MODEL 이 필요하다")
    raise RuntimeError("쓸 수 있는 LLM 이 없다 — " + llm_status() + " / 로컬: LLM_BASE_URL·LLM_MODEL 없음")


def complete_json(filler: GroupFiller, system: str, user: str, *, retries: int = 1, on_retry: str | None = None) -> dict[str, Any]:
    """complete → JSON dict. 깨진 JSON 이면 '올바른 JSON 만' 을 덧붙여 retries 번 더 묻는다. 끝내 실패하면 ValueError."""
    last: Exception | None = None
    prompt = user
    for _attempt in range(retries + 1):
        raw = filler.complete(system, prompt)
        try:
            return parse_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            last = e
            prompt = user + "\n\n" + (on_retry or "응답은 코드펜스 없이 올바른 JSON 객체 하나여야 한다. 설명 없이 JSON 만 다시 내라.")
    raise ValueError(f"LLM 이 올바른 JSON 을 주지 않았다 ({retries + 1}회): {last}")


def llm_available() -> bool:
    """anthropic 패키지 + ANTHROPIC_API_KEY (환경변수 또는 프로젝트 .env)."""
    return anthropic_installed() and has_key()


def anthropic_installed() -> bool:
    try:
        import anthropic  # type: ignore  # noqa: F401
    except ImportError:
        return False
    return True


def anthropic_version() -> str | None:
    """설치된 anthropic SDK 버전 (없으면 None). 버전마다 Messages.create 인자가 달라 문제 추적에 쓴다."""
    try:
        import anthropic  # type: ignore
    except ImportError:
        return None
    return str(getattr(anthropic, "__version__", "?"))


def has_key() -> bool:
    from ..env import has

    return has("ANTHROPIC_API_KEY")


def key_problem() -> str | None:
    """키가 있어도 모양이 아니면 한 줄로 말해 준다 (값은 안 보인다). Anthropic 키는 'sk-ant-' 로 시작하고 길다."""
    from ..env import env

    v = env("ANTHROPIC_API_KEY") or ""
    if not v:
        return None
    if not v.startswith("sk-ant-") or len(v) < 40:
        return f"ANTHROPIC_API_KEY 가 Anthropic 키 모양이 아니다 ('{v[:6]}…', {len(v)}자) — 'sk-ant-' 로 시작하는 긴 키여야 한다. .env 를 확인"
    return None


# Console(platform.claude.com → API 키) 이 발급하는 키 계열: 사용자 연결 키 sk-ant-usr-… (2026-10 현재), 레거시 sk-ant-api03-…
API_KEY_FAMILIES = ("sk-ant-usr", "sk-ant-api")
API_KEY_FAMILY = API_KEY_FAMILIES[1]


def key_family(v: str | None = None) -> str:
    """키의 계열만 — 세 번째 하이픈까지 ('sk-ant-api03-', 'sk-ant-usr-'). 값은 드러나지 않는다."""
    from ..env import env

    v = v if v is not None else (env("ANTHROPIC_API_KEY") or "")
    parts = v.split("-", 3)
    return "-".join(parts[:3]) + "-" if len(parts) >= 4 else v[:7]


def key_hint() -> str | None:
    """막지는 않지만 알려 줄 것: Console API 키 계열(sk-ant-api…)이 아니면 인증이 거절될 가능성이 높다."""
    from ..env import env

    v = env("ANTHROPIC_API_KEY") or ""
    if not v or key_problem():
        return None
    fam = key_family(v)
    if not any(fam.startswith(f) for f in API_KEY_FAMILIES):
        return (f"키 계열이 '{fam}' — Console(platform.claude.com → API 키) 이 발급하는 키는 'sk-ant-usr-' 또는 'sk-ant-api03-' 로 시작한다. "
                "인증이 거절되면 그곳에서 다시 발급해 .env 에 넣을 것")
    return None


def llm_status() -> str:
    """콘솔·CLI 가 보여 주는 한 줄 — 값은 절대 안 보인다."""
    from ..env import describe

    pkg = f"anthropic {anthropic_version()} 설치됨" if anthropic_installed() else "anthropic 미설치 (pip install -e \".[llm]\")"
    prob = key_problem() or key_hint()
    return f"{describe('ANTHROPIC_API_KEY')} · {pkg}" + (f" · ⚠ {prob}" if prob else "")


# ---------------------------------------------------------------------------- 연결 확인
ANTHROPIC_API = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-sonnet-4-5"   # (호환용) 실제 기본은 llm.models.pick_model 이 목록에서 고른다
WORKSPACE_HELP = ("이 키는 '조직' 범위라 워크스페이스를 지정해야 한다. 둘 중 하나: (a) .env 에 ANTHROPIC_WORKSPACE_ID=wrkspc_… "
                  "(Console → 워크스페이스 → 해당 워크스페이스 → ID) 를 넣는다, (b) 범위가 특정 워크스페이스(예: Default)인 키를 쓴다")


class LlmCheck:
    """`mindmap llm-check` 결과. 키 값은 어디에도 담지 않는다 — 출처·계열·길이만."""

    def __init__(self) -> None:
        self.key_source = ""
        self.key_family = ""
        self.key_len = 0
        self.key_problem: str | None = None
        self.key_hint: str | None = None
        self.pkg_installed = False
        self.workspace = ""                        # anthropic-workspace-id 헤더 (ANTHROPIC_WORKSPACE_ID) 설정 여부
        self.model = ""
        self.model_explicit = False               # 인자/KSTOCK_LLM_MODEL 로 지정했나 (아니면 목록에서 자동 선택)
        self.auth_ok: bool | None = None          # None = 시도 안 함
        self.models: list[str] = []
        self.model_available: bool | None = None
        self.call_ok: bool | None = None
        self.call_text = ""
        self.usage: tuple[int, int] | None = None
        self.elapsed = 0.0
        self.error: str | None = None
        self.notes: list[str] = []

    @property
    def ok(self) -> bool:
        return bool(self.auth_ok) and (self.call_ok is not False) and (self.model_available is not False)

    def lines(self) -> list[str]:
        out = [f"키     : {self.key_source} · 계열 {self.key_family or '-'} · {self.key_len}자"]
        if self.key_problem:
            out.append(f"         ⚠ {self.key_problem}")
        elif self.key_hint:
            out.append(f"         ⚠ {self.key_hint}")
        out.append("패키지 : " + (f"anthropic {anthropic_version()} 설치됨" if self.pkg_installed else "anthropic 미설치 — pip install -e \".[llm]\" (그룹 채움에 필요; 이 확인은 없어도 된다)"))
        out.append("워크스페이스: " + (f"헤더 설정됨 (ANTHROPIC_WORKSPACE_ID {self.workspace})" if self.workspace else "헤더 없음 (워크스페이스 범위 키면 불필요)"))
        if self.auth_ok is None:
            out.append("인증   : 시도 안 함" + (f" — {self.error}" if self.error else ""))
        elif self.auth_ok:
            out.append(f"인증   : OK — 사용 가능 모델 {len(self.models)}개")
            if self.models:
                out.append("         " + ", ".join(self.models[:12]) + (" …" if len(self.models) > 12 else ""))
            avail = "있음" if self.model_available else ("없음 — KSTOCK_LLM_MODEL 을 위 목록의 모델로 바꿀 것" if self.model_available is False else "?")
            how = "KSTOCK_LLM_MODEL/인자" if self.model_explicit else "자동 선택 (KSTOCK_LLM_MODEL 미설정)"
            out.append(f"모델   : {self.model or '-'} · 목록에 {avail} · {how}")
        else:
            out.append(f"인증   : 실패 — {self.error}")
        if self.call_ok is not None:
            if self.call_ok:
                u = f" · 토큰 in/out {self.usage[0]}/{self.usage[1]}" if self.usage else ""
                out.append(f"호출   : OK — 응답 {self.call_text!r}{u} · {self.elapsed:.1f}초")
            else:
                out.append(f"호출   : 실패 — {self.error}")
        for n in self.notes:
            out.append(f"메모   : {n}")
        out.append("결과   : " + ("LLM 그룹 채움을 쓸 수 있다" if self.ok else "지금은 LLM 그룹 채움을 쓸 수 없다 — 위 ⚠/실패 줄을 고칠 것"))
        return out


def _api_error_text(status: int, body: str) -> str:
    try:
        j = json.loads(body)
        e = j.get("error") or {}
        return f"HTTP {status} {e.get('type', '')}: {e.get('message', '')}".strip()
    except Exception:
        return f"HTTP {status} {body[:160].strip()}"


def llm_check(*, model: str | None = None, call: bool = True, timeout: float = 20.0,
              opener: Callable[..., Any] | None = None) -> LlmCheck:
    """키 → 인증(GET /v1/models) → 모델 존재 → 아주 작은 호출 1회(POST /v1/messages, max_tokens 16).
    SDK 없이 urllib 로 한다(패키지 미설치여도 키는 확인된다). opener 는 테스트용(urllib.request.urlopen 대체).
    키 값은 헤더로만 보내고 결과·로그 어디에도 넣지 않는다."""
    import os
    import time
    import urllib.error
    import urllib.request

    from ..env import describe, env, load_dotenv, shadowed, shell_var_fix_hint

    load_dotenv()
    r = LlmCheck()
    v = env("ANTHROPIC_API_KEY") or ""
    r.key_source = describe("ANTHROPIC_API_KEY")
    if shadowed("ANTHROPIC_API_KEY"):
        r.notes.append("셸 환경변수가 .env 의 키를 가리고 있다 — 지우고 새 창에서 다시:\n" + shell_var_fix_hint("ANTHROPIC_API_KEY"))
    r.key_family = key_family(v) if v else ""
    r.key_len = len(v)
    r.key_problem = key_problem()
    r.key_hint = key_hint()
    r.pkg_installed = anthropic_installed()
    ws = (env("ANTHROPIC_WORKSPACE_ID") or "").strip()
    r.workspace = (ws[:10] + "…") if len(ws) > 12 else ws
    explicit = (model or os.environ.get("KSTOCK_LLM_MODEL", "")).strip()
    r.model_explicit = bool(explicit)
    r.model = explicit
    if not v:
        r.error = "ANTHROPIC_API_KEY 없음 — 프로젝트 루트 .env 에 ANTHROPIC_API_KEY=… (.env.example 참고)"
        return r
    if os.environ.get("KSTOCK_OFFLINE"):
        r.error = "KSTOCK_OFFLINE 설정 — 네트워크 확인은 건너뜀"
        return r
    open_ = opener or urllib.request.urlopen
    hdr = {"x-api-key": v, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    if ws:
        hdr["anthropic-workspace-id"] = ws

    def _get(url: str, data: bytes | None = None) -> tuple[int, str]:
        req = urllib.request.Request(url, data=data, headers=hdr, method="POST" if data else "GET")
        try:
            with open_(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    t0 = time.time()
    try:
        status, body = _get(f"{ANTHROPIC_API}/v1/models?limit=100")
    except Exception as e:  # 네트워크 자체가 안 됨
        r.auth_ok = None
        r.error = f"네트워크 오류: {type(e).__name__}: {e}"
        return r
    if status != 200:
        r.auth_ok = False
        r.error = _api_error_text(status, body)
        if status == 401 and r.key_hint:
            r.notes.append(r.key_hint)
        if "workspace" in body.lower():
            r.notes.append(WORKSPACE_HELP)
        return r
    r.auth_ok = True
    try:
        r.models = sorted(m.get("id", "") for m in json.loads(body).get("data", []) if m.get("id"))
    except Exception:
        r.models = []
    from ..llm.models import FALLBACK_MODEL, pick_model

    if r.model_explicit:
        r.model_available = (r.model in r.models) if r.models else None
        if r.model_available is False:
            alt = pick_model(r.models)
            if alt:
                r.notes.append(f"대신 쓸 수 있는 모델 예: {alt} — .env 에 KSTOCK_LLM_MODEL={alt}")
    else:
        chosen = pick_model(r.models) if r.models else None
        r.model = chosen or FALLBACK_MODEL
        r.model_available = True if chosen else None
        if chosen:
            r.notes.append(f"KSTOCK_LLM_MODEL 이 없어 목록에서 {chosen} 을 골랐다 — 고정하려면 .env 에 KSTOCK_LLM_MODEL={chosen}")
    if call and r.model_available is not False:
        # Claude 5 계열은 사고(thinking) 가 기본으로 켜져 작은 max_tokens 를 사고가 다 먹는다 → 끄는 값을 차례로 시도
        # (between_tools → disabled → 없음). 통한 값을 메모에 남겨 ClaudeFiller 와 같은 판단을 보여 준다.
        from ..llm.filler import _THINKING_OFF_CANDIDATES

        status, body = 0, ""
        for mode in _THINKING_OFF_CANDIDATES:
            req: dict[str, Any] = {"model": r.model, "max_tokens": 64,
                                   "messages": [{"role": "user", "content": "한 단어로만 답하라: pong"}]}
            if mode is not None:
                req["thinking"] = mode
            try:
                status, body = _get(f"{ANTHROPIC_API}/v1/messages", json.dumps(req).encode("utf-8"))
            except Exception as e:
                r.call_ok = False
                r.error = f"네트워크 오류: {type(e).__name__}: {e}"
                return r
            if status == 400 and ("thinking" in body.lower() or "effort" in body.lower()):
                continue
            r.notes.append("사고(thinking) 설정: " + (f"{mode['type']} 로 끔" if mode else "모델이 끄기 값을 안 받아 기본(사고 켜짐) 으로 호출 — max_tokens 를 넉넉히"))
            break
        if status != 200:
            r.call_ok = False
            r.error = _api_error_text(status, body)
        else:
            r.call_ok = True
            try:
                j = json.loads(body)
                r.call_text = "".join(b.get("text", "") for b in j.get("content", []) if isinstance(b, dict) and b.get("type", "text") == "text").strip()[:40]
                u = j.get("usage") or {}
                r.usage = (int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0)))
                if not r.call_text:
                    kinds = [b.get("type") for b in j.get("content", []) if isinstance(b, dict)]
                    r.notes.append(f"⚠ 응답에 텍스트가 없다 (stop_reason={j.get('stop_reason')}, 블록={kinds}) — 사고 토큰이 예산을 다 썼을 수 있다")
            except Exception:
                r.call_text = body[:40]
    r.elapsed = time.time() - t0
    return r


# ---------------------------------------------------------------------------- 도우미
def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").rstrip("%").strip())
    except ValueError:
        return None


def _date(v: Any) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _txt(v: Any, n: int = 400) -> str | None:
    if v is None:
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s[:n] or None


def evidence_in(evidence: str | None, materials: str) -> bool:
    """인용 문장이 자료에 있나 — 공백·구두점을 지우고 토큰의 70% 이상이 이어져 나오면 인정."""
    if not evidence:
        return False
    norm = lambda s: re.sub(r"[\s,\.\(\)%원·\-–—:;\"'“”]+", "", s)  # noqa: E731
    e, mat = norm(evidence), norm(materials)
    if len(e) < 6:
        return False
    if e in mat:
        return True
    # 느슨하게: 6글자 조각들의 비율
    chunks = [e[i:i + 6] for i in range(0, max(1, len(e) - 5), 6)]
    hit = sum(1 for c in chunks if c in mat)
    return hit / max(1, len(chunks)) >= 0.7


# ---------------------------------------------------------------------------- 그룹별 적용
def apply_drivers(m: NewMap, data: dict[str, Any], d: ReportDigest, *, today: date, materials: str) -> list[str]:
    notes: list[str] = []
    items = data.get("drivers") or []
    if not isinstance(items, list) or not items:
        return ["LLM 드라이버 응답 없음 — 규칙 채움 유지"]
    existing = {n.id: n for n in m.drivers()}
    src_llm = Source(name="리포트 인용 (LLM 추출)", url=d.source_all().url, retrieved_at=d.as_of)
    chosen: list[Node] = []
    new_nodes: list[Node] = []
    for it in items[:5]:
        if not isinstance(it, dict):
            continue
        keep = it.get("keep_id")
        label = _txt(it.get("label"), 60)
        if keep and keep in existing:
            n = existing[keep]
            dr = n.driver
            assert dr is not None
            # 숫자는 규칙 채움 그대로. 라벨·근거·확인지표·내 추정만 받는다
            if label:
                n.label = label
            my = _num(it.get("my_estimate"))
            if my is not None:
                dr.my_estimate = Measurement(value=my, as_of=today, rationale=f"{_txt(it.get('rationale'), 200) or 'LLM'} — {DRAFT}")
            if _txt(it.get("check_indicator")):
                dr.check_indicator = _txt(it.get("check_indicator"), 120) or dr.check_indicator
            nc = _date(it.get("next_check"))
            if nc and nc > today:
                dr.next_check = nc
            chosen.append(n)
            continue
        cur = _num(it.get("current"))
        if cur is None or not label:
            notes.append(f"LLM 드라이버 '{label or '?'}' 제외 — current 없음")
            continue
        if not evidence_in(_txt(it.get("evidence"), 300), materials):
            notes.append(f"LLM 드라이버 '{label}' 제외 — 인용(evidence) 이 자료에 없다 (숫자 환각 방어)")
            continue
        cons = _num(it.get("consensus"))
        my = _num(it.get("my_estimate"))
        if my is None:
            my = cons if cons is not None else cur
        drv = Driver(current=Measurement(value=cur, as_of=_date(it.get("current_as_of")) or d.as_of, source=src_llm,
                                         rationale=_txt(it.get("evidence"), 200)),
                     consensus=Measurement(value=cons, as_of=d.as_of, source=src_llm if cons is not None else None) if cons is not None else Measurement(),
                     my_estimate=Measurement(value=my, as_of=today, rationale=f"{_txt(it.get('rationale'), 200) or 'LLM'} — {DRAFT}"),
                     check_indicator=_txt(it.get("check_indicator"), 120) or "", next_check=_date(it.get("next_check")),
                     higher_is_better=bool(it.get("higher_is_better", True)))
        node = Node(id="A1.tmp", map=MapKind.NEW, parent="A1", label=label, node_type="estimate", value_type="number",
                    unit=_txt(it.get("unit"), 12), as_of=today, driver=drv, confidence="L",
                    question="이 변수가 틀리면 논제가 무너지는가?")
        new_nodes.append(node)
    if not chosen and not new_nodes:
        return notes + ["LLM 드라이버 전부 제외 — 규칙 채움 유지"]
    # 슬롯 재배치: 유지된 것 먼저, 새 것은 빈 슬롯 또는 A1.4/A1.5
    slot_ids = [n.id for n in m.children("A1")]
    keep_ids = {n.id for n in chosen}
    free = [sid for sid in slot_ids if sid not in keep_ids]
    for node in new_nodes:
        if free:
            sid = free.pop(0)
            tgt = m[sid]
            tgt.label, tgt.unit, tgt.driver, tgt.as_of, tgt.confidence = node.label, node.unit, node.driver, today, node.confidence
            mark_draft(tgt)
            chosen.append(tgt)
        else:
            if len(chosen) >= 5:
                break
            nid = f"A1.{len(slot_ids) + 1}"
            slot_ids.append(nid)
            node.id = nid
            mark_draft(node)
            m.add(node)
            chosen.append(node)
    # LLM 이 고르지 않은 규칙 드라이버는 비운다 (3개 미만이 되면 남긴다). 설계 v5 의 원인 드라이버(role=cause) 는 남긴다
    for sid in free:
        if len(chosen) >= 3 and not (m[sid].driver is not None and m[sid].driver.role == "cause"):
            m[sid].driver = None
            m[sid].label = f"드라이버 {sid.split('.')[1]}"
            m[sid].unit = None
    notes.append(f"LLM 드라이버 {len(chosen)}개 (유지 {len(keep_ids)}, 신규 {len(new_nodes)})")
    return notes


def apply_thesis(m: NewMap, data: dict[str, Any], *, today: date) -> list[str]:
    notes: list[str] = []
    t = _txt(data.get("thesis"), 600)
    if t:
        m["A0"].set(f"[LLM 초안] {t}", as_of=today, status=NodeStatus.unverified)
        mark_draft(m["A0"])
    if data.get("horizon") in ("3M", "1Y", "3Y+"):
        m["A0.2"].set(data["horizon"], as_of=today, status=NodeStatus.unverified)
    if data.get("confidence") in ("H", "M", "L"):
        m["A0.3"].set(data["confidence"], as_of=today, status=NodeStatus.unverified)
    if _txt(data.get("implied")):
        m["A2.1"].set(_txt(data["implied"], 500), as_of=today, source=m["A2.1"].source or Source(name="LLM (리포트 발췌 기반)", retrieved_at=today))
        mark_draft(m["A2.1"])
    if _txt(data.get("mine")):
        m["A2.2"].set(f"[LLM 초안] {_txt(data['mine'], 500)}", as_of=today, status=NodeStatus.unverified)
        mark_draft(m["A2.2"])
    if data.get("why_market_wrong") in ("시간축", "정보", "심리", "구조"):
        m["A2.3"].set(data["why_market_wrong"], as_of=today, status=NodeStatus.unverified)
        mark_draft(m["A2.3"])
    if data.get("invest_type") in ("growth", "value", "turnaround", "momentum", "event") and data["invest_type"] != (m.profile.value if m.profile else None):
        notes.append(f"LLM 이 제안한 투자유형 {data['invest_type']} ≠ 현재 프로파일 {m.profile.value if m.profile else '-'} — 바꾸려면 새 맵을 만든다")
    return notes


def apply_scenarios(m: NewMap, data: dict[str, Any], *, today: date, anchors: dict[str, float]) -> list[str]:
    notes: list[str] = []
    probs: dict[str, float] = {}
    tps: dict[str, float] = {}
    for nm in ("Bear", "Base", "Bull"):
        sc = data.get(nm)
        if not isinstance(sc, dict):
            return ["LLM 시나리오 응답 불완전 — 규칙 앵커 유지"]
        p, tp = _num(sc.get("probability")), _num(sc.get("target_price"))
        if p is None or tp is None or tp <= 0:
            return ["LLM 시나리오에 확률/목표가 누락 — 규칙 앵커 유지"]
        anchor = anchors.get(nm)
        if anchor:
            lo, hi = anchor * 0.75, anchor * 1.25
            if not (lo <= tp <= hi):
                notes.append(f"{nm} 목표가 {tp:,.0f} 가 앵커 {anchor:,.0f} ±25% 밖 → {min(max(tp, lo), hi):,.0f} 로 자름")
                tp = min(max(tp, lo), hi)
        probs[nm], tps[nm] = p, tp
    s = sum(probs.values())
    if s <= 0:
        return ["LLM 시나리오 확률 합 0 — 규칙 앵커 유지"]
    if abs(s - 1.0) > 0.01:
        notes.append(f"확률 합 {s:.2f} → 1.0 으로 정규화")
        probs = {k: v / s for k, v in probs.items()}
    # 순서 보정
    if not (tps["Bear"] < tps["Base"] < tps["Bull"]):
        notes.append("목표가 순서 Bear<Base<Bull 위반 → 정렬")
        vals = sorted(tps.values())
        tps = dict(zip(("Bear", "Base", "Bull"), vals, strict=True))
    for nid, nm in (("A3.1", "Bear"), ("A3.2", "Base"), ("A3.3", "Bull")):
        sc = data[nm]
        assumptions = [str(a)[:120] for a in (sc.get("assumptions") or []) if str(a).strip()][:6]
        if not assumptions and m[nid].scenario is not None:
            assumptions = list(m[nid].scenario.assumptions)
        m[nid].scenario = Scenario(name=nm, probability=round(probs[nm], 3), target_price=round(tps[nm], -2) if tps[nm] >= 10000 else round(tps[nm]),
                                   assumptions=assumptions, horizon_months=12,
                                   valuation_method=(_txt(sc.get("method"), 160) or "") + " (LLM 초안)")
        m[nid].as_of = today
        mark_draft(m[nid])
    # 마지막 확률을 맞춰 합이 정확히 1 이 되게
    tot = sum(m[n].scenario.probability or 0.0 for n in ("A3.1", "A3.2", "A3.3"))
    if abs(tot - 1.0) > 1e-6:
        m["A3.2"].scenario.probability = round(m["A3.2"].scenario.probability + (1.0 - tot), 3)
    return notes


def apply_risks(m: NewMap, data: dict[str, Any], *, today: date) -> list[str]:
    notes: list[str] = []
    src = Source(name="LLM (리포트 발췌 기반)", retrieved_at=today)
    risks = [r for r in (data.get("risks") or []) if isinstance(r, dict) and _txt(r.get("text"))][:3]
    for nid, r in zip(("A4.1", "A4.2", "A4.3"), risks, strict=False):
        p = _num(r.get("probability"))
        imp = _num(r.get("impact_pct"))
        m[nid].set({"probability": min(max(p if p is not None else 0.25, 0.0), 1.0), "impact_pct": -abs(imp) if imp is not None else -20,
                    "text": _txt(r["text"], 200) + f" ({DRAFT})"}, as_of=today, source=src)
        mark_draft(m[nid])
    driver_ids = {n.id for n in m.drivers()}
    kills = [k for k in (data.get("kill") or []) if isinstance(k, dict)][:2]
    made = 0
    for k in kills:
        did, op, thr = k.get("driver_id"), k.get("op"), _num(k.get("threshold"))
        if did not in driver_ids or op not in ("<", "<=", ">", ">=") or thr is None:
            notes.append(f"LLM Kill 조건 제외 — driver_id/op/threshold 가 맞지 않음: {k}")
            continue
        made += 1
        nid = "A4.4.1" if made == 1 else "A4.4.2"
        rule = Rule(trigger_type="fact", condition=f"driver[{did}].current {op} {thr:g}", action=Action.exit, escalate_to="RED",
                    note=(_txt(k.get("note"), 200) or "") + f" — {DRAFT}")
        if nid in m:
            m[nid].rule = rule
            m[nid].as_of = today
            mark_draft(m[nid])
        else:
            node = Node(id=nid, map=MapKind.NEW, parent="A4.4", label=f"무효화 조건 {made}", node_type="rule", value_type="text", as_of=today, rule=rule)
            mark_draft(node)
            m.add(node)
    if _txt(data.get("early_warning")):
        m["A4.5"].set(_txt(data["early_warning"], 300), as_of=today, source=src)
        mark_draft(m["A4.5"])
    return notes


def apply_catalysts(m: NewMap, data: dict[str, Any], *, today: date) -> list[str]:
    events = [e for e in (data.get("events") or []) if isinstance(e, dict)]
    good: list[Event] = []
    driver_ids = {n.id for n in m.drivers()}
    for e in events:
        dt = _date(e.get("date"))
        fact = _txt(e.get("fact"), 160)
        if dt is None or not fact or dt < today:
            continue
        est = bool(e.get("estimated"))
        good.append(Event(date=dt, fact=fact + (" (예정일 추정)" if est and "추정" not in fact else ""),
                          affects=[a for a in (e.get("affects") or []) if a in driver_ids],
                          direction=e.get("direction") if e.get("direction") in ("강화", "약화", "중립") else "중립",
                          priced_in="아니오", materiality=e.get("materiality") if e.get("materiality") in ("H", "M", "L") else "M",
                          source=Source(name="LLM (리포트 발췌 기반)" + (" · 통상 일정 추정" if est else ""), retrieved_at=today)))
    if not good:
        return ["LLM 촉매 응답 없음 — 규칙 일정 유지"]
    for n in list(m.children("A5.1")):
        del m.nodes[n.id]
    for i, ev in enumerate(sorted(good, key=lambda e: e.date)[:5], start=1):
        node = Node(id=f"A5.1.{i}", map=MapKind.NEW, parent="A5.1", label=ev.fact[:30], node_type="event", value_type="text", as_of=today, event=ev)
        mark_draft(node)
        m.add(node)
    return [f"LLM 촉매 {min(len(good), 5)}개"]


def apply_evidence(m: NewMap, data: dict[str, Any], *, today: date) -> list[str]:
    src = Source(name="LLM (리포트 발췌 요약)", retrieved_at=today)
    for key, nid in (("business_model", "A8.1.1"), ("moat", "A8.1.2"), ("governance_payout", "A8.1.3"), ("peers", "A3.6.2")):
        t = _txt(data.get(key), 400)
        if t and t.lower() != "null":
            m[nid].set(t, as_of=today, source=src)
            mark_draft(m[nid])
    return []


APPLY = {"drivers": apply_drivers, "thesis": apply_thesis, "scenarios": apply_scenarios, "risks": apply_risks,
         "catalysts": apply_catalysts, "evidence": apply_evidence}


# ---------------------------------------------------------------------------- 진입점
def fill_groups(m: NewMap, d: ReportDigest, filler: GroupFiller, *, today: date, sector: str | None,
                anchors: dict[str, float], groups: tuple[str, ...] = GROUPS, progress: Progress | None = None) -> list[str]:
    notes: list[str] = []
    materials = d.materials()
    status: dict[str, str] = {}
    for i, g in enumerate(groups, start=1):
        if progress:
            progress(f"LLM {g}", i, len(groups))
        try:
            raw = filler.complete(SYSTEM_GROUP, group_prompt(g, m, d, sector=sector, anchors=anchors))
            data = parse_json(raw)
        except Exception as e:  # noqa: BLE001 — 한 그룹이 실패해도 나머지는 간다
            reason = f"{type(e).__name__}: {str(e)[:120]}"
            status[g] = "fail: " + reason
            notes.append(f"LLM {g} 실패: {reason} — 규칙 채움 유지")
            if "authentication" in type(e).__name__.lower() or "401" in str(e) or "invalid x-api-key" in str(e).lower():
                notes.append("LLM 인증 실패 — .env 의 ANTHROPIC_API_KEY 가 틀렸다. 나머지 그룹도 건너뛴다")
                for rest in groups[i:]:
                    status[rest] = "skip: 인증 실패"
                break
            continue
        status[g] = "ok"
        if g == "drivers":
            notes += apply_drivers(m, data, d, today=today, materials=materials)
        elif g == "scenarios":
            notes += apply_scenarios(m, data, today=today, anchors=anchors)
        else:
            notes += APPLY[g](m, data, today=today)  # type: ignore[operator]
    ok = sum(1 for v in status.values() if v == "ok")
    meta = m.meta.setdefault("draft", {})
    meta["llm"] = ok > 0
    meta["llm_groups"] = status
    notes.append(f"LLM 그룹 {ok}/{len(groups)} 성공" + ("" if ok == len(groups) else " — 실패한 그룹은 규칙 값이 남아 있다"))
    return notes


__all__ = ["GroupFiller", "MockGroupFiller", "ClaudeGroupFiller", "llm_available", "anthropic_installed", "has_key", "key_problem", "llm_status",
           "fill_groups", "evidence_in", "GROUPS"]
