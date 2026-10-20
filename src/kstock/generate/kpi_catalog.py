"""섹터 KPI 사전 (자동 생성 계획 §4).

드라이버 후보는 섹터마다 정해져 있다. 사전이 없으면 생성기가 매번 다른 변수를 고른다.
섹터는 리포트 본문의 단어로 추정한다 (관심종목에 섹터가 적혀 있으면 그것이 우선).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class Kpi:
    key: str
    label: str
    unit: str
    higher_is_better: bool = True
    check_indicator: str = ""
    cadence_days: int = 90
    # 리포트 추정치 표에서 바로 채울 수 있는 지표면 metric 키 (revenue/op/np/eps/opm…), 아니면 None(수동·LLM)
    metric: str | None = None


COMMON_KPIS: list[Kpi] = [
    Kpi("op_annual", "연간 영업이익", "억원", True, "분기 실적 발표 (잠정 → 확정) · 컨센서스 추이", 90, "op"),
    Kpi("rev_annual", "연간 매출액", "억원", True, "분기 실적 발표 · 월별 수출 데이터", 90, "revenue"),
    Kpi("eps_fwd", "EPS", "원", True, "컨센서스 EPS 리비전 (월 1회)", 30, "eps"),
    Kpi("opm", "영업이익률", "%", True, "분기 실적 발표", 90, "opm"),
]

SECTOR_KPIS: dict[str, tuple[str, list[Kpi]]] = {
    "KR-SEMI": ("반도체", [
        Kpi("dram_price", "DRAM 계약가 변동률", "%", True, "DRAMeXchange 월간 계약가 · 트렌드포스", 30),
        Kpi("hbm_share", "HBM 점유율", "%", True, "분기 컨콜 HBM 코멘트 · 트렌드포스 분기 점유율", 90),
        Kpi("capex", "설비투자", "조원", False, "분기 실적 발표 CAPEX 가이던스", 90),
        Kpi("inventory_days", "재고일수", "일", False, "분기보고서", 90),
    ]),
    "KR-BATT": ("2차전지", [
        Kpi("cell_shipment", "셀 출하량", "GWh", True, "분기 실적 · 고객사 전기차 판매", 90),
        Kpi("cathode_asp", "양극재 판가", "달러/kg", True, "리튬 가격 · 월별 수출 단가", 30),
        Kpi("backlog", "수주잔고", "조원", True, "분기 실적 · 공시", 90),
        Kpi("utilization", "가동률", "%", True, "분기보고서", 90),
    ]),
    "KR-ROBOT": ("로봇", [
        Kpi("backlog", "수주잔고", "억원", True, "공급계약 공시 · 분기 실적", 90),
        Kpi("mass_production", "양산 가동률", "%", True, "IR · 분기 실적", 90),
        Kpi("opm", "영업이익률", "%", True, "분기 실적", 90, "opm"),
    ]),
    "KR-AUTO": ("자동차", [
        Kpi("global_sales", "글로벌 판매량", "만대", True, "월별 판매 실적 (매월 초)", 30),
        Kpi("asp", "ASP", "만원", True, "분기 실적 (믹스)", 90),
        Kpi("incentive", "인센티브 (미국)", "달러/대", False, "월별 미국 인센티브 데이터", 30),
        Kpi("fx", "원/달러 환율", "원", True, "일별", 30),
    ]),
    "KR-BIO": ("바이오·제약", [
        Kpi("clinical_stage", "임상 단계 (핵심 파이프라인)", "단계", True, "학회 발표 · 공시", 90),
        Kpi("rev_growth", "매출 성장률", "%", True, "분기 실적", 90),
        Kpi("milestone", "기술이전 마일스톤", "억원", True, "공시", 90),
    ]),
    "KR-BANK": ("은행·금융", [
        Kpi("nim", "NIM", "%", True, "분기 실적 발표", 90),
        Kpi("credit_cost", "대손비용률", "%", False, "분기 실적 발표", 90),
        Kpi("cet1", "CET1 비율", "%", True, "분기 실적 발표", 90),
        Kpi("payout", "주주환원율", "%", True, "연간 실적 · 밸류업 공시", 180),
    ]),
    "KR-SHIP": ("조선", [
        Kpi("backlog", "수주잔고", "조원", True, "월별 수주 공시 · 클락슨", 30),
        Kpi("newbuild_price", "신조선가 지수", "pt", True, "클락슨 주간", 30),
        Kpi("opm", "영업이익률", "%", True, "분기 실적", 90, "opm"),
    ]),
    "KR-DEFENSE": ("방산", [
        Kpi("backlog", "수주잔고", "조원", True, "수출 계약 공시", 90),
        Kpi("export_ratio", "수출 비중", "%", True, "분기 실적", 90),
    ]),
    "KR-INTERNET": ("인터넷·플랫폼", [
        Kpi("mau", "MAU", "백만", True, "분기 실적 발표", 90),
        Kpi("take_rate", "테이크레이트", "%", True, "분기 실적 발표", 90),
        Kpi("opm", "영업이익률", "%", True, "분기 실적", 90, "opm"),
    ]),
    "KR-ENT": ("엔터·미디어·게임", [
        Kpi("album_concert", "앨범·콘서트 모객", "만명", True, "월별 집계 · 분기 실적", 90),
        Kpi("new_title", "신작·신규 IP 매출", "억원", True, "출시 일정 · 분기 실적", 90),
    ]),
    "KR-SEC": ("증권", [
        Kpi("trading_value", "일평균 거래대금", "조원", True, "KRX 월별", 30),
        Kpi("roe", "ROE", "%", True, "분기 실적", 90, "roe"),
    ]),
    "KR-UTIL": ("유틸리티·에너지", [
        Kpi("smp", "SMP (계통한계가격)", "원/kWh", True, "전력거래소 월별", 30),
        Kpi("fuel_cost", "연료비 단가", "원/kWh", False, "분기 실적", 90),
    ]),
}

SECTOR_WORDS: dict[str, list[str]] = {
    "KR-SEMI": ["DRAM", "NAND", "HBM", "메모리", "반도체", "파운드리", "웨이퍼", "노광", "식각", "증착", "CXMT", "LPDDR", "D램"],
    "KR-BATT": ["양극재", "음극재", "배터리", "2차전지", "이차전지", "셀", "GWh", "전해액", "분리막", "ESS", "LFP", "리튬"],
    "KR-ROBOT": ["로봇", "휴머노이드", "액추에이터", "감속기", "협동로봇", "로보틱스"],
    "KR-AUTO": ["완성차", "자동차", "판매량", "인센티브", "SUV", "하이브리드", "전기차 판매", "부품사", "모빌리티"],
    "KR-BIO": ["임상", "파이프라인", "바이오", "제약", "FDA", "기술이전", "신약", "바이오시밀러", "CDMO", "항체"],
    "KR-BANK": ["NIM", "대손", "CET1", "은행", "금융지주", "순이자마진", "충당금 적립률", "밸류업"],
    "KR-SHIP": ["조선", "신조선가", "수주잔고", "LNG선", "컨테이너선", "VLCC", "도크", "클락슨"],
    "KR-DEFENSE": ["방산", "K9", "천무", "폴란드", "무기", "유도무기", "방위"],
    "KR-INTERNET": ["플랫폼", "MAU", "광고 매출", "커머스", "검색", "웹툰", "핀테크", "테이크레이트"],
    "KR-ENT": ["엔터", "앨범", "콘서트", "아티스트", "게임", "신작", "IP", "드라마", "MD 매출"],
    "KR-SEC": ["증권", "브로커리지", "거래대금", "IB", "WM", "리테일 수수료"],
    "KR-UTIL": ["SMP", "전력", "발전", "원전", "LNG 발전", "송전", "유틸리티", "요금 인상"],
}


def guess_sector(text: str, *, name: str | None = None) -> tuple[str | None, int]:
    """본문 단어 빈도로 섹터 코드 추정. (코드, 점수). 애매하면 (None, 점수)."""
    scores: Counter[str] = Counter()
    low = text
    for code, words in SECTOR_WORDS.items():
        for w in words:
            n = len(re.findall(re.escape(w), low, re.I))
            if n:
                scores[code] += min(n, 12) * (2 if len(w) >= 3 else 1)
    if name:
        for code, words in SECTOR_WORDS.items():
            if any(w in name for w in words):
                scores[code] += 6
    if not scores:
        return None, 0
    (best, s1), *rest = scores.most_common(2)
    s2 = rest[0][1] if rest else 0
    if s1 < 6 or (s2 and s1 < s2 * 1.3):
        return None, s1
    return best, s1


def sector_name(code: str | None) -> str:
    if not code:
        return "기타"
    return SECTOR_KPIS.get(code, (code, []))[0]


def kpi_candidates(sector: str | None) -> list[Kpi]:
    """공통 KPI + 섹터 KPI (중복 key 는 섹터 것이 앞)."""
    sec = SECTOR_KPIS.get(sector or "", ("", []))[1]
    keys = {k.key for k in sec}
    return list(sec) + [k for k in COMMON_KPIS if k.key not in keys]


__all__ = ["Kpi", "COMMON_KPIS", "SECTOR_KPIS", "SECTOR_WORDS", "guess_sector", "sector_name", "kpi_candidates"]
