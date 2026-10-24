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
    # ---- 설계 v5: 원인 변수(cause) 는 A1, 결과 지표(result) 는 A3.7 로 간다
    role: str = "cause"
    driver_id: str | None = None      # Claim 의 driver_id (DRIVERS) — 이 드라이버의 Claim 수치 셀을 찾는 열쇠
    variable: str | None = None       # 셀의 변수 축: asp | volume | cost | margin | op | revenue | fcf | orders | backlog | capacity
    template: str | None = None       # 민감도 템플릿: price_volume | cost_ratio | direct_op | payout | orders
    segment: str | None = None        # 민감도에 쓰는 사업부 매출 (세그먼트 표의 행 이름)
    kind: str = "quant"               # quant | qualitative (공개 숫자가 없는 변수는 정성 + proxies)
    proxies: tuple[str, ...] = ()


COMMON_KPIS: list[Kpi] = [
    Kpi("op_annual", "연간 영업이익", "억원", True, "분기 실적 발표 (잠정 → 확정) · 컨센서스 추이", 90, "op", role="result"),
    Kpi("rev_annual", "연간 매출액", "억원", True, "분기 실적 발표 · 월별 수출 데이터", 90, "revenue", role="result"),
    Kpi("eps_fwd", "EPS", "원", True, "컨센서스 EPS 리비전 (월 1회)", 30, "eps", role="result"),
    Kpi("opm", "영업이익률", "%", True, "분기 실적 발표", 90, "opm", role="result"),
]

# 설계 v5 §1·§7 ①: KR-SEMI 원인 드라이버 5개 — 라벨·순서는 사용자 결정 그대로. 다른 섹터는 그 종목 맵을 만들 때 정의한다 (§7 ⑤).
SECTOR_KPIS: dict[str, tuple[str, list[Kpi]]] = {
    "KR-SEMI": ("반도체", [
        Kpi("dram_asp", "DRAM ASP", "%", True, "DRAMeXchange 월간 계약가 · 트렌드포스 · 분기 실적 컨콜 ASP 코멘트 (계약가와 현물가를 구별)", 30,
            driver_id="dram_price", variable="asp", template="price_volume", segment="DRAM",
            proxies=("DRAM 계약가 (DRAMeXchange)", "현물가 추이", "bit growth")),
        Kpi("hbm", "HBM", "%", True, "HBM4 고객 인증·양산 전환 · 수율 · 패키징 CAPA · 트렌드포스 HBM 출하 (B/G 와 ASP·믹스를 따로)", 90,
            driver_id="hbm", variable="volume", template="price_volume", segment="HBM",
            proxies=("고객 인증·양산 출하", "HBM4 수율·패키징 CAPA", "세대별 제품 구성", "고객별 점유율")),
        Kpi("memory_cost", "메모리 원가", "%", False, "웨이퍼 투입량 · 수율 · 감가상각비 · 첨단 패키징 비용 · 재고 변동 (분기보고서)", 90,
            driver_id="cost", variable="cost", template="cost_ratio", segment="메모리", kind="qualitative",
            proxies=("웨이퍼 투입량", "수율", "감가상각비", "패키징 비용", "재고 변동")),
        Kpi("foundry", "파운드리 손익", "조원", True, "선단공정 가동률 · 대형 고객 양산 전환 · 수율 · 테일러 공장 비용 (분기 실적 사업부 손익)", 90,
            driver_id="foundry", variable="op", template="direct_op", segment="파운드리",
            proxies=("선단공정 가동률", "대형 고객 양산 전환", "수율", "테일러 공장 비용")),
        Kpi("fcf_payout", "FCF·주주환원", "조원", True, "영업현금흐름 − CAPEX · 배당금 · 자사주 매입·소각 (분기보고서 · IR · 밸류업 공시)", 90,
            driver_id="shareholder_return", variable="fcf", template="payout",
            proxies=("영업활동현금흐름", "CAPEX", "운전자본", "배당·자사주 소각")),
        # 보조 원인 후보 (5개 뒤)
        Kpi("capex", "설비투자", "조원", False, "분기 실적 발표 CAPEX 가이던스", 90, driver_id="capex", variable="capex"),
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


def cause_kpis(sector: str | None) -> list[Kpi]:
    """섹터의 원인 KPI (role=cause, 정의된 순서) — A1 을 채우는 순서다 (설계 v5). `driver_id` 가 있는(v5 로 정의된) 것만 —
    옛 보조 KPI(MAU·테이크레이트 …) 는 A1 후보가 아니고, 그런 섹터는 쟁점 표의 테마로 채운다 (사용자 결정 ⑤)."""
    return [k for k in SECTOR_KPIS.get(sector or "", ("", []))[1] if k.role == "cause" and k.metric is None and k.driver_id]


def result_kpis() -> list[Kpi]:
    """결과 지표 (role=result, 표에서 채움) — A3.7 로 간다."""
    return [k for k in COMMON_KPIS if k.role == "result" and k.metric]


# --------------------------------------------------------------------------- 드라이버 정규화 사전 (설계 v3 §2.2 / §2.3)
@dataclass(frozen=True)
class DriverDef:
    id: str
    label: str
    synonyms: tuple[str, ...]
    sector: str | None = None          # None = 공통
    default_target: str | None = None  # 보통 영향을 주는 대상 지표 (metric id)
    role: str = "cause"                # 설계 v5: 결과 지표(실적·추정치 변경·마진·매출 성장·배수) 는 A1 원인 후보에서 뺀다


RESULT_DRIVER_IDS = frozenset({"revenue_growth", "opm", "earnings", "estimate_revision", "valuation_multiple"})


DRIVERS: list[DriverDef] = [
    # 공통
    DriverDef("revenue_growth", "매출 성장", ("매출 성장", "매출액 증가", "외형 성장", "top-line"), None, "revenue", role="result"),
    DriverDef("opm", "영업이익률", ("영업이익률", "마진", "OPM", "수익성"), None, "op", role="result"),
    # 실적 그 자체(분기 잠정치·서프라이즈·부문 실적·분기 전망) 와 추정치 변경은 테마가 갈라지지 않게 공통 id 로 모은다 (실측에서 자유 라벨 8종이 따로 섰다)
    DriverDef("earnings", "실적·영업이익", ("영업이익", "실적", "서프라이즈", "잠정", "컨센서스 상회", "컨센서스 부합", "어닝", "분기 전망"), None, "op", role="result"),
    DriverDef("estimate_revision", "추정치 변경", ("추정치 상향", "추정치 하향", "EPS 상향", "EPS 하향", "EPS(", "전망치 상향", "전망치 하향",
                                                "상향 조정", "하향 조정", "추정 변경", "추정치 변경"), None, "eps", role="result"),
    DriverDef("cost", "비용", ("비용 증가", "비용 부담", "판관비", "마케팅비", "인건비", "감가상각", "원가"), None, "op"),
    DriverDef("fx", "환율", ("환율", "원/달러", "달러 약세", "달러 강세", "원화 강세"), None, "op"),
    DriverDef("shareholder_return", "주주환원", ("주주환원", "배당", "자사주", "소각", "밸류업"), None, "eps"),
    DriverDef("valuation_multiple", "밸류에이션 배수", ("PER", "PBR", "멀티플", "밸류에이션", "리레이팅", "re-rating", "디스카운트",
                                                   "목표주가", "시장 우려", "우려 해소", "주가 전망", "주가 상승", "저평가", "재평가"), None, "target_price", role="result"),
    DriverDef("regulation", "규제·정책", ("규제", "정책", "입법", "공정위", "관세"), None, "op"),
    DriverDef("macro_rates", "금리·매크로", ("금리", "경기", "매크로", "소비 둔화"), None, "op"),
    DriverDef("capex", "설비투자", ("CAPEX", "설비투자", "투자 확대", "증설"), None, "op"),
    DriverDef("ma_investment", "투자·인수·지분", ("인수", "합병", "지분 투자", "유상증자", "M&A", "자본 배치"), None, "eps"),
    # 반도체
    DriverDef("dram_price", "DRAM 가격", ("DRAM 가격", "DRAM 판가", "메모리 가격", "계약가", "ASP 상승", "판가 인상", "가격 인상"), "KR-SEMI", "op"),
    DriverDef("nand_price", "NAND 가격", ("NAND", "낸드"), "KR-SEMI", "op"),
    DriverDef("hbm", "HBM", ("HBM", "HBM3E", "HBM4", "고대역폭"), "KR-SEMI", "op"),
    DriverDef("hbm_mix", "HBM 비중", ("HBM 비중", "HBM 믹스", "HBM 매출 비중", "믹스 개선"), "KR-SEMI", "op"),
    DriverDef("foundry", "파운드리", ("파운드리", "LSI", "비메모리", "시스템LSI"), "KR-SEMI", "op"),
    DriverDef("ai_demand", "AI 수요", ("AI 수요", "AI 서버", "데이터센터 수요", "AI 투자"), "KR-SEMI", "revenue"),
    DriverDef("mobile_demand", "모바일·세트 수요", ("스마트폰", "MX", "세트 수요", "폴더블"), "KR-SEMI", "op"),
    DriverDef("china_competition", "중국 경쟁", ("CXMT", "YMTC", "중국 업체", "중국 경쟁"), "KR-SEMI", "op"),
    # 인터넷·플랫폼
    DriverDef("ad_growth", "광고 성장", ("광고 매출", "광고 성장", "서치플랫폼", "디스플레이 광고", "AI 브리핑 광고"), "KR-INTERNET", "revenue"),
    DriverDef("commerce", "커머스", ("커머스", "거래액", "GMV", "N배송", "멤버십", "쇼핑"), "KR-INTERNET", "revenue"),
    DriverDef("fintech", "핀테크·결제", ("파이낸셜", "결제액", "Npay", "페이", "두나무", "스테이블코인"), "KR-INTERNET", "revenue"),
    DriverDef("ai_dc", "AI 데이터센터·클라우드", ("AI DC", "AI 데이터센터", "네오클라우드", "AI팩토리", "클라우드", "GPU", "소버린 AI", "엔비디아"), "KR-INTERNET", "revenue"),
    DriverDef("content", "콘텐츠·웹툰", ("웹툰", "콘텐츠", "웹소설"), "KR-INTERNET", "revenue"),
    DriverDef("marketing_cost", "마케팅·인프라 비용", ("마케팅비", "인프라비", "파트너비", "중계권", "단말기"), "KR-INTERNET", "op"),
    # 유틸리티·에너지·원전·기계
    DriverDef("nuclear_orders", "원전 수주", ("원전 수주", "원자력", "체코 원전", "웨스팅하우스", "해외원전", "주기기"), "KR-UTIL", "revenue"),
    DriverDef("smr", "SMR", ("SMR", "소형모듈원자로", "NuScale", "TerraPower", "X-energy"), "KR-UTIL", "revenue"),
    DriverDef("gas_turbine", "가스터빈", ("가스터빈", "LNG 발전", "복합화력", "GE Vernova", "Siemens"), "KR-UTIL", "revenue"),
    DriverDef("backlog", "수주잔고", ("수주잔고", "신규수주", "수주 모멘텀", "수주 가시화"), "KR-UTIL", "revenue"),
    DriverDef("subsidiaries", "자회사 실적", ("자회사", "건설기계", "밥캣", "연료전지"), "KR-UTIL", "op"),
    # 2차전지·자동차·바이오·금융 (씨앗)
    DriverDef("ev_demand", "전기차 수요", ("전기차", "EV 수요", "캐즘"), "KR-BATT", "revenue"),
    DriverDef("lithium", "리튬·원재료", ("리튬", "메탈 가격", "양극재 판가"), "KR-BATT", "op"),
    DriverDef("us_sales", "미국 판매·인센티브", ("미국 판매", "인센티브"), "KR-AUTO", "op"),
    DriverDef("pipeline", "파이프라인·임상", ("임상", "파이프라인", "기술이전", "FDA"), "KR-BIO", "revenue"),
    DriverDef("nim", "NIM", ("NIM", "순이자마진"), "KR-BANK", "op"),
]
_DRIVER_BY_ID = {d.id: d for d in DRIVERS}


def driver_def(driver_id: str | None) -> DriverDef | None:
    return _DRIVER_BY_ID.get(driver_id or "")


def normalize_driver(label: str | None, *, sector: str | None = None) -> str | None:
    """자유 라벨 → 사전 id. 동의어 부분 일치 (긴 동의어 우선, 섹터 것 우선). 모르면 None."""
    if not label:
        return None
    t = label.strip().lower()
    if t in _DRIVER_BY_ID:
        return t
    best: tuple[int, int, str] | None = None
    for d in DRIVERS:
        for syn in d.synonyms:
            sl = syn.lower()
            if sl and sl in t:
                score = (1 if d.sector == sector else 0, len(sl), d.id)
                if best is None or score > best[:2] + (best[2],):
                    best = score
    return best[2] if best else None


def driver_catalog_text(sector: str | None) -> str:
    """프롬프트에 넣는 사전: 'id — 라벨 (동의어…)' 한 줄씩. 공통 + 해당 섹터."""
    rows = [d for d in DRIVERS if d.sector in (None, sector)]
    return "\n".join(f"- {d.id} — {d.label} ({', '.join(d.synonyms[:4])})" for d in rows)


__all__ = ["Kpi", "COMMON_KPIS", "SECTOR_KPIS", "SECTOR_WORDS", "guess_sector", "sector_name", "kpi_candidates", "cause_kpis", "result_kpis", "RESULT_DRIVER_IDS",
           "DriverDef", "DRIVERS", "driver_def", "normalize_driver", "driver_catalog_text"]
