"""스키마 2.0 (2026-10-28) — 노드 번호 개편: 「밸류에이션 & 시나리오」(옛 A3) → A3 밸류에이션 + A4 시나리오, 옛 A4~A8 → A5~A9.
옛 파일은 읽을 때 변환되고(링크 · 수식 · meta 키 포함), 이미 2.0 이면 손대지 않는다. B 맵은 id 는 그대로, A 링크만 옮긴다."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from helpers import D0, build_all, build_new_map  # noqa: F401  (sys.path 설정)
from kstock.models import load_map, load_map_file
from kstock.models.migrate import ID_MAP_2_0, SCHEMA_VERSION, migrate_map_data, remap_id, remap_text

EXAMPLES = Path(__file__).resolve().parent / "fixtures" / "maps_v1"     # v1.0 스키마로 저장된 옛 맵


class TestRemap(unittest.TestCase):
    def test_remap_id(self):
        cases = {"A3": "A3", "A3.7": "A3.1", "A3.7.2": "A3.1.2", "A3.8": "A3.2", "A3.9": "A3.3", "A3.10": "A4.7", "A3.11": "A3.4", "A3.6.4": "A3.5.4",
                 "A3.0": "A4.1", "A3.1": "A4.2", "A3.2": "A4.3", "A3.3": "A4.4", "A3.4": "A4.5", "A3.5": "A4.6",
                 "A4": "A5", "A4.4.1": "A5.4.1", "A4.6.2": "A5.6.2", "A5.1.3": "A6.1.3", "A6.5": "A7.5", "A7.2": "A8.2", "A8.4.1": "A9.4.1",
                 "A0.4": "A0.4", "A1.1": "A1.1", "A2.4": "A2.4", "B3.1": "B3.1", "A005930": "A005930"}
        for old, new in cases.items():
            self.assertEqual(remap_id(old), new, old)
        self.assertEqual(len(ID_MAP_2_0), 17)

    def test_remap_text(self):
        self.assertEqual(remap_text("driver[A1.1].current < 10 ; A3.1 → A4.4 ; A6.5 판단 ; 종목 A005930 ; 3Q26A"),
                         "driver[A1.1].current < 10 ; A4.2 → A5.4 ; A7.5 판단 ; 종목 A005930 ; 3Q26A")


class TestMigrateMap(unittest.TestCase):
    def test_old_example_map_is_converted_on_load(self):
        raw = json.loads((EXAMPLES / "gaon_A.json").read_text(encoding="utf-8"))
        self.assertEqual(raw.get("schema_version", "1.0"), "1.0")
        m = load_map(raw)
        self.assertEqual(m.schema_version, SCHEMA_VERSION)
        self.assertEqual(m["A3"].label, "밸류에이션")
        self.assertEqual(m["A4"].label, "시나리오")                           # 새 묶음이 생긴다
        self.assertEqual([m[x].label for x in ("A4.2", "A4.3", "A4.4")], ["Bear", "Base", "Bull"])
        self.assertEqual(m["A4.2"].parent, "A4")
        self.assertEqual(m["A5.4.1"].rule.condition, "driver[A1.2].current < 3500")
        self.assertEqual(m.decision.value, "BUY")                              # A7.5
        self.assertEqual([n.id for n in m.children("A0")], ["A0.1", "A0.2", "A0.3", "A0.4", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9"])
        self.assertEqual(set(m.scenarios()), {"Bear", "Base", "Bull"})
        self.assertAlmostEqual(m.value("A4.5"), 0.457, places=3)
        self.assertEqual(len(m.kill_rules()), 1)
        self.assertFalse([k for k in m.nodes if k in ("A3.7", "A3.8", "A3.9", "A3.10", "A3.11")])
        # 다시 읽어도 그대로 (2.0 은 손대지 않는다)
        again, changed = migrate_map_data(json.loads(m.to_json()))
        self.assertFalse(changed)

    def test_links_formula_meta_and_b_map(self):
        raw = json.loads((EXAMPLES / "gaon_A.json").read_text(encoding="utf-8"))
        raw["nodes"]["A3.4"]["links"] = ["A3.1", "A3.2"]
        raw["nodes"]["A3.4"]["formula"] = "sum over A3.1~A3.3"
        raw["meta"] = {"draft": {"suggestions": {"A6.2": [{"field": "value"}], "A1.1": [{"field": "my_estimate"}]},
                                 "rule_events": [{"rule": "A4.6.1", "driver": "A1.1"}], "note": "A8.4.1 에서"}}
        data, changed = migrate_map_data(raw)
        self.assertTrue(changed)
        n = data["nodes"]["A4.5"]
        self.assertEqual((n["links"], n["formula"]), (["A4.2", "A4.3"], "sum over A4.2~A4.4"))
        self.assertEqual(set(data["meta"]["draft"]["suggestions"]), {"A7.2", "A1.1"})
        self.assertEqual(data["meta"]["draft"]["rule_events"][0]["rule"], "A5.6.1")
        self.assertEqual(data["meta"]["draft"]["note"], "A9.4.1 에서")
        b = json.loads((EXAMPLES / "gaon_B.json").read_text(encoding="utf-8"))
        b["nodes"]["B1.3"]["links"] = ["A3.2"]
        bd, changed = migrate_map_data(b)
        self.assertTrue(changed)
        self.assertEqual(bd["nodes"]["B1.3"]["links"], ["A4.3"])
        self.assertIn("B3.1", bd["nodes"])
        self.assertEqual(load_map(bd).schema_version, SCHEMA_VERSION)

    def test_new_skeleton_is_2_0_and_cli_migrate(self):
        from kstock.cli import main as cli_main

        m = build_new_map()
        self.assertEqual(m.schema_version, SCHEMA_VERSION)
        self.assertEqual([n.id for n in m.children("A4")], ["A4.1", "A4.2", "A4.3", "A4.4", "A4.5", "A4.6", "A4.7"])
        self.assertEqual([n.id for n in m.children("A3")][:5], ["A3.1", "A3.2", "A3.3", "A3.4", "A3.5"])
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "old_A.json"
            src.write_text((EXAMPLES / "gaon_A.json").read_text(encoding="utf-8"), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli_main(["migrate", str(src), "--render"])
            self.assertEqual(rc, 0)
            self.assertIn("1.0 → 2.0", buf.getvalue())
            self.assertIn("변환 1", buf.getvalue())
            saved = json.loads(src.read_text(encoding="utf-8"))
            self.assertEqual(saved["schema_version"], SCHEMA_VERSION)
            self.assertIn("A4.2", saved["nodes"])
            self.assertTrue(src.with_suffix(".html").exists())
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli_main(["migrate", str(src)])
            self.assertIn("이미 2.0 1", buf.getvalue())
            self.assertEqual(load_map_file(src)["A7.5"].value, "BUY")


if __name__ == "__main__":
    unittest.main()
