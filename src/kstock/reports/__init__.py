"""리포트 읽기: 파일(PDF/TXT/MD/HTML/DOCX) → 텍스트(loader) → 사실(extract) → 종목 단위 합산(digest)."""

from .digest import EstimateCell, ReportDigest, TargetPoint
from .extract import ReportFacts, Sentence, extract_facts
from .loader import SUPPORTED_SUFFIXES, ReportDoc, ReportError, load_report, load_reports


def digest_files(paths, *, ticker: str | None = None, name: str | None = None, today=None) -> tuple[ReportDigest, list[ReportDoc]]:
    """파일 경로들 → (ReportDigest, 읽은 문서들). 형식 오류는 ReportError."""
    docs = load_reports(list(paths))
    facts = [extract_facts(d, hint_ticker=ticker, today=today) for d in docs]
    return ReportDigest.build(facts, ticker=ticker, name=name, today=today), docs


__all__ = ["ReportDoc", "ReportError", "ReportFacts", "Sentence", "ReportDigest", "TargetPoint", "EstimateCell",
           "load_report", "load_reports", "extract_facts", "digest_files", "SUPPORTED_SUFFIXES"]
