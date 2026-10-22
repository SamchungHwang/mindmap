"""평가 루프 (설계 v3 P0): 정답표 초안 생성 · 추출 정확도 · Claim 재현율/근거 불일치율."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from helpers import D0  # noqa: F401  (sys.path 설정)
from kstock.cli import main as cli_main
from kstock.generate.evals import eval_claims, eval_extract, write_golden_drafts
from kstock.models import Claim, ClaimSet, ClaimStatus, EvidenceSpan, report_id_for

FIX = Path(__file__).resolve().parent / "fixtures" / "reports"
TXTS = sorted(FIX.glob("*.txt"))
TODAY = date(2026, 10, 20)


class TestGoldenAndExtractEval(unittest.TestCase):
    def test_draft_then_eval_then_miss(self):
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "golden"
            written = write_golden_drafts(TXTS, out, today=TODAY, root=FIX.parent.parent.parent)
            self.assertEqual(len(written), 3)
            g = json.loads(written[0].read_text(encoding="utf-8"))
            self.assertFalse(g["verified"])
            self.assertIn("facts", g)
            self.assertIn("required_claims", g)
            ev = eval_extract(out, root=FIX.parent.parent.parent, today=TODAY)
            self.assertEqual(ev.reports, 3)
            self.assertAlmostEqual(ev.overall, 1.0)
            # 사람이 정답을 고치면 틀린 곳이 잡힌다
            g["facts"]["target_price"] = 999999.0
            g["verified"] = True
            written[0].write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
            ev2 = eval_extract(out, root=FIX.parent.parent.parent, today=TODAY)
            self.assertLess(ev2.overall, 1.0)
            self.assertEqual(ev2.fields["target_price"].ok, 2)
            self.assertTrue(ev2.fields["target_price"].misses[0].startswith(g["report_id"]))
            ev3 = eval_extract(out, root=FIX.parent.parent.parent, today=TODAY, verified_only=True)
            self.assertEqual(ev3.reports, 1)
            # verified 정답표는 초안 생성이 덮지 않는다
            again = write_golden_drafts(TXTS, out, today=TODAY, root=FIX.parent.parent.parent)
            self.assertEqual(len(again), 2)
            self.assertIn("정확도", ev2.table())

    def test_cli(self):
        with tempfile.TemporaryDirectory() as t:
            out = str(Path(t) / "g")
            self.assertEqual(cli_main(["golden-draft", "--reports", str(FIX / "*.txt"), "--out", out, "--date", "2026-10-20",
                                       "--root", str(FIX.parent.parent.parent)]), 0)
            self.assertEqual(cli_main(["eval-extract", "--golden", out, "--date", "2026-10-20", "--root", str(FIX.parent.parent.parent),
                                       "--min-acc", "0.99"]), 0)

    def test_report_id(self):
        self.assertEqual(report_id_for("x/삼성전자_96588.pdf"), "hk_96588")
        self.assertEqual(report_id_for("x/hk_652446.pdf"), "hk_652446")
        self.assertTrue(report_id_for("x/hana_005930_20261012.txt", "t").startswith("f_"))


class TestClaimEval(unittest.TestCase):
    def test_recall_and_mismatch(self):
        sp = EvidenceSpan.make("r1", 1, 0, 40, "HBM4 공급 확대로 2027년 메모리 영업이익이 증가할 전망이다")
        c1 = Claim(claim_id="c1", report_id="r1", proposition="HBM4 공급 확대가 2027년 메모리 영업이익 증가를 견인한다", roles=["thesis"],
                   direction="positive", span_ids=[sp.span_id], status=ClaimStatus.verified)
        c2 = Claim(claim_id="c2", report_id="r1", proposition="고객사 인증이 완료되어 공급이 확정되었다", roles=["thesis"],
                   direction="positive", span_ids=[sp.span_id], status=ClaimStatus.verified)
        c3 = Claim(claim_id="c3", report_id="r1", proposition="중국 CXMT 의 DDR5 양산이 범용 가격을 누른다", roles=["risk"],
                   direction="negative", status=ClaimStatus.needs_review)
        cs = ClaimSet(report_id="r1", spans=[sp], claims=[c1, c2, c3])
        golden = {
            "required_claims": [{"proposition": "HBM4 공급 확대가 2027년 메모리 영업이익 증가를 견인", "quote": "HBM4 공급 확대로 2027년 메모리 영업이익이 증가"}],
            "required_risks": [{"proposition": "CXMT DDR5 양산 시 범용 DRAM 가격 하락 압력"}],
            "candidate_review": [{"claim_id": "c1", "supported": True}, {"claim_id": "c2", "supported": False, "note": "원문은 미확인이라 함"}],
        }
        ev = eval_claims(golden, cs)
        self.assertEqual((ev.required, ev.found), (2, 1))          # 리스크는 needs_review 라 usable 이 아니다
        self.assertAlmostEqual(ev.recall, 0.5)
        self.assertEqual(ev.candidates, 2)
        self.assertEqual((ev.reviewed, ev.supported, ev.mismatched), (2, 1, 1))
        self.assertAlmostEqual(ev.mismatch_rate, 0.5)


if __name__ == "__main__":
    unittest.main()


class TestClaimEvalMatching(unittest.TestCase):
    def test_pending_and_misses_and_review_by_proposition(self):
        from kstock.generate.evals import eval_claims
        from kstock.models import (
            Claim,
            ClaimSet,
            ClaimStatus,
            EvidenceSpan,
            SupportVerdict,
            Validation,
        )

        sp = EvidenceSpan.make("hk_1", 1, 0, 30, "HBM 점유율은 40%까지 상승할 것으로 기대한다")
        ok = Claim(claim_id="hk_1:c1", report_id="hk_1", proposition="HBM 점유율이 40% 까지 상승한다", span_ids=[sp.span_id],
                   validation=Validation(quote="ok", numbers="ok", support=SupportVerdict.supported), status=ClaimStatus.verified)
        pend = Claim(claim_id="hk_1:c2", report_id="hk_1", proposition="파운드리 적자가 축소된다", span_ids=[],
                     validation=Validation(quote="miss"), status=ClaimStatus.needs_review)
        cs = ClaimSet(report_id="hk_1", spans=[sp], claims=[ok, pend])
        golden = {"required_claims": [
            {"id": "a", "proposition": "HBM 점유율 40% 상승", "quote": "HBM 점유율은 40%까지 상승할 것으로 기대한다"},
            {"id": "b", "proposition": "파운드리 적자가 축소된다"},
            {"id": "c", "proposition": "2026년 영업이익 추정치를 상향했다"},
        ], "candidate_review": [
            {"claim_id": "hk_1:c1", "proposition": "전혀 다른 명제 — id 만 같다", "supported": False},   # 명제가 안 맞으면 붙이지 않는다
            {"claim_id": "zzz", "proposition": "HBM 점유율이 40% 까지 상승한다", "supported": True},  # 명제로 붙는다
        ]}
        ev = eval_claims(golden, cs)
        self.assertEqual((ev.required, ev.found, ev.found_pending), (3, 1, 1))
        self.assertEqual(len(ev.misses), 2)
        self.assertTrue(ev.misses[0].startswith("[검토 대기]"))
        self.assertTrue(ev.misses[1].startswith("[미추출]"))
        self.assertEqual((ev.reviewed, ev.supported, ev.mismatched), (1, 1, 0))


class TestV4Evals(unittest.TestCase):
    def test_eval_issues_and_approved_invalid(self):
        from kstock.generate.evals import eval_claims, eval_issues
        from kstock.models import (
            Claim,
            ClaimSet,
            ClaimStatus,
            Coverage,
            EvidenceSpan,
            Issue,
            IssueDisplay,
            IssueLevel,
            IssueSet,
            SupportVerdict,
            Validation,
        )

        iss = IssueSet(ticker="005930", issues=[
            Issue(issue_id="dram_price", level=IssueLevel.theme, driver_id="dram_price", driver_label="DRAM 가격", display=IssueDisplay.common,
                  coverage={"A": Coverage.agree, "B": Coverage.agree}),
            Issue(issue_id="mobile_demand", level=IssueLevel.theme, driver_id="mobile_demand", driver_label="모바일·세트 수요", display=IssueDisplay.common,
                  coverage={"A": Coverage.agree, "B": Coverage.agree}),
        ])
        golden = {"ticker": "005930", "required_issues": [
            {"driver_id": "dram_price", "display": "공통 견해"},
            {"driver_id": "mobile_demand", "display": "핵심 이견", "brokers_disagree": ["A", "B"]},
            {"driver_id": "fx", "label": "환율", "display": "독립 주장"},
        ]}
        ev = eval_issues(golden, iss)
        self.assertEqual((ev.required, ev.found, ev.display_ok, ev.missed_disagreements), (3, 2, 1, 1))
        self.assertTrue(any("[분류 다름]" in m for m in ev.misses) and any("[테마 없음]" in m for m in ev.misses))
        # 잘못된 Claim 자동 승인률: 기계 verified 2건 중 사람이 '지지 안 됨' 1건 → 50%; 사람 승인 1건도 '지지 안 됨'
        sp = EvidenceSpan.make("hk_1", 1, 0, 10, "인용")
        mk = lambda cid, prop, by: Claim(claim_id=cid, report_id="hk_1", proposition=prop, span_ids=[sp.span_id], status=ClaimStatus.verified,  # noqa: E731
                                        verified_by=by, validation=Validation(quote="ok", numbers="none", support=SupportVerdict.supported))
        cs = ClaimSet(report_id="hk_1", spans=[sp], claims=[mk("c1", "기계가 통과시킨 옳은 주장", "machine"), mk("c2", "기계가 통과시킨 틀린 주장", "machine"),
                                                          mk("c3", "사람이 승인한 틀린 주장", "human")])
        g2 = {"required_claims": [], "candidate_review": [
            {"proposition": "기계가 통과시킨 옳은 주장", "supported": True},
            {"proposition": "기계가 통과시킨 틀린 주장", "supported": False},
            {"proposition": "사람이 승인한 틀린 주장", "supported": False},
        ]}
        ce = eval_claims(g2, cs)
        self.assertEqual((ce.reviewed, ce.mismatched, ce.machine_verified, ce.machine_mismatched, ce.human_mismatched), (3, 2, 2, 1, 1))
        self.assertAlmostEqual(ce.approved_invalid_rate, 0.5)
