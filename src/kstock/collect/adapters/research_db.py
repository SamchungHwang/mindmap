"""리서치 프로젝트 DB (`C:\\kstock\\research\\data\\db\\kstock.sqlite`) 읽기 전용 어댑터 (자동 생성 계획 §2 소스 1).

표: prices(trade_date, stock_code, close, volume, high, low, market_cap …), reports(report_id, stock_code, broker, title,
posted_date …) + report_details(opinion, target_price, prev_target_price). 쓰지 않는다 — `mode=ro` 로 연다.
없으면 모든 메서드가 None/빈 목록을 돌려준다 (초안 생성은 리포트 파일만으로도 끝까지 간다).
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from ...models import Source


@dataclass(frozen=True)
class DbTarget:
    date: date
    broker: str | None
    tp: float
    prev_tp: float | None
    opinion: str | None
    title: str | None


@dataclass(frozen=True)
class DbReport:
    """reports + report_details 의 한 행 (파일명의 리포트 번호로 찾는다). 없는 열은 None."""

    report_id: str
    stock_code: str | None
    broker: str | None
    title: str | None
    posted_date: date | None
    opinion: str | None
    target_price: float | None
    prev_target_price: float | None
    pdf_url: str | None
    earnings_date: date | None = None   # (있으면) 해당 종목의 최신 실적 공시일


@dataclass
class PriceFacts:
    as_of: date
    close: float
    high_52w: float | None
    low_52w: float | None
    trend: str | None            # 상승 | 횡보 | 하락 (20일 vs 60일 평균)
    volume_ratio: float | None   # 최근 거래량 / 20일 평균
    ret_1m: float | None
    market_cap: float | None

    def source(self, path: Path) -> Source:
        return Source(name=f"리서치 DB prices — {self.as_of} 종가 {self.close:,.0f}", url=str(path), retrieved_at=self.as_of)


class ResearchDb:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(self.path)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)

    # ------------------------------------------------------------------ 시세
    def price_facts(self, code: str) -> PriceFacts | None:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT trade_date, close, high, low, volume, market_cap FROM prices WHERE stock_code = ? "
                "ORDER BY trade_date DESC LIMIT 260", (code,)).fetchall()
        finally:
            conn.close()
        rows = [r for r in rows if r[1]]
        if not rows:
            return None
        as_of = date.fromisoformat(str(rows[0][0])[:10])
        closes = [float(r[1]) for r in rows]
        cutoff = as_of - timedelta(days=365)
        year = [r for r in rows if date.fromisoformat(str(r[0])[:10]) >= cutoff]
        hi = max((float(r[2] or r[1]) for r in year), default=None)
        lo = min((float(r[3] or r[1]) for r in year), default=None)
        trend = None
        if len(closes) >= 60:
            ma20, ma60 = statistics.fmean(closes[:20]), statistics.fmean(closes[:60])
            trend = "상승" if ma20 > ma60 * 1.02 else "하락" if ma20 < ma60 * 0.98 else "횡보"
        vols = [float(r[4]) for r in rows[:21] if r[4]]
        vr = (vols[0] / statistics.fmean(vols[1:])) if len(vols) > 5 and statistics.fmean(vols[1:]) else None
        ret_1m = (closes[0] / closes[20] - 1) if len(closes) > 20 and closes[20] else None
        return PriceFacts(as_of=as_of, close=closes[0], high_52w=hi, low_52w=lo, trend=trend,
                          volume_ratio=round(vr, 2) if vr else None, ret_1m=ret_1m, market_cap=float(rows[0][5]) if rows[0][5] else None)

    # ------------------------------------------------------------------ 컨센서스
    def targets(self, code: str, *, days: int = 90, as_of: date | None = None) -> list[DbTarget]:
        as_of = as_of or date.today()
        since = (as_of - timedelta(days=days)).isoformat()
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT r.posted_date, r.broker, d.target_price, d.prev_target_price, d.opinion, r.title FROM reports r "
                "JOIN report_details d ON d.report_id = r.report_id WHERE r.stock_code = ? AND r.posted_date >= ? "
                "AND d.target_price IS NOT NULL AND d.target_price > 0 ORDER BY r.posted_date DESC", (code, since)).fetchall()
        finally:
            conn.close()
        out: list[DbTarget] = []
        for pd, broker, tp, prev, op, title in rows:
            try:
                dd = date.fromisoformat(str(pd)[:10])
            except ValueError:
                continue
            out.append(DbTarget(dd, broker, float(tp), float(prev) if prev else None, op, title))
        return out

    def _columns(self, conn: sqlite3.Connection, table: str) -> set[str]:
        try:
            return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except sqlite3.Error:
            return set()

    def report_by_id(self, report_id: str | int) -> DbReport | None:
        """파일명의 리포트 번호(hk_96588 → 96588) 로 reports/report_details 를 찾는다. 열 이름은 PRAGMA 로 확인해 없는 것은 None."""
        rid = str(report_id).replace("hk_", "").strip()
        if not rid.isdigit():
            return None
        conn = self._conn()
        try:
            rc = self._columns(conn, "reports")
            if not rc:
                return None
            dc = self._columns(conn, "report_details")
            want_r = [c for c in ("report_id", "stock_code", "broker", "title", "posted_date", "pdf_url") if c in rc]
            want_d = [c for c in ("opinion", "target_price", "prev_target_price") if c in dc]
            sql = "SELECT " + ", ".join(f"r.{c}" for c in want_r) + (", " + ", ".join(f"d.{c}" for c in want_d) if want_d else "")
            sql += " FROM reports r" + (" LEFT JOIN report_details d ON d.report_id = r.report_id" if dc else "") + " WHERE r.report_id = ?"
            row = conn.execute(sql, (int(rid),)).fetchone()
            if row is None:
                row = conn.execute(sql, (rid,)).fetchone()
            if row is None:
                return None
            vals = dict(zip(want_r + want_d, row, strict=False))
        finally:
            conn.close()

        def _d(v):  # noqa: ANN001
            if not v:
                return None
            try:
                return date.fromisoformat(str(v)[:10])
            except ValueError:
                return None

        def _f(v):  # noqa: ANN001
            try:
                return float(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                return None

        return DbReport(report_id=f"hk_{rid}", stock_code=vals.get("stock_code"), broker=vals.get("broker"), title=vals.get("title"),
                        posted_date=_d(vals.get("posted_date")), opinion=vals.get("opinion"), target_price=_f(vals.get("target_price")),
                        prev_target_price=_f(vals.get("prev_target_price")), pdf_url=vals.get("pdf_url"))

    def latest_earnings_date(self, code: str, *, as_of: date | None = None) -> date | None:
        """disclosures 표에 실적 공시(잠정실적·영업(잠정)실적·분기보고서) 가 있으면 가장 최근 날짜. 표/열이 없으면 None."""
        conn = self._conn()
        try:
            cols = self._columns(conn, "disclosures")
            if not cols:
                return None
            dcol = next((c for c in ("disclosed_at", "rcept_dt", "date", "posted_date", "disclosure_date") if c in cols), None)
            tcol = next((c for c in ("title", "report_nm", "subject", "name") if c in cols), None)
            ccol = next((c for c in ("stock_code", "code", "ticker") if c in cols), None)
            if not (dcol and tcol and ccol):
                return None
            rows = conn.execute(f"SELECT {dcol}, {tcol} FROM disclosures WHERE {ccol} = ? ORDER BY {dcol} DESC LIMIT 200", (code,)).fetchall()
        except sqlite3.Error:
            return None
        finally:
            conn.close()
        for d, t in rows:
            if t and any(k in str(t) for k in ("잠정실적", "영업(잠정)실적", "잠정 실적", "분기보고서", "반기보고서", "사업보고서")):
                try:
                    dd = date.fromisoformat(str(d)[:10])
                except ValueError:
                    continue
                if as_of is None or dd <= as_of:
                    return dd
        return None

    def recent_titles(self, code: str, *, days: int = 90, as_of: date | None = None, limit: int = 20) -> list[tuple[date, str | None, str]]:
        as_of = as_of or date.today()
        since = (as_of - timedelta(days=days)).isoformat()
        conn = self._conn()
        try:
            rows = conn.execute("SELECT posted_date, broker, title FROM reports WHERE stock_code = ? AND posted_date >= ? "
                                "ORDER BY posted_date DESC LIMIT ?", (code, since, limit)).fetchall()
        finally:
            conn.close()
        return [(date.fromisoformat(str(p)[:10]), b, t) for p, b, t in rows]


__all__ = ["ResearchDb", "DbTarget", "PriceFacts"]
