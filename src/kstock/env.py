""".env 읽기 — 표준 라이브러리만으로 (python-dotenv 없이).

찾는 순서: 환경변수 `KSTOCK_ENV` 가 가리키는 파일 → 프로젝트 루트 `.env` (이 파일의 세 단계 위, 즉 `C:\\kstock\\mindmap\\.env`)
→ 현재 폴더와 그 위 두 단계의 `.env`. 처음 찾은 파일 하나만 읽는다.
이미 환경변수에 있는 키는 덮어쓰지 않는다 (셸에서 준 값이 우선). 값은 어디에도 찍지 않는다 — 키 이름만 로그에 남긴다.

    from kstock.env import load_dotenv, env
    load_dotenv()                      # 한 번만 실제로 읽고, 다음부터는 캐시
    key = env("ANTHROPIC_API_KEY")     # 없으면 None
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NAME = ".env"
_LINE = re.compile(r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<val>.*)$")

_loaded_from: Path | None = None
_loaded_keys: list[str] = []
_tried = False


def parse_dotenv(text: str) -> dict[str, str]:
    """KEY=value 줄들 → dict. 주석(#)·빈 줄 무시, `export KEY=`, 따옴표("…" '…'), 따옴표 없는 값의 뒤쪽 ` # 주석` 제거."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, val = m["key"], m["val"].strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            quote = val[0]
            val = val[1:-1]
            if quote == '"':
                val = val.replace("\\n", "\n")
        else:
            val = re.split(r"\s+#", val, maxsplit=1)[0].strip()
        out[key] = val
    return out


def find_dotenv(start: Path | None = None) -> Path | None:
    cands: list[Path] = []
    explicit = os.environ.get("KSTOCK_ENV")
    if explicit:
        cands.append(Path(explicit).expanduser())
    cands.append(ROOT / DEFAULT_NAME)
    here = (start or Path.cwd()).resolve()
    for p in (here, *here.parents[:2]):
        cands.append(p / DEFAULT_NAME)
    seen: set[Path] = set()
    for c in cands:
        if c in seen:
            continue
        seen.add(c)
        if c.is_file():
            return c
    return None


def load_dotenv(path: str | Path | None = None, *, override: bool = False, force: bool = False) -> list[str]:
    """`.env` 를 os.environ 에 넣는다. 돌려주는 것은 새로 넣은 키 이름들 (값은 아니다). 두 번째부터는 캐시 (force 로 다시)."""
    global _loaded_from, _loaded_keys, _tried
    if _tried and not force and path is None:
        return list(_loaded_keys)
    _tried = True
    p = Path(path).expanduser() if path else find_dotenv()
    _loaded_keys = []
    if p is None or not p.is_file():
        _loaded_from = None
        return []
    try:
        data = parse_dotenv(p.read_text(encoding="utf-8-sig"))
    except OSError:
        _loaded_from = None
        return []
    for k, v in data.items():
        if override or k not in os.environ:
            os.environ[k] = v
            _loaded_keys.append(k)
    _loaded_from = p
    return list(_loaded_keys)


def loaded_from() -> Path | None:
    """마지막으로 읽은 .env 경로 (없으면 None). 화면·로그에 '어디서 읽었나' 를 보일 때."""
    return _loaded_from


def env(key: str, default: str | None = None) -> str | None:
    load_dotenv()
    v = os.environ.get(key)
    return v if v not in (None, "") else default


def has(key: str) -> bool:
    return env(key) is not None


def describe(key: str) -> str:
    """'ANTHROPIC_API_KEY: .env 에서 읽음 (경로)' 처럼 값 없이 출처만 말하는 한 줄."""
    load_dotenv()
    if key not in os.environ or not os.environ[key]:
        src = loaded_from()
        return f"{key} 없음" + (f" — {src} 에도 없다" if src else " — .env 파일을 찾지 못함 (.env.example 참고)")
    if key in _loaded_keys and _loaded_from is not None:
        return f"{key}: .env 에서 읽음 ({_loaded_from})"
    return f"{key}: 환경변수"


__all__ = ["parse_dotenv", "find_dotenv", "load_dotenv", "loaded_from", "env", "has", "describe", "ROOT", "DEFAULT_NAME"]
