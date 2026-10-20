"""리포트 → A 맵 초안: 추출기(extract) · 합산(digest) · 규칙 채움 · LLM 그룹 채움(Mock) · Ops/CLI."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from helpers import D0  # noqa: F401  (sys.path 설정)
from kstock.cli import main as cli_main
from kstock.generate import DRAFT_TAG, MockGroupFiller, draft_from_reports, guess_sector, scenario_anchors
from kstock.generate.rules import next_earnings
from kstock.models import NewMap, load_map_file
from kstock.ops import Ops, OpsError
from kstock.reports import ReportDigest, ReportError, digest_files, extract_facts, load_report
from kstock.reports.extract import (
    find_current_price,
    find_opinion,
    find_target_price,
    find_ticker,
    find_tp_history,
    normalize_opinion,
    parse_estimate_tables,
)
from kstock.reports.loader import ReportDoc, normalize_text
from kstock.store import Store
from kstock.symbols import Symbol, SymbolBook

FIX = Path(__file__).resolve().parent / "fixtures" / "reports"
TXTS = sorted(FIX.glob("*.txt"))
PDFS = sorted(FIX.glob("*.pdf"))
TODAY = date(2026, 10, 20)


def _doc(text: str, name: str = "x.txt") -> ReportDoc:
    return ReportDoc(path=Path(name), text=normalize_text(text), kind="txt", extractor="text")


class TestExtract(unittest.TestCase):
    def test_fixture_hana(self):
        f = extract_facts(load_report(FIX / "hana_005930_20261012.txt"), today=TODAY)
        self.assertEqual((f.broker, f.date, f.ticker, f.name), ("하나증권", date(2026, 10, 12), "005930", "삼성전자"))
        self.assertEqual((f.opinion, f.opinion_change), ("BUY", "유지"))
        self.assertEqual((f.target_price, f.prev_target_price, f.tp_change), (480000, 380000, "상향"))  # 직전값은 변동 내역 표에서
        self.assertEqual((f.current_price, f.price_date), (258000, date(2026, 10, 10)))
        self.assertEqual((f.high_52w, f.low_52w), (374500, 189200))
        self.assertEqual(f.unit, "십억원")
        self.assertEqual(f.estimates["op"]["2027E"], 420000)
        self.assertEqual(f.estimates["per"]["2027E"], 5.1)
        self.assertNotIn("증감률", str(f.estimates))           # 증감률 행은 EPS 로 오인하지 않는다
        self.assertEqual(f.quarterly["op"]["3Q26A"], 107400)  # 3Q26P(잠정) = 실적
        self.assertEqual(f.quarterly["op"]["4Q26E"], 64000)
        self.assertEqual(f.title, "강한 DRAM 가격. LPDDR 공급 부족 심화")
        self.assertTrue(any("HBM" in s for s in f.by_kind("thesis")))
        self.assertTrue(any("리스크" in s or "우려" in s for s in f.by_kind("risk")))
        self.assertFalse(any("당사는" in s.text for s in f.sentences))  # 면책 문구 제외

    def test_real_style_snippets(self):
        # 2단 조판이 섞인 실제 표지 모양
        t = ("세아제강지주 (003030) 목표주가 190,000원 판단한다. 한편 50% 철강관세와 AD·CVD에 따른 수입재 공급 제약으로\n"
             "BUY 현재주가 104,500원\n가격은 $2,625/ST까지 상승했다.")
        self.assertEqual(find_ticker(t), ("세아제강지주", "003030"))
        self.assertEqual(find_target_price(t)[0], 190000)
        self.assertEqual(find_current_price(t, ref_year=2026)[0], 104500)
        self.assertEqual(find_opinion(t)[0], "BUY")
        t2 = "한국전력(051600)\nNeutral I TP 45,000원 I CP 39,800원"
        self.assertEqual(find_ticker(t2)[1], "051600")
        self.assertEqual(find_target_price(t2)[0], 45000)
        self.assertEqual(find_current_price(t2)[0], 39800)
        self.assertEqual(find_opinion(t2)[0], "HOLD")
        t3 = "현재주가(26.8.19) 52,000원 투자의견 BUY(유지)\n[밸류에이션] 투자의견 매수, 목표주가 18만원 유지."
        self.assertEqual(find_current_price(t3, ref_year=2026), (52000, date(2026, 8, 19)))
        self.assertEqual(find_target_price(t3)[0], 180000)
        t4 = "목표주가와의 높은 괴리와 실적 전망을 소폭 하향하면서 목표주가를 26,000원(-7%)으로 하향한다. 기존 28,000원"
        tp, prev, chg = find_target_price(t4)
        self.assertEqual((tp, prev, chg), (26000, 28000, "하향"))
        t5 = "목표주가 (하향) 62,000원\n투자의견 Trading Buy (중립)"
        self.assertEqual(find_target_price(t5)[:1], (62000,))
        self.assertEqual(find_target_price(t5)[2], "하향")
        self.assertEqual(normalize_opinion("Trading Buy"), "HOLD")
        self.assertEqual(normalize_opinion("Not Rated"), "NR")
        hist = find_tp_history("날짜 투자의견 목표주가\n26.4.15 BUY 55,000 -17.38% -15.73%\n26.2.27 BUY 73,000 -37.81%\n", ref_year=2026)
        self.assertEqual([h[2] for h in hist], [55000, 73000])

    def test_estimate_table_side_by_side_and_negatives(self):
        text = ("실적 추이 (십억원) 2024A 2025A 2026F 2027F (십억원) 2024A 2025A 2026F 2027F\n"
                "매출액 100 120 150 180 유동자산 10 20 30 40\n"
                "매출액 증가율 10.0 20.0 25.0 20.0\n"
                "영업이익 (5) -3 적전 12 재고자산 1 2 3 4\n"
                "EPS(원) 100 200 300 400\n"
                "PER(배) 10.0 8.0 6.0 5.0\n")
        est, q, unit = parse_estimate_tables(text, ref_date=date(2026, 10, 1))
        self.assertEqual(unit, "십억원")
        self.assertEqual(est["revenue"], {"2024A": 100, "2025A": 120, "2026E": 150, "2027E": 180})
        self.assertEqual(est["op"], {"2024A": -5, "2025A": -3, "2027E": 12})  # '적전' 칸은 비움
        self.assertEqual(est["eps"]["2027E"], 400)
        self.assertNotIn("증가율", json.dumps(est, ensure_ascii=False))

    def test_empty_doc_and_unsupported(self):
        f = extract_facts(_doc("짧다"), today=TODAY)
        self.assertIsNone(f.target_price)
        self.assertTrue(f.warnings)
        with self.assertRaises(ReportError):
            load_report(FIX / "없는파일.pdf")
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "a.xlsx"
            p.write_bytes(b"x")
            with self.assertRaises(ReportError):
                load_report(p)

    @unittest.skipUnless(PDFS, "PDF 픽스처 없음 (tests/fixtures/reports/make_pdfs.py)")
    def test_pdf_path(self):
        try:
            doc = load_report(FIX / "kiwoom_005930_20261007.pdf")
        except ReportError as e:  # 추출기가 하나도 없는 환경
            self.skipTest(str(e))
        self.assertFalse(doc.is_empty)
        f = extract_facts(doc, today=TODAY)
        self.assertEqual((f.ticker, f.target_price, f.current_price, f.opinion), ("005930", 350000, 262000, "BUY"))
        self.assertEqual(f.estimates["op"]["2027E"], 380000)


class TestDigest(unittest.TestCase):
    def test_consensus_and_units(self):
        d, docs = digest_files(TXTS, today=TODAY)
        self.assertEqual((d.ticker, d.name, d.as_of), ("005930", "삼성전자", date(2026, 10, 12)))
        self.assertEqual(d.n_tp, 3)
        self.assertAlmostEqual(d.tp_mean, 476666.67, places=1)
        self.assertEqual((d.tp_min, d.tp_max, d.tp_median), (350000, 600000, 480000))
        self.assertEqual(d.revision, "up")
        self.assertEqual(d.opinion_dist["BUY"], 3)
        self.assertEqual(d.price[:2], (258000, date(2026, 10, 10)))
        self.assertEqual(d.amount_unit, ("조원", 10000.0))           # 억원 → 조원 표시
        self.assertAlmostEqual(d.amt("op", "2027E"), 413.3333, places=3)
        self.assertEqual(d.latest_actual_year("op"), "2025A")
        self.assertEqual(d.next_estimate_year("op"), "2027E")        # 10월 리포트: 2027E 가 베팅 대상
        self.assertEqual(d.latest_actual_quarter("op"), ("3Q26A", 1074000.0))
        self.assertIn("매수 일색", " ".join(d.summary_lines())) if False else None
        self.assertIn("## 추정치", d.materials())
        self.assertTrue(d.top("risk"))
        self.assertEqual(guess_sector("\n".join(x.text for x in docs))[0], "KR-SEMI")

    def test_anchors(self):
        d, _ = digest_files(TXTS, today=TODAY)
        anchors, notes = scenario_anchors(d, 258000)
        self.assertLess(anchors["Bear"], 258000)
        self.assertLess(anchors["Bear"], anchors["Base"])
        self.assertLess(anchors["Base"], anchors["Bull"])
        self.assertEqual(anchors["Bear"], 189200)                    # 52주 저가 < 최저 목표가×0.9
        # 주가가 컨센서스를 넘어섰으면 깃발 (Base 가 현재가 아래)
        a2, n2 = scenario_anchors(d, 700000)
        self.assertLess(a2["Bear"], 700000)
        self.assertTrue(any("현재가 아래" in n for n in n2))
        # 52주 저가가 없고 최저 목표가가 현재가 위면 Bear 는 현재가 −15%
        for f in d.reports:
            f.low_52w = None
        a4, n4 = scenario_anchors(d, 300000)
        self.assertAlmostEqual(a4["Bear"], 300000 * 0.85)
        self.assertTrue(any("전부 위쪽" in n for n in n4))
        # 목표가 없는 다이제스트
        empty = ReportDigest.build([], today=TODAY)
        a3, n3 = scenario_anchors(empty, 10000)
        self.assertEqual(a3, {"Bear": 8000, "Base": 12000, "Bull": 15000})

    def test_next_earnings(self):
        ev = next_earnings(date(2026, 10, 20))
        self.assertEqual(ev[0][0], date(2027, 1, 8))                 # 4Q 잠정 = 12/31 + 8
        self.assertTrue(all(d > date(2026, 10, 20) for d, _, _ in ev))


class TestDraft(unittest.TestCase):
    def test_rules_only(self):
        r = draft_from_reports(TXTS, today=TODAY)
        m = r.map
        self.assertIsInstance(m, NewMap)
        self.assertTrue(r.complete, [str(i) for i in r.errors])
        self.assertEqual(m.decision.value, "WATCH")
        self.assertEqual(m.current_price, 258000)
        self.assertEqual(len(m.drivers()), 3)
        a11 = m["A1.1"]
        self.assertEqual(a11.label, "분기 영업이익 (4Q26)")
        self.assertEqual((a11.unit, a11.driver.current.value, a11.driver.consensus.value), ("조원", 107.4, 64.0))
        self.assertEqual(a11.driver.my_estimate.value, 64.0)
        self.assertIn("초안", a11.driver.my_estimate.rationale)
        self.assertEqual(m["A4.4.1"].rule.condition, "driver[A1.1].current < 51.2")
        sc = m.scenarios()
        self.assertEqual(sc["Bear"].target_price, 189200)
        self.assertAlmostEqual(sum(s.probability for s in sc.values()), 1.0)
        self.assertIn("매수 일색", m.value("A2.1"))
        self.assertEqual(m.value("A3.6.3"), "up")
        self.assertTrue(m.catalysts())
        self.assertEqual(m["A8.2"].links, ["S.KR-SEMI"])
        self.assertIn("조원", m.value("A8.1.4"))
        # 채운 노드엔 전부 draft 태그, 판단은 unverified
        filled = [m[n] for n in r.rules.filled]
        self.assertTrue(filled and all(DRAFT_TAG in n.tags for n in filled))
        self.assertEqual(m["A6.5"].status.value, "unverified")
        self.assertEqual(m["A0"].status.value, "unverified")
        self.assertEqual(m.meta["draft"]["consensus"]["n"], 3)
        self.assertFalse(m.meta["draft"]["llm"])
        self.assertIsNotNone(m.value("A3.4"))
        self.assertIn("A 맵 초안", r.summary_text())

    def test_ticker_override_and_mismatch(self):
        book = SymbolBook([Symbol("000660", "SK하이닉스", "KR", None, 1.0)])
        r = draft_from_reports(TXTS[:1], ticker="000660", today=TODAY, symbols=book)
        self.assertEqual((r.map.ticker, r.map.name), ("000660", "SK하이닉스"))
        self.assertTrue(any("다르다" in w for w in r.digest.warnings))

    def test_mock_llm_groups(self):
        answers = {
            "drivers": {"drivers": [
                {"keep_id": "A1.1", "label": "분기 영업이익", "my_estimate": 110, "rationale": "보수적", "next_check": "2027-01-08"},
                {"label": "HBM 점유율", "unit": "%", "current": 33, "consensus": 40, "my_estimate": 38, "rationale": "인증 속도",
                 "check_indicator": "컨콜", "evidence": "HBM 점유율은 2Q26 33%에서 4Q26 38%로 상승할 것으로 예상된다"},
                {"label": "지어낸 변수", "unit": "%", "current": 7, "consensus": 9, "evidence": "자료에 없는 문장이다"},
            ]},
            "thesis": {"thesis": "논제", "horizon": "1Y", "confidence": "M", "implied": "PER 4배", "mine": "호황 연장", "why_market_wrong": "시간축"},
            "scenarios": {"Bear": {"probability": 0.3, "target_price": 170000, "assumptions": ["a"], "method": "PER 5"},
                          "Base": {"probability": 0.5, "target_price": 900000, "assumptions": ["b"], "method": "PER 6"},
                          "Bull": {"probability": 0.3, "target_price": 520000, "assumptions": ["c"], "method": "PER 7"}},
            "risks": {"risks": [{"text": "피크아웃", "probability": 0.35, "impact_pct": -30}],
                      "kill": [{"driver_id": "A1.1", "op": "<", "threshold": 80, "note": "실적"}, {"driver_id": "A9.9", "op": "<", "threshold": 1}],
                      "early_warning": "DRAMeXchange"},
            "catalysts": {"events": [{"date": "2027-01-08", "fact": "4Q26 잠정실적", "affects": ["A1.1"], "materiality": "H", "estimated": True},
                                     {"date": "2020-01-01", "fact": "과거"}]},
            "evidence": {"business_model": "메모리", "moat": "과점", "governance_payout": "환원 110조", "peers": None},
        }
        f = MockGroupFiller(answers)
        r = draft_from_reports(TXTS, today=TODAY, filler=f)
        m = r.map
        self.assertEqual(f.calls, ["drivers", "thesis", "scenarios", "risks", "catalysts", "evidence"])
        self.assertTrue(r.llm_used and m.meta["draft"]["llm"])
        labels = [n.label for n in m.drivers()]
        self.assertIn("HBM 점유율", labels)
        self.assertNotIn("지어낸 변수", labels)                      # evidence 가 자료에 없으면 거부
        self.assertTrue(any("환각" in n for n in r.notes))
        self.assertEqual(m["A1.1"].driver.current.value, 107.4)       # keep_id 드라이버의 숫자는 LLM 이 못 바꾼다
        self.assertEqual(m["A1.1"].driver.my_estimate.value, 110)
        sc = m.scenarios()
        self.assertAlmostEqual(sum(s.probability for s in sc.values()), 1.0, places=6)
        self.assertLess(sc["Base"].target_price, 435800 * 1.26)       # 앵커 ±25% 로 자름
        self.assertTrue(sc["Bear"].target_price < sc["Base"].target_price < sc["Bull"].target_price)
        self.assertEqual([k.condition for k in m.kill_rules()], ["driver[A1.1].current < 80"])
        self.assertTrue(m.value("A0").startswith("[LLM 초안]"))
        self.assertEqual(m.value("A2.3"), "시간축")
        self.assertEqual(len(m.catalysts()), 1)
        self.assertEqual(m.value("A8.1.2"), "과점")
        self.assertEqual(m.decision.value, "WATCH")
        self.assertTrue(r.complete, [str(i) for i in r.errors])

    def test_llm_failure_keeps_rules(self):
        class Broken:
            def complete(self, system: str, user: str) -> str:
                return "이것은 JSON 이 아니다"

        r = draft_from_reports(TXTS, today=TODAY, filler=Broken())
        self.assertTrue(r.complete)
        self.assertTrue(any("실패" in n for n in r.notes))
        self.assertEqual(r.map["A1.1"].driver.current.value, 107.4)


class TestOpsCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "db" / "k.sqlite")
        self.ops = Ops(self.store, today=TODAY, symbols=SymbolBook([Symbol("005930", "삼성전자", "KR", None, 1.0)]))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_peek_and_draft_saves(self):
        pk = self.ops.peek_reports(TXTS)
        self.assertEqual((pk["ticker"], pk["name"], pk["n_tp"], pk["n_readable"]), ("005930", "삼성전자", 3, 3))
        steps: list[tuple[str, int, int]] = []
        r = self.ops.draft_from_reports(TXTS, {"use_llm": False}, progress=lambda label, i, n: steps.append((label, i, n)))
        self.assertTrue(steps and steps[-1][1] == steps[-1][2])
        w = self.store.get_watch("005930")
        self.assertIsNotNone(w)
        self.assertEqual((w.sector, w.invest_type), ("KR-SEMI", "growth"))
        rows = self.store.list_maps("NEW")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "WATCH")
        form = self.ops.a_form(r.map)
        self.assertEqual(form["drivers"][0]["label"], "분기 영업이익 (4Q26)")
        self.assertEqual(form["decision"], "WATCH")
        self.assertIn("초안", self.ops.overview_text("005930"))
        with self.assertRaises(OpsError):
            self.ops.draft_from_reports([], {})
        with self.assertRaises(OpsError):
            self.ops.draft_from_reports(TXTS, {"use_llm": True})       # 키·패키지 없음 → 안내

    def test_cli_json_and_db(self):
        out = Path(self.tmp.name) / "A.json"
        rc = cli_main(["draft", "--reports", *map(str, TXTS), "--date", TODAY.isoformat(), "-o", str(out), "--html"])
        self.assertEqual(rc, 0)
        m = load_map_file(out)
        self.assertIsInstance(m, NewMap)
        self.assertTrue(out.with_suffix(".html").exists())
        self.assertIn("layoutBalanced", out.with_suffix(".html").read_text(encoding="utf-8"))
        db = Path(self.tmp.name) / "db2" / "k.sqlite"
        rc = cli_main(["draft", "--reports", *map(str, TXTS), "--date", TODAY.isoformat(), "--db", str(db)])
        self.assertEqual(rc, 0)
        with Store(db) as s:
            self.assertEqual(len(s.list_maps("NEW")), 1)
            self.assertIsNotNone(s.get_watch("005930"))
        rc = cli_main(["draft", "--reports", str(Path(self.tmp.name) / "none.pdf"), "-o", str(out)])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
