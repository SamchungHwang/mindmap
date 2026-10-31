"""삼성전자(005930) 10-31 생성본 검토 → 재무 수치 정합성 · 재무 개념 분리 · 이벤트 주제별 규칙 · 시점 추출 (2026-10-31).

실측 (data/maps/A_005930_2026-10-31.json · data/claims/hk_973*.json):
    A3.1.1  2027E 매출 471,980조원 — 유안타 표가 ×1000 (단위 토큰을 근처 문장에서 집음) 인데 셋을 그대로 평균
    A3.1.3  EPS 71,314 ↔ A3.3 EPS 30,722,739 · PER 0.0156배 — 신한 「순이익」 행이 순이익률(11.5→115), 유안타 ×1000 이 정규화 EPS 에 들어감
    A1.5    FCF·주주환원: 현재 70조(배당) → 컨센 30조(특별배당) — 배당 수치가 FCF 축으로
    A6.1.5  주주환원 정책 이벤트에 AI 인프라 규칙(유상 계약·가동률) · 「27년 1월」 을 못 읽어 2027-12-31
    A1.4    파운드리 손익 −0조원 (조원 반올림)
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from helpers import D0  # noqa: E402, F401
from kstock.models import Claim, ClaimSet, Event, QuantRef  # noqa: E402
from kstock.reports.digest import ReportDigest  # noqa: E402
from kstock.reports.extract import ReportFacts, parse_estimate_tables  # noqa: E402
from kstock.templates import new_map_skeleton  # noqa: E402

TODAY = date(2026, 10, 31)

TABLE = """Earnings Forecast
(십억원, IFRS 연결) 2024A 2025A 2026E 2027E 2028E
매출액 300,871 333,606 712,997 979,976 1,143,904
영업이익 32,726 43,601 380,266 629,114 788,387
순이익률 11.5 13.6 42.1 50.1 50.5
지배주주순이익 33,621 44,261 294,250 472,002 534,000
EPS(원) 4,897 6,504 43,689 71,636 89,158
"""


def _facts(name: str, broker: str, unit: str, scale: float, *, np_pct_row: bool = False, eps: bool = True, quarterly: dict | None = None) -> ReportFacts:
    """합성 ReportFacts — 같은 회사를 세 증권사가 적은 표. scale 로 단위 오류를 흉내 낸다 (유안타 ×1000)."""
    f = ReportFacts(path=Path(name), broker=broker, date=date(2026, 10, 30), ticker="005930", name="삼성전자", unit=unit,
                    target_price=480000.0, shares_out=5_846_289_300.0, shares_note="보통주")
    f.estimates = {
        "revenue": {"2024A": 300871 * scale, "2025A": 333606 * scale, "2026E": 712997 * scale, "2027E": 979976 * scale},
        "op": {"2024A": 32726 * scale, "2025A": 43601 * scale, "2026E": 380266 * scale, "2027E": 629114 * scale},
        "np": ({"2024A": 115.0, "2025A": 136.0, "2026E": 421.0, "2027E": 501.0} if np_pct_row
               else {"2024A": 33621 * scale, "2025A": 44261 * scale, "2026E": 294250 * scale, "2027E": 472002 * scale}),
    }
    f.basis = {"np": "지배"}
    if eps:
        f.estimates["eps"] = {"2024A": 4897.0, "2025A": 6504.0, "2026E": 43689.0, "2027E": 71636.0}
    if quarterly:
        f.quarterly = quarterly
    return f


class TestExtractNpRow(unittest.TestCase):
    def test_np_margin_row_is_not_np(self):
        est, _q, unit = parse_estimate_tables(TABLE, ref_date=TODAY)
        self.assertEqual(unit, "십억원")
        self.assertEqual(est["np"]["2027E"], 472002.0)            # 순이익률 50.1 이 아니다
        self.assertEqual(est["np"]["2024A"], 33621.0)
        self.assertNotIn("opm", est)                              # 순이익률은 영업이익률도 아니다
        self.assertEqual(est["eps"]["2027E"], 71636.0)


class TestDigestSanity(unittest.TestCase):
    def test_unit_calibration_across_brokers(self):
        """유안타 표가 ×1000 — 실적(A) 열이 다른 증권사와 10^3 어긋나면 보정하고, 평균은 세 증권사가 같은 자릿수로 모인다."""
        facts = [_facts("shinhan.txt", "신한투자증권", "십억원", 1.0), _facts("yuanta.txt", "유안타증권", "조원", 1.0),
                 _facts("hana.txt", "하나증권", "십억원", 1.0)]
        d = ReportDigest.build(facts, today=TODAY)
        self.assertAlmostEqual(d.unit_fix.get("yuanta.txt"), 0.001)
        c = d.estimates["revenue"]["2027E"]
        self.assertEqual(c.n, 3)
        self.assertAlmostEqual(c.by_broker["유안타증권"], 979976 * 10.0)          # 억원 — 신한·하나와 같다
        self.assertAlmostEqual(c.mean, 9_799_760.0)
        self.assertTrue(any("×1000 어긋나" in w for w in d.warnings), d.warnings)
        # 정규화 EPS 도 보정된 배수를 쓴다 → PER 가 상식 범위
        e = d.eps_std("2027E")
        self.assertEqual(e.n, 3)
        for v in e.by_broker.values():
            self.assertAlmostEqual(v, 472002 * 10 * 1e8 / 5_846_289_300.0, delta=1.0)
        self.assertTrue(5 <= 480000 / e.mean <= 8)

    def test_np_margin_row_is_dropped_and_eps_std_guarded(self):
        """신한 순이익 115·136·421·501 (순이익률 ×10) — 영업이익의 0.08% 라 순이익이 아니다 → 제외, 정규화 EPS 8.5원도 평균에서 빠진다."""
        facts = [_facts("shinhan.txt", "신한투자증권", "십억원", 1.0, np_pct_row=True), _facts("hana.txt", "하나증권", "십억원", 1.0)]
        d = ReportDigest.build(facts, today=TODAY)
        self.assertIn(("신한투자증권", "2027E"), d.np_dropped)
        self.assertNotIn("신한투자증권", d.estimates["np"]["2027E"].by_broker)
        self.assertTrue(any("순이익 행이 아니다" in w for w in d.warnings), d.warnings)
        e = d.eps_std("2027E")
        self.assertEqual(list(e.by_broker), ["하나증권"])
        self.assertIn("신한투자증권", e.excluded)
        self.assertAlmostEqual(e.mean, 472002 * 10 * 1e8 / 5_846_289_300.0, delta=1.0)

    def test_eps_std_far_from_reported_is_excluded(self):
        """순이익은 그럴듯한데 주식 수·단위가 틀려 정규화 EPS 가 리포트 EPS 의 10배면 — 정규화 쪽을 뺀다."""
        f = _facts("x.txt", "X증권", "십억원", 1.0)
        f.estimates["np"] = {k: v * 10 for k, v in f.estimates["np"].items()}    # 순이익만 ×10 (영업이익의 7.5배 → NP/OP 가드엔 걸린다)
        g = _facts("hana.txt", "하나증권", "십억원", 1.0)
        d = ReportDigest.build([f, g], today=TODAY)
        e = d.eps_std("2027E")
        self.assertNotIn("X증권", e.by_broker)
        self.assertEqual(list(e.by_broker), ["하나증권"])

    def test_quarterly_cell_far_from_annual_is_dropped(self):
        """3Q26A 영업이익 0.11조 (연간 380조의 1/3,500) 은 분기 표 단위가 어긋난 것 — 뺀다."""
        q = {"op": {"3Q26A": 110.0, "4Q26E": 130.0}, "revenue": {"3Q26A": 195000.0, "4Q26E": 180000.0}}
        f = _facts("hana.txt", "하나증권", "십억원", 1.0, quarterly=q)
        d = ReportDigest.build([f], today=TODAY)
        self.assertNotIn("3Q26A", d.quarterly.get("op", {}))
        self.assertIn("3Q26A", d.quarterly["revenue"])                        # 매출 분기(27%) 는 정상
        self.assertTrue(any("분기 표 단위가 어긋났다" in w for w in d.warnings), d.warnings)

    def test_quarterly_table_in_other_unit_is_calibrated(self):
        """분기 표가 조원(한 자리 소수) 인데 십억원으로 읽어 ×1000 작으면 — 증권사 단위로 분기/연간 비율 중앙값을 재서 ×1000 보정 (실제 삼성전자 하나·신한 표)."""
        q = {"op": {"2Q26A": 89.5, "3Q26A": 107.4, "4Q26E": 134.1}, "revenue": {"2Q26A": 171.5, "3Q26A": 195.3, "4Q26E": 222.3}}   # 조원 표를 십억원으로 읽음
        f = _facts("hana.txt", "하나증권", "십억원", 1.0, quarterly=q)
        d = ReportDigest.build([f], today=TODAY)
        self.assertAlmostEqual(d.quarterly["op"]["3Q26A"].mean, 1_074_000.0)           # 107.4조원
        self.assertAlmostEqual(d.quarterly["revenue"]["4Q26E"].mean, 2_223_000.0)
        self.assertEqual(d.latest_actual_quarter("op"), ("3Q26A", 1_074_000.0))
        self.assertTrue(any("분기 값 6개를 ×1000 보정" in w for w in d.warnings), d.warnings)
        self.assertFalse(any("제외" in w and "분기" in w for w in d.warnings))

    def test_two_brokers_far_apart_warn_but_three_drop_outlier(self):
        a = _facts("a.txt", "A증권", "십억원", 1.0)
        b = _facts("b.txt", "B증권", "십억원", 1.0)
        b.estimates["op"]["2028E"] = 1_000_000.0                               # 혼자 2028E — 다른 셀과 비교 불가 → 그대로
        c = _facts("c.txt", "C증권", "십억원", 1.0)
        c.estimates["fcf"] = {"2027E": 5_000_000.0}                             # 500조×10 = 5,000조 → 범위 안이지만 혼자
        a.estimates["fcf"] = {"2027E": 523398.0}
        b.estimates["fcf"] = {"2027E": 535634.0}
        d = ReportDigest.build([a, b, c], today=TODAY)
        self.assertEqual(set(d.estimates["fcf"]["2027E"].by_broker), {"A증권", "B증권"})   # C 의 FCF(10배) 는 중앙값의 4배 밖 → 제외
        self.assertTrue(any("평균에서 제외" in w for w in d.warnings), d.warnings)

    def test_segment_unit_calibrated_against_total_revenue(self):
        """사업부 표를 십억원으로 가정해 ×10 했는데 실제는 조원(한 자리 소수) — 가장 큰 사업부가 전사 매출의 0.04% 면 ×1000."""
        f = _facts("shinhan.txt", "신한투자증권", "십억원", 1.0)
        f.segments = {"DS": {"revenue": {"2025": 1301.0, "3Q26P": 1471.0}, "op": {"2025": 249.0, "3Q26P": 1065.0}},
                      "파운드리": {"revenue": {"3Q26P": 50.0}, "op": {"3Q26P": -5.0}}}
        f.segments_unit = None
        d = ReportDigest.build([f], today=TODAY)
        self.assertAlmostEqual(d.seg_fix.get("shinhan.txt"), 1000.0)
        self.assertAlmostEqual(d.segments[("DS", "revenue", "2025")].mean, 1_301_000.0)      # 130.1조원
        self.assertAlmostEqual(d.segments[("파운드리", "op", "3Q26P")].mean, -5000.0)        # −0.5조원 — 방향이 남는다
        self.assertTrue(any("사업부 표 단위 보정 ×1000" in w for w in d.warnings), d.warnings)


class TestValuationGuards(unittest.TestCase):
    def test_per_outside_sane_range_stops_the_price(self):
        from kstock.engine.valuation import common_tail

        m = new_map_skeleton("005930", "삼성전자", "growth", TODAY)
        m.meta.setdefault("draft", {})["financials"] = {"year": "2027E", "np": {"mean": 4_720_023.0, "n": 1}, "op": {"mean": 6_291_144.0, "n": 1},
                                                         "eps": {"mean": 30_722_739.0, "n": 3}, "tp_median": 480000.0, "tp_n": 3,
                                                         "years": {"2027E": {"eps": {"mean": 30_722_739.0, "n": 3}}}}
        tl = common_tail(m)
        self.assertIsNone(tl.per)
        self.assertEqual(tl.per_source, "missing")
        self.assertTrue(any("목표 PER" in x and "밖" in x for x in tl.missing), tl.missing)
        self.assertFalse(tl.ok)
        m.meta["draft"]["financials"]["eps"] = {"mean": 86_000.0, "n": 2}
        m.meta["draft"]["financials"]["years"]["2027E"]["eps"] = {"mean": 86_000.0, "n": 2}
        tl2 = common_tail(m)
        self.assertAlmostEqual(tl2.per, 480000 / 86000)
        self.assertEqual(tl2.per_source, "default")

    def test_result_eps_aligned_with_normalized(self):
        from kstock.generate.draft import _align_result_eps
        from kstock.generate.rules import ensure_result_nodes
        from kstock.models import Driver, Measurement

        m = new_map_skeleton("005930", "삼성전자", "growth", TODAY)
        ensure_result_nodes(m)
        n = m["A3.1.3"]
        n.label = "EPS (2027E)"
        n.driver = Driver(current=Measurement(value=6534.0), consensus=Measurement(value=71314.0, rationale="2027E 컨센서스 — 리포트 2건 평균"),
                          my_estimate=Measurement(value=None), metric="eps", role="result", kind="quant")
        fin = {"year": "2027E", "eps": {"mean": 83_000.0, "n": 2, "basis": "std", "by_broker": {"하나증권": 80735.0, "유안타증권": 85265.0},
                                       "reported": {"신한투자증권": 71636.0, "하나증권": 70992.0}, "excluded": {"신한투자증권": "순이익 행 제외"}}}
        notes = _align_result_eps(m, fin, today=TODAY)
        self.assertEqual(n.driver.consensus.value, 83000.0)
        self.assertIn("정규화 EPS", n.driver.consensus.rationale)
        self.assertIn("리포트 EPS 평균 71,314원", n.driver.consensus.rationale)
        self.assertIn("정규화", n.label)
        self.assertTrue(notes)
        self.assertEqual(_align_result_eps(m, fin, today=TODAY), [])         # 두 번 적지 않는다
        self.assertEqual(_align_result_eps(m, {"year": "2027E", "eps": {"mean": 71314.0, "n": 2, "basis": "reported"}}, today=TODAY), [])


class TestPayoutVsFcf(unittest.TestCase):
    def _claim(self, cid: str, broker: str, prop: str, quants: list[tuple[str, str | None, float, str]], *, certainty: str = "expected",
               asserted_by: str = "analyst") -> Claim:
        return Claim(claim_id=cid, report_id="r_" + broker, broker=broker, proposition=prop, driver_id="shareholder_return", status="verified",
                     certainty=certainty, asserted_by=asserted_by, horizon="1Q27" if cid == "c15" else None,
                     quant_refs=[QuantRef(metric=mt, col=col, value=v, unit=u, source="text") for mt, col, v, u in quants])

    def test_special_dividend_is_not_fcf(self):
        from kstock.generate.cause import cause_cells, fill_cause_drivers, pick_cell, variables_compatible

        self.assertFalse(variables_compatible("fcf", "payout"))
        self.assertFalse(variables_compatible("payout", "fcf"))
        shinhan = ClaimSet(report_id="hk_97317", broker="신한투자증권", claims=[
            self._claim("c15", "신한투자증권", "특별배당 30조원 외 추가 주주환원이 없었던 점은 아쉬우나, 1Q27 주주환원계획부터 반복적이고 지속 가능한 정책 확인을 기대한다.",
                        [("special_dividend", None, 30.0, "조원")])])
        hana = ClaimSet(report_id="hk_97363", broker="하나증권", claims=[
            self._claim("c15h", "하나증권", "삼성전자는 2026년 연간 주주환원 규모를 최대 110조원으로 공유했으며, 이 중 배당금은 약 70조원, 자사주 매입 및 소각은 약 40조원이 될 것으로 추정한다.",
                        [("shareholder_return", "2026E", 110.0, "조원"), ("dividend", "2026E", 70.0, "조원"), ("buyback", "2026E", 40.0, "조원")],
                        certainty="announced", asserted_by="company"),
            self._claim("c22", "하나증권", "삼성전자의 잉여현금흐름(FCF)은 2027년 523,397.8십억원으로 급증할 것으로 추정된다.", [("fcf", "2027E", 523397.8, "십억원")])])
        cells = cause_cells([shinhan, hana], sector="KR-SEMI")
        kinds = {(c.variable, c.col, c.certainty): c for c in cells if c.driver_id == "shareholder_return"}
        self.assertIn(("payout", "1Q27", "expected"), kinds)                   # 특별배당 30조 (horizon 1Q27)
        self.assertIn(("payout", "2026", "realized"), kinds)                   # 회사 발표 110조
        self.assertIn(("fcf", "2027", "expected"), kinds)                      # FCF 523조
        self.assertIsNone(pick_cell(cells, "shareholder_return", "fcf", certainty="realized"))   # 배당 70조는 FCF 현재값이 아니다
        m = new_map_skeleton("005930", "삼성전자", "growth", TODAY, n_drivers=5)
        fill_cause_drivers(m, sector="KR-SEMI", claimsets=[shinhan, hana], issue_set=None, today=TODAY)
        n = next(x for x in m.drivers() if x.label.startswith("FCF·주주환원"))
        self.assertEqual((n.driver.period, n.unit, n.driver.consensus.value), ("2027", "조원", 523.4))   # FCF 만 컨센
        self.assertIsNone(n.driver.current.value)                                                  # 배당 70조는 현재값으로 안 간다
        self.assertIn("주주환원", n.question)
        self.assertIn("30조원", n.question)
        memos = [x for x in m.meta["draft"]["skipped_cells"] if x["driver_id"] == "shareholder_return"]
        self.assertTrue(all(x["reason"] == "incompatible" and x["variable"] == "payout" for x in memos), memos)
        self.assertEqual({round(x["value"]) for x in memos}, {300000, 1100000})


class TestEventTopics(unittest.TestCase):
    def test_parse_two_digit_year_month_and_iso(self):
        from kstock.generate.catalysts import parse_when

        w = parse_when("주주환원 규모의 구체적 결정은 27년 1월 이사회 이후에 이루어질 예정이다.", TODAY)
        self.assertEqual((w.date, w.period), (date(2027, 1, 31), "2027-01"))
        w2 = parse_when("'27년 1월 말 발표될 차기 주주환원 정책", TODAY)
        self.assertEqual((w2.date, w2.period), (date(2027, 1, 31), "2027-01"))
        w3 = parse_when("2027-01", TODAY)                                        # Claim horizon 표기
        self.assertEqual((w3.date, w3.period), (date(2027, 1, 31), "2027-01"))
        self.assertEqual(parse_when("2027-01-15 공시", TODAY).date, date(2027, 1, 15))   # 날짜가 있으면 날짜
        self.assertEqual(parse_when("2027년 상반기", TODAY).period, "2027H1")

    def test_periods_overlap(self):
        from kstock.units import periods_overlap

        self.assertTrue(periods_overlap("2027-01", "2027Q1"))
        self.assertTrue(periods_overlap("2027Q1", "2027"))
        self.assertTrue(periods_overlap("1H27", "2027Q2"))
        self.assertFalse(periods_overlap("2026Q4", "2027Q1"))
        self.assertFalse(periods_overlap("2026Q3", "2026Q4"))
        self.assertFalse(periods_overlap(None, "2027"))

    def test_topics_and_templates(self):
        from kstock.generate.catalysts import default_precheck, default_rule, event_topic

        pay = Event(date=date(2027, 1, 31), period="2027-01", fact="[회사 계획] 주주환원 규모의 구체적 결정은 27년 1월 이사회 이후에 이루어질 예정이다.",
                    certainty="회사 계획 (리포트 인용)", materiality="H", direction="미정", priced_in="미판단")
        lta = Event(date=date(2030, 12, 31), period="2030", fact="[회사 발표] 2030년까지 Capa 대비 LTA 비중 60~70% 목표 달성 가능성이 커졌다고 밝혔다.",
                    certainty="회사 계획 (리포트 인용)", materiality="H", direction="미정", priced_in="미판단")
        ai = Event(date=date(2027, 6, 30), period="2027H1", fact="[회사 계획] AI 데이터센터·클라우드 55MW (2027H1)", certainty="회사 계획 (리포트 인용)",
                   materiality="H", direction="미정", priced_in="미판단")
        hbm = Event(date=date(2027, 3, 31), period="2027Q1", fact="[회사 계획] HBM4 주요 고객 인증 완료 목표", certainty="회사 계획 (리포트 인용)",
                    materiality="H", direction="미정", priced_in="미판단")
        self.assertEqual([event_topic(e) for e in (pay, lta, ai, hbm)], ["payout", "lta", "ai_dc", "hbm"])
        self.assertIn("환원율", default_rule(pay))
        self.assertNotIn("가동률", default_rule(pay))
        self.assertIn("계약 CAPA 비중", default_rule(lta))
        self.assertIn("유상 계약", default_rule(ai))
        self.assertIn("배당 총액·DPS", default_precheck(pay))
        self.assertIn("계약 CAPA 비중(%)", default_precheck(lta))
        self.assertIn("GPU 가동률", default_precheck(ai))
        self.assertIn("수율", default_precheck(hbm))

    def test_link_catalysts_swaps_wrong_template_rule(self):
        from kstock.generate.catalysts import TOPIC_TEMPLATES, link_catalysts, set_catalysts

        m = new_map_skeleton("005930", "삼성전자", "growth", TODAY, n_drivers=5)
        wrong = str(TOPIC_TEMPLATES["ai_dc"]["rule"])
        pay = Event(date=date(2027, 1, 31), period="2027-01", fact="[회사 계획] 주주환원 규모의 구체적 결정은 27년 1월 이사회 이후에 이루어질 예정이다.",
                    certainty="회사 계획 (리포트 인용)", materiality="H", direction="미정", priced_in="미판단", rule=wrong)
        human = Event(date=date(2030, 12, 31), period="2030", fact="[회사 발표] 2030년까지 Capa 대비 LTA 비중 60~70% 목표", certainty="회사 계획 (리포트 인용)",
                      materiality="H", direction="미정", priced_in="미판단", rule="내가 직접 쓴 규칙 — 비중 65% 미달이면 하방")
        set_catalysts(m, [pay, human], today=TODAY)
        link_catalysts(m)
        kids = {n.event.period: n.event for n in m.children("A6.1")}
        self.assertEqual(kids["2027-01"].rule, TOPIC_TEMPLATES["payout"]["rule"])        # 주제가 틀린 템플릿은 바꿔 끼운다
        self.assertEqual(kids["2027-01"].precheck, TOPIC_TEMPLATES["payout"]["precheck"])
        self.assertEqual(kids["2027-01"].bull, TOPIC_TEMPLATES["payout"]["bull"])
        self.assertEqual(kids["2030"].rule, "내가 직접 쓴 규칙 — 비중 65% 미달이면 하방")   # 사람이 쓴 규칙은 그대로
        self.assertEqual(kids["2030"].precheck, TOPIC_TEMPLATES["lta"]["precheck"])
        if "A9.1.3" in m.nodes:
            self.assertIn("A9.1.3", kids["2027-01"].affects)

    def test_forecast_event_merges_into_same_topic_company_plan(self):
        """「1Q27 주주환원계획 기대」(리포트 전망, 2027Q1) 는 「27년 1월 주주환원 결정」(회사 계획, 2027-01) 과 같은 사건 — 하나로 합치고 문장은 증거로 남는다.
        용량 마일스톤(project) 끼리는 기간이 겹쳐도(2027H1 ⊂ 2027) 다른 사건이다."""
        from kstock.generate.catalysts import merge_events, same_event

        plan = Event(date=date(2027, 1, 31), period="2027-01", fact="[회사 계획] 주주환원 규모의 구체적 결정은 27년 1월 이사회 이후", certainty="회사 계획 (리포트 인용)",
                     materiality="H", direction="미정", priced_in="미판단", event_id="claim:c16", evidence=["(·) [하나증권] 주주환원 규모의 구체적 결정은 …"])
        fc = Event(date=date(2027, 3, 31), period="2027Q1", fact="아쉽지만 1Q27 주주환원계획부터 반복적이고 지속 가능한 정책 확인 기대. (리포트)", certainty="리포트 전망",
                   direction="미정", priced_in="미판단", event_id="text:2027-03-31:x", affects=["A1.5"])
        self.assertTrue(same_event(plan, fc))
        merged, _notes = merge_events([fc], [plan])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].certainty.value, "회사 계획 (리포트 인용)")
        self.assertEqual(merged[0].affects, ["A1.5"])
        self.assertTrue(any("1Q27 주주환원계획" in x for x in merged[0].evidence))          # 밀려난 전망의 문장은 증거로
        a = Event(date=date(2027, 6, 30), period="2027H1", fact="[회사 계획] AI 데이터센터·클라우드 55MW (2027H1)", certainty="회사 계획 (리포트 인용)",
                  direction="미정", priced_in="미판단", project="ai_dc", event_id="ai_dc:capacity_1h27:2027h1")
        b = Event(date=date(2027, 12, 31), period="2027", fact="[회사 계획] AI 데이터센터·클라우드 100MW (2027)", certainty="회사 계획 (리포트 인용)",
                  direction="미정", priced_in="미판단", project="ai_dc", event_id="ai_dc:capacity_2027:2027")
        self.assertFalse(same_event(a, b))
        two_plans = Event(date=date(2027, 3, 31), period="2027Q1", fact="[회사 계획] 1Q27 배당 정책 발표", certainty="회사 계획 (리포트 인용)", direction="미정", priced_in="미판단")
        self.assertFalse(same_event(plan, two_plans))                                        # 회사 계획 둘은 합치지 않는다 (전망 + 계획만)

    def test_evidence_respects_quarters_and_month_in_quarter(self):
        """4Q26 영업이익 전망은 3Q26 분기보고서가 아니라 4Q26 잠정실적에 붙고, 1Q27 주주환원 전망은 「27년 1월」 정책 이벤트에 붙는다."""
        from kstock.generate.catalysts import attach_evidence, link_catalysts, parse_when, set_catalysts

        m = new_map_skeleton("005930", "삼성전자", "growth", TODAY, n_drivers=5)
        rep = Event(date=date(2026, 11, 16), fact="3Q26 확정실적·분기보고서 (법정 시한)", certainty="통상 일정 추정", event_id="report:3Q26", direction="미정", priced_in="미판단")
        prov = Event(date=date(2027, 1, 8), fact="4Q26 잠정실적 발표 (예정일 추정)", certainty="통상 일정 추정", event_id="earnings:4Q26", materiality="H",
                     direction="미정", priced_in="미판단")
        pay = Event(date=date(2027, 1, 31), period="2027-01", fact="[회사 계획] 주주환원 규모의 구체적 결정은 27년 1월 이사회 이후", certainty="회사 계획 (리포트 인용)",
                    materiality="H", direction="미정", priced_in="미판단", affects=["A1.5"])
        set_catalysts(m, [rep, prov, pay], today=TODAY)
        link_catalysts(m)
        by_id = {n.event.event_id: n for n in m.children("A6.1")}
        w = parse_when("4Q26 영업이익은 137.3조원(OPM 53.2%)으로 컨센서스를 18.4% 상회할 전망이다", TODAY, anchor=date(2026, 10, 29))
        nid = attach_evidence(m, when=w, driver_node="A1.1", text="[유안타증권] 4Q26 영업이익은 137.3조원(OPM 53.2%)으로 컨센서스를 18.4% 상회할 전망이다",
                              direction="positive", today=TODAY)
        self.assertEqual(nid, by_id["earnings:4Q26"].id)
        self.assertEqual(by_id["report:3Q26"].event.evidence, [])
        w2 = parse_when("1Q27 주주환원계획부터 반복적이고 지속 가능한 정책 확인을 기대한다", TODAY, anchor=date(2026, 10, 30))
        pay_node = next(n for n in m.children("A6.1") if n.event.period == "2027-01")
        nid2 = attach_evidence(m, when=w2, driver_node="A1.5", text="[신한투자증권] 1Q27 주주환원계획부터 반복적이고 지속 가능한 정책 확인을 기대한다",
                               direction="positive", today=TODAY)
        self.assertEqual(nid2, pay_node.id)                                                      # 2027-01 ⊂ 2027Q1


class TestSmallAmountsKeepSign(unittest.TestCase):
    def test_fmt_amount_small_jo(self):
        from kstock.units import fmt_amount

        self.assertEqual(fmt_amount(-0.03, "조원"), "-300억원")
        self.assertEqual(fmt_amount(-0.0005, "조원"), "-5억원")
        self.assertEqual(fmt_amount(0.5, "조원"), "0.5조원")
        self.assertEqual(fmt_amount(-5000.0, "억원"), "-5,000억원")

    def test_foundry_loss_shown_in_eok(self):
        """사업부 표 파운드리 3Q26P 영업손익 −300억 — 노드 단위를 억원으로 두어 「−0.0조원」 이 되지 않는다."""
        from kstock.generate.cause import fill_cause_drivers
        from kstock.reports.digest import EstimateCell

        class Dg:
            def __init__(self):
                c = EstimateCell(values=[-300.0], by_broker={"신한투자증권": -300.0})
                e = EstimateCell(values=[-100.0], by_broker={"신한투자증권": -100.0})
                self.segments = {("파운드리", "op", "3Q26P"): c, ("파운드리", "op", "4Q26E"): e}

            def segment_cell(self, seg, metric, col):  # noqa: ANN001, ANN202
                for (s, mm, cc), cell in self.segments.items():
                    if s == seg and mm == metric and cc.rstrip("EPAF") == col.rstrip("EPAF"):
                        return cell
                return None

        m = new_map_skeleton("005930", "삼성전자", "growth", TODAY, n_drivers=5)
        fill_cause_drivers(m, sector="KR-SEMI", claimsets=[], issue_set=None, today=TODAY, digest=Dg())
        n = next(x for x in m.drivers() if x.label.startswith("파운드리"))
        self.assertEqual((n.unit, n.driver.current.value, n.driver.consensus.value), ("억원", -300.0, -100.0))
        self.assertIn("-300억원", n.driver.current.rationale)


if __name__ == "__main__":
    unittest.main()
