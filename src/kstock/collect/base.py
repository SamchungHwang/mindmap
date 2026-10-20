"""데이터 수집기 인터페이스 (템플릿 §7 운영 루프의 '데이터 수집 → 이벤트 분류').

실제 API 호출은 adapters/ 의 어댑터가 담당한다. 여기에는 계약(Protocol)과 오프라인에서 동작하는
CSV 소스, 규칙 기반 이벤트 분류기만 있다.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..models import Event, EventDirection, HoldingMap, MarketState, Materiality, PricedIn, Source


# --------------------------------------------------------------------------- data shapes
@dataclass(frozen=True)
class Quote:
    ticker: str
    date: date
    close: float
    volume: float | None = None
    eps_fwd: float | None = None  # 12M forward EPS (있으면)
    band_percentile: float | None = None  # 역사 밸류 밴드 백분위 (있으면)


@dataclass(frozen=True)
class RawDisclosure:
    ticker: str
    date: date
    title: str
    body: str = ""
    url: str = ""
    source: str = ""  # DART | KRX | NEWS | IR …


@dataclass(frozen=True)
class Consensus:
    ticker: str
    as_of: date
    eps_fwd_12m: float | None = None
    target_price_mean: float | None = None
    revision_3m: str | None = None  # up | flat | down
    n_analysts: int | None = None


# --------------------------------------------------------------------------- protocols
@runtime_checkable
class PriceSource(Protocol):
    def get_quote(self, ticker: str, as_of: date | None = None) -> Quote: ...
    def get_history(self, ticker: str, start: date, end: date) -> list[Quote]: ...


@runtime_checkable
class DisclosureSource(Protocol):
    def list_disclosures(self, ticker: str, since: date) -> list[RawDisclosure]: ...


@runtime_checkable
class ConsensusSource(Protocol):
    def get_consensus(self, ticker: str) -> Consensus: ...


@runtime_checkable
class NewsSource(Protocol):
    def list_news(self, ticker: str, since: date) -> list[RawDisclosure]: ...


@runtime_checkable
class EventClassifier(Protocol):
    """RawDisclosure → Event. affects / direction / priced_in / materiality 를 판정한다 (§7 출력 계약)."""

    def classify(self, raw: RawDisclosure, holding: HoldingMap) -> Event: ...


class NotConfigured(RuntimeError):
    """어댑터에 API 키·설정이 없을 때."""


# --------------------------------------------------------------------------- offline CSV source
class CsvPriceSource:
    """로컬 CSV (date,close[,volume][,eps_fwd][,band_percentile]) — 테스트·오프라인용.

    파일명은 `<dir>/<ticker>.csv`.
    """

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)

    def _rows(self, ticker: str) -> list[Quote]:
        p = self.dir / f"{ticker}.csv"
        if not p.exists():
            raise FileNotFoundError(p)
        out: list[Quote] = []
        with p.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                out.append(Quote(
                    ticker=ticker, date=date.fromisoformat(r["date"]), close=float(r["close"]),
                    volume=float(r["volume"]) if r.get("volume") else None,
                    eps_fwd=float(r["eps_fwd"]) if r.get("eps_fwd") else None,
                    band_percentile=float(r["band_percentile"]) if r.get("band_percentile") else None,
                ))
        return sorted(out, key=lambda q: q.date)

    def get_quote(self, ticker: str, as_of: date | None = None) -> Quote:
        rows = self._rows(ticker)
        if as_of is None:
            return rows[-1]
        cand = [q for q in rows if q.date <= as_of]
        if not cand:
            raise LookupError(f"no quote for {ticker} on/before {as_of}")
        return cand[-1]

    def get_history(self, ticker: str, start: date, end: date) -> list[Quote]:
        return [q for q in self._rows(ticker) if start <= q.date <= end]


# --------------------------------------------------------------------------- rule-based classifier
_WEAK = ["하향", "감소", "부진", "지연", "취소", "소송", "제재", "유상증자", "횡령", "감자", "적자", "리콜", "하락"]
_STRONG = ["상향", "증가", "수주", "계약", "호조", "확대", "진출", "승인", "자사주", "배당", "흑자", "상승", "출시"]
_HIGH = ["가이던스", "유상증자", "경영진", "대표이사", "M&A", "인수", "합병", "지배구조", "물적분할", "횡령", "상장폐지"]


class RuleBasedClassifier:
    """키워드 휴리스틱 분류기 — LLM 분류기(llm.ClassifierLLM) 가 없을 때의 기준선.

    affects: 보유 맵 드라이버 라벨의 핵심 단어가 제목/본문에 들어 있으면 그 드라이버 id.
    direction: 약화/강화 키워드. materiality: 중요 키워드 또는 affects 가 있으면 H/M, 아니면 L.
    priced_in: 알 수 없음 → 아니오 (주가 반응은 수집기가 뒤에서 덧붙인다).
    """

    def classify(self, raw: RawDisclosure, holding: HoldingMap) -> Event:
        text = f"{raw.title} {raw.body}"
        affects: list[str] = []
        for n in holding.drivers():
            key = re.sub(r"[\(\)（）\[\]].*", "", n.label).strip()
            key = key.split()[0] if key else ""
            if key and key in text:
                affects.append(n.id)
        weak = any(k in text for k in _WEAK)
        strong = any(k in text for k in _STRONG)
        direction = EventDirection.약화 if weak and not strong else EventDirection.강화 if strong and not weak else EventDirection.중립
        if any(k in text for k in _HIGH):
            mat = Materiality.H
        elif affects:
            mat = Materiality.M
        else:
            mat = Materiality.L
        return Event(date=raw.date, fact=raw.title, affects=affects, direction=direction,
                     priced_in=PricedIn.아니오, materiality=mat,
                     source=Source(name=raw.source or "disclosure", url=raw.url, retrieved_at=raw.date))


# --------------------------------------------------------------------------- market state builder
def build_market_state(quote: Quote, *, holding: HoldingMap, total_assets: float | None,
                       consensus: Consensus | None = None, adv_shares: float | None = None,
                       cash_yield: float | None = None) -> MarketState:
    """시세 + 컨센서스 + 포지션 → MarketState (B 맵 계산 입력)."""
    pos = holding.position
    w_now = None
    if pos and total_assets:
        w_now = pos.quantity * quote.close / total_assets
    return MarketState(
        as_of=quote.date, price=quote.close,
        eps_fwd=(consensus.eps_fwd_12m if consensus and consensus.eps_fwd_12m else quote.eps_fwd),
        band_percentile=quote.band_percentile, w_now=w_now, total_assets=total_assets,
        adv_shares=adv_shares if adv_shares is not None else quote.volume, cash_yield=cash_yield,
    )


@dataclass
class CollectorBundle:
    """수집기 묶음 — 운영 루프(§7) 가 한 번에 받는다. 없는 소스는 None."""

    prices: PriceSource | None = None
    disclosures: DisclosureSource | None = None
    consensus: ConsensusSource | None = None
    news: NewsSource | None = None
    classifier: EventClassifier = field(default_factory=RuleBasedClassifier)


__all__ = [
    "Quote", "RawDisclosure", "Consensus", "PriceSource", "DisclosureSource", "ConsensusSource", "NewsSource",
    "EventClassifier", "NotConfigured", "CsvPriceSource", "RuleBasedClassifier", "build_market_state", "CollectorBundle",
]
