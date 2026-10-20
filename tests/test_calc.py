import unittest

from helpers import D0, D1, build_all, build_new_map  # noqa: F401  (sys.path 설정)
from kstock.engine import calc
from kstock.models import Scenario, Settings, ValuationState, WeightState

SC = {
    "Bear": Scenario(name="Bear", probability=0.25, target_price=48000),
    "Base": Scenario(name="Base", probability=0.50, target_price=85000),
    "Bull": Scenario(name="Bull", probability=0.25, target_price=120000),
}


class TestScenarioMath(unittest.TestCase):
    def test_expected_return_matches_template_example(self):
        self.assertAlmostEqual(calc.expected_return(SC, 58000), 0.457, places=3)

    def test_risk_reward_base_and_bull(self):
        self.assertAlmostEqual(calc.risk_reward(SC, 58000, "base"), 2.70, places=2)
        self.assertAlmostEqual(calc.risk_reward(SC, 58000, "bull"), (120000 - 58000) / (58000 - 48000), places=6)

    def test_probability_sum_enforced(self):
        bad = dict(SC)
        bad["Bull"] = Scenario(name="Bull", probability=0.5, target_price=120000)
        with self.assertRaises(calc.CalcError):
            calc.expected_return(bad, 58000)

    def test_risk_reward_none_when_no_downside(self):
        sc = dict(SC)
        sc["Bear"] = Scenario(name="Bear", probability=0.25, target_price=60000)
        self.assertIsNone(calc.risk_reward(sc, 58000))


class TestWeight(unittest.TestCase):
    def test_suggested_weight_formula(self):
        # 5% × f_conf(M)=1.0 × clamp(2.7/2, .5, 1.5)=1.35
        self.assertAlmostEqual(calc.suggested_weight("M", 2.70), 0.0675, places=4)
        self.assertAlmostEqual(calc.suggested_weight("H", 10.0), 0.05 * 1.5 * 1.5, places=6)
        self.assertAlmostEqual(calc.suggested_weight("L", 0.1), 0.05 * 0.5 * 0.5, places=6)

    def test_weight_cap(self):
        self.assertLessEqual(calc.suggested_weight("H", 100.0, Settings(w_max=0.10)), 0.10)

    def test_weight_band_is_min_of_abs_and_rel(self):
        self.assertAlmostEqual(calc.weight_band(0.05), 0.015)
        self.assertAlmostEqual(calc.weight_band(0.20), 0.03)

    def test_weight_state(self):
        self.assertEqual(calc.weight_state(0.072, 0.05), WeightState.과다)
        self.assertEqual(calc.weight_state(0.06, 0.05), WeightState.적정)
        self.assertEqual(calc.weight_state(0.03, 0.05), WeightState.과소)
        self.assertIsNone(calc.weight_state(None, 0.05))


class TestDecomposition(unittest.TestCase):
    def test_template_example(self):
        d = calc.decompose_return(58000, 76000, 3200, 3392)
        self.assertAlmostEqual(d.r_total, 0.3103, places=4)
        self.assertAlmostEqual(d.d_metric, 0.06, places=4)
        self.assertAlmostEqual(d.d_multiple, 0.236, places=3)
        self.assertTrue(d.consistent)
        self.assertIn("기대감", d.interpretation)

    def test_multiple_compression(self):
        d = calc.decompose_return(100, 90, 10, 12)
        self.assertIn("멀티플 압축", d.interpretation)

    def test_no_metric(self):
        d = calc.decompose_return(100, 120, None, None)
        self.assertFalse(d.consistent)


class TestMisc(unittest.TestCase):
    def test_valuation_state(self):
        self.assertEqual(calc.valuation_state(20), ValuationState.저평가)
        self.assertEqual(calc.valuation_state(50), ValuationState.적정)
        self.assertEqual(calc.valuation_state(82), ValuationState.고평가)

    def test_drawdown(self):
        self.assertAlmostEqual(calc.drawdown_from_peak(100, 80), 0.2)
        self.assertEqual(calc.drawdown_from_peak(100, 110), 0.0)

    def test_hhi_and_edge(self):
        self.assertAlmostEqual(calc.hhi([0.5, 0.5]), 0.5)
        self.assertAlmostEqual(calc.relative_edge(0.125, [0.03, 0.10]), 0.025)
        self.assertIsNone(calc.relative_edge(0.1, []))

    def test_gain_driven_share(self):
        self.assertAlmostEqual(calc.gain_driven_weight_share(0.05, 0.072, 0.31), 0.0155 / 0.022, places=3)
        self.assertIsNone(calc.gain_driven_weight_share(0.05, 0.04, 0.31))


if __name__ == "__main__":
    unittest.main()
