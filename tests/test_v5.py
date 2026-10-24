"""설계 v5 — 핵심 드라이버 재설계: 사업부 표(R4) · 원인 변수 셀·선택(R2) · 결과 지표 A3.7(R1) · 민감도(R3) · 평가(R5) · 옛 맵 호환."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from helpers import build_new_map
from kstock.engine.lifecycle import snapshot_to_holding
from kstock.engine.sensitivity import compute_all, compute_sensitivity, sensitivity_line
from kstock.engine.validate import validate_new_map
from kstock.generate.cause import cause_cells, fill_cause_drivers, pick_cell
from kstock.generate.draft import draft_from_reports
from kstock.generate.evals import eval_drivers
from kstock.generate.kpi_catalog import RESULT_DRIVER_IDS, cause_kpis, result_kpis
from kstock.models import (
    Claim,
    ClaimSet,
    Driver,
    Issue,
    IssueDisplay,
    IssueLevel,
    IssueSet,
    Position,
    QuantRef,
    load_map_file,
)
from kstock.reports.digest import ReportDigest
from kstock.reports.extract import ReportFacts
from kstock.reports.segments import extract_segment_tables, merge_segment_tables
from kstock.templates import new_map_skeleton

TODAY = date(2026, 10, 23)
FIX = sorted((Path(__file__).resolve().parent / "fixtures" / "reports").glob("*.txt"))

SEG_TABLE_IBK = """표 5. 삼성전자 사업부별 세부 전망
(단위: 십억원) 1Q25 2Q25 3Q25 4Q25 1Q26 2Q26 3Q26E 4Q26E 2025 2026E 2027E
매출액 79,141 74,566 86,062 93,837 133,873 171,499 195,461 200,793 333,606 701,626 955,513
반도체 25,126 27,935 33,098 43,983 81,658 127,507 148,055 157,826 130,142 515,047 756,097
메모리 19,126 21,249 26,714 37,092 74,778 120,821 141,358 150,259 104,181 487,217 723,809
DRAM 12,152 13,136 17,348 25,984 51,451 80,778 95,378 100,499 68,621 328,106 486,072
비메모리 6,000 6,686 6,385 6,891 6,880 6,686 6,697 7,567 25,961 27,830 32,288
IM 37,058 29,208 34,147 29,329 38,146 33,235 34,317 29,718 129,742 135,417 132,284
무선 36,225 28,499 33,545 28,305 37,532 32,314 33,580 28,908 126,574 132,333 129,476
기타 -6,892 -6,695 -7,264 -8,418 -10,805 -15,848 -13,471 -13,201 -29,269 -53,325 -50,216
영업이익 6,685 4,676 12,166 20,074 57,233 89,492 107,408 114,154 43,602 368,286 571,621
DRAM 3,902 3,336 7,048 15,584 40,951 70,278 85,878 90,499 29,871 287,606 439,072
SLSI -2,448 -2,624 -725 -810 -842 -624 -1,544 -472 -6,606 -3,483 -699
영업이익률 8.4% 6.3% 14.1% 21.4% 42.8% 52.2% 55.0% 56.9% 13.1% 52.5% 59.8%
DRAM 32.1% 25.4% 40.6% 60.0% 79.6% 87.0% 90.0% 90.0% 43.5% 87.7% 90.3%
자료: IBK투자증권
"""
SEG_TABLE_DS = """표4 삼성전자 부문별 상세실적
1Q26 2Q26 3Q26P 4Q26E 1Q27E 2Q27E 3Q27E 4Q27E 2025 2026E 2027E
매출액 (십억원) 133,873 171,499 195,354 211,904 221,945 233,027 251,634 260,804 333,606 712,631 967,410
DS 81,681 127,571 148,492 166,226 176,553 191,722 205,929 222,111 129,878 523,969 796,315
DRAM 53,614 84,924 100,692 114,779 120,587 131,609 140,953 153,751 70,868 354,009 546,900
LSI/Foundry 6,941 6,661 7,283 7,840 8,347 8,628 8,816 8,981 25,722 28,725 34,772
영업이익 (십억원) 57,233 89,492 107,391 122,602 130,020 146,370 160,397 175,295 43,601 376,719 612,083
DRAM 43,113 69,488 83,780 95,748 99,582 110,946 119,715 131,457 28,506 292,129 461,699
LSI/Foundry -1,207 -2,849 -1,945 -1,633 -1,265 -869 644 790 -5,646 -7,635 -700
자료: DS투자증권 리서치센터 추정
"""


def _claim(cid: str, rid: str, broker: str, prop: str, did: str, quants: list[tuple[str, str, float, str]], certainty: str = "expected") -> Claim:
    return Claim(claim_id=cid, report_id=rid, broker=broker, proposition=prop, driver_id=did, status="verified", certainty=certainty,
                 quant_refs=[QuantRef(metric=mt, col=col, value=v, unit=u, source="text") for mt, col, v, u in quants])


def _semi_claimsets() -> list[ClaimSet]:
    yj = ClaimSet(report_id="hk_1", broker="유진", claims=[
        _claim("c1", "hk_1", "유진", "3Q26 DRAM ASP +21% qoq, NAND +22%", "dram_price", [("dram_asp_qoq", "3Q26", 21.0, "%"), ("nand_asp_qoq", "3Q26", 22.0, "%")]),
        _claim("c2", "hk_1", "유진", "HBM 3Q26 B/G +25% qoq, ASP +13% qoq", "hbm", [("hbm_bg_qoq", "3Q26", 25.0, "%"), ("hbm_asp_qoq", "3Q26", 13.0, "%")]),
        _claim("c3", "hk_1", "유진", "Foundry/LSI 3Q26 영업손실 1.5조원", "foundry", [("op", "3Q26", -1.5, "조원")]),
        _claim("c4", "hk_1", "유진", "2027E 영업이익 633조원", "earnings", [("op", "2027E", 633.0, "조원")]),
    ])
    ds = ClaimSet(report_id="hk_2", broker="DS", claims=[
        _claim("c5", "hk_2", "DS", "3Q26 DRAM B/G 4%, ASP 19% 상승", "dram_price", [("dram_asp_change", "3Q26", 19.0, "%"), ("dram_bg_change", "3Q26", 4.0, "%")]),
        _claim("c6", "hk_2", "DS", "3Q26 DRAM ASP 는 18% 상승했다 (잠정)", "dram_price", [("dram_asp_change", "3Q26", 18.0, "%")], certainty="realized"),
    ])
    return [yj, ds]


class TestSegments(unittest.TestCase):
    def test_extract_and_merge(self):
        tabs = extract_segment_tables(SEG_TABLE_IBK)
        self.assertEqual(len(tabs), 1)
        t = tabs[0]
        self.assertEqual(t.unit, "십억원")
        self.assertEqual(len(t.cols), 11)
        self.assertEqual(t.value("DRAM", "revenue", "2027E"), 4860720.0)           # 486,072 십억 → 억원
        self.assertEqual(t.value("DRAM", "op", "3Q26E"), 858780.0)
        self.assertEqual(t.value("DRAM", "opm", "2027E"), 90.3)                     # % 는 그대로
        self.assertEqual(t.value("파운드리", "revenue", "2027E"), 322880.0)         # 비메모리 → 파운드리
        self.assertEqual(t.value("파운드리", "op", "2027E"), -6990.0)               # SLSI → 파운드리
        self.assertEqual(t.value("MX", "revenue", "2027E"), 1322840.0)             # IM → MX (무선은 두 번째라 버림)
        self.assertEqual(t.value("DS", "revenue", "2025"), 1301420.0)              # 반도체 → DS
        self.assertNotIn("기타", t.data)
        ds = extract_segment_tables(SEG_TABLE_DS)[0]
        self.assertEqual(ds.unit, "십억원")                                          # 단위가 블록 줄에 있어도 읽는다
        self.assertEqual(ds.value("DRAM", "op", "3Q26P"), 837800.0)
        self.assertEqual(ds.value("파운드리", "op", "2027E"), -7000.0)
        merged = merge_segment_tables([t, ds])
        self.assertIn("3Q26P", merged.cols)
        self.assertIn("3Q26E", merged.cols)
        self.assertEqual(merged.value("DRAM", "op", "3Q26P"), 837800.0)
        # 표가 없는 글
        self.assertEqual(extract_segment_tables("사업부별 실적은 좋았다. 매출액 100 200 300"), [])

    def test_digest_segment_cells_merge_suffixes(self):
        f1 = ReportFacts(path=Path("ibk.pdf"), broker="IBK", date=TODAY)
        f1.segments = extract_segment_tables(SEG_TABLE_IBK)[0].data
        f2 = ReportFacts(path=Path("ds.pdf"), broker="DS", date=TODAY)
        f2.segments = extract_segment_tables(SEG_TABLE_DS)[0].data
        d = ReportDigest.build([f1, f2], ticker="005930", today=TODAY)
        self.assertIn("DRAM", d.segment_names())
        c = d.segment_cell("DRAM", "op", "3Q26")                 # 접미 없이 → IBK 3Q26E + DS 3Q26P 합침
        self.assertEqual(set(c.by_broker), {"IBK", "DS"})
        self.assertIn("3Q26P", c.sources["DS"][2])
        self.assertEqual(d.segment_cell("DRAM", "op", "3Q26E").by_broker, {"IBK": 858780.0})   # 접미 있으면 그 셀만
        self.assertAlmostEqual(d.segment_value("DRAM", "revenue", "2027E"), (4860720.0 + 5469000.0) / 2)


class TestCauseCells(unittest.TestCase):
    def test_cells_group_by_variable_measure_period(self):
        cells = cause_cells(_semi_claimsets(), sector="KR-SEMI")
        asp = pick_cell(cells, "dram_price", "asp")
        self.assertEqual(asp.measure, "qoq")                                   # 'ASP 19% 상승'(change, 분기) 도 qoq 로 합쳐진다
        self.assertEqual(asp.by_broker, {"유진": 21.0, "DS": 19.0})
        self.assertEqual(sorted(asp.metric_names), ["dram_asp_change", "dram_asp_qoq"])
        bg = pick_cell(cells, "hbm", "volume")
        self.assertEqual(bg.by_broker, {"유진": 25.0})                           # hbm_bg_qoq → volume (밑줄 경계)
        self.assertEqual(pick_cell(cells, "hbm", "asp").by_broker, {"유진": 13.0})
        fo = pick_cell(cells, "foundry", "op")
        self.assertEqual((fo.unit, fo.by_broker), ("억원", {"유진": -15000.0}))    # 조원 → 억원
        self.assertIsNone(pick_cell(cells, "earnings", None))                  # 결과 지표는 셀이 아니다
        real = pick_cell(cells, "dram_price", "asp", certainty="realized")
        self.assertEqual(real.by_broker, {"DS": 18.0})                          # realized 는 따로 (current 용)

    def test_fill_cause_drivers_semi_order_and_kinds(self):
        m = new_map_skeleton("005930", "삼성전자", "growth", TODAY, n_drivers=5)
        notes = fill_cause_drivers(m, sector="KR-SEMI", claimsets=_semi_claimsets(), issue_set=None, today=TODAY)
        labels = [n.label.split(" (")[0] for n in m.drivers()]
        self.assertEqual(labels, ["DRAM ASP", "HBM", "메모리 원가", "파운드리 손익", "FCF·주주환원"])   # 사용자 결정 ①
        a11 = m["A1.1"].driver
        self.assertEqual((a11.kind, a11.role, a11.period, a11.consensus.value, a11.current.value), ("quant", "cause", "3Q26", 20.0, 18.0))
        self.assertEqual(a11.metric, "dram_price:asp:qoq")
        self.assertIn("유진 21", a11.consensus.rationale)
        self.assertEqual(m["A1.2"].driver.consensus.value, 25.0)              # HBM = B/G(volume) 우선
        self.assertEqual(m["A1.3"].driver.kind, "qualitative")
        self.assertTrue(m["A1.3"].driver.proxies)
        self.assertEqual((m["A1.4"].driver.consensus.value, m["A1.4"].unit), (-1.5, "조원"))   # Claim 의 억원 수치는 조원으로 보인다
        self.assertEqual(m["A1.5"].driver.kind, "qualitative")
        self.assertIn("선택 근거", m["A1.1"].question)
        self.assertEqual(m.meta["draft"]["drivers"]["A1.1"]["driver_id"], "dram_price")
        self.assertTrue(any("원인 드라이버 5개" in n for n in notes))
        self.assertIn("DRAMeXchange", m.value("A4.5"))
        # 검증: 정성 드라이버는 숫자 대신 무효화 조건을 요구한다
        errs = {(i.node_id, i.message) for i in validate_new_map(m, today=TODAY) if i.level == "error"}
        self.assertIn(("A1.3", "정성 드라이버 — 무효화 조건(invalidation_condition) 이 없다"), errs)
        self.assertNotIn(("A1.3", "driver.current 가 없다"), errs)
        self.assertIn(("A1.1", "driver.my_estimate 가 없다"), errs)
        m["A1.3"].driver.invalidation_condition = "메모리 원가율이 2분기 연속 상승"
        errs = {(i.node_id, i.message) for i in validate_new_map(m, today=TODAY) if i.level == "error"}
        self.assertFalse(any(nid == "A1.3" for nid, _ in errs))

    def test_segment_table_preferred_for_pnl_drivers(self):
        f1 = ReportFacts(path=Path("ibk.pdf"), broker="IBK", date=TODAY)
        f1.segments = extract_segment_tables(SEG_TABLE_IBK)[0].data
        f2 = ReportFacts(path=Path("ds.pdf"), broker="DS", date=TODAY)
        f2.segments = extract_segment_tables(SEG_TABLE_DS)[0].data
        d = ReportDigest.build([f1, f2], ticker="005930", today=TODAY)
        css = _semi_claimsets()
        css[1].claims.append(_claim("c7", "hk_2", "DS", "Foundry 3Q26 영업손실 1.9조", "foundry", [("op", "3Q26", -1.9, "조원")]))
        m = new_map_skeleton("005930", "삼성전자", "growth", TODAY, n_drivers=5)
        fill_cause_drivers(m, sector="KR-SEMI", claimsets=css, issue_set=None, today=TODAY, digest=d)
        a14 = m["A1.4"]
        # Claim 2곳(−1.5·−1.9) 이 있어도 사업부 표(잠정 3Q26P → 전망 4Q26E) 가 이긴다
        self.assertEqual(a14.unit, "조원")
        self.assertIn("4Q26", a14.label)
        self.assertAlmostEqual(a14.driver.current.value, round((-15440.0 - 19450.0) / 2 / 10000, 2))    # IBK 3Q26E −1.544조 · DS 3Q26P −1.945조
        self.assertAlmostEqual(a14.driver.consensus.value, round((-4720.0 - 16330.0) / 2 / 10000, 2))   # 4Q26E
        self.assertIn("사업부 표", a14.driver.consensus.rationale)
        self.assertEqual(a14.driver.period, "4Q26")

    def test_fill_from_themes_without_sector_dictionary(self):
        # 섹터 사전이 없는 회사 — 쟁점 표의 원인 테마로 (결과 지표 테마는 제외), 라벨은 리포트 표현 그대로
        css = [ClaimSet(report_id="hk_9", broker="A", claims=[_claim("c9", "hk_9", "A", "광고 매출 YoY +6%", "ad_growth", [("ad_revenue_yoy", "3Q26", 6.0, "%")])]),
               ClaimSet(report_id="hk_8", broker="B", claims=[_claim("c8", "hk_8", "B", "광고 매출 YoY +4.5%", "ad_growth", [("revenue_yoy", "3Q26", 4.5, "%")])])]
        iss = IssueSet(ticker="X", issues=[
            Issue(issue_id="ad_growth", level=IssueLevel.theme, driver_id="ad_growth", driver_label="광고 성장", claim_ids=["c9", "c8"],
                  directions={"A": "positive", "B": "negative"}, consensus_direction="mixed", display=IssueDisplay.disagreement),
            Issue(issue_id="commerce", level=IssueLevel.theme, driver_id="commerce", driver_label="커머스", claim_ids=[], directions={"A": "positive", "B": "positive"},
                  consensus_direction="positive", display=IssueDisplay.common),
            Issue(issue_id="earnings", level=IssueLevel.theme, driver_id="earnings", driver_label="실적·영업이익", claim_ids=[], directions={"A": "positive", "B": "positive", "C": "positive"},
                  consensus_direction="positive", display=IssueDisplay.common),
            Issue(issue_id="marketing_cost", level=IssueLevel.theme, driver_id="marketing_cost", driver_label="마케팅·인프라 비용", claim_ids=[], directions={"A": "negative"},
                  consensus_direction="negative", display=IssueDisplay.independent),
        ])
        m = new_map_skeleton("035420", "NAVER", "growth", TODAY, n_drivers=5)
        notes = fill_cause_drivers(m, sector="KR-INTERNET", claimsets=css, issue_set=iss, today=TODAY)
        labels = [n.label.split(" (")[0] for n in m.drivers()]
        self.assertEqual(labels[0], "광고 성장")                                  # 핵심 이견 2곳 (3×2) + 수치 > 커머스 공통 2곳 (2×2)
        self.assertIn("커머스", labels)
        self.assertIn("마케팅·인프라 비용", labels)
        self.assertNotIn("실적·영업이익", labels)                                 # 결과 지표 테마는 A1 후보가 아니다
        self.assertEqual(m["A1.1"].driver.consensus.value, 5.25)                # ad_revenue_yoy 와 revenue_yoy 가 한 셀 (광고 YoY 3Q26)
        self.assertTrue(any("섹터 원인 KPI 사전 없음" in n for n in notes))
        self.assertIn("점수", m["A1.1"].question)


class TestResultMetricsAndCompat(unittest.TestCase):
    def test_draft_puts_result_metrics_in_a37_and_cause_in_a1(self):
        r = draft_from_reports(FIX, ticker="005930", name="삼성전자", today=TODAY, claims="rules", fetch_price=False)
        m = r.map
        self.assertEqual([n.driver.role for n in m.drivers()], ["cause"] * 5)
        res = [n for n in m.children("A3.7") if n.driver is not None]
        self.assertTrue(res)
        self.assertTrue(all(n.driver.role == "result" and n.driver.metric in ("revenue", "op", "eps", "opm") for n in res))
        self.assertEqual(m["A3.7.2"].driver.current.value, 107.4)
        self.assertEqual(set(result_kpis()[0].role for _ in [0]), {"result"})
        self.assertEqual([k.key for k in cause_kpis("KR-SEMI")], ["dram_asp", "hbm", "memory_cost", "foundry", "fcf_payout", "capex"])
        self.assertEqual(cause_kpis("KR-INTERNET"), [])                         # v5 로 정의되지 않은 섹터 → 쟁점 테마로
        self.assertTrue(RESULT_DRIVER_IDS >= {"earnings", "opm"})
        # 스냅숏: 정성 드라이버(at_entry None) 도 B 맵으로 넘어간다
        b = snapshot_to_holding(m, Position(entry_date=TODAY, entry_price=258000, quantity=10, w_target=0.05), today=TODAY)
        self.assertEqual(len(b.drivers()), 5)
        # 민감도: 합성 fixture 엔 사업부 표가 없어 미계산이 명시된다
        s = m["A1.1"].driver.sensitivity
        self.assertIsNotNone(s)
        self.assertEqual(s.status, "미계산")
        self.assertTrue(any("DRAM 매출" in x for x in s.missing))
        self.assertIn("미계산", sensitivity_line(s))

    def test_old_maps_still_load_and_render(self):
        m = load_map_file(Path(__file__).resolve().parents[1] / "examples" / "gaon_A.json")
        self.assertIsNone(m["A1.1"].driver.role)                               # 옛 맵: 판정 전
        self.assertEqual(m["A1.1"].driver.kind, "quant")
        self.assertNotIn("A3.7", m)
        from kstock.render import to_html

        self.assertIn("핵심 드라이버", to_html(m))
        m2 = build_new_map()
        self.assertFalse([i for i in validate_new_map(m2, today=TODAY) if i.level == "error" and i.node_id and i.node_id.startswith("A1.")])


class TestSensitivity(unittest.TestCase):
    def _map(self):
        m = new_map_skeleton("005930", "삼성전자", "growth", TODAY, n_drivers=5)
        m["A0.4"].set(258000.0, as_of=TODAY)
        fill_cause_drivers(m, sector="KR-SEMI", claimsets=_semi_claimsets(), issue_set=None, today=TODAY)
        m.meta["draft"]["financials"] = {"year": "2027E", "np": {"mean": 5_000_000.0, "n": 3}, "op": {"mean": 6_000_000.0, "n": 3},
                                         "eps": {"mean": 80_000.0, "n": 3}, "tp_median": 480_000.0}
        m.meta["draft"]["segments"] = {"DRAM": {"revenue": {"2027E": {"mean": 5_400_000.0, "n": 3}}},
                                       "파운드리": {"op": {"2027E": {"mean": -7_000.0, "n": 3}}, "revenue": {"2027E": {"mean": 350_000.0, "n": 3}}},
                                       "HBM": {"revenue": {"2027E": {"mean": 1_000_000.0, "n": 1}}}}
        return m

    def test_price_volume_chain_hand_calc(self):
        m = self._map()
        s = compute_sensitivity(m, "A1.1", today=TODAY)
        # ΔOP = 540조 × 10% × 1.0 = 54조 ; t = 1 − 500/600 = 1/6 ; ΔNP = 45조 ; N = 500조/8만 = 6.25B 주 ; ΔEPS = 45조/6.25B = 7,200원 ; PER = 480,000/80,000 = 6 ; ΔP = 43,200
        self.assertEqual(s.status, "계산됨")
        self.assertAlmostEqual(s.op_delta, 540_000.0)
        self.assertAlmostEqual(s.np_delta, 450_000.0, places=3)
        self.assertAlmostEqual(s.eps_delta, 7_200.0, places=3)
        self.assertAlmostEqual(s.price_delta, 43_200.0, places=3)
        self.assertAlmostEqual(s.price_delta_pct, 43_200 / 258_000 * 100, places=6)
        srcs = {i.name: i.source for i in s.inputs}
        self.assertEqual(srcs["DRAM 매출"], "segment")
        self.assertEqual(srcs["전가율 k"], "default")                            # 사용자 결정 ③: k=1.0 "기본값" 표시
        self.assertEqual(srcs["유효세율 t"], "derived")
        # 사용자가 k 를 주면 출처가 user 로 바뀌고 값이 비례한다
        m["A1.1"].driver.sensitivity_inputs = {"pass_through": 0.5}
        s2 = compute_sensitivity(m, "A1.1", today=TODAY)
        self.assertAlmostEqual(s2.price_delta, 21_600.0, places=3)
        self.assertEqual({i.name: i.source for i in s2.inputs}["전가율 k"], "user")
        # 충격 크기
        s3 = compute_sensitivity(m, "A1.1", delta_pct=5.0, today=TODAY)
        self.assertAlmostEqual(s3.op_delta, 135_000.0)                           # 540조 × 5% × 0.5

    def test_templates_and_missing_inputs(self):
        m = self._map()
        notes = compute_all(m, today=TODAY)
        self.assertEqual(len(notes), 5)
        d4 = m["A1.4"].driver.sensitivity                                        # 파운드리 direct_op: |−0.7조| × 10%
        self.assertEqual((d4.template, d4.status), ("direct_op", "계산됨"))
        self.assertAlmostEqual(d4.op_delta, 700.0)
        d3 = m["A1.3"].driver.sensitivity                                        # 메모리 원가 cost_ratio: 메모리 매출 없음 → 미계산
        self.assertEqual((d3.template, d3.status), ("cost_ratio", "미계산"))
        self.assertTrue(any("메모리 매출" in x for x in d3.missing))
        d5 = m["A1.5"].driver.sensitivity                                        # payout: FCF 는 사용자 입력
        self.assertEqual((d5.template, d5.status), ("payout", "미계산"))
        m["A1.5"].driver.sensitivity_inputs = {"fcf": 400_000.0}
        d5 = compute_sensitivity(m, "A1.5", today=TODAY)
        self.assertEqual(d5.status, "계산됨")
        self.assertAlmostEqual(d5.price_delta, 400_000.0 * 0.10 * 1e8 / 6.25e9, places=3)   # 주당 환원 640원
        self.assertIn("주당 환원", sensitivity_line(d5))
        # 표 숫자가 없으면 공통 입력이 missing 으로 드러난다
        m.meta["draft"]["financials"] = {"year": "2027E"}
        s = compute_sensitivity(m, "A1.1", today=TODAY)
        self.assertEqual(s.status, "미계산")
        self.assertTrue(any("유효세율" in x for x in s.missing) and any("주식 수" in x for x in s.missing) and any("목표 PER" in x for x in s.missing))
        self.assertIsNotNone(s.op_delta)                                         # ΔOP 까지는 계산된다

    def test_eval_drivers_and_cli(self):
        from kstock.cli import main

        m = self._map()
        compute_all(m, today=TODAY)
        g = json.loads((Path(__file__).resolve().parent / "golden" / "drivers" / "005930.json").read_text(encoding="utf-8"))
        ev = eval_drivers(g, m)
        self.assertEqual((ev.found, ev.expected, ev.order_ok, ev.forbidden_hits), (5, 5, True, []))
        self.assertEqual(ev.quant_ok, 3)
        self.assertEqual(ev.sensitivity_ok, 3)
        # 결과 지표가 A1 에 있으면 잡힌다
        m["A1.5"].driver.role = "result"
        self.assertTrue(eval_drivers(g, m).forbidden_hits)
        m["A1.5"].driver.role = "cause"
        with tempfile.TemporaryDirectory() as tmp:
            p = m.save(Path(tmp) / "A.json")
            self.assertEqual(main(["eval-drivers", str(p), "--golden", str(Path(__file__).resolve().parent / "golden" / "drivers")]), 0)
            self.assertEqual(main(["sensitivity", str(p), "--driver", "A1.1", "--set", "pass_through=0.5", "--save"]), 0)
            m2 = load_map_file(p)
            self.assertAlmostEqual(m2["A1.1"].driver.sensitivity.price_delta, 21_600.0, places=3)
            self.assertEqual(main(["sensitivity", str(p)]), 0)


class TestDriverModel(unittest.TestCase):
    def test_defaults_and_validation(self):
        d = Driver()
        self.assertEqual((d.kind, d.role, d.proxies, d.sensitivity, d.sensitivity_inputs), ("quant", None, [], None, {}))
        with self.assertRaises(ValueError):
            Driver(kind="fuzzy")
        with self.assertRaises(ValueError):
            Driver(role="effect")


if __name__ == "__main__":
    unittest.main()
