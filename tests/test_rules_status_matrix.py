import unittest
from datetime import date

from helpers import D0, D1, build_all, build_new_map  # noqa: F401
from kstock.engine import RuleContext, compute_light, decide, evaluate_condition, validate_condition
from kstock.engine.rules import ConditionError
from kstock.models import Action, Light


class TestConditionDSL(unittest.TestCase):
    def setUp(self):
        self.ctx = RuleContext(today=date(2027, 1, 15), price=76000, w_now=0.072, drawdown=0.1,
                               thesis_state="약화", driver={"A1.1": {"current": 9.0, "at_entry": 8.1}})

    def test_numeric_and_path(self):
        self.assertTrue(evaluate_condition("driver[A1.1].current < 10.0", self.ctx))
        self.assertFalse(evaluate_condition("driver[A1.1].current < 8", self.ctx))
        self.assertTrue(evaluate_condition("w_now > 0.065", self.ctx))

    def test_and_or_and_strings(self):
        self.assertTrue(evaluate_condition("drawdown > 0.05 and thesis_state == '약화'", self.ctx))
        self.assertFalse(evaluate_condition("drawdown > 0.5 and thesis_state == '약화'", self.ctx))
        self.assertTrue(evaluate_condition("drawdown > 0.5 or price > 70000", self.ctx))

    def test_missing_value_is_false(self):
        self.assertFalse(evaluate_condition("driver[A9.9].current < 10", self.ctx))

    def test_syntax_errors(self):
        self.assertTrue(validate_condition("w_now >"))
        self.assertTrue(validate_condition("w_now 0.1"))
        self.assertEqual(validate_condition("w_now > 0.1 and price < 1"), [])
        with self.assertRaises(ConditionError):
            evaluate_condition("unknown_var > 1", self.ctx)


class TestMatrix(unittest.TestCase):
    def test_core_rows(self):
        self.assertEqual(decide("유효", "저평가", "적정").action, Action.add)
        self.assertEqual(decide("유효", "저평가", "과다").action, Action.hold)
        self.assertEqual(decide("유효", "적정", "적정").action, Action.hold)
        self.assertEqual(decide("유효", "적정", "과다").action, Action.trim)
        self.assertEqual(decide("유효", "고평가", "과다").action, Action.trim)
        self.assertEqual(decide("무효", "저평가", "과소").action, Action.exit)
        self.assertEqual(decide("변질", None, None).action, Action.exit)
        self.assertEqual(decide("약화", "적정", "적정").action, Action.hold)
        self.assertEqual(decide("약화", "고평가", "적정").action, Action.trim)

    def test_profile_dependent_overvalued(self):
        self.assertEqual(decide("유효", "고평가", "적정", profile="growth").action, Action.trim)
        self.assertEqual(decide("유효", "고평가", "적정", profile="momentum").action, Action.hold)
        self.assertEqual(decide("유효", "고평가", "적정", profile="momentum", trend_intact=False).action, Action.hold)

    def test_switch_and_special(self):
        self.assertEqual(decide("유효", "적정", "적정", relative_edge=-0.15).action, Action.switch)
        self.assertEqual(decide("유효", "적정", "적정", relative_edge=-0.05).action, Action.hold)
        self.assertEqual(decide("강화", "적정", "적정", relative_edge=-0.15).action, Action.hold)  # 강화는 switch 안 함
        self.assertEqual(decide("유효", "적정", "적정", no_thesis=True).action, Action.review)
        self.assertEqual(decide("유효", "적정", "적정", event_imminent=True).action, Action.hedge)

    def test_unjudged_thesis_is_flagged(self):
        r = decide(None, "적정", "적정")
        self.assertEqual(r.action, Action.hold)
        self.assertIn("미판정", r.rule)


class TestLight(unittest.TestCase):
    def test_demo_is_yellow_then_red_on_kill(self):
        a, b, c, rep = build_all()
        self.assertEqual(rep["B"]["light"], "YELLOW")
        # kill criteria: 수주잔고 < 3500
        b["B2.1.2"].driver.current.value = 3000
        res = compute_light(b, today=D1)
        self.assertEqual(res.light, Light.RED)
        self.assertTrue(any("무효화 조건" in r for r in res.reasons))

    def test_green_when_clean(self):
        a, b, c, rep = build_all()
        b.market.w_now = 0.05
        b.market.band_percentile = 50
        from kstock.engine import evaluate_holding
        for n in b.rules():
            n.rule.status = "armed"
        for nid in ("B5.1", "B5.2", "B5.3", "B5.4", "B5.5", "B5.6"):
            b[nid].value = False
        r = evaluate_holding(b, today=D1)
        self.assertEqual(r.light.light, Light.GREEN, r.light.reasons)


if __name__ == "__main__":
    unittest.main()
