"""삼성전자(005930) A 맵 — 2026-10-20 기준, 공개 자료로 채운 실제 예시.

    python examples/samsung_005930/build.py              # JSON + HTML 생성 (examples/samsung_005930/)
    python examples/samsung_005930/build.py --db data/db/kstock.sqlite   # 저장소에도 등록

값의 성격을 구분해 둔다.
  fact      출처가 있는 사실 (리서치 DB·공시·기사). Source 에 URL·날짜를 적었다.
  consensus 증권사 추정 (리서치 DB reports/report_details 집계, 기사 인용)
  내 추정   **초안**이다. 논제의 주인이 숫자를 고쳐야 한다 — rationale 에 '초안' 이라고 적어 두었다.

이 파일은 「마인드맵 자동 생성기」가 만들어 내야 할 결과물의 모양을 손으로 먼저 만든 것이다
(docs/auto-generation-plan.md 의 1단계 산출물).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kstock.engine import evaluate_new_map, validate  # noqa: E402
from kstock.models import (  # noqa: E402
    Action,
    Driver,
    Event,
    MapKind,
    Measurement,
    Node,
    Rule,
    Scenario,
    Source,
)
from kstock.render import write_html, write_markdown  # noqa: E402
from kstock.templates import new_map_skeleton  # noqa: E402

TODAY = date(2026, 10, 20)
TICKER, NAME = "005930", "삼성전자"

# ---------------------------------------------------------------------------- 출처
SRC_PRICE = Source(name="서울신문 10/19 마감 (종가 218,000)", url="https://www.seoul.co.kr/news/economy/securities/2026/10/19/20261019500235", retrieved_at=TODAY)
SRC_Q3 = Source(name="삼성전자 뉴스룸 — 2026년 3분기 잠정실적 (10/8)", url="https://news.samsung.com/kr/", retrieved_at=TODAY)
SRC_KB = Source(name="한국경제 10/12 — KB증권 (4Q 114조, HBM4 판가 +100%, 주주환원 110조, PER 4배, TP 60만)", url="https://www.hankyung.com/article/2026101249176", retrieved_at=TODAY)
SRC_KB_HBM = Source(name="서울경제 9/11 — KB증권 (HBM 점유율 2Q 33% → 4Q 40%, HBM4 매출 3배)", url="https://www.sedaily.com/article/20089704", retrieved_at=TODAY)
SRC_FLOW = Source(name="뉴스핌 10/19 — 외국인 10월 순매도 17.7조 중 삼성 4.7조, 10월 -18.8%", url="https://www.newspim.com/news/view/20261019001384", retrieved_at=TODAY)
SRC_CXMT = Source(name="뉴스핌 10/14 — CXMT 4F² DDR5 발표, 10/13 삼성 -7.39%", url="https://www.newspim.com/news/view/20261014000524", retrieved_at=TODAY)
SRC_DB = Source(name="리서치 DB reports/report_details — 최근 90일 목표가 21건 (35만~63만, 평균 51.6만)", url="C:/kstock/research/data/db/kstock.sqlite", retrieved_at=TODAY)
SRC_DB_PX = Source(name="리서치 DB prices — 6/19 고가 374,500 · 9/21 종가 274,000 · 52주 저가 189,200", url="C:/kstock/research/data/db/kstock.sqlite", retrieved_at=TODAY)

DRAFT = "초안 — 논제의 주인이 확정할 것"


def build():
    a = new_map_skeleton(TICKER, NAME, "growth", TODAY, n_drivers=3)

    # ---- A0 논제
    a["A0"].set(
        "삼성전자는 HBM4 양산 확대(HBM 점유율 33%→40%)와 2027년 HBM4 판가 2배 전망으로 '분기 영업이익 100조' 체제가 "
        "2027년까지 이어지며 12개월 내 360,000원(2027E PER 6배). 시장은 10월 급락(-19%)으로 사이클 고점·중국 추격 우려를 "
        "과도하게 반영해 PER 4배에 두고 있다", as_of=TODAY)
    a["A0.2"].set("1Y", as_of=TODAY)
    a["A0.3"].set("M", as_of=TODAY)
    a["A0.4"].set(218000, as_of=TODAY, source=SRC_PRICE)

    # ---- A1 핵심 드라이버 — 이 변수가 틀리면 논제가 무너진다
    a["A1.1"].label, a["A1.1"].unit = "분기 영업이익", "조원"
    a["A1.1"].driver = Driver(
        current=Measurement(value=107.4, as_of=date(2026, 10, 8), source=SRC_Q3),
        consensus=Measurement(value=114.0, as_of=date(2026, 10, 12), source=SRC_KB, rationale="4Q26 KB 114조 · 키움 116조"),
        my_estimate=Measurement(value=110.0, as_of=TODAY, rationale=f"4Q26 — 환율·충당금을 보수적으로 본 값. {DRAFT}"),
        check_indicator="4Q26 잠정실적 (1월 초) · 분기 메모리 영업이익", next_check=date(2027, 1, 8))
    a["A1.2"].label, a["A1.2"].unit = "HBM 점유율", "%"
    a["A1.2"].driver = Driver(
        current=Measurement(value=33.0, as_of=date(2026, 9, 11), source=SRC_KB_HBM, rationale="2Q26"),
        consensus=Measurement(value=40.0, as_of=date(2026, 9, 11), source=SRC_KB_HBM, rationale="4Q26 KB 전망"),
        my_estimate=Measurement(value=38.0, as_of=TODAY, rationale=f"HBM4 인증·램프업 속도 감안 소폭 보수적. {DRAFT}"),
        check_indicator="분기 실적 컨콜 HBM 코멘트 · 트렌드포스 분기 점유율", next_check=date(2027, 1, 29))
    a["A1.3"].label, a["A1.3"].unit = "2027 HBM4 판가 상승률", "%"
    a["A1.3"].driver = Driver(
        current=Measurement(value=0.0, as_of=TODAY, source=SRC_KB, rationale="2027 계약가 미확정 (0 = 아직 반영 전)"),
        consensus=Measurement(value=100.0, as_of=date(2026, 10, 12), source=SRC_KB, rationale="KB: 전년 대비 100% 이상"),
        my_estimate=Measurement(value=70.0, as_of=TODAY, rationale=f"고객 협상력·경쟁사 공급 증가를 감안. {DRAFT}"),
        check_indicator="1Q27 HBM4 공급 계약가 보도 · DRAM ASP 가이던스", next_check=date(2027, 1, 31))

    # ---- A2 컨센서스 vs 내 뷰
    a["A2.1"].set("PER 4배 = 2027년 이익이 정점이고 2028년 급감(사이클 피크아웃)한다는 가정 + 중국(CXMT) 추격 + 외국인 이탈 지속",
                  as_of=TODAY)
    a["A2.2"].set("HBM4 판가·공급 부족이 최소 2027년까지 이익을 유지시키고(KB: 2028년까지 호황), 3개년 주주환원 재원 110조가 "
                  "하방을 받친다. CXMT 4F²는 양산·수율·인증이 검증되지 않았다", as_of=TODAY)
    a["A2.3"].set("시간축", as_of=TODAY)

    # ---- A3 시나리오 (P0 = 218,000)
    a["A3.1"].scenario = Scenario(name="Bear", probability=0.30, target_price=170000,
                                  assumptions=["2027 상반기 DRAM 계약가 하락 전환", "2027E EPS 35,000", "PER 5배"],
                                  valuation_method="PER 5배 × 2027E EPS 35,000 (52주 저가 189,200 하회)")
    a["A3.2"].scenario = Scenario(name="Base", probability=0.45, target_price=360000,
                                  assumptions=["분기 OP 100조대 2027년 유지", "2027E EPS 60,000", "PER 6배"],
                                  valuation_method="PER 6배 × 2027E EPS 60,000 (컨센서스 평균 51.6만보다 보수적)")
    a["A3.3"].scenario = Scenario(name="Bull", probability=0.25, target_price=520000,
                                  assumptions=["호황 2028년까지", "HBM 점유율 40%+", "주주환원 확대(소각 30~40조)", "2027E EPS 70,000", "PER 7.5배"],
                                  valuation_method="PER 7.5배 × 2027E EPS 70,000 ≈ 증권사 평균 목표가")
    a["A3.6.1"].set(10, as_of=TODAY, source=Source(name="추정: 현재 PER≈4배(KB) vs 과거 10년 PER 밴드 6~20배 → 하위 10%ile", retrieved_at=TODAY))
    a["A3.6.3"].set("up", as_of=TODAY, source=Source(name="메리츠 10/7 '2027년 메모리 판가·실적 상향', 3Q 컨센서스 상회", retrieved_at=TODAY))
    a["A3.6.2"].set("SK하이닉스 대비 HBM 점유율 열위지만 PER·PBR(1.33배) 모두 할인. 할인 근거: HBM 2위, 파운드리 적자, 지배구조", as_of=TODAY,
                    source=SRC_FLOW)

    # ---- A4 리스크 & 무효화 조건
    a["A4.1"].set({"probability": 0.35, "impact_pct": -30, "text": "메모리 가격 피크아웃 — 2027 상반기 DRAM 계약가 하락 전환"}, as_of=TODAY)
    a["A4.2"].set({"probability": 0.25, "impact_pct": -20, "text": "중국 추격 — CXMT 4F² DDR5 양산 성공 시 범용 DRAM 가격 압박"}, as_of=TODAY)
    a["A4.3"].set({"probability": 0.40, "impact_pct": -15, "text": "외국인 수급·매크로 — 유가·미 금리 상승·원화 약세 지속 (10월 외국인 순매도 17.7조 중 삼성 4.7조)"}, as_of=TODAY)
    a["A4.4.1"].rule = Rule(trigger_type="fact", condition="driver[A1.1].current < 80", action=Action.exit, escalate_to="RED",
                            note="분기 영업이익 80조 미만 확인 = 사이클 꺾임. 가격이 아니라 실적으로 논제를 버린다")
    a.add(Node(id="A4.4.2", map=MapKind.NEW, parent="A4.4", label="무효화 조건 2", node_type="rule", value_type="text", as_of=TODAY,
               rule=Rule(trigger_type="fact", condition="driver[A1.2].current < 25", action=Action.exit, escalate_to="RED",
                         note="HBM 점유율 25% 미만 = HBM4 인증/램프업 실패")))
    a["A4.5"].set("DRAMeXchange DDR5 계약가 월간 변동률(2개월 연속 마이너스), 트렌드포스 분기 HBM 점유율, KRX 외국인 월간 순매수",
                  as_of=TODAY, source=Source(name="직접 설정", retrieved_at=TODAY))

    # ---- A5 촉매 & 타임라인 (날짜는 통상 일정 기준 추정 — 확정 공시 확인 필요)
    for i, (d, fact, direction, mat) in enumerate((
        (date(2026, 10, 30), "3Q26 확정실적·컨퍼런스콜 — 사업부별 이익, 주주환원 언급 (예정일 추정)", "중립", "H"),
        (date(2027, 1, 8), "4Q26 잠정실적 — 분기 OP 110~116조 컨센서스 확인 (예정일 추정)", "중립", "H"),
        (date(2027, 1, 29), "2027~2029 주주환원 정책 발표 가능 (KB: FCF 50% 초과 가능성)", "강화", "H"),
        (date(2027, 1, 31), "1Q27 HBM4 공급 계약가 — 2027 판가 상승률 확인", "강화", "H"),
    ), start=1):
        a.add(Node(id=f"A5.1.{i}", map=MapKind.NEW, parent="A5.1", label=fact[:30], node_type="event", value_type="text", as_of=TODAY,
                   event=Event(date=d, fact=fact, affects=["A1.1"] if "실적" in fact else ["A1.3"] if "판가" in fact else [],
                               direction=direction, priced_in="아니오", materiality=mat,
                               source=Source(name="통상 일정 기준 추정", retrieved_at=TODAY))))
    a["A5.2.1"].set("하락", as_of=TODAY, source=SRC_FLOW)
    a["A5.2.2"].set(1.3, as_of=TODAY, source=Source(name="10/19 거래량 1,569만주 — 9월 일평균 대비 추정", retrieved_at=TODAY))
    a["A5.2.3"].set(-47286, as_of=TODAY, source=SRC_FLOW)  # 10월 외국인 순매도 4조 7,286억
    a["A5.2"].question = "10월 외국인 순매도 -4.7조(10/19 +6,247억 순매수 전환), 10월 -18.8%, 10/13 CXMT 쇼크 -7.39%"

    # ---- A6 실행 계획 (판단은 WATCH — 매수 여부는 논제의 주인이 정한다)
    a["A6.2"].set({"price_range": [200000, 220000], "tranches": 3}, as_of=TODAY)
    a["A6.3"].rule = Rule(trigger_type="price", condition="drawdown > 0.25", action=Action.review, escalate_to="YELLOW",
                          note="고점 대비 -25% 면 논제 재검토 (손절은 사실 기준 A4.4 가 한다)")
    a["A6.4"].rule = Rule(trigger_type="price", condition="price > 360000", action=Action.trim, size="50%",
                          note="Base 목표가 도달 시 절반 축소")
    a["A6.5"].set("WATCH", as_of=TODAY)

    # ---- A8 근거 자료
    a["A8.1.1"].set("메모리(DRAM·NAND·HBM)가 이익의 거의 전부 — 4Q26 메모리 OP 113조 / 전사 114조 전망. 파운드리·시스템LSI, MX(적자 ~1조 방어), DX",
                    as_of=TODAY, source=SRC_KB)
    a["A8.1.2"].set("HBM4 양산 확대·HBM4E 샘플 공급, DRAM 3사 과점. 열위: HBM 점유율 2위(33%), 파운드리 적자", as_of=TODAY, source=SRC_KB_HBM)
    a["A8.1.3"].set("3개년 주주환원 잔여 재원 110조 (현금배당 70~80조, 자사주 소각 30~40조 가능). 차기 정책 FCF 50% 초과 가능성",
                    as_of=TODAY, source=SRC_KB)
    a["A8.1.4"].set("3Q26 매출 195조(QoQ +13.7%, YoY +126.6%), 영업이익 107.4조(QoQ +20.0%, YoY +782.5%). PER≈4배, PBR 1.33배",
                    as_of=TODAY, source=SRC_Q3)
    a["A8.2"].links = ["S.KR-SEMI"]
    a["A8.3"].links = ["M0"]
    a["A3.6"].question = "참고: 증권사 목표가 최근 90일 21건 35만~63만, 평균 51.6만 (리서치 DB). 6월 고가 374,500 → 9/21 274,000 → 10/19 218,000"
    a["A8.1"].question = str(SRC_DB.name)
    a["A8"].question = f"{SRC_DB_PX.name}"

    # ---- 평가·검증
    evaluate_new_map(a, today=TODAY)
    sug = (a["A6.1"].value or {}).get("suggested")
    a["A6.1"].value = {**(a["A6.1"].value or {}), "confirmed": 0.05, "reason": f"제안 {sug:.2%} → 5%로 확정 (초안: 포트폴리오 맥락 미반영)"}
    return a


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="삼성전자 A 맵 예시 생성")
    ap.add_argument("--db", default=None, help="저장소에도 등록 (예 data/db/kstock.sqlite)")
    ap.add_argument("-o", "--out", default=str(HERE))
    args = ap.parse_args(argv)
    a = build()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    a.save(out / "A_005930.json")
    write_html(a, out / "A_005930.html")
    write_markdown(a, out / "A_005930.md")
    issues = validate(a, today=TODAY)
    print(f"A 맵 {a.map_id} → {out}")
    print(f"  기대수익률 {a.value('A3.4'):+.1%} · 손익비 {a.value('A3.5')} · 판단 {a.decision.value} · 검증 오류 {sum(1 for i in issues if i.level == 'error')} 경고 {sum(1 for i in issues if i.level == 'warn')}")
    for i in issues:
        print("   ", i)
    if args.db:
        from kstock.store import Store

        with Store(args.db) as store:
            store.add_watch(TICKER, NAME, sector="KR-SEMI", invest_type="growth", note="메모리 슈퍼사이클 · HBM4 (예시 맵)")
            p = store.save_map(a)
            print(f"  저장소 등록 → {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
