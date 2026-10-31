"""종목 찾기 — 코드 하나, 이름 하나만 있어도 나머지를 채운다.

출처 (앞에서부터, 있는 것을 쓴다)
1. `data/ref/symbols.csv`           이 프로젝트가 들고 있는 스냅숏 (code,name,market,sector)
2. 리서치 DB `prices` 표            `C:\\kstock\\research\\data\\db\\kstock.sqlite` 의 최신 거래일 행 (KOSPI·KOSDAQ ≈ 2,700 종목).
                                     읽기 전용으로 연다. 경로는 설정 `research_db` → 환경변수 `KSTOCK_RESEARCH_DB` → 형제 폴더 순.
3. 이미 등록된 관심종목             위 둘이 없어도 한 번 넣은 종목은 다시 찾힌다.

검색 규칙: 코드는 숫자만 남겨 6자리로 맞춘다 (`5930` → `005930`, `A005930` → `005930`). 이름은 정확 일치 →
공백·대소문자 무시 일치 → 접두 → 포함 순으로 좁히고, 같은 단계에서 여러 개면 시가총액 큰 순으로 돌려준다.
"""

from __future__ import annotations

import csv
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import Store

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = ROOT / "data" / "ref" / "symbols.csv"
DEFAULT_RESEARCH_DB = ROOT.parent / "research" / "data" / "db" / "kstock.sqlite"


@dataclass(frozen=True)
class Symbol:
    code: str
    name: str
    market: str = "KR"
    sector: str | None = None
    market_cap: float | None = None

    def label(self) -> str:
        return f"{self.code} {self.name} ({self.market})"


@dataclass
class Resolution:
    """찾기 결과. status: exact(하나) / many(여러 후보) / none(없음)."""

    status: str
    candidates: list[Symbol] = field(default_factory=list)
    query: str = ""

    @property
    def one(self) -> Symbol | None:
        return self.candidates[0] if self.status == "exact" else None


def normalize_code(value: str | None) -> str | None:
    """'5930' / 'A005930' / ' 005930 ' → '005930'. 숫자가 아니면 None."""
    if value is None:
        return None
    s = str(value).strip().upper()
    if s.startswith("A") and s[1:].isdigit():
        s = s[1:]
    if not s or not s.isdigit():
        return None
    return s.zfill(6) if len(s) <= 6 else s


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", "", s).casefold()


def looks_like_code(value: str | None) -> bool:
    return normalize_code(value) is not None


class SymbolBook:
    def __init__(self, symbols: list[Symbol] | None = None, *, source: str = "memory") -> None:
        self._by_code: dict[str, Symbol] = {}
        self.sources: list[str] = []
        if symbols:
            self.extend(symbols, source)

    # ------------------------------------------------------------------ 구성
    def extend(self, symbols: list[Symbol], source: str = "") -> int:
        n = 0
        for s in symbols:
            code = normalize_code(s.code) or s.code
            if code not in self._by_code:
                self._by_code[code] = Symbol(code, s.name, s.market, s.sector, s.market_cap)
                n += 1
        if source and n:
            self.sources.append(f"{source} {n}")
        return n

    def __len__(self) -> int:
        return len(self._by_code)

    def __iter__(self):
        return iter(sorted(self._by_code.values(), key=lambda s: -(s.market_cap or 0)))

    @classmethod
    def from_csv(cls, path: str | Path) -> SymbolBook:
        p = Path(path)
        out: list[Symbol] = []
        with p.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                code = normalize_code(r.get("code"))
                if not code or not r.get("name"):
                    continue
                mc = r.get("market_cap")
                out.append(Symbol(code, r["name"].strip(), (r.get("market") or "KR").strip(), (r.get("sector") or None),
                                  float(mc) if mc else None))
        return cls(out, source=f"csv:{p.name}")

    @classmethod
    def from_research_db(cls, path: str | Path) -> SymbolBook:
        """리서치 DB 의 `prices` 최신 거래일 행. 읽기 전용."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(p)
        uri = f"{p.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            rows = conn.execute(
                "SELECT stock_code, name, market, sector, market_cap FROM prices "
                "WHERE trade_date = (SELECT MAX(trade_date) FROM prices) ORDER BY market_cap DESC").fetchall()
            # prices 에 없는 종목은 reports 의 이름으로 보탠다 (상장폐지·신규 등)
            extra = conn.execute(
                "SELECT DISTINCT stock_code, stock_name FROM reports WHERE stock_code IS NOT NULL AND stock_name IS NOT NULL").fetchall()
        finally:
            conn.close()
        out = [Symbol(normalize_code(c) or c, n, _market(m), s, mc) for c, n, m, s, mc in rows if c and n]
        seen = {s.code for s in out}
        out += [Symbol(normalize_code(c) or c, n, "KR") for c, n in extra if normalize_code(c) not in seen and c and n]
        return cls(out, source=f"research:{p.name}")

    def to_csv(self, path: str | Path) -> int:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["code", "name", "market", "sector", "market_cap"])
            n = 0
            for s in self:
                w.writerow([s.code, s.name, s.market, s.sector or "", "" if s.market_cap is None else f"{s.market_cap:.0f}"])
                n += 1
        return n

    # ------------------------------------------------------------------ 찾기
    def by_code(self, code: str | None) -> Symbol | None:
        c = normalize_code(code)
        return self._by_code.get(c) if c else None

    def search_name(self, query: str, limit: int = 12) -> list[Symbol]:
        q = (query or "").strip()
        if not q:
            return []
        qn = _norm_name(q)
        exact = [s for s in self._by_code.values() if s.name == q]
        if exact:
            return _rank(exact)[:limit]
        norm = [s for s in self._by_code.values() if _norm_name(s.name) == qn]
        if norm:
            return _rank(norm)[:limit]
        prefix = [s for s in self._by_code.values() if _norm_name(s.name).startswith(qn)]
        if prefix:
            return _rank(prefix)[:limit]
        contains = [s for s in self._by_code.values() if qn in _norm_name(s.name)]
        return _rank(contains)[:limit]

    def resolve(self, query: str | None) -> Resolution:
        """코드든 이름이든 하나로. 코드가 맞으면 exact, 이름은 단계별 검색."""
        q = (query or "").strip()
        if not q:
            return Resolution("none", [], q)
        if looks_like_code(q):
            s = self.by_code(q)
            return Resolution("exact", [s], q) if s else Resolution("none", [], q)
        hits = self.search_name(q)
        if not hits:
            return Resolution("none", [], q)
        # search_name 은 정확 일치가 있으면 그 단계만 돌려주므로, 하나면 확정이고 여럿이면 고르게 한다
        return Resolution("exact" if len(hits) == 1 else "many", hits, q)


def _market(m: str | None) -> str:
    if not m:
        return "KR"
    u = str(m).upper()
    if u in ("KOSPI", "KOSDAQ", "KONEX", "KR"):
        return "KR"
    return u


def _rank(items: list[Symbol]) -> list[Symbol]:
    return sorted(items, key=lambda s: (-(s.market_cap or 0), len(s.name), s.code))


# ---------------------------------------------------------------------------- 기본 구성
RESEARCH_DB_OFF = ("off", "none", "0", "-")      # KSTOCK_RESEARCH_DB=off → 형제 폴더 기본값도 쓰지 않는다 (테스트 · 리서치 DB 없이 돌릴 때)


def research_db_path(store: Store | None = None) -> Path | None:
    """설정 → 환경변수 → 형제 폴더 순으로 리서치 DB 경로. 없으면 None. 환경변수(또는 설정) 가 off/none 이면 아예 쓰지 않는다."""
    cands: list[Path] = []
    if store is not None:
        v = store.get_setting("research_db")
        if v and str(v).strip().lower() in RESEARCH_DB_OFF:
            return None
        if v:
            cands.append(Path(str(v)))
    env = os.environ.get("KSTOCK_RESEARCH_DB")
    if env and env.strip().lower() in RESEARCH_DB_OFF:
        return None
    if env:
        cands.append(Path(env))
    cands.append(DEFAULT_RESEARCH_DB)
    for p in cands:
        if p.exists():
            return p
    return None


def default_book(store: Store | None = None, *, csv_path: Path | None = None) -> SymbolBook:
    """있는 출처를 모두 합친다: CSV 스냅숏 → 리서치 DB → 관심종목. 실패한 출처는 건너뛴다."""
    book = SymbolBook()
    p = csv_path or DEFAULT_CSV
    if p.exists():
        try:
            book.extend(list(SymbolBook.from_csv(p)), f"csv:{p.name}")
        except Exception:  # noqa: BLE001
            pass
    rp = research_db_path(store)
    if rp is not None:
        try:
            book.extend(list(SymbolBook.from_research_db(rp)), f"research:{rp.name}")
        except Exception:  # noqa: BLE001
            pass
    if store is not None:
        book.extend([Symbol(w.ticker, w.name, w.market, w.sector) for w in store.list_watch(include_archived=True)], "watchlist")
    return book


def refresh_csv(store: Store | None = None, *, csv_path: Path | None = None) -> tuple[Path, int]:
    """리서치 DB → symbols.csv 스냅숏. 리서치 DB 가 없으면 FileNotFoundError."""
    rp = research_db_path(store)
    if rp is None:
        raise FileNotFoundError("리서치 DB 를 찾지 못했습니다 (설정 research_db / 환경변수 KSTOCK_RESEARCH_DB)")
    book = SymbolBook.from_research_db(rp)
    p = csv_path or DEFAULT_CSV
    return p, book.to_csv(p)


__all__ = ["Symbol", "SymbolBook", "Resolution", "normalize_code", "looks_like_code", "default_book", "refresh_csv",
           "research_db_path", "DEFAULT_CSV", "DEFAULT_RESEARCH_DB"]
