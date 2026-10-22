"""리포트 읽기: 파일(PDF/TXT/MD/HTML/DOCX) → 텍스트(loader) → 사실(extract) → 종목 단위 합산(digest)."""

from .dbcheck import DbCheck, cross_check_db, latest_earnings_from_db
from .digest import MAX_AGE_DAYS, EstimateCell, ReportDigest, TargetPoint
from .extract import ReportFacts, Sentence, extract_facts
from .loader import SUPPORTED_SUFFIXES, ReportDoc, ReportError, load_report, load_reports


def digest_files(paths, *, ticker: str | None = None, name: str | None = None, today=None, db=None,
                 latest_per_broker: bool = True, max_age_days: int | None = MAX_AGE_DAYS) -> tuple[ReportDigest, list[ReportDoc]]:
    """파일 경로들 → (ReportDigest, 읽은 문서들). 형식 오류는 ReportError.
    db(ResearchDb) 를 주면 파일명의 리포트 번호로 증권사·날짜·제목·TP 를 교차 확인하고, 실적 공시일을 가져와 '실적 전' 판정에 쓴다."""
    docs = load_reports(list(paths))
    facts = [extract_facts(d, hint_ticker=ticker, today=today) for d in docs]
    earnings = None
    check = None
    if db is not None:
        check = cross_check_db(facts, db, ticker=ticker)
        code = ticker or next((f.ticker for f in facts if f.ticker), None)
        if code:
            earnings = latest_earnings_from_db(db, code, as_of=today)
    d = ReportDigest.build(facts, ticker=ticker, name=name, today=today, latest_per_broker=latest_per_broker,
                           max_age_days=max_age_days, earnings_date=earnings)
    if check is not None and check.found:
        d.warnings += [ln for ln in check.lines()[1:]]
        d.db_check = check
    return d, docs


__all__ = ["ReportDoc", "ReportError", "ReportFacts", "Sentence", "ReportDigest", "TargetPoint", "EstimateCell",
           "load_report", "load_reports", "extract_facts", "digest_files", "SUPPORTED_SUFFIXES", "DbCheck", "cross_check_db",
           "latest_earnings_from_db", "MAX_AGE_DAYS"]
