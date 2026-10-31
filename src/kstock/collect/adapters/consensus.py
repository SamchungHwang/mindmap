"""컨센서스 소스 (스텁). EPS 리비전 방향(A3.5.3) · 12M forward EPS(B2.2 분해) · 목표주가 평균.

한국 시장 선택지
- FnGuide (유료 API / 컴퍼니가이드 페이지 스크래핑: https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?pGB=1&gicode=A<ticker>)
- 네이버 금융 종목분석 페이지 (비공식)
- 증권사 리서치 PDF → llm.filler 로 추출

revision_3m 은 3개월 전 EPS 컨센서스 대비 현재 값의 변화율로 판정한다 (±3% 기준 up/flat/down).
"""

from __future__ import annotations

from ..base import Consensus, ConsensusSource


class StubConsensusSource(ConsensusSource):
    """고정 값을 돌려주는 테스트용 소스. 실제 어댑터는 같은 인터페이스를 구현한다."""

    def __init__(self, table: dict[str, Consensus] | None = None):
        self.table = table or {}

    def get_consensus(self, ticker: str) -> Consensus:
        if ticker in self.table:
            return self.table[ticker]
        raise LookupError(f"no consensus for {ticker}")

    @staticmethod
    def revision(eps_now: float, eps_3m_ago: float, tol: float = 0.03) -> str:
        if eps_3m_ago == 0:
            return "flat"
        chg = eps_now / eps_3m_ago - 1
        return "up" if chg > tol else "down" if chg < -tol else "flat"


class FnGuideConsensusSource(ConsensusSource):
    def get_consensus(self, ticker: str) -> Consensus:
        # TODO: 컴퍼니가이드 페이지 파싱 또는 유료 API
        raise NotImplementedError("FnGuide 연동 구현 필요")


__all__ = ["StubConsensusSource", "FnGuideConsensusSource", "Consensus"]
