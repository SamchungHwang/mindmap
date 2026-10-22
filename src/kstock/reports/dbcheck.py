"""리서치 DB 교차 확인 (설계 v3 §2.6) — 파일명의 한경 리포트 번호(hk_96588) 로 `reports/report_details` 를 찾아
본문에서 읽은 증권사·날짜·제목·목표주가·직전 목표주가·의견과 대조한다. 본문에서 못 읽은 칸은 DB 값으로 채우고(출처 표기),
다르면 경고를 남긴다. DB 가 없으면 아무것도 하지 않는다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..models import report_id_for
from .extract import ReportFacts, normalize_opinion


@dataclass
class DbCheck:
    checked: int = 0
    found: int = 0
    filled: list[str] = field(default_factory=list)      # "hk_96588: title ← DB"
    mismatches: list[str] = field(default_factory=list)  # "hk_96588: target_price 본문 330,000 / DB 320,000"
    wrong_ticker: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"리서치 DB 교차 확인: 리포트 {self.checked}건 중 {self.found}건 DB 에 있음"]
        out += [f"  채움 {x}" for x in self.filled]
        out += [f"  ⚠ 불일치 {x}" for x in self.mismatches]
        out += [f"  ⚠ 종목 다름 {x}" for x in self.wrong_ticker]
        return out


def _fmt(v: object) -> str:
    if isinstance(v, float):
        return f"{v:,.0f}"
    return str(v)


def cross_check_db(facts: list[ReportFacts], db, *, ticker: str | None = None) -> DbCheck:  # noqa: ANN001
    """facts 를 제자리에서 보강하고 DbCheck 를 돌려준다. db 는 ResearchDb (report_by_id 가 있어야 한다)."""
    res = DbCheck()
    for f in facts:
        rid = report_id_for(str(f.path))
        if not rid.startswith("hk_"):
            continue
        res.checked += 1
        try:
            r = db.report_by_id(rid)
        except Exception:  # noqa: BLE001
            r = None
        if r is None:
            continue
        res.found += 1
        tag = f"리서치 DB reports#{rid[3:]}"
        if r.stock_code and (ticker or f.ticker) and r.stock_code != (ticker or f.ticker):
            res.wrong_ticker.append(f"{rid}: DB 종목 {r.stock_code} ≠ {ticker or f.ticker}")
        pairs: list[tuple[str, object, object]] = [
            ("broker", f.broker, r.broker), ("date", f.date, r.posted_date), ("title", f.title, r.title),
            ("target_price", f.target_price, r.target_price), ("prev_target_price", f.prev_target_price, r.prev_target_price),
            ("opinion", f.opinion, normalize_opinion(r.opinion) if r.opinion else None),
        ]
        for name, mine, theirs in pairs:
            if theirs in (None, ""):
                continue
            if mine in (None, ""):
                setattr(f, name, theirs)
                f.warnings.append(f"{name} 을 본문에서 못 읽어 {tag} 값으로 채움")
                res.filled.append(f"{rid}: {name} ← DB ({_fmt(theirs)})")
                continue
            same = (abs(float(mine) - float(theirs)) < 1 if isinstance(mine, (int, float)) and isinstance(theirs, (int, float))
                    else (str(mine).strip() == str(theirs).strip() if name != "date" else mine == theirs))
            if name == "title" and not same:
                same = str(theirs).strip()[:15] in str(mine) or str(mine).strip()[:15] in str(theirs)
            if name == "broker" and not same:
                same = str(theirs).replace("증권", "")[:2] == str(mine).replace("증권", "")[:2]
            if not same:
                res.mismatches.append(f"{rid}: {name} 본문 {_fmt(mine)} / DB {_fmt(theirs)}")
                f.warnings.append(f"{name} 이 {tag} 와 다르다: 본문 {_fmt(mine)} / DB {_fmt(theirs)}")
    return res


def latest_earnings_from_db(db, code: str, *, as_of: date | None = None) -> date | None:  # noqa: ANN001
    try:
        return db.latest_earnings_date(code, as_of=as_of)
    except Exception:  # noqa: BLE001
        return None


__all__ = ["DbCheck", "cross_check_db", "latest_earnings_from_db"]
