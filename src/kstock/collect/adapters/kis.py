"""한국투자증권 KIS Developers Open API 어댑터 (스텁) — 시세·잔고(포지션) 소스.

- 포털: https://apiportal.koreainvestment.com  → 앱 등록 후 `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT`
- 실전/모의 도메인: https://openapi.koreainvestment.com:9443 / https://openapivts.koreainvestment.com:29443
- 주요 엔드포인트
    토큰 발급         POST /oauth2/tokenP            {grant_type:"client_credentials", appkey, appsecret}
    주식 현재가 시세  GET  /uapi/domestic-stock/v1/quotations/inquire-price     (tr_id FHKST01010100)
    일봉             GET  /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice (FHKST03010100)
    주식 잔고 조회    GET  /uapi/domestic-stock/v1/trading/inquire-balance     (TTTC8434R / 모의 VTTC8434R)
- 구현 메모
    * 토큰은 24h 유효 — 파일 캐시. 초당 호출 제한(실전 20/s) 준수.
    * inquire-balance 결과가 C 맵 롤업의 포지션(수량·평가금액·현금) 입력이 된다 → `PositionSource`.
    * 이 PC 에 eFriend Plus 가 설치돼 있다면 KIS Developers 계정도 같은 계좌로 쓸 수 있다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

from ..base import NotConfigured, PriceSource, Quote


@dataclass(frozen=True)
class BrokerPosition:
    ticker: str
    name: str
    quantity: float
    avg_cost: float  # meta_only — 판단 입력으로 쓰지 않는다
    market_value: float


@dataclass(frozen=True)
class BrokerBalance:
    as_of: date
    cash: float
    total_assets: float
    positions: list[BrokerPosition]


class KisClient:
    REAL = "https://openapi.koreainvestment.com:9443"
    PAPER = "https://openapivts.koreainvestment.com:29443"

    def __init__(self, app_key: str | None = None, app_secret: str | None = None, account: str | None = None,
                 *, paper: bool = True):
        self.app_key = app_key or os.environ.get("KIS_APP_KEY")
        self.app_secret = app_secret or os.environ.get("KIS_APP_SECRET")
        self.account = account or os.environ.get("KIS_ACCOUNT")
        self.base = self.PAPER if paper else self.REAL
        self._token: str | None = None

    def _require(self) -> None:
        if not (self.app_key and self.app_secret and self.account):
            raise NotConfigured("KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT 가 필요합니다 (.env 참고)")

    def token(self) -> str:
        self._require()
        # TODO: POST {base}/oauth2/tokenP → access_token (24h 캐시)
        raise NotImplementedError("KIS 토큰 발급 구현 필요")

    def balance(self) -> BrokerBalance:
        self._require()
        # TODO: GET /uapi/domestic-stock/v1/trading/inquire-balance → BrokerBalance
        raise NotImplementedError("KIS 잔고 조회 구현 필요")


class KisPriceSource(PriceSource):
    def __init__(self, client: KisClient | None = None):
        self.client = client or KisClient()

    def get_quote(self, ticker: str, as_of: date | None = None) -> Quote:
        # TODO: GET /uapi/domestic-stock/v1/quotations/inquire-price (tr_id FHKST01010100)
        raise NotImplementedError("KIS 현재가 조회 구현 필요")

    def get_history(self, ticker: str, start: date, end: date) -> list[Quote]:
        # TODO: GET /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice (FHKST03010100)
        raise NotImplementedError("KIS 일봉 조회 구현 필요")


__all__ = ["KisClient", "KisPriceSource", "BrokerPosition", "BrokerBalance"]
