"""ANTHROPIC_API_KEY 가 실제로 동작하는지 혼자서 확인하는 스크립트 (표준 라이브러리만, 패키지 설치 불필요).

    python tools/check_api_key.py                # 프로젝트 루트 .env 의 ANTHROPIC_API_KEY
    python tools/check_api_key.py --env C:\\path\\.env
    python tools/check_api_key.py --model claude-sonnet-4-5
    python tools/check_api_key.py --no-call      # 인증·모델 목록만 (메시지 호출 안 함)
    python tools/check_api_key.py --env-first    # 셸 환경변수가 있어도 .env 의 키를 쓴다

셸 환경변수 ANTHROPIC_API_KEY 가 있으면 그것이 우선이다 (프로젝트의 kstock.env 와 같은 규칙). .env 와 값이 다르면 알려 주고,
셸 키가 인증에 실패하면 .env 의 키로 한 번 더 시도해 어느 쪽이 살아 있는지 보여 준다.

하는 일:  1) .env 에서 키 읽기 (셸 환경변수가 있으면 그것이 우선)  2) 키 모양 (계열·길이)  3) GET /v1/models 로 인증
         4) 설정한 모델이 목록에 있는지  5) POST /v1/messages 로 아주 작은 호출 1회 (max_tokens 64, 사고 끔)
         6) anthropic 패키지가 설치돼 있으면 SDK 경로로도 1회 (프로젝트의 ClaudeFiller 가 쓰는 길)
키 값은 화면·파일 어디에도 찍지 않는다 — 계열(세 번째 하이픈까지)과 길이만 보인다.
종료 코드 0 = 쓸 수 있음, 1 = 문제 있음.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.anthropic.com"
VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"            # 목록을 못 받았을 때만
MODEL_PREFERENCE = ("claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6", "claude-sonnet-4-5-20250929", "claude-sonnet-4-5")
FAMILIES = ("sk-ant-usr-", "sk-ant-api")   # Console 이 발급하는 계열: 사용자 연결 키 / 레거시


def pick_model(available: list[str]) -> str | None:
    """목록에서 선호 순서(최신 Sonnet)대로 하나 — kstock.llm.models.pick_model 과 같은 규칙 (이 스크립트는 패키지 없이 돈다)."""
    ids = sorted({m for m in available if m})
    for m in MODEL_PREFERENCE:
        if m in ids:
            return m
    sonnets = [m for m in ids if "sonnet" in m]
    return sonnets[-1] if sonnets else (ids[-1] if ids else None)


def find_env(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    if os.environ.get("KSTOCK_ENV") and Path(os.environ["KSTOCK_ENV"]).is_file():
        return Path(os.environ["KSTOCK_ENV"])
    here = Path(__file__).resolve().parent
    for base in (here.parent, Path.cwd(), *Path.cwd().parents):
        p = base / ".env"
        if p.is_file():
            return p
    return None


def shell_var_hint(key: str) -> str:
    """셸 변수가 어디서 왔나(Windows 레지스트리) + 지우는 명령. kstock.env.shell_var_fix_hint 와 같은 논리 (여긴 표준 라이브러리만)."""
    origin = "셸 세션"
    if os.name == "nt":
        try:
            import winreg  # type: ignore

            def _has(root, sub):  # noqa: ANN001
                try:
                    with winreg.OpenKey(root, sub) as h:
                        winreg.QueryValueEx(h, key)
                        return True
                except OSError:
                    return False

            user = _has(winreg.HKEY_CURRENT_USER, r"Environment")
            mach = _has(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment")
            origin = ("Windows 사용자 환경변수 + 시스템 환경변수" if user and mach else "Windows 사용자 환경변수" if user
                      else "Windows 시스템 환경변수 (관리자 권한 필요)" if mach
                      else "이 셸 세션에서 설정됨 — PowerShell 프로필($PROFILE) 또는 conda 환경 변수 (conda env config vars list -n ksa)")
        except Exception:  # noqa: BLE001
            origin = "알 수 없음"
    lines = [f"셸 변수 출처: {origin}", f"이번 창만:  Remove-Item Env:{key}"]
    if "사용자" in origin:
        lines.append(f"영구:       [Environment]::SetEnvironmentVariable('{key}', $null, 'User')")
    if "시스템" in origin:
        lines.append(f"영구:       [Environment]::SetEnvironmentVariable('{key}', $null, 'Machine')   (관리자 PowerShell)")
    if "세션" in origin:
        lines.append(f"conda:      conda env config vars unset {key} -n ksa  → conda activate ksa 다시")
        lines.append(f"프로필:     notepad $PROFILE 에서 {key} 줄 삭제")
    lines.append("또는 .env 에 KSTOCK_PREFER_DOTENV=1 한 줄 — .env 의 키가 셸 변수를 덮는다 (mindmap 명령에 적용; 이 스크립트는 --env-first)")
    return "\n".join(lines)


def read_key(env_path: Path | None, *, env_first: bool = False) -> tuple[str, str]:
    """(키, 출처). 셸 환경변수 > .env (env_first 면 .env 먼저). .env 는 KEY=value / export KEY=value / 따옴표 허용, # 주석 무시."""
    v = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if v and not env_first:
        return v, "셸 환경변수"
    fv, fsrc = file_key(env_path)
    if fv:
        return fv, fsrc
    if v:
        return v, "셸 환경변수 (.env 에는 없음)"
    return "", fsrc


def file_value(env_path: Path | None, name: str) -> str:
    """.env 에 적힌 name 의 값 (없으면 ''). KEY=value / export KEY=value / 따옴표 허용, # 주석 무시."""
    if env_path is None:
        return ""
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = raw.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        if ln.startswith("export "):
            ln = ln[7:].strip()
        k, val = ln.split("=", 1)
        if k.strip() != name:
            continue
        val = val.strip()
        if val[:1] in "\"'" and val[-1:] == val[:1] and len(val) >= 2:
            val = val[1:-1]
        elif " #" in val:
            val = val.split(" #", 1)[0].strip()
        return val
    return ""


def file_key(env_path: Path | None) -> tuple[str, str]:
    """.env 에 적힌 ANTHROPIC_API_KEY 만 (셸 무시)."""
    if env_path is None:
        return "", ".env 없음"
    v = file_value(env_path, "ANTHROPIC_API_KEY")
    return (v, f".env ({env_path})") if v else ("", f".env 에 ANTHROPIC_API_KEY 없음 ({env_path})")


def family(v: str) -> str:
    parts = v.split("-", 3)
    return "-".join(parts[:3]) + "-" if len(parts) >= 4 else v[:7]


WORKSPACE: str | None = None   # anthropic-workspace-id 헤더 (ANTHROPIC_WORKSPACE_ID / --workspace)
WORKSPACE_HELP = """    → 이 키는 '조직' 범위(워크스페이스 미지정)라 요청마다 워크스페이스를 알려 줘야 한다. 둘 중 하나:
      (a) .env 에  ANTHROPIC_WORKSPACE_ID=wrkspc_…  추가 — Console(platform.claude.com) → 워크스페이스 → 쓸 워크스페이스(예: Default) → ID 복사.
          프로젝트(ClaudeFiller·llm-check)도 같은 변수를 읽어 헤더를 붙인다.  지금 바로 시험:  --workspace wrkspc_…
      (b) 범위가 특정 워크스페이스인 키를 쓴다 — Console 키 목록에서 '범위' 열이 Default 인 키 (헤더 불필요)"""


def request(url: str, key: str, data: bytes | None = None, timeout: float = 30.0) -> tuple[int, str]:
    headers = {"x-api-key": key, "anthropic-version": VERSION, "content-type": "application/json"}
    if WORKSPACE:
        headers["anthropic-workspace-id"] = WORKSPACE
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def err_text(status: int, body: str) -> str:
    try:
        e = json.loads(body).get("error") or {}
        return f"HTTP {status} {e.get('type', '')}: {e.get('message', '')}".strip()
    except Exception:
        return f"HTTP {status} {body[:200].strip()!r}" + ("  ← JSON 이 아니다: 프록시/방화벽이 대신 답했을 가능성" if body.strip() else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", help=".env 경로 (기본: 프로젝트 루트 / 현재 폴더)")
    ap.add_argument("--model", help=f"확인할 모델 (기본 KSTOCK_LLM_MODEL 또는 {DEFAULT_MODEL})")
    ap.add_argument("--no-call", action="store_true", help="메시지 호출은 하지 않음")
    ap.add_argument("--no-sdk", action="store_true", help="anthropic 패키지 경로 확인은 건너뜀")
    ap.add_argument("--env-first", action="store_true", help="셸 환경변수가 있어도 .env 의 키를 쓴다")
    ap.add_argument("--workspace", help="anthropic-workspace-id 헤더 값 (wrkspc_…; 기본 .env/환경변수 ANTHROPIC_WORKSPACE_ID)")
    a = ap.parse_args(argv)
    ok = True
    global WORKSPACE

    env_path = find_env(a.env)
    if not a.env_first and env_path is not None and file_value(env_path, "KSTOCK_PREFER_DOTENV").strip().lower() in ("1", "true", "yes", "on"):
        a.env_first = True                       # .env 가 스스로 "나를 우선" 이라고 했다 (kstock.env 와 같은 규칙)
        print("    (.env 의 KSTOCK_PREFER_DOTENV=1 — .env 의 키를 셸 변수보다 먼저 쓴다)")
    key, src = read_key(env_path, env_first=a.env_first)
    print(f"[1] 키 출처 : {src}")
    if not key:
        print("    ✗ ANTHROPIC_API_KEY 를 찾지 못했다 — 프로젝트 루트 .env 에 ANTHROPIC_API_KEY=… 를 적을 것")
        return 1
    shell_v = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    file_v, _ = file_key(env_path)
    conflict = bool(shell_v) and bool(file_v) and shell_v != file_v
    if conflict:
        other = ("셸 환경변수", shell_v) if a.env_first else (".env", file_v)
        print(f"    ⚠ 셸 환경변수와 .env 의 키가 서로 다르다 — 지금 쓰는 것은 {src}, 안 쓰는 쪽은 {other[0]} (계열 {family(other[1])} · {len(other[1])}자)")
        if not a.env_first:
            print("      프로젝트(kstock.env)도 셸 변수를 우선하므로 .env 를 고쳐도 이 셸 변수가 남아 있으면 반영되지 않는다")
    WORKSPACE = (a.workspace or os.environ.get("ANTHROPIC_WORKSPACE_ID") or file_value(env_path, "ANTHROPIC_WORKSPACE_ID") or "").strip() or None
    fam = family(key)
    bad = [c for c in key if c.isspace() or c in "\"'"]
    print(f"[2] 키 모양 : 계열 {fam} · 길이 {len(key)}자" + (" · ⚠ 공백/따옴표가 섞여 있다" if bad else "")
          + (f" · 워크스페이스 헤더 {WORKSPACE[:10]}…" if WORKSPACE else " · 워크스페이스 헤더 없음"))
    if not any(fam.startswith(f) for f in FAMILIES):
        print(f"    ⚠ Console(platform.claude.com → API 키) 이 발급하는 키는 {' / '.join(FAMILIES)} 로 시작한다")
    if bad:
        ok = False

    t0 = time.time()
    try:
        status, body = request(f"{API}/v1/models?limit=100", key)
    except Exception as e:  # DNS·프록시·TLS
        print(f"[3] 인증    : ✗ 네트워크 오류 {type(e).__name__}: {e}")
        print("    → 이 PC 에서 api.anthropic.com 에 닿지 않는다 (프록시/방화벽). 키 문제가 아닐 수 있다")
        return 1
    if status != 200:
        print(f"[3] 인증    : ✗ {err_text(status, body)}")
        if status == 401 and conflict and not a.env_first:
            # 셸의 (아마 예전) 키가 거절됐다 — .env 의 키로 한 번 더
            print(f"    → 셸 환경변수의 키가 거절됐다. .env 의 키(계열 {family(file_v)} · {len(file_v)}자) 로 다시 시도한다 …")
            try:
                st2, body2 = request(f"{API}/v1/models?limit=5", file_v)
            except Exception as e:
                st2, body2 = 0, str(e)
            if st2 == 200:
                print("    ✓ .env 의 키는 살아 있다. 문제는 셸 환경변수에 남은 예전 키다. 지우고 새 창에서 다시:")
                for ln in shell_var_hint("ANTHROPIC_API_KEY").splitlines():
                    print("        " + ln)
                print("        지금 바로 .env 키로 끝까지 확인하려면:  python tools\\check_api_key.py --env-first")
            else:
                print(f"    ✗ .env 의 키도 거절됐다 ({err_text(st2, body2)}). Console 에서 키 상태와 복사한 값(앞뒤 잘림) 을 확인할 것")
            return 1
        if status == 401:
            print("    → 키가 거절됐다. Console 에서 키 상태(활성/삭제)와 복사한 값(앞뒤 잘림) 을 확인하고, 필요하면 새 키를 발급해 .env 에 넣을 것")
        if "workspace" in body.lower():
            print(WORKSPACE_HELP)
        return 1
    models = sorted(m.get("id", "") for m in json.loads(body).get("data", []) if m.get("id"))
    print(f"[3] 인증    : ✓ OK — 사용 가능 모델 {len(models)}개 ({time.time() - t0:.1f}초)")
    for m in models:
        print(f"              {m}")

    explicit = (a.model or os.environ.get("KSTOCK_LLM_MODEL") or file_value(env_path, "KSTOCK_LLM_MODEL") or "").strip()
    if explicit:
        model = explicit
        if models and model not in models:
            alt = pick_model(models)
            print(f"[4] 모델    : ✗ '{model}' 이 목록에 없다 → .env 에 KSTOCK_LLM_MODEL={alt} 처럼 바꾸거나 --model 로 지정")
            return 1
        print(f"[4] 모델    : ✓ {model} (KSTOCK_LLM_MODEL/--model)")
    else:
        model = pick_model(models) or DEFAULT_MODEL
        print(f"[4] 모델    : ✓ {model} — KSTOCK_LLM_MODEL 이 없어 목록에서 자동 선택 (고정하려면 .env 에 KSTOCK_LLM_MODEL={model})")

    if a.no_call:
        print("[5] 호출    : 건너뜀 (--no-call)")
        return 0 if ok else 1
    # Claude 5 계열(Sonnet 5.5 …) 은 사고(thinking) 가 기본으로 켜져 작은 max_tokens 를 사고가 다 먹어 텍스트가 빈다.
    # 끄는 값을 모델이 받는 것부터 차례로 시도: between_tools(5.5) → disabled(4.x) → 없음(옛 모델).
    t0 = time.time()
    status, body, used_mode = 0, "", None
    for mode in ({"type": "between_tools"}, {"type": "disabled"}, None):
        req = {"model": model, "max_tokens": 64, "messages": [{"role": "user", "content": "한 단어로만 답하라: pong"}]}
        if mode is not None:
            req["thinking"] = mode
        status, body = request(f"{API}/v1/messages", key, json.dumps(req).encode("utf-8"))
        if status == 400 and ("thinking" in body.lower() or "effort" in body.lower()):
            continue
        used_mode = mode
        break
    if status != 200:
        print(f"[5] 호출    : ✗ {err_text(status, body)}")
        return 1
    j = json.loads(body)
    text = "".join(b.get("text", "") for b in j.get("content", []) if isinstance(b, dict) and b.get("type", "text") == "text").strip()
    u = j.get("usage") or {}
    print(f"[5] 호출    : ✓ 응답 {text!r} · 토큰 in/out {u.get('input_tokens')}/{u.get('output_tokens')} · {time.time() - t0:.1f}초"
          + f" · 사고 {'끔(' + used_mode['type'] + ')' if used_mode else '모델 기본'}")
    if not text:
        kinds = [b.get("type") for b in j.get("content", []) if isinstance(b, dict)]
        print(f"    ⚠ 텍스트가 비었다 (stop_reason={j.get('stop_reason')}, 블록={kinds}) — 사고 토큰이 예산을 다 썼을 수 있다")

    if a.no_sdk:
        print("[6] SDK     : 건너뜀 (--no-sdk)")
    else:
        try:
            import anthropic  # type: ignore
        except ImportError:
            print("[6] SDK     : anthropic 패키지 미설치 — 그룹 채움(ClaudeFiller) 에는 필요: pip install -e \".[llm]\"  (키 자체는 위에서 확인됨)")
        else:
            try:
                t0 = time.time()
                client = anthropic.Anthropic(api_key=key, default_headers={"anthropic-workspace-id": WORKSPACE} if WORKSPACE else None)
                kw = {"model": model, "max_tokens": 64, "messages": [{"role": "user", "content": "한 단어로만 답하라: pong"}]}
                if used_mode is not None:
                    kw["thinking"] = used_mode
                msg = client.messages.create(**kw)
                sdk_text = "".join(getattr(b, "text", "") or "" for b in msg.content if getattr(b, "type", "text") == "text").strip()
                print(f"[6] SDK     : ✓ anthropic {getattr(anthropic, '__version__', '?')} · 응답 {sdk_text!r} · {time.time() - t0:.1f}초")
            except Exception as e:
                print(f"[6] SDK     : ✗ {type(e).__name__}: {str(e)[:200]}")
                ok = False
    print("결과       : " + ("✓ 이 키로 LLM 그룹 채움을 쓸 수 있다 (mindmap draft --llm / 콘솔 LLM 체크)" if ok else "✗ 위 ✗ 줄을 고칠 것"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
