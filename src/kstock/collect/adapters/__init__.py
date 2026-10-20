"""외부 데이터 어댑터. 실제 호출은 비워 둔 골격 — 인터페이스(collect.base) 만 지키면 교체 가능."""

from .consensus import FnGuideConsensusSource, StubConsensusSource
from .dart import DartDisclosureSource
from .kis import BrokerBalance, BrokerPosition, KisClient, KisPriceSource
from .krx import KrxPriceSource
from .research_db import DbTarget, PriceFacts, ResearchDb

__all__ = [
    "DartDisclosureSource", "KrxPriceSource", "KisClient", "KisPriceSource", "BrokerBalance", "BrokerPosition",
    "StubConsensusSource", "FnGuideConsensusSource", "ResearchDb", "DbTarget", "PriceFacts",
]
