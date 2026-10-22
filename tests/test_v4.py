"""설계 v4 (검토의견 3 반영): V1 사실·전망 구분 · V2 기계/사람 검증 분리 · V3 기준 명제 판정·안 읽음 · V5 시나리오 재해석 · V6 Kill 후보 · V8 규칙 모드 제한."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from helpers import D0  # noqa: F401  (sys.path 설정)
from kstock.generate import MockGroupFiller
from kstock.generate.issues import build_issues, issues_table, themes
from kstock.models import (
    AssertedBy,
    Certainty,
    Claim,
    ClaimSet,
    ClaimStatus,
    Coverage,
    EpistemicType,
    IssueDisplay,
    QuantRef,
    SupportVerdict,
    Validation,
)
from kstock.reports import extract_facts, load_report
from kstock.reports.claims import extract_claims
from kstock.reports.validate_claims import mark_review_required, review, run_all

FIX = Path(__file__).resolve().parent / "fixtures" / "reports"
HANA = FIX / "hana_005930_20261012.txt"
Q1 = "HBM4는 4분기부터 주요 고객사 공급이 본격화되어 4Q26 HBM 점유율은 40%까지 상승할 것으로 기대한다"


def _c(cid, broker, prop, driver, direction, *, roles=("thesis",), quant=None, cond=None, certainty="expected", asserted_by="analyst",
       status="verified", support="supported", target="op", mech=None):
    return Claim(claim_id=cid, report_id=f"r_{broker}", broker=broker, proposition=prop, roles=list(roles), driver_id=driver, mechanism=mech,
                 direction=direction, quant_refs=[QuantRef(**q) for q in (quant or [])], condition=cond, target_metric_id=target,
                 certainty=Certainty(certainty), asserted_by=AssertedBy(asserted_by), status=ClaimStatus(status),
                 validation=Validation(quote="ok", numbers="ok" if quant else "none", support=SupportVerdict(support)),
                 verified_by="machine" if status == "verified" else "")


class TestV1Certainty(unittest.TestCase):
    def test_c3_fields_parsed_and_rules(self):
        doc = load_report(HANA)
        f = extract_facts(doc, today=date(2026, 10, 20))
        mock = MockGroupFiller({"claims": {"claims": [
            {"proposition": "3분기 영업이익 12.1조원을 발표했다", "roles": ["business"], "direction": "positive", "quotes": [Q1],
             "asserted_by": "company", "certainty": "realized", "as_of": "2026-09-30", "epistemic_type": "estimate"},
            {"proposition": "공급을 30% 늘릴 계획이라고 밝혔다", "roles": ["thesis"], "direction": "positive", "quotes": [Q1],
             "asserted_by": "company", "certainty": "planned"},
            {"proposition": "공급이 늘면 영업이익이 개선될 것이다", "roles": ["thesis"], "direction": "positive", "quotes": [Q1],
             "certainty": "conditional"},                         # condition 없음 → expected 로 내려간다
            {"proposition": "옛 형식(c2) 주장 — 필드 없음", "roles": ["thesis"], "direction": "positive", "quotes": [Q1]},
        ]}})
        cs = extract_claims(doc, f, mock, sector="KR-SEMI")
        a, b, c, d = cs.claims
        self.assertEqual((a.asserted_by, a.certainty, a.epistemic_type), (AssertedBy.company, Certainty.realized, EpistemicType.fact))
        self.assertEqual(a.as_of, date(2026, 9, 30))
        self.assertEqual(a.tag, "실적")
        self.assertEqual((b.certainty, b.tag), (Certainty.planned, "기업 계획"))
        self.assertEqual(c.certainty, Certainty.expected)
        self.assertEqual((d.asserted_by, d.certainty, d.tag), (AssertedBy.analyst, Certainty.expected, "증권사 전망"))
        self.assertEqual(d.effective_as_of, f.date)
        self.assertEqual(cs.total_chars, len(doc.text))
        self.assertGreater(cs.analyzed_ratio, 0.9)

    def test_old_claimset_json_still_loads(self):
        raw = {"report_id": "hk_1", "claims": [{"claim_id": "hk_1:c1", "report_id": "hk_1", "proposition": "옛 저장본의 주장이다"}]}
        cs = ClaimSet.model_validate(raw)
        self.assertEqual(cs.claims[0].certainty, Certainty.expected)
        self.assertEqual(cs.claims[0].verified_by, "")
        self.assertEqual(cs.analyzed_ratio, 1.0)


class TestV2Verification(unittest.TestCase):
    def test_verified_by_and_review_required_and_note(self):
        a = ClaimSet(report_id="r_A", broker="A", claims=[
            _c("a1", "A", "2027E 영업이익 600조원으로 전망한다", "dram_price", "positive", quant=[{"metric": "op", "col": "2027E", "value": 600, "unit": "조원"}]),
            _c("a2", "A", "HBM 경쟁력이 회복되고 있다", "hbm", "positive"),
            _c("a3", "A", "수율이 60% 를 밑돌면 수익성 개선이 지연된다", "hbm", "negative", roles=("risk",), cond="수율 60% 미달", certainty="conditional"),
            _c("a4", "A", "3분기 실적이 컨센서스를 상회했다", "earnings", "positive", certainty="realized", asserted_by="analyst"),
            _c("a5", "A", "인용이 모자란 주장", "hbm", "positive", status="needs_review", support="insufficient"),
        ])
        b = ClaimSet(report_id="r_B", broker="B", claims=[
            _c("b1", "B", "2027E 영업이익 800조원으로 전망한다", "dram_price", "positive", quant=[{"metric": "op", "col": "2027E", "value": 800, "unit": "조원"}]),
        ])
        n = mark_review_required([a, b])
        by = {c.claim_id: c for c in a.claims + b.claims}
        self.assertIn("판단 지표 수치", by["a1"].review_reason)
        self.assertIn("증권사 간 수치 차이", by["a1"].review_reason)        # 600 vs 800
        self.assertFalse(by["a2"].review_required)
        self.assertIn("조건부 리스크", by["a3"].review_reason)
        self.assertIn("사실로 단정", by["a4"].review_reason)
        self.assertTrue(by["b1"].review_required)
        self.assertEqual(n, 4)
        # 검토 대기함: needs_review + 확인 권고 (verified 유지)
        q = a.review_queue()
        self.assertEqual({c.claim_id for c in q}, {"a1", "a3", "a4", "a5"})
        self.assertEqual(by["a1"].status, ClaimStatus.verified)
        self.assertEqual(len(a.review_queue(include_required=False)), 1)
        # 근거 불충분 승인은 이유 필수
        with self.assertRaises(ValueError):
            review(a, "a5", "approve")
        review(a, "a5", "approve", note="원문 p.2 표에 있음")
        self.assertEqual((by["a5"].status, by["a5"].verified_by, by["a5"].review_required), (ClaimStatus.verified, "human", False))
        # 사람이 정한 것은 재검증이 되돌리지 않는다
        run_all(a, None, None, semantic=False)
        self.assertEqual(by["a5"].status, ClaimStatus.verified)
        self.assertEqual(by["a5"].verified_by, "human")
        review(a, "a2", "reject")
        self.assertEqual(by["a2"].verified_by, "human")
        run_all(a, None, None, semantic=False)
        self.assertEqual(by["a2"].status, ClaimStatus.rejected)


class TestV3ReferenceProposition(unittest.TestCase):
    def _sets(self):
        a = ClaimSet(report_id="r_A", broker="A", ticker="005930", claims=[
            _c("a1", "A", "HBM4 공급 증가가 이익을 끌어올린다", "hbm", "positive", mech="공급 증가 → 이익"),
        ])
        b = ClaimSet(report_id="r_B", broker="B", ticker="005930", claims=[
            _c("b1", "B", "HBM4 공급은 늘지만 가격 하락으로 수익성에는 부정적이다", "hbm", "negative", mech="공급 증가 → 가격 하락 → 수익성"),
        ])
        c = ClaimSet(report_id="r_C", broker="C", ticker="005930", claims=[
            _c("c1", "C", "HBM4 출하가 확대된다", "hbm", "positive", mech="출하 확대"),
        ], total_chars=100000, analyzed_chars=60000)                          # C 는 60% 만 읽었다
        d = ClaimSet(report_id="r_D", broker="D", ticker="005930", claims=[
            _c("d1", "D", "환율 하락이 역풍이다", "fx", "negative"),
        ])
        return [a, b, c, d]

    def test_reference_proposition_direction_basis_and_unanalyzed(self):
        iss = build_issues(self._sets(), ticker="005930")
        hbm = {t.driver_id: t for t in themes(iss)}["hbm"]
        self.assertIn("HBM", hbm.reference_proposition)
        self.assertEqual(hbm.coverage_basis, "direction")
        self.assertEqual(hbm.coverage["D"], Coverage.silent)
        fx = {t.driver_id: t for t in themes(iss)}["fx"]
        self.assertEqual(fx.coverage["C"], Coverage.unanalyzed)               # 안 읽은 리포트는 침묵이 아니다
        self.assertEqual(fx.coverage["A"], Coverage.silent)
        self.assertIn("?", issues_table(iss))
        self.assertIn("안 읽은 리포트 1", " ".join(iss.notes))

    def test_llm_judgement_overrides_direction(self):
        sets = self._sets()
        # 판정: A 동의, B 부분 동의(공급엔 동의·이익엔 반대), C 동의 — 방향 통계로는 B 가 '반대' 라 핵심 이견이었을 것
        mock = MockGroupFiller({"issues": {"verdicts": [
            {"id": "hbm", "broker": "A", "verdict": "agree", "why": "같은 방향"},
            {"id": "hbm", "broker": "B", "verdict": "partial", "why": "공급 증가엔 동의, 이익 효과는 반대"},
            {"id": "hbm", "broker": "C", "verdict": "agree", "why": "출하 확대"},
        ]}})
        plain = build_issues(sets, ticker="005930")
        self.assertEqual({t.driver_id: t for t in themes(plain)}["hbm"].display, IssueDisplay.disagreement)
        iss = build_issues(sets, ticker="005930", filler=mock)
        hbm = {t.driver_id: t for t in themes(iss)}["hbm"]
        self.assertEqual(mock.calls, ["issues"])
        self.assertEqual(hbm.coverage_basis, "llm")
        self.assertEqual(hbm.coverage["B"], Coverage.partial)
        self.assertEqual(hbm.coverage["A"], Coverage.agree)
        self.assertEqual(hbm.display, IssueDisplay.common)                      # 부분 동의는 반대로 세지 않는다
        self.assertIn("공급 증가엔 동의", hbm.judgement_notes["B"])
        table = issues_table(iss)
        self.assertIn("◐", table)
        self.assertIn("[판정: LLM 기준 명제", table)
        self.assertIn("≡", table)
        self.assertTrue(any("판정(LLM) 적용 1" in n for n in iss.notes))
        # 판정이 깨져도 방향 통계로 남는다
        broken = MockGroupFiller({"issues": {"nope": 1}})
        iss2 = build_issues(sets, ticker="005930", filler=broken)
        self.assertEqual({t.driver_id: t for t in themes(iss2)}["hbm"].coverage_basis, "direction")
        off = build_issues(sets, ticker="005930", filler=mock, issue_check=False)
        self.assertEqual({t.driver_id: t for t in themes(off)}["hbm"].coverage_basis, "direction")


class TestV5V6V8Map(unittest.TestCase):
    def test_scenario_case_vs_tp_reinterpretation(self):
        from kstock.generate.scenarios import ScenarioCandidate

        case = ScenarioCandidate("A", date(2026, 10, 1), "리포트 제시", "Bear", 300000.0)
        tp = ScenarioCandidate("B", date(2026, 10, 1), "목표가", None, 500000.0)
        self.assertEqual((case.origin, tp.origin), ("broker_case", "broker_tp"))
        self.assertFalse(case.is_reinterpretation("Bear"))
        self.assertTrue(case.is_reinterpretation("Base"))
        self.assertTrue(tp.is_reinterpretation("Bull"))
        self.assertTrue(tp.is_reinterpretation("Base"))

    def test_rules_mode_makes_no_claim_nodes_and_kill_is_candidate_only(self):
        from kstock.generate.draft import draft_from_reports

        txts = sorted(FIX.glob("*_005930_*.txt"))
        r = draft_from_reports(txts, ticker="005930", name="삼성전자", today=date(2026, 10, 20), claims="rules", fetch_price=False)
        m = r.map
        self.assertEqual(m.meta["draft"]["quality"], "rules")
        self.assertTrue(m.meta["draft"].get("rules_mode"))
        self.assertEqual([n for n in m.nodes if n.startswith("A2.1.")], [])                        # 쟁점 노드 없음
        self.assertTrue(all(m[nid].value is None for nid in ("A4.1", "A4.2", "A4.3")))              # 리스크 없음
        self.assertIn("미생성", m["A2.1"].question)
        self.assertTrue(any("규칙 모드" in n for n in r.notes))
        self.assertIn("키워드 분류 — 미검증", m["A8.4.1"].value)
        self.assertIsNone(m["A4.4.1"].rule)
        self.assertEqual(r.errors, [])

    def test_kill_candidates_from_conditional_risk(self):
        from kstock.generate.mapping import apply_claims_to_map
        from kstock.reports.digest import ReportDigest
        from kstock.templates import new_map_skeleton

        today = date(2026, 10, 20)
        m = new_map_skeleton("005930", "삼성전자", "growth", today=today)
        cs = ClaimSet(report_id="r_A", broker="A", ticker="005930", claims=[
            _c("a1", "A", "수율이 60% 를 밑돌면 수익성 개선이 지연된다", "hbm", "negative", roles=("risk",), cond="수율 60% 미달", certainty="conditional",
               quant=[{"metric": "yield", "col": None, "value": 60, "unit": "%"}]),
            _c("a2", "A", "HBM 점유율이 오른다", "hbm", "positive"),
        ])
        facts = extract_facts(load_report(HANA), today=today)
        d = ReportDigest.build([facts], ticker="005930", name="삼성전자", today=today)
        iss = build_issues([cs], ticker="005930")
        notes = apply_claims_to_map(m, [cs], iss, d, today=today, name="삼성전자")
        kc = m.meta["draft"]["kill_candidates"]
        self.assertEqual(len(kc), 1)
        self.assertEqual((kc[0]["kpi"], kc[0]["threshold"], kc[0]["claim_id"]), ("yield", 60.0, "a1"))
        self.assertIsNone(kc[0]["observable"])
        self.assertIsNone(m["A4.4.1"].rule)                                                       # 자동 Rule 없음
        self.assertIn("후보: 1건", m["A4.4.1"].question)
        self.assertTrue(any("후보 1건" in n for n in notes))
        self.assertTrue(m["A4.1"].value["text"].startswith("[조건부]"))                            # V1 접두어


if __name__ == "__main__":
    unittest.main()


class TestV4CellBackfill(unittest.TestCase):
    """v4 V4: 증권사별 대표 문서는 최신 1건이지만, 거기 없는 (지표, 기간) 셀은 같은 증권사 이전 리포트(45일 안) 로 보강한다."""

    def test_backfill_from_older_same_broker_report(self):
        import copy
        from datetime import timedelta

        from kstock.reports import extract_facts, load_reports
        from kstock.reports.digest import ReportDigest

        today = date(2026, 10, 20)
        facts = [extract_facts(d, today=today) for d in load_reports(sorted(FIX.glob("*_005930_*.txt")))]
        new = facts[0]
        old = copy.deepcopy(new)
        old.date = new.date - timedelta(days=20)
        old.path = Path(str(new.path).replace(".txt", "_old.txt"))
        # 최신 리포트에는 2028E 가 없고, 이전 리포트에만 있다 (분기 Preview 가 장기 추정을 안 싣는 경우)
        for cols in new.estimates.values():
            cols.pop("2028E", None)
        for metric in ("op", "revenue"):
            old.estimates.setdefault(metric, {})["2028E"] = 777.0
        old.estimates["op"]["2027E"] = 1.0                       # 대표 문서에 있는 셀은 덮지 않는다
        d = ReportDigest.build(facts + [old], today=today)
        self.assertIn(old.path.name, d.excluded)                 # 대표 문서 집계에서는 제외
        cell = d.estimates["op"]["2028E"]
        self.assertIn(new.broker, cell.by_broker)
        self.assertEqual(cell.sources[new.broker][2], "보강")
        self.assertEqual(cell.sources[new.broker][0], old.path.name)
        self.assertEqual(cell.n_backfilled, 1)
        c27 = d.estimates["op"]["2027E"]
        self.assertEqual(c27.sources[new.broker][2], "대표")
        self.assertNotIn(1.0 * 10.0, c27.values)                 # 이전 리포트의 2027E 는 안 쓴다 (십억원 환산 뒤에도)
        table = d.estimates_table()
        self.assertIn("*", table)
        self.assertIn("보강", table)
        self.assertTrue(any("보강" in w for w in d.warnings))
        # 45일 넘은 이전 리포트는 보강에도 안 쓴다
        stale = copy.deepcopy(old)
        stale.date = new.date - timedelta(days=60)
        stale.path = Path("stale.txt")
        stale.estimates["eps"] = {"2029E": 5.0}
        d2 = ReportDigest.build(facts + [stale], today=today)
        self.assertNotIn("2029E", d2.estimates.get("eps", {}))

    def test_adjusted_op_is_separate_metric(self):
        import re

        from kstock.reports.extract import ROW_LABELS

        pats = dict(ROW_LABELS)
        self.assertTrue(re.match(pats["op_adj"], "조정 영업이익"))
        self.assertFalse(re.match(pats["op_adj"], "영업이익"))
        self.assertTrue(re.match(pats["op"], "영업이익"))


class TestV6KillConfirm(unittest.TestCase):
    def test_apply_kill_candidate_requires_three_fields(self):
        import tempfile

        from kstock.ops import Ops, OpsError
        from kstock.store import Store
        from kstock.templates import new_map_skeleton

        today = date(2026, 10, 20)
        with tempfile.TemporaryDirectory() as t:
            store = Store(Path(t) / "db.sqlite")
            ops = Ops(store, today=today)
            m = new_map_skeleton("005930", "삼성전자", "growth", today=today)
            m.meta["draft"] = {"kill_candidates": [{"kpi": "yield", "driver_node": None, "op": "<", "threshold": 60.0, "unit": "%",
                                                    "condition": "수율 60% 미달", "proposition": "수율이 60% 를 밑돌면 수익성 개선이 지연된다",
                                                    "broker": "A", "claim_id": "a1", "quote": "", "observable": None, "update_cadence": None, "action": None}]}
            self.assertEqual(len(ops.kill_candidate_labels(m)), 1)
            with self.assertRaises(OpsError):
                ops.apply_kill_candidate(m, 0, observable="", update_cadence="분기", action="exit", save=False)
            with self.assertRaises(OpsError):                                                # KPI 가 A1 에 없으면 조건식을 적어야 한다
                ops.apply_kill_candidate(m, 0, observable="분기 IR 수율 코멘트", update_cadence="분기", action="exit", save=False)
            msg = ops.apply_kill_candidate(m, 0, observable="분기 IR 수율 코멘트", update_cadence="분기", action="review",
                                           condition="driver[A1.1].current < 60", save=False)
            self.assertIn("A4.4.1", msg)
            r = m["A4.4.1"].rule
            self.assertIsNotNone(r)
            self.assertEqual(r.action.value, "review")
            self.assertIn("사용자 확정 Kill", r.note)
            self.assertIn("관측: 분기 IR 수율 코멘트", r.note)
            self.assertEqual(m.meta["draft"]["kill_confirmed"][0]["origin"], "user_confirmed")
            self.assertNotIn("pending", m["A4.4.1"].tags)
            store.close()
