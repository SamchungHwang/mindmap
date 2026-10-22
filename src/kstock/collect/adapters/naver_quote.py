"""작성 시점의 현재가 — 네이버 금융 모바일 API (표준 라이브러리 urllib, 짧은 타임아웃).

맵의 「현재가」는 리포트에 적힌 날짜가 아니라 **맵을 만드는 시점**의 값이어야 한다. 리포트는 며칠 전 종가를 적고,
리서치 DB 는 배치가 돈 날까지만 있다. 그래서 초안을 만들 때 한 번 조회한다:

    https://m.stock.naver.com/api/stock/{code}/basic                       → closePrice, localTradedAt, marketStatus
    https://polling.finance.naver.com/api/realtime/domestic/stock/{code}  → datas[0].closePrice, localTradedAt

둘 다 공개 화면이 쓰는 JSON 이다. 실패하면 QuoteError — 부르는 쪽이 리서치 DB·리포트 값으로 내려간다.
환경변수 `KSTOCK_OFFLINE=1` 이면 조회하지 않는다 (테스트·오프라인).
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from ...models import Source

ENDPOINTS = (
    "https://m.stock.naver.com/api/stock/{code}/basic",
    "https://polling.finance.naver.com/api/realtime/domestic/stock/{code}",
)
USER_AGENT = "Mozilla/5.0 (kstock-mindmap; +local)"
Opener = Callable[[str, float], bytes]


class QuoteError(RuntimeError):
    """조회 실패 (네트워크·형식). 메시지는 사람이 읽는 한 줄."""


@dataclass(frozen=True)
class LiveQuote:
    code: str
    name: str | None
    price: float
    as_of: date                 # 체결 기준일 (장 마감 후면 그날 종가, 장중이면 오늘의 현재가)
    traded_at: datetime | None
    market_status: str | None   # OPEN | CLOSE | …
    url: str

    def source(self) -> Source:
        when = self.traded_at.strftime("%Y-%m-%d %H:%M") if self.traded_at else self.as_of.isoformat()
        status = {"OPEN": "장중", "CLOSE": "종가"}.get((self.market_status or "").upper(), self.market_status or "")
        return Source(name=f"네이버 금융 {when} {status} {self.price:,.0f}".strip(), url=self.url, retrieved_at=self.as_of)


def offline() -> bool:
    return os.environ.get("KSTOCK_OFFLINE", "").strip().lower() in ("1", "true", "yes", "y")


def _default_opener(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — https 고정 엔드포인트
        return resp.read()


def _num(v: object) -> float | None:
    if v is None:
        return None
    s = re.sub(r"[^\d.\-]", "", str(v))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def _when(v: object) -> datetime | None:
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s.replace("+09:00", "+0900"), fmt)
        except ValueError:
            continue
    return None


def parse_quote(data: object, code: str, url: str) -> LiveQuote:
    """두 엔드포인트의 JSON 을 모두 받는다. 값이 없으면 QuoteError."""
    row: object = data
    if isinstance(data, dict) and isinstance(data.get("datas"), list) and data["datas"]:
        row = data["datas"][0]
    if not isinstance(row, dict):
        raise QuoteError("응답 형식을 모른다")
    price = _num(row.get("closePrice") or row.get("currentPrice") or row.get("nowPrice") or row.get("closePriceAfterMarket"))
    if price is None or price <= 0:
        raise QuoteError("응답에 현재가가 없다")
    traded = _when(row.get("localTradedAt") or row.get("tradedAt"))
    as_of = traded.date() if traded else date.today()
    return LiveQuote(code=code, name=row.get("stockName") or row.get("itemName"), price=price, as_of=as_of, traded_at=traded,
                     market_status=row.get("marketStatus"), url=url)


def fetch_quote(code: str, *, timeout: float = 5.0, opener: Opener | None = None) -> LiveQuote:
    """현재가 한 건. 엔드포인트를 차례로 시도하고 첫 성공을 돌려준다. 전부 실패하면 QuoteError."""
    if offline():
        raise QuoteError("KSTOCK_OFFLINE 설정 — 조회하지 않음")
    opener = opener or _default_opener
    errors: list[str] = []
    for tpl in ENDPOINTS:
        url = tpl.format(code=code)
        try:
            raw = opener(url, timeout)
            return parse_quote(json.loads(raw.decode("utf-8", "replace")), code, url)
        except QuoteError as e:
            errors.append(f"{url.split('/')[2]}: {e}")
        except Exception as e:  # noqa: BLE001 — 네트워크·JSON 전부 한 줄로
            errors.append(f"{url.split('/')[2]}: {type(e).__name__}: {str(e)[:80]}")
    raise QuoteError("; ".join(errors))


__all__ = ["LiveQuote", "QuoteError", "fetch_quote", "parse_quote", "offline", "ENDPOINTS"]
