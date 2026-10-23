"""리포트 기반 제안값 (설계 v4 §6 / V13): 생성 · 미리 채움 옵션 · 편집 폼 수락 기록 · 수락률/수정률."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from helpers import D0  # noqa: F401  (sys.path 설정)
from kstock.generate.draft import draft_from_reports
from kstock.generate.edits import EDIT_SOURCE, apply_edit_ops
from kstock.generate.suggest import (
    ACCEPT_SOURCE,
    PREFILL_TAG,
    apply_prefill,
    build_suggestions,
    stats_text,
    suggestion_stats,
)
from kstock.models import (
    Claim,
    ClaimSet,
    EvidenceSpan,
    Issue,
    IssueDisplay,
    IssueLevel,
    IssueSet,
    load_map_file,
)
from kstock.render import to_html
from kstock.templates import new_map_skeleton

FIX = sorted((Path(__file__).resolve().parent / "fixtures" / "reports").glob("*.txt"))
TODAY = date(2026, 10, 23)


def _draft(**kw):
    return draft_from_reports(FIX, ticker="005930", name="삼성전자", today=TODAY, claims="rules", fetch_price=False, **kw)


class TestSuggestions(unittest.TestCase):
    def test_build_from_fixture_reports(self):
        r = _draft()
        sugg = r.map.meta["draft"]["suggestions"]
        kinds = {nid: [s["kind"] for s in rows] for nid, rows in sugg.items()}
        self.assertEqual(kinds["A1.1"], ["consensus_median"])
        self.assertEqual(kinds["A6.2"], ["price_band"])
        self.assertNotIn("A2.3", kinds)                      # 열거형 노드엔 글 제안을 붙이지 않는다
        s = sugg["A1.2"][0]
        self.assertEqual(s["field"], "driver.my_estimate.value")
        self.assertEqual(s["value"], r.map["A1.2"].driver.consensus.value)   # 사용자 결정: 내 추정 제안의 기본값 = 컨센
        self.assertIn("컨센", s["label"])
        self.assertIn("KB증권", s["label"])                   # 증권사별 값이 label 에
        self.assertTrue(s["report_ids"])
        band = sugg["A6.2"][0]
        lo, hi = band["value"]["price_range"].split("~")
        self.assertLess(float(lo.replace(",", "")), float(hi.replace(",", "")))
        self.assertEqual(float(hi.replace(",", "")), r.map.current_price)
        self.assertIsNone(band["value"]["tranches"])        # 분할 횟수는 사람 몫
        self.assertTrue(any("제안값" in n for n in r.notes))
        # 값이 있는 칸엔 제안이 없다 — 미리 채운 뒤 다시 만들면 비어 있어야 한다
        for n in r.map.drivers():
            self.assertIsNone(n.driver.my_estimate.value)
        self.assertNotIn(PREFILL_TAG, r.map["A1.1"].tags)

    def test_prefill_option_and_edit_promotes_to_user(self):
        r = _draft(prefill_suggestions=True)
        m = r.map
        n = m["A1.1"]
        self.assertEqual(n.driver.my_estimate.value, n.driver.consensus.value)
        self.assertIn(PREFILL_TAG, n.tags)
        self.assertIn("pending", n.tags)
        self.assertIn("리포트 제안(미확정)", n.driver.my_estimate.source.name)
        self.assertTrue(m.meta["draft"]["suggestions"]["A1.1"][0]["prefilled"])
        self.assertEqual(set(m.meta["draft"]["prefilled"]), {"A1.1", "A1.2", "A1.3", "A6.2"})
        self.assertTrue(any("미리 채움" in x for x in r.notes))
        # 사람이 편집 폼에서 저장하면 suggested → user
        apply_edit_ops(m, [{"op": "set", "id": "A1.1", "fields": {"driver": {**n.driver.model_dump(mode="json"), "my_estimate": {"value": 70.0}}}}], today=TODAY)
        self.assertNotIn(PREFILL_TAG, m["A1.1"].tags)
        self.assertIn("user", m["A1.1"].tags)
        # 다시 만들어도 이미 값이 있는 칸엔 제안이 안 생기고, 미리 채운 것은 두 번 채우지 않는다
        self.assertEqual(apply_prefill(m, today=TODAY), [])

    def test_accept_records_source_and_stats(self):
        r = _draft()
        m = r.map
        s = m.meta["draft"]["suggestions"]["A1.2"][0]
        drv = {**m["A1.2"].driver.model_dump(mode="json"), "my_estimate": {"value": s["value"]}}
        res = apply_edit_ops(m, [{"op": "set", "id": "A1.2", "fields": {"driver": drv, "_accepted": [{"field": s["field"], "kind": s["kind"], "report_ids": s["report_ids"], "value": s["value"]}]}}],
                             today=TODAY)
        self.assertIn("제안 수락", res.applied[0])
        h = m["A1.2"].history[-1]
        self.assertTrue(h.source.name.startswith(ACCEPT_SOURCE))
        self.assertIn("컨센서스", h.source.name)
        self.assertIn(s["report_ids"][0], h.source.name)
        self.assertEqual(m.meta["draft"]["suggestions"]["A1.2"][0]["accepted_at"], TODAY.isoformat())
        self.assertEqual(m.meta["draft"]["suggestion_log"][0]["node"], "A1.2")
        st = suggestion_stats(m)
        self.assertEqual((st["total"], st["accepted"], st["modified_after_accept"]), (4, 1, 0))
        self.assertAlmostEqual(st["accept_rate"], 0.25)
        self.assertEqual(st["modify_rate"], 0.0)
        self.assertIn("수락 1", stats_text(m))
        # _accepted 는 노드에 저장되지 않는다
        self.assertNotIn("_accepted", m["A1.2"].model_dump())
        # 수락한 값을 나중에 다시 고치면 '수락 뒤 수정' 으로 센다
        apply_edit_ops(m, [{"op": "set", "id": "A1.2", "fields": {"driver": {**m["A1.2"].driver.model_dump(mode="json"), "my_estimate": {"value": 800.0}}}}], today=TODAY)
        self.assertTrue(m["A1.2"].history[-1].source.name.startswith(EDIT_SOURCE))
        self.assertEqual(suggestion_stats(m)["modify_rate"], 1.0)
        # 받아들였다가 폼에서 값을 바꾸면(_accepted 없음) 보통 편집이다
        s3 = m.meta["draft"]["suggestions"]["A1.3"][0]
        apply_edit_ops(m, [{"op": "set", "id": "A1.3", "fields": {"driver": {**m["A1.3"].driver.model_dump(mode="json"), "my_estimate": {"value": s3["value"] + 1}}}}], today=TODAY)
        self.assertTrue(m["A1.3"].history[-1].source.name.startswith(EDIT_SOURCE))
        self.assertNotIn("accepted_at", m.meta["draft"]["suggestions"]["A1.3"][0])

    def test_minority_opinion_suggestion(self):
        m = new_map_skeleton("005930", "삼성전자", "growth", TODAY)
        sp = EvidenceSpan.make("hk_1", 3, 0, 20, "MX 적자폭이 축소된 것으로 파악한다")
        c1 = Claim(claim_id="c1", report_id="hk_1", broker="DS", proposition="MX 적자폭이 축소됐다", direction="positive", driver_id="mobile_demand",
                   span_ids=[sp.span_id])
        c2 = Claim(claim_id="c2", report_id="hk_2", broker="IBK", proposition="MX 적자가 커졌다", direction="negative", driver_id="mobile_demand")
        c3 = Claim(claim_id="c3", report_id="hk_3", broker="유진", proposition="MX 2.0조원 적자", direction="negative", driver_id="mobile_demand")
        css = [ClaimSet(report_id="hk_1", broker="DS", claims=[c1], spans=[sp]), ClaimSet(report_id="hk_2", broker="IBK", claims=[c2]),
               ClaimSet(report_id="hk_3", broker="유진", claims=[c3])]
        t = Issue(issue_id="mobile_demand", level=IssueLevel.theme, driver_id="mobile_demand", driver_label="모바일·세트 수요", claim_ids=["c1", "c2", "c3"],
                  directions={"DS": "positive", "IBK": "negative", "유진": "negative"}, consensus_direction="negative", display=IssueDisplay.disagreement)
        iss = IssueSet(ticker="005930", issues=[t])
        m.meta["draft"] = {"claims": {"hk_1": {"broker": "DS"}, "hk_2": {"broker": "IBK"}, "hk_3": {"broker": "유진"}}}
        sugg = build_suggestions(m, issue_set=iss, claimsets=css)
        rows = sugg["A2.2"]
        self.assertEqual(len(rows), 1)                        # 소수 = 다수 방향(↓) 과 다른 DS 하나
        self.assertIn("DS↑", rows[0]["label"])
        self.assertIn("IBK↓", rows[0]["label"])
        self.assertIn("소수 의견", rows[0]["value"])
        self.assertIn("MX 적자폭이 축소됐다", rows[0]["value"])
        self.assertEqual(rows[0]["report_ids"], ["hk_1"])
        # 공통 견해 테마엔 제안이 없다
        t.display = IssueDisplay.common
        self.assertNotIn("A2.2", build_suggestions(m, issue_set=iss, claimsets=css))

    def test_html_carries_suggestions_and_cli(self):
        from kstock.cli import main

        r = _draft()
        html = to_html(r.map)
        self.assertIn('"suggest": [{"field": "driver.my_estimate.value"', html)
        self.assertIn("채우기", html)
        with tempfile.TemporaryDirectory() as tmp:
            p = r.map.save(Path(tmp) / "A.json")
            m2 = load_map_file(p)
            self.assertEqual(len(m2.meta["draft"]["suggestions"]), 4)
            self.assertEqual(main(["eval-suggestions", str(p)]), 0)


if __name__ == "__main__":
    unittest.main()
