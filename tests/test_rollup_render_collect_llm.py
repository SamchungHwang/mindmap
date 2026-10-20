import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from helpers import D0, D1, build_all, build_new_map  # noqa: F401
from kstock.collect import CsvPriceSource, RawDisclosure, RuleBasedClassifier, build_market_state
from kstock.collect.adapters import KrxPriceSource
from kstock.engine import rollup
from kstock.llm import MockFiller, fill_map, parse_json_block
from kstock.render import build_tree, to_html, to_markdown, to_markmap_html, to_mermaid
from kstock.templates import (
    hold_map_skeleton,
    macro_skeleton,
    new_map_skeleton,
    portfolio_skeleton,
    sector_skeleton,
)


class TestRollup(unittest.TestCase):
    def test_template_numbers(self):
        a, b, c, rep = build_all()
        c0 = c.value("C0")
        self.assertEqual(c0["n_holdings"], 1)
        self.assertAlmostEqual(c0["cash_weight"], 0.08)
        self.assertEqual(c.value("C3.1")["net_sell"], 22_000_000)
        self.assertAlmostEqual(c.value("C3.2"), 0.102)
        self.assertAlmostEqual(c.value("C5.4"), 0.072 * 0.125 + 0.08 * 0.03, places=3)
        self.assertEqual(c.value("C6.3"), {"disposition": 1})
        self.assertEqual(c.value("C5.3"), 1.0)  # kill criteria(fact) 가 커버리지에 포함

    def test_conflict_detection(self):
        a, b, c, rep = build_all()
        b2 = b.model_copy(deep=True)
        b2.map_id, b2.ticker = "B:999999", "999999"
        b2.meta["matrix_default"] = "add"
        b2["B6"].value = "add"
        b2.market.w_now = 0.02
        c = rollup([b, b2], cash=80_000_000, total_assets=1_000_000_000, today=D1)
        self.assertTrue(any("같은 섹터" in x for x in c.value("C3.4")))
        self.assertEqual(c.value("C0")["n_holdings"], 2)


class TestRender(unittest.TestCase):
    def test_markdown_structure_and_fold(self):
        a, b, c, rep = build_all()
        md = to_markdown(a)
        self.assertIn("# 가온로보틱스 투자 논제", md)
        self.assertIn("## 핵심 드라이버", md)
        self.assertIn("<!-- markmap: fold -->", md)
        self.assertIn("기대수익률: 45.7%", md)
        html = to_markmap_html(b)
        self.assertIn("markmap-autoloader", html)
        self.assertIn("🟡", html)
        # 다크 모드에서 markmap 기본 글자색(#333)이 남지 않도록 변수를 덮어쓴다
        self.assertIn("--markmap-text-color: var(--fg)", html)
        self.assertIn('prefers-color-scheme: dark', html)
        self.assertIn('id="theme-toggle"', html)

    def test_native_html_is_self_contained_and_bidirectional(self):
        a, b, c, rep = build_all()
        html = to_html(a)                       # 기본: 자체 렌더러, 양쪽 균형
        self.assertNotIn("cdn.jsdelivr.net", html)
        self.assertIn('"layout": "balanced"', html)
        self.assertIn("layoutBalanced", html)
        self.assertIn("layoutRadial", html)
        self.assertIn('<option value="radial">', html)
        tree = build_tree(a)
        self.assertEqual(tree["id"], "A0")
        ids = [c["id"] for c in tree["children"]]
        self.assertEqual(ids[0], "A0.*")          # 기본 정보 묶음
        self.assertIn("A1", ids)
        self.assertTrue(next(c for c in tree["children"] if c["id"] == "A8")["fold"])
        self.assertIn("<b>", tree["children"][0]["children"][0]["html"] + "<b>")  # 마크다운 → HTML 변환 경로
        for lay in ("radial", "right"):
            self.assertIn(f'"layout": "{lay}"', to_html(a, layout=lay))
        with self.assertRaises(ValueError):
            to_html(a, layout="diagonal")
        self.assertIn("논제 상태", to_html(b, summary=True))

    def test_summary_view(self):
        a, b, c, rep = build_all()
        md = to_markdown(b, summary=True)
        self.assertIn("논제 상태", md)
        self.assertNotIn("## 매수 당시 논제 복원", md)

    def test_mermaid(self):
        a, b, c, rep = build_all()
        mm = to_mermaid(a)
        self.assertTrue(mm.startswith("mindmap"))
        self.assertNotIn("(", mm.split("\n", 2)[2])  # 루트 이후 괄호 없음

    def test_skeletons_render(self):
        for m in (new_map_skeleton("1", "x", "value"), hold_map_skeleton("1", "x", "value"),
                  portfolio_skeleton(), macro_skeleton(), sector_skeleton("KR-SEMI", "반도체")):
            self.assertTrue(to_markdown(m).startswith("---"))


class TestCollect(unittest.TestCase):
    def test_csv_source_and_market_state(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "000000.csv"
            with p.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["date", "close", "volume", "eps_fwd", "band_percentile"])
                w.writerow(["2027-01-14", "75000", "40000", "3392", "80"])
                w.writerow(["2027-01-15", "76000", "50000", "3392", "82"])
            src = CsvPriceSource(d)
            q = src.get_quote("000000")
            self.assertEqual(q.close, 76000)
            self.assertEqual(src.get_quote("000000", date(2027, 1, 14)).close, 75000)
            self.assertEqual(len(src.get_history("000000", date(2027, 1, 1), date(2027, 1, 31))), 2)
            a, b, c, rep = build_all()
            mk = build_market_state(q, holding=b, total_assets=1_000_000_000)
            self.assertAlmostEqual(mk.w_now, 862 * 76000 / 1e9)
            self.assertEqual(mk.band_percentile, 82)

    def test_rule_classifier(self):
        a, b, c, rep = build_all()
        ev = RuleBasedClassifier().classify(
            RawDisclosure("000000", date(2027, 1, 20), "3분기 영업이익률 가이던스 하향", source="DART"), b)
        self.assertEqual(ev.direction.value, "약화")
        self.assertEqual(ev.materiality.value, "H")
        self.assertIn("B2.1.1", ev.affects)
        noise = RuleBasedClassifier().classify(RawDisclosure("000000", date(2027, 1, 20), "기업설명회 개최 안내"), b)
        self.assertTrue(noise.is_noise())

    def test_percentile_helper(self):
        self.assertEqual(KrxPriceSource.percentile([1, 2, 3, 4], 5), 100.0)
        self.assertEqual(KrxPriceSource.percentile([1, 2, 3, 4], 2.5), 50.0)


class TestLLM(unittest.TestCase):
    def test_mock_fill(self):
        m = new_map_skeleton("000000", "X", "growth", D0)
        filler = MockFiller({"A2.1": {"value": "PER 15배 가정", "confidence": "M", "source": {"name": "리포트"}},
                             "A3.6.3": "up", "A0.4": {"value": "58,000", "source": {"name": "KRX"}}})
        results = fill_map(m, filler, node_ids=["A2.1", "A3.6.3", "A0.4", "A3.6.2"])
        self.assertEqual(m.value("A2.1"), "PER 15배 가정")
        self.assertEqual(m.value("A3.6.3"), "up")
        self.assertEqual(m.value("A0.4"), 58000.0)
        self.assertEqual(m["A0.4"].status.value, "confirmed")
        self.assertIn("needs_data", m["A3.6.2"].tags)
        self.assertEqual(len(results), 4)

    def test_default_targets_skip_sub_schema_and_meta(self):
        m = hold_map_skeleton("000000", "X", "growth", D0)
        filler = MockFiller()
        fill_map(m, filler)
        self.assertNotIn("B0.5", filler.calls)
        self.assertNotIn("B3.1", filler.calls)
        self.assertIn("B0.1", filler.calls)

    def test_parse_json_block(self):
        self.assertEqual(parse_json_block('```json\n{"value": 1}\n```')["value"], 1)
        self.assertEqual(parse_json_block('설명 {"value": "x", "confidence": "H"} 끝')["confidence"], "H")


if __name__ == "__main__":
    unittest.main()
