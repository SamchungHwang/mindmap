"""Issue 통합 (설계 v3 P3): 테마→메커니즘 계층 · 복수 상태 · coverage(침묵≠반대) · 수치 분산 · 화면 분류 · CLI."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import D0  # noqa: F401  (sys.path 설정)
from kstock.cli import main as cli_main
from kstock.generate.issues import build_issues, issues_table, mechanisms_of, themes
from kstock.models import Claim, ClaimSet, ClaimStatus, Coverage, IssueDisplay, QuantRef
from kstock.reports.claims import save_claimset


def _c(cid, broker, prop, driver, direction, *, mech=None, roles=("thesis",), quant=None, horizon=None, cond=None, status="verified", inferred=False):
    return Claim(claim_id=cid, report_id=f"r_{broker}", broker=broker, proposition=prop, roles=list(roles), driver_id=driver, mechanism=mech,
                 direction=direction, quant_refs=[QuantRef(**q) for q in (quant or [])], horizon=horizon, condition=cond,
                 status=ClaimStatus(status), inferred=inferred, target_metric_id="op")


def _sets():
    a = ClaimSet(report_id="r_A", broker="A", ticker="005930", claims=[
        _c("a1", "A", "HBM4 인증 완료가 출하 증가로 이어진다", "hbm", "positive", mech="인증 완료 → 출하", quant=[{"metric": "op", "col": "2027E", "value": 600, "unit": "조원"}]),
        _c("a2", "A", "MX 적자가 확대된다", "mobile_demand", "negative", mech="원가 → MX 적자"),
        _c("a3", "A", "환율 하락은 역풍이다", "fx", "negative", roles=("risk",)),
    ])
    b = ClaimSet(report_id="r_B", broker="B", ticker="005930", claims=[
        _c("b1", "B", "HBM4 수율 개선으로 원가가 내려간다", "hbm", "positive", mech="수율 → 원가", quant=[{"metric": "op", "col": "2027E", "value": 800, "unit": "조원"}]),
        _c("b2", "B", "MX 적자는 믹스 개선으로 줄어든다", "mobile_demand", "positive", mech="믹스 → MX 적자 축소"),
        _c("b3", "B", "추론한 주장", "hbm", "positive", inferred=True),
        _c("b4", "B", "검토 대기 주장", "hbm", "positive", status="needs_review"),
    ])
    c = ClaimSet(report_id="r_C", broker="C", ticker="005930", claims=[
        _c("c1", "C", "HBM4 믹스 확대로 ASP 가 오른다", "hbm", "positive", mech="믹스 → ASP", horizon="2027E"),
        _c("c2", "C", "수율이 안정되지 않으면 공급이 지연된다", "hbm", "negative", mech="수율 → 공급", cond="초기 수율이 안정되지 않으면", roles=("risk",)),
    ])
    return [a, b, c]


class TestIssues(unittest.TestCase):
    def test_hierarchy_and_states(self):
        iss = build_issues(_sets(), ticker="005930")
        ts = {t.driver_id: t for t in themes(iss)}
        hbm = ts["hbm"]
        self.assertEqual(hbm.consensus_direction, "mixed")                 # C 의 조건부 리스크가 부정
        self.assertIn("direction", hbm.disagreement_dimensions)
        self.assertEqual(hbm.display, IssueDisplay.disagreement)
        self.assertEqual(hbm.coverage["A"], Coverage.agree)
        self.assertEqual(hbm.directions["C"], "mixed")
        self.assertGreaterEqual(len(mechanisms_of(iss, hbm)), 3)           # 인증→출하 / 수율→원가 / 믹스→ASP 는 다른 가정
        self.assertNotIn("b3", hbm.claim_ids)                               # inferred 제외
        self.assertNotIn("b4", hbm.claim_ids)                               # needs_review 제외
        self.assertIsNotNone(hbm.numeric_spread)
        self.assertEqual(hbm.numeric_spread.col, "2027E")
        self.assertAlmostEqual(hbm.numeric_spread.rel, 200 / 700, places=3)
        self.assertIn("magnitude", hbm.disagreement_dimensions)
        self.assertTrue(any("조건 확인 필요" in q for q in hbm.unresolved_questions))
        mob = ts["mobile_demand"]
        self.assertEqual(mob.display, IssueDisplay.disagreement)
        self.assertEqual(mob.coverage["C"], Coverage.silent)                # 침묵은 반대가 아니다
        fx = ts["fx"]
        self.assertEqual(fx.display, IssueDisplay.independent)
        self.assertEqual(fx.consensus_direction, "negative")
        table = issues_table(iss)
        self.assertIn("[HBM]", table)
        self.assertIn("핵심 이견", table)
        # include_review 면 b4 가 들어온다
        iss2 = build_issues(_sets(), ticker="005930", include_review=True)
        self.assertIn("b4", {c for t in themes(iss2) for c in t.claim_ids})

    def test_common_when_two_agree(self):
        sets = _sets()
        sets[2].claims = [sets[2].claims[0]]                                # C 의 리스크 제거 → HBM 은 전원 긍정
        iss = build_issues(sets)
        hbm = next(t for t in themes(iss) if t.driver_id == "hbm")
        self.assertEqual(hbm.consensus_direction, "positive")
        self.assertEqual(hbm.display, IssueDisplay.disagreement)           # 수치 분산(600 vs 800) 때문에 여전히 이견
        for c in sets:
            for cl in c.claims:
                cl.quant_refs = []
        iss2 = build_issues(sets)
        hbm2 = next(t for t in themes(iss2) if t.driver_id == "hbm")
        self.assertEqual(hbm2.display, IssueDisplay.common)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as t:
            for cs in _sets():
                save_claimset(cs, Path(t))
            out = Path(t) / "issues.json"
            self.assertEqual(cli_main(["issues", "--claims", t, "--ticker", "005930", "--out", str(out)]), 0)
            self.assertTrue(out.is_file())
            self.assertEqual(cli_main(["issues", "--claims", t, "--ticker", "999999"]), 2)


if __name__ == "__main__":
    unittest.main()


class TestThemeKeys(unittest.TestCase):
    """실측 보강: 자유 라벨은 사전으로 다시 정규화, 못 하면 대상 지표로 묶고, 한 증권사의 상향·하향 혼재는 '이견' 이 아니다."""

    def test_label_and_metric_fallback_and_single_broker_mixed(self):
        from kstock.generate.issues import _theme_key

        up = _c("x1", "A", "2026년 EPS 추정치를 상향했다", None, "positive", mech="실적 상회 → EPS 상향")
        up.driver_label = "EPS(26) 상향"
        dn = _c("x2", "A", "2027년 EPS 추정치를 하향했다", None, "negative", mech="추정 변경 → EPS 하향")
        dn.driver_label = "EPS(27) 하향"
        self.assertEqual(_theme_key(up), "estimate_revision")
        self.assertEqual(_theme_key(dn), "estimate_revision")
        surprise = _c("x3", "B", "3Q26 영업이익은 컨센서스를 상회했다", None, "positive")
        surprise.driver_label = "3Q26 영업이익 서프라이즈"
        self.assertEqual(_theme_key(surprise), "earnings")
        odd = _c("x4", "B", "알 수 없는 라벨", None, "positive")
        odd.driver_label = "ㅁㄴㅇㄹ"
        odd.target_metric_id = "eps"
        self.assertEqual(_theme_key(odd), "metric:eps")
        iss = build_issues([ClaimSet(report_id="r_A", broker="A", ticker="005930", claims=[up, dn]),
                            ClaimSet(report_id="r_B", broker="B", ticker="005930", claims=[surprise, odd])], ticker="005930")
        ts = {t.driver_id or t.issue_id: t for t in themes(iss)}
        rev = ts["estimate_revision"]
        self.assertEqual(rev.directions["A"], "mixed")
        self.assertNotIn("direction", rev.disagreement_dimensions)          # 한 곳의 혼재
        self.assertEqual(rev.display, IssueDisplay.independent)
        self.assertEqual(ts["metric:eps"].driver_label, "EPS 관련")
        table = issues_table(iss)
        self.assertIn("[추정치 변경]", table)
        self.assertIn("[EPS 관련]", table)

    def test_table_collapses_single_broker_mechanisms_under_shared_theme(self):
        iss = build_issues(_sets(), ticker="005930")
        table = issues_table(iss)
        self.assertIn("└ 단독 근거", table)                                  # hbm 테마(3곳) 아래 1곳짜리 메커니즘은 한 줄로
