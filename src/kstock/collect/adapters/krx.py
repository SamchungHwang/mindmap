"""KRX 시세 어댑터 (스텁).

선택지
1. `pykrx` 패키지 (비공식, KRX 정보데이터시스템 스크래핑): pip install pykrx
     from pykrx import stock
     stock.get_market_ohlcv_by_date("20260101", "20261019", "005930")   # DataFrame
     stock.get_market_fundamental_by_date(...)                           # PER/PBR/EPS/DIV
2. KRX 정보데이터시스템 직접 호출 (http://data.krx.co.kr) — 세션·referer 필요, 변경 잦음.
3. 증권사 Open API (kis.py) 로 대체.

밸류 밴드 백분위(band_percentile) 는 PER 시계열(get_market_fundamental_by_date) 에서
현재 PER 의 백분위를 계산해 채운다 (A3.6.1 / B3.6 입력).
"""

from __future__ import annotations

from datetime import date

from ..base import PriceSource, Quote


class KrxPriceSource(PriceSource):
    def __init__(self, *, lookback_days: int = 365 * 5):
        self.lookback_days = lookback_days

    def get_quote(self, ticker: str, as_of: date | None = None) -> Quote:
        # TODO: pykrx.stock.get_market_ohlcv_by_date / get_market_fundamental_by_date
        #       → Quote(ticker, as_of, close, volume, eps_fwd=None(KRX 는 trailing EPS), band_percentile=…)
        raise NotImplementedError("pykrx 또는 KRX API 연동 구현 필요")

    def get_history(self, ticker: str, start: date, end: date) -> list[Quote]:
        raise NotImplementedError("pykrx 또는 KRX API 연동 구현 필요")

    @staticmethod
    def percentile(series: list[float], value: float) -> float:
        """현재 값이 시계열 분포의 몇 백분위인가 (0~100). 밸류 밴드 위치 계산용 — 순수 함수라 바로 쓸 수 있다."""
        if not series:
            raise ValueError("empty series")
        below = sum(1 for x in series if x < value)
        equal = sum(1 for x in series if x == value)
        return 100.0 * (below + 0.5 * equal) / len(series)


__all__ = ["KrxPriceSource"]
