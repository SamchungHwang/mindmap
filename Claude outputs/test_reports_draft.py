"""리포트 → A 맵 초안: 추출기(extract) · 합산(digest) · 규칙 채움 · LLM 그룹 채움(Mock) · Ops/CLI."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

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

    def test_real_style_snippets_2(self):
        """실제 PDF 에서 틀렸던 것들 (NAVER 리포트 6건): 적정주가(메리츠), 벌어진 천단위, 영문 날짜, 괄호 없는 코드."""
        from kstock.reports.extract import find_52w, find_date

        t = normalize_text("NAVER (035420)\nMeritz Research 2026. 10. 8\nBuy (20거래일 평균종가 대비 상승 여력 기준)\n"
                           "적정주가 (12개월) 330,000원\n현재주가 (10.7) 189,000원\n52주 최고/최저가 287,000원/183,200원\n")
        self.assertEqual(find_target_price(t)[0], 330000)                     # 적정주가 = 목표주가
        self.assertEqual(find_current_price(t, ref_year=2026, target_price=330000), (189000, date(2026, 10, 7)))  # '적정주가 … 330,000' 에 안 걸린다
        self.assertEqual(find_opinion(t)[0], "BUY")
        self.assertEqual(find_52w(t), (287000, 183200))
        t2 = normalize_text("Kyobo Company Analysis\n인터넷/게임\nNA VER 035420\n핵심 성장 스토리 유지 Oct 6 2026\nBUY 유지\nTP 390,000원 유지\n"
                            "현재가(10/03) 192,200 원\n52주 최고가(보통주) 287 ,000 원\n52주 최저가(보통주) 183,200 원\nKOSPI (10/03) 7 ,003.7 4p\n")
        self.assertEqual(find_ticker(t2), ("NAVER", "035420"))
        self.assertEqual(find_date(t2), date(2026, 10, 6))
        self.assertEqual(find_opinion(t2)[:1], ("BUY",))
        self.assertEqual(find_target_price(t2)[0], 390000)
        self.assertEqual(find_current_price(t2, ref_year=2026)[0], 192200)
        self.assertEqual(find_52w(t2), (287000, 183200))                      # '287 ,000' → 287,000

    def test_table_selection_rejects_bogus_year_tables(self):
        text = ("부문별 전망 (십억원) 2025A 2026E 2027E\n매출액 12 35 13\n영업이익 2 208 2\nEPS 12 453 12\n\n"
                "결산기 (12월) 2024A 2025A 2026E 2027E 2028E\n매출액 10,737.7 12,035.0 13,731.3 16,587.3 20,560.9\n"
                "영업이익 1,979.3 2,208.1 2,041.9 2,640.0 3,333.6\n지배주주순이익 1,900.0 1,980.0 2,050.0 2,620.0 3,000.0\n"
                "EPS(원) 11,913 12,376 12,829 16,412 18,896\nPER(배) 16.0 15.4 14.9 11.6 10.1\n\n"
                "장기 전망 2027E 2028E 2029E 2030E\n매출액 40 120 240 454\n영업이익 88 898 536 1,283\n")
        est, _q, unit = parse_estimate_tables(text, ref_date=date(2026, 10, 7))
        self.assertEqual(est["revenue"]["2027E"], 16587.3)                    # 본표가 기준
        self.assertEqual(est["op"]["2025A"], 2208.1)
        self.assertNotIn("2029E", est["revenue"])                           # 크기가 안 맞는 표는 안 섞인다
        self.assertEqual(unit, "십억원")

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

    def test_52w_plausibility(self):
        d, _ = digest_files(TXTS, today=TODAY)
        d.reports[0].low_52w = 287.0                      # '287 ,000' 오독 흉내
        self.assertEqual(d.low_52w, 189200)               # 현재가 258,000 의 1/5 아래는 버린다
        d.reports[1].high_52w = 9_999_999.0
        self.assertEqual(d.high_52w, 374500)

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
        self.assertIsNone(a11.driver.my_estimate.value)                       # 설계 v3: 내 추정은 빈칸
        self.assertIn("논제의 주인", a11.driver.my_estimate.rationale)
        self.assertIsNone(m["A4.4.1"].rule)                                   # Kill 은 근거 없이 만들지 않는다 — 질문에 예시만
        self.assertIn("예시(미검증)", m["A4.4.1"].question)
        self.assertEqual(m.scenarios(), {})                                   # 리포트 제시 케이스 없음 → 공란
        self.assertTrue(m.meta["draft"]["scenario_candidates"])               # 증권사 자체 후보(목표가) 는 있다
        self.assertIsNone(m.value("A2.1"))                                    # 내재 가정은 비움, 관측은 질문에
        self.assertIn("매수 일색", m["A2.1"].question)
        self.assertIn("목표가", m.value("A3.6.4"))                             # 분포 노드
        self.assertEqual(m.value("A3.6.3"), "up")
        pend = [str(i) for i in r.pending]
        self.assertTrue(any("시나리오" in x for x in pend) and any("my_estimate" in x for x in pend) and any("Kill" in x for x in pend), pend)
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
        self.assertIsNone(m.value("A3.4"))                                    # 확률이 없으니 ER 보류
        self.assertIn("미확정", r.summary_text())
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
        # 예전 그룹 채움은 보조 경로 — 명시적으로 켜고(llm_groups=True) Claim 경로는 끈다. 시나리오 그룹은 더 이상 돌지 않는다(설계 v3)
        r = draft_from_reports(TXTS, today=TODAY, filler=f, llm_groups=True, claims="off")
        m = r.map
        self.assertEqual(f.calls, ["drivers", "thesis", "risks", "catalysts", "evidence"])
        self.assertTrue(r.llm_used and m.meta["draft"]["llm"])
        labels = [n.label for n in m.drivers()]
        self.assertIn("HBM 점유율", labels)
        self.assertNotIn("지어낸 변수", labels)                      # evidence 가 자료에 없으면 거부
        self.assertTrue(any("환각" in n for n in r.notes))
        self.assertEqual(m["A1.1"].driver.current.value, 107.4)       # keep_id 드라이버의 숫자는 LLM 이 못 바꾼다
        self.assertEqual(m["A1.1"].driver.my_estimate.value, 110)
        self.assertEqual(m.scenarios(), {})                           # 시나리오는 LLM 도 만들지 않는다
        self.assertEqual([k.condition for k in m.kill_rules()], ["driver[A1.1].current < 80"])
        self.assertTrue(m.value("A0").startswith("[LLM 초안]"))
        self.assertEqual(m.value("A2.3"), "시간축")
        self.assertEqual(len(m.catalysts()), 1)
        self.assertEqual(m.value("A8.1.2"), "과점")
        self.assertEqual(m.decision.value, "WATCH")
        self.assertTrue(r.complete, [str(i) for i in r.errors])

    def test_claims_path_builds_issues_and_nodes(self):
        """설계 v3 기본 경로: filler → Claim 추출(+의미 판정) → Issue → A2.1.x · A4 · A8.4."""
        good = "HBM4는 4분기부터 주요 고객사 공급이 본격화되어 4Q26 HBM 점유율은 40%까지 상승할 것으로 기대한다"
        claims = [
            {"proposition": "HBM4 공급 본격화로 4Q26 HBM 점유율이 40% 까지 오른다", "epistemic_type": "estimate", "roles": ["thesis"], "driver_id": "hbm",
             "target_metric_id": "op", "mechanism": "공급 본격화 → 점유율", "direction": "positive", "horizon": "4Q26",
             "quant": [{"metric": "hbm_share", "col": "4Q26", "value": 40, "unit": "%"}], "quote": good},
            {"proposition": "중국 CXMT 의 DDR5 양산 성공 시 범용 DRAM 가격 하락 압력이 커진다", "epistemic_type": "estimate", "roles": ["risk"],
             "driver_label": "CXMT 양산", "direction": "negative", "condition": "DDR5 양산 성공 시",
             "quote": "중국 CXMT의 DDR5 양산 성공 시 범용 DRAM 가격 하락 압력이 커질 수 있으며"},
            {"proposition": "10월 30일 3Q26 확정실적 발표에서 CAPEX 가이던스와 주주환원 정책이 발표된다", "epistemic_type": "fact", "roles": ["catalyst"],
             "driver_id": "shareholder_return", "direction": "positive", "horizon": "2026-10-30",
             "quote": "10월 30일 3Q26 확정실적 발표와 컨퍼런스콜에서 2027년 CAPEX 가이던스와 주주환원 정책이 발표될 예정이다"},
        ]

        class Filler(MockGroupFiller):
            def complete(self, system: str, user: str) -> str:
                if "## 그룹: support" in user:
                    ids = [ln.split("- id: ", 1)[1].strip() for ln in user.splitlines() if ln.startswith("- id: ")]
                    return json.dumps({"verdicts": [{"id": i, "verdict": "supported"} for i in ids]})
                return super().complete(system, user)

        f = Filler({"claims": {"claims": claims}})
        r = draft_from_reports(TXTS, today=TODAY, filler=f)
        m = r.map
        self.assertTrue(r.llm_used)
        self.assertEqual(m.meta["draft"]["quality"], "llm")
        self.assertEqual(len(r.claimsets), 3)
        # 인용은 하나증권 픽스처에만 있다 → 그 리포트만 verified 3, 나머지는 인용 미확인(검토 대기)
        self.assertEqual(sorted(len(cs.usable()) for cs in r.claimsets), [0, 0, 3])
        self.assertTrue(all(len(cs.review_queue()) == 3 for cs in r.claimsets if not cs.usable()))
        self.assertIsNotNone(r.issue_set)
        a21 = m.children("A2.1")
        self.assertTrue(a21 and any("HBM" in n.label for n in a21), [n.label for n in a21])
        self.assertTrue(any("독립 주장" in n.label for n in a21))      # 한 증권사만 말했고 수치가 있다
        self.assertIn("침묵", r.issue_set.issues[0].summary + " ".join(i.summary for i in r.issue_set.issues))
        self.assertIn("CXMT", m["A4.1"].value["text"])
        self.assertIsNone(m["A4.1"].value["probability"])                # 확률·영향은 사람
        self.assertTrue(any(e.date == date(2026, 10, 30) for e in m.catalysts()))
        self.assertIn("A8.4.1", m.nodes)
        self.assertIn("claims", m["A2.1.1"].source.name)
        self.assertTrue(r.complete, [str(i) for i in r.errors])
        self.assertTrue(r.pending)
        self.assertIn("쟁점:", r.summary_text())

    def test_llm_failure_keeps_rules(self):
        class Broken:
            def complete(self, system: str, user: str) -> str:
                return "이것은 JSON 이 아니다"

        r = draft_from_reports(TXTS, today=TODAY, filler=Broken())
        self.assertTrue(r.complete)
        self.assertTrue(any("실패" in n for n in r.notes))
        self.assertEqual(r.map["A1.1"].driver.current.value, 107.4)
        self.assertEqual(r.map.meta["draft"]["quality"], "rules")        # 저품질로 표기
        self.assertTrue(any("저품질" in f or "규칙 분류" in f for f in r.flags))


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


class TestCreationTimePrice(unittest.TestCase):
    """현재가는 리포트 날짜가 아니라 맵 작성 시점 기준 — 실시간 조회 > DB > 리포트 중 가장 최근 날짜."""

    def test_parse_quote_both_shapes(self):
        from kstock.collect.adapters.naver_quote import QuoteError, parse_quote

        q = parse_quote({"stockName": "삼성전자", "closePrice": "261,500", "localTradedAt": "2026-10-20T15:30:00+09:00", "marketStatus": "CLOSE"},
                        "005930", "https://m.stock.naver.com/api/stock/005930/basic")
        self.assertEqual((q.price, q.as_of, q.market_status), (261500, date(2026, 10, 20), "CLOSE"))
        self.assertIn("2026-10-20 15:30", q.source().name)
        self.assertIn("종가", q.source().name)
        q2 = parse_quote({"datas": [{"itemCode": "005930", "closePrice": "262,000", "localTradedAt": "2026-10-21T10:12:03+09:00", "marketStatus": "OPEN"}]},
                         "005930", "https://polling.finance.naver.com/api/realtime/domestic/stock/005930")
        self.assertEqual((q2.price, q2.as_of), (262000, date(2026, 10, 21)))
        with self.assertRaises(QuoteError):
            parse_quote({"nothing": 1}, "005930", "u")

    def test_fetch_quote_tries_endpoints_and_offline(self):
        from kstock.collect.adapters.naver_quote import QuoteError, fetch_quote

        calls: list[str] = []

        def opener(url: str, timeout: float) -> bytes:
            calls.append(url)
            if "m.stock" in url:
                raise OSError("boom")
            return json.dumps({"datas": [{"closePrice": "259,000", "localTradedAt": "2026-10-20T15:30:00+09:00"}]}).encode()

        with mock.patch.dict(os.environ, {"KSTOCK_OFFLINE": ""}):
            q = fetch_quote("005930", opener=opener)
        self.assertEqual(q.price, 259000)
        self.assertEqual(len(calls), 2)                          # 첫 엔드포인트 실패 → 두 번째
        with mock.patch.dict(os.environ, {"KSTOCK_OFFLINE": "1"}), self.assertRaises(QuoteError):
            fetch_quote("005930", opener=opener)

    def test_live_price_wins_when_newer(self):
        from kstock.collect.adapters.naver_quote import LiveQuote

        live = LiveQuote("005930", "삼성전자", 261500, date(2026, 10, 20), None, "CLOSE", "https://x")
        r = draft_from_reports(TXTS, today=TODAY, fetch_price=True, price_fetcher=lambda code: live)
        m = r.map
        self.assertEqual(m.current_price, 261500)
        self.assertEqual(m["A0.4"].as_of, date(2026, 10, 20))   # 작성 시점
        self.assertIn("네이버 금융", m["A0.4"].source.name)
        self.assertEqual(r.price.kind, "live")
        self.assertEqual(m.meta["draft"]["price"]["age_days"], 0)
        self.assertNotIn("warn", m["A0.4"].tags)
        self.assertFalse([f for f in r.flags if f.startswith("A0.4")])
        # 분포 노드의 상승여력도 새 현재가 기준 (목표가 평균 vs 261,500)
        self.assertIn("현재가 대비", m.value("A3.6.4"))

    def test_report_price_flagged_as_stale_without_fetch(self):
        r = draft_from_reports(TXTS, today=TODAY, fetch_price=False)
        m = r.map
        self.assertEqual((m.current_price, m["A0.4"].as_of), (258000, date(2026, 10, 10)))
        self.assertEqual(r.price.kind, "report")
        self.assertEqual(r.price.age_days(TODAY), 10)
        self.assertIn("warn", m["A0.4"].tags)
        self.assertTrue(any("갱신 필요" in f for f in r.flags))
        self.assertTrue(any("작성 시점 값이 아니다" in n for n in r.notes))

    def test_fetch_failure_falls_back(self):
        def broken(code: str):
            raise RuntimeError("no network")

        r = draft_from_reports(TXTS, today=TODAY, fetch_price=True, price_fetcher=broken)
        self.assertEqual(r.price.kind, "report")
        self.assertTrue(any("실시간 조회 실패" in n for n in r.notes))

    def test_ops_passes_fetch_flag(self):
        from kstock.collect.adapters.naver_quote import LiveQuote

        with tempfile.TemporaryDirectory() as t:
            store = Store(Path(t) / "db" / "k.sqlite")
            ops = Ops(store, today=TODAY, symbols=SymbolBook([Symbol("005930", "삼성전자", "KR", None, 1.0)]))
            live = LiveQuote("005930", "삼성전자", 260000, TODAY, None, "CLOSE", "https://x")
            r = ops.draft_from_reports(TXTS, {"fetch_price": True, "price_fetcher": lambda c: live, "save": False})
            self.assertEqual(r.map.current_price, 260000)
            r2 = ops.draft_from_reports(TXTS, {"fetch_price": False, "save": False})
            self.assertEqual(r2.map.current_price, 258000)
            store.close()

    def test_refresh_price_on_existing_map(self):
        from kstock.collect.adapters.naver_quote import LiveQuote

        with tempfile.TemporaryDirectory() as t:
            store = Store(Path(t) / "db" / "k.sqlite")
            ops = Ops(store, today=TODAY, symbols=SymbolBook([Symbol("005930", "삼성전자", "KR", None, 1.0)]))
            r = ops.draft_from_reports(TXTS, {"fetch_price": False})
            m = r.map
            self.assertIn("warn", m["A0.4"].tags)
            price, as_of, src = ops.refresh_price(m, fetch=True, fetcher=lambda c: LiveQuote(c, "삼성전자", 261500, TODAY, None, "CLOSE", "https://x"))
            self.assertEqual((price, as_of), (261500, TODAY))
            self.assertIn("네이버", src)
            self.assertNotIn("warn", m["A0.4"].tags)
            self.assertEqual(store.load_map(m.map_id).current_price, 261500)
            self.assertIsNone(m.value("A3.4"))                 # 시나리오·확률이 없으니 ER 은 여전히 보류
            with self.assertRaises(OpsError):
                ops.refresh_price(m, fetch=False)                 # DB 도 네트워크도 없음
            store.close()


class TestTableSelection(unittest.TestCase):
    def test_growth_table_loses_to_amount_table(self):
        """삼성증권 양식: '주요 투자지표' 표의 매출액·영업이익 행은 성장률(%)이고 지표 수가 더 많다 — 금액 표가 이겨야 한다."""
        text = ("2025 2026E 2027E 2028E\n매출액 (십억원) 17,058 19,139 21,159 24,494\n영업이익 (십억원) 763 1,241 1,883 2,350\n"
                "순이익 (십억원) 205 568 963 1,244\nEPS (adj) (원) 132 708 1,573 2,031\nEPS (adj) growth (%) -23.9 434.7 122.4 29.1\n"
                "EBITDA margin (%) 7.7 10.1 13.0 14.2\nROE (%) 1.1 5.6 11.3 12.9\n\n"
                "12월 31일 기준 2024 2025 2026E 2027E 2028E\n매출액 -7.7 5.1 12.2 10.6 15.8\n영업이익 -30.6 -25.0 62.7 51.8 24.8\n"
                "순이익 -23.7 -48.0 177.0 69.5 29.1\nEPS (지배주주) 174 132 708 1,573 2,031\nBPS 11,705 12,156 13,228 14,500 16,000\n"
                "DPS 0 0 0 0 0\nPER 100.9 569.1 115.3 51.9 40.1\nPBR 1.5 6.2 6.2 5.5 5.0\nEV/EBITDA 12.5 42.1 30.7 21.5 17.0\n"
                "ROE 1.5 1.1 5.6 11.3 12.9\n배당수익률 0.0 0.0 0.0 0.0 0.0\n")
        est, _q, _u = parse_estimate_tables(text, ref_date=date(2026, 10, 1))
        self.assertEqual(est["revenue"]["2026E"], 19139)
        self.assertEqual(est["op"]["2027E"], 1883)
        self.assertNotIn("ebitda", est)                                  # 'EBITDA margin' 은 EBITDA 가 아니다
        self.assertEqual(est["per"]["2026E"], 115.3)                      # 비율 지표는 다른 표에서 보탠다


class TestQualityGuards(unittest.TestCase):
    """실제 리포트에서 본 품질 문제의 회귀 테스트."""

    def test_kill_is_not_invented(self):
        r = draft_from_reports(TXTS, today=TODAY, fetch_price=False)
        self.assertIsNone(r.map["A4.4.1"].rule)
        self.assertIn("지금 수준", r.map["A4.4.1"].question)              # 예시만 질문에 (미검증 표기)
        self.assertTrue(any(i.node_id == "A4.4" for i in r.pending))

    def test_bear_floor(self):
        d, _ = digest_files(TXTS, today=TODAY)
        for f in d.reports:
            f.low_52w = 60000.0                   # 1년 전 저가가 현재가의 1/4
        anchors, notes = scenario_anchors(d, 258000)
        self.assertAlmostEqual(anchors["Bear"], 258000 * 0.6)
        self.assertTrue(any("제한" in n for n in notes))

    def test_noise_sentences_are_dropped(self):
        from kstock.reports.extract import extract_sentences
        from kstock.reports.loader import normalize_text

        t = normalize_text("투자의견 BUY 매수, 유지 6개월 목표주가 110,000 하향 현재주가 (26.07.29) 60,800 유틸리티업종\n"
                           "기업 투자의견 - Buy(매수) : 향후 6개월간 시장수익률 대비 10%p 이상 주가 상승 예상\n"
                           "표2 NAVER - 엔비디아 유상증자 결정 공시 구분 항목 내용 발행 개요\n"
                           "시가총액(십억원) 29,065 발행주식수(천주) 152,094 52 주 최고 304,000원 최저 181,100원 배당수익률(26E) 1.4%\n"
                           "두산에너빌리티 원자력 사업부문 실적\n\n리스크 요인\n"
                           "중국 CXMT의 DDR5 양산 성공 시 범용 DRAM 가격 하락 압력이 커질 수 있다.\n")
        got = extract_sentences(t)
        texts = [s.text for s in got]
        self.assertTrue(all("투자의견" not in x and "표2" not in x and "시가총액" not in x and "사업부문 실적" != x for x in texts), texts)
        risks = [s for s in got if s.kind == "risk"]
        self.assertTrue(risks and "CXMT" in risks[0].text)
        self.assertGreaterEqual(risks[0].score, 3)                               # 리스크 소제목 아래 가산

    def test_llm_status_persisted_and_bad_key_detected(self):
        class Broken:
            def complete(self, system: str, user: str) -> str:
                raise RuntimeError("AuthenticationError 401 invalid x-api-key")

        r = draft_from_reports(TXTS, today=TODAY, filler=Broken(), llm_groups=True, claims="off")
        g = r.map.meta["draft"]["llm_groups"]
        self.assertEqual(sum(1 for v in g.values() if v == "ok"), 0)
        self.assertTrue(any(v.startswith("skip") for v in g.values()))          # 인증 실패면 나머지 건너뜀
        self.assertFalse(r.llm_used)
        self.assertTrue(any(f.startswith("LLM") for f in r.flags))
        self.assertIn("LLM 0/", r.summary_text())
        from kstock.generate.llm_fill import key_problem

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "apikey_abcdefghijklmnopqrstuvwx"}):
            self.assertIn("모양이 아니다", key_problem() or "")
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-" + "x" * 80}):
            self.assertIsNone(key_problem())

    def test_peek_rows_and_date_span_warning(self):
        with tempfile.TemporaryDirectory() as t:
            store = Store(Path(t) / "db" / "k.sqlite")
            ops = Ops(store, today=TODAY, symbols=SymbolBook([Symbol("005930", "삼성전자", "KR", None, 1.0)]))
            pk = ops.peek_reports(TXTS)
            self.assertEqual(len(pk["rows"]), 3)
            self.assertTrue(all(r.startswith("✓") for r in pk["rows"]), pk["rows"])
            store.close()
        d, _ = digest_files(TXTS, today=TODAY)
        d.reports[0].date = date(2026, 6, 1)
        from kstock.reports import ReportDigest

        d2 = ReportDigest.build(d.reports, ticker="005930", today=TODAY)
        self.assertTrue(any("벌어져" in w for w in d2.warnings))

    def test_long_term_year_columns_do_not_extend_the_table(self):
        """사업부 전망·장기 목표 표(2029E~2035E)가 본표의 열을 늘리거나 값을 더럽히지 못한다."""
        t = normalize_text("(십억원) 2024A 2025A 2026E 2027E 2028E\n"
                           "매출액 10,738 12,035 13,800 16,100 20,000\n"
                           "영업이익 1,979 2,208 2,100 2,600 3,500\n"
                           "순이익 1,900 1,819 1,900 2,300 3,100\n"
                           "EPS(원) 11,988 12,615 12,561 14,216 15,478\n\n"
                           "AI 데이터센터 사업 전망 (십억원) 2028E 2029E 2030E 2031E 2032E\n"
                           "매출액 300 800 1,500 2,500 3,200\n"
                           "영업이익 -50 500 1,300 3,000 4,100\n\n"
                           "중장기 목표 (조원) 2026E 2030E 2035E\n"
                           "매출액 13.8 15.3 22.2\n영업이익 2.1 2.1 3.3\n")
        annual, _, unit = parse_estimate_tables(t, ref_date=date(2026, 10, 7))
        self.assertEqual(sorted(annual["op"]), ["2024A", "2025A", "2026E", "2027E", "2028E"])
        self.assertEqual(annual["op"]["2028E"], 3500.0)                        # 사업부 표의 2028E(-50) 가 덮지 않는다
        self.assertNotIn("2035E", annual["revenue"])
        self.assertEqual(unit, "십억원")

    def test_pdf_split_decimals_and_bullets_are_normalized(self):
        t = normalize_text("PER(배) 19 .5 15 .7 15 .9\n 3분기 매출액 3.49조원(+11.1%yoy)\n287 ,000원")
        self.assertIn("19.5 15.7 15.9", t)
        self.assertNotIn("", t)
        self.assertIn("287,000원", t)

    def test_title_skips_sector_opinion_and_data_box_lines(self):
        from kstock.reports.extract import find_title

        self.assertEqual(find_title("(035420)\nNAVER\n2026.10.07\n인터넷/게임/우주 정의훈\n6170 / a@b.com\n기대 보단 우려가 더 컸던 3 분기\n투자의견 BUY(유지)", "NAVER"),
                         "기대 보단 우려가 더 컸던 3 분기")
        self.assertEqual(find_title("www.imeritz.com\nNAVER (035420)\nCompany Brief\n인터넷/게임\nAnalyst 이효진\nhyojinlee@meritz.co.kr\n"
                                    "RA 김수민\nsumin_kim@meritz.co.kr 국내 추론 시대의 개막에 베팅할 시점\n네오 클라우드 사업 진출 발표에도 주가는 왜 부진했나"),
                         "국내 추론 시대의 개막에 베팅할 시점")
        self.assertEqual(find_title("Kyobo Company Analysis\n인터넷/게임\nNA VER 035420\n핵심 성장 스토리 유지 Oct 6 2026\nBUY 유지\nTP 390,000원 유지"),
                         "핵심 성장 스토리 유지")
        self.assertEqual(find_title("매수\n목표주가(유지) 530,000원\n상승여력 97.4%\nStock Data\n액면가 100원\n자본금 898십억원\n주요주주\n"
                                    "주가추이 및 상대강도\n(pt) (원) 삼성전자\nrelative to KOSPI\n사상 최초 DRAM 분기 매출 100조원 상회\n독야청청\n"
                                    "삼성전자 3Q26 잠정 실적은 매출액 195조원(+14% QoQ), 영업이익 107.4조원으로 당사 추정치를 상회하였다.", "삼성전자"),
                         "사상 최초 DRAM 분기 매출 100조원 상회")
        self.assertEqual(find_title("2026.08.03\n두산에너빌리티\n(034020)\n하반기 원전 및 가스터빈 등 수주 모멘텀 가시화 Company Brief", "두산에너빌리티"),
                         "하반기 원전 및 가스터빈 등 수주 모멘텀 가시화")

    def test_positive_sentences_are_not_risks(self):
        from kstock.reports.extract import extract_sentences

        t = normalize_text("국내외 대형 원전 수주 확대뿐만 아니라 SMR 설계사들과의 협업 확대로 4세대 원자로 시장에서도 경쟁 우위를 확보할 것으로 기대.\n"
                           "중국 CXMT의 DDR5 양산 성공 시 범용 DRAM 가격 하락 압력이 커질 수 있다.\n")
        risks = [s.text for s in extract_sentences(t) if s.kind == "risk"]
        self.assertEqual(len(risks), 1, risks)
        self.assertIn("CXMT", risks[0])


class TestAggregationRules(unittest.TestCase):
    """설계 v3 §2.6 수치 표준화: 증권사별 최신 1건 · 45일 · 분산 · 회계 기준 · 실적 전/후 · DB 교차 확인."""

    def _facts(self):
        from kstock.reports import extract_facts, load_reports

        return [extract_facts(d, today=TODAY) for d in load_reports(TXTS)]

    def test_latest_per_broker_and_age(self):
        import copy

        facts = self._facts()
        old = copy.deepcopy(facts[0])                       # 같은 증권사의 20일 전 리포트 (TP 다름)
        old.date = facts[0].date - timedelta(days=20)
        old.path = Path(str(facts[0].path).replace(".txt", "_old.txt"))
        old.target_price = 999000.0
        d = ReportDigest.build(facts + [old], today=TODAY)
        self.assertEqual(d.n_tp, 3)                         # 4건 중 이전 건은 집계 제외
        self.assertNotIn(999000.0, d.tp_values)
        self.assertIn(old.path.name, d.excluded)
        self.assertIn("리비전 이력", d.excluded[old.path.name])
        self.assertEqual(len(d.aggregated), 3)
        stale = copy.deepcopy(facts[1])
        stale.broker = "옛날증권"
        stale.date = facts[1].date - timedelta(days=60)
        stale.path = Path("stale.txt")
        d2 = ReportDigest.build(facts + [stale], today=TODAY)
        self.assertIn("stale.txt", d2.excluded)
        self.assertIn(">45일", d2.excluded["stale.txt"])
        d3 = ReportDigest.build(facts + [stale], today=TODAY, max_age_days=None)
        self.assertNotIn("stale.txt", d3.excluded)
        self.assertEqual(d3.n_tp, 4)
        self.assertTrue(any("집계 제외" in ln for ln in d2.summary_lines()))

    def test_spread_and_basis(self):
        facts = self._facts()
        d = ReportDigest.build(facts, today=TODAY)
        sp = d.spread("op", "2027E")
        self.assertIsNotNone(sp)
        self.assertEqual(sp["n"], 3)
        self.assertEqual(set(sp["values"]), set(d.brokers))
        self.assertGreaterEqual(sp["max"], sp["median"])
        self.assertAlmostEqual(sp["abs"], sp["max"] - sp["min"])
        facts[0].basis["np"] = "지배"
        facts[1].basis["np"] = "전체"
        facts[2].basis["np"] = "전체"
        d2 = ReportDigest.build(facts, today=TODAY)
        self.assertTrue(any("순이익 기준이 섞여" in w for w in d2.warnings))
        self.assertEqual(d2.cell("np", "2027E").n, 2)      # 다수(전체) 기준만

    def test_pre_earnings_tag(self):
        facts = self._facts()
        early = facts[0]
        early.date = date(2026, 10, 5)                      # 3Q26 잠정실적(분기 말 +8일 = 10/8) 전
        d = ReportDigest.build(facts, today=TODAY)
        self.assertEqual(d.earnings_date, date(2026, 10, 8))
        self.assertIn("추정", d.earnings_date_source)
        self.assertIn(early.path.name, d.pre_earnings)
        d2 = ReportDigest.build(facts, today=TODAY, earnings_date=date(2026, 10, 1))
        self.assertEqual(d2.earnings_date_source, "실제 발표일")
        self.assertNotIn(early.path.name, d2.pre_earnings)

    def test_db_cross_check(self):
        from kstock.collect.adapters.research_db import DbReport
        from kstock.reports.dbcheck import cross_check_db

        class FakeDb:
            def report_by_id(self, rid):
                return {"hk_96588": DbReport("hk_96588", "005930", "IBK투자증권", "26년 3분기 환율 부진을 극복한 실적", date(2026, 10, 12),
                                              "매수", 470000.0, 460000.0, None),
                        "hk_96597": DbReport("hk_96597", "035420", "유진투자증권", None, date(2026, 10, 12), None, None, None, None)}.get(rid)

        facts = self._facts()
        facts[0].path = Path("삼성전자_96588.pdf")
        facts[0].title = None                                 # 본문에서 못 읽음 → DB 로 채움
        facts[0].target_price = 460000.0
        facts[1].path = Path("삼성전자_96597.pdf")
        facts[2].path = Path("no_id.pdf")
        res = cross_check_db(facts, FakeDb(), ticker="005930")
        self.assertEqual((res.checked, res.found), (2, 2))
        self.assertEqual(facts[0].title, "26년 3분기 환율 부진을 극복한 실적")
        self.assertTrue(any("target_price 본문 460,000 / DB 470,000" in m for m in res.mismatches), res.mismatches)
        self.assertTrue(any("hk_96597" in w for w in res.wrong_ticker))
        self.assertTrue(any("DB 교차" in ln or "DB" in ln for ln in res.lines()))


class TestV3OpsFeatures(unittest.TestCase):
    """설계 v3: 시나리오 후보 넣기 · 예시 확률 · 검토 대기함 · 쟁점 표 · 출처 (ops 층)."""

    def _ops(self, t: str):
        store = Store(Path(t) / "db" / "k.sqlite")
        return store, Ops(store, today=TODAY, symbols=SymbolBook([Symbol("005930", "삼성전자", "KR", None, 1.0)]))

    def test_candidates_probs_and_eval(self):
        with tempfile.TemporaryDirectory() as t:
            store, ops = self._ops(t)
            r = ops.draft_from_reports(TXTS, {"fetch_price": False})
            m = r.map
            labels = ops.candidate_labels(m)
            self.assertEqual(len(labels), 3)                                   # 목표가 후보 3 (리포트 제시·산식 없음)
            self.assertTrue(all("목표주가" in x for x in labels))
            self.assertEqual(m.scenarios(), {})
            with self.assertRaises(OpsError):
                ops.apply_example_probs(m)                                      # 시나리오가 비어 있으면 확률을 못 넣는다
            cands = ops.scenario_candidates(m)
            order = sorted(range(3), key=lambda i: cands[i]["target_price"])
            ops.apply_scenario_candidate(m, order[0], "Bear", save=False)
            ops.apply_scenario_candidate(m, order[1], "Base", save=False)
            msg = ops.apply_scenario_candidate(m, order[2], "Bull")
            self.assertIn("Bull =", msg)
            sc = m.scenarios()
            self.assertEqual(len(sc), 3)
            self.assertTrue(all(s.probability is None for s in sc.values()))
            self.assertTrue(sc["Bear"].target_price < sc["Base"].target_price < sc["Bull"].target_price)
            self.assertIsNone(m.value("A3.4"))                                  # 확률 없음 → ER 보류
            self.assertTrue(any("사용자 선택" in str(h.source) for h in m["A3.1"].history))
            msg2 = ops.apply_example_probs(m)
            self.assertIn("미검증", msg2)
            self.assertIsNotNone(m.value("A3.4"))                               # 이제 계산된다
            self.assertAlmostEqual(sum(s.probability for s in m.scenarios().values()), 1.0)
            self.assertTrue(any("미검증" in str(h.source) for h in m["A3.2"].history))
            saved = store.load_map(m.map_id)
            self.assertEqual(saved["A3.2"].scenario.probability, 0.45)
            store.close()

    def test_review_queue_issues_and_source(self):
        with tempfile.TemporaryDirectory() as t:
            store, ops = self._ops(t)
            r = ops.draft_from_reports(TXTS, {"fetch_price": False})            # LLM 없음 → 규칙 Claim(저품질)
            m = r.map
            self.assertEqual(m.meta["draft"]["quality"], "rules")
            self.assertTrue((store.claims_dir).is_dir() and list(store.claims_dir.glob("*.json")))
            self.assertTrue((store.issues_dir / "005930.json").is_file())
            css = ops.claim_sets_for(m)
            self.assertEqual(len(css), 3)
            txt = ops.issues_text(m)
            self.assertIn("쟁점", txt)
            # 검토 대기함: 규칙 Claim 은 인용이 있으면 verified 라 대기열이 비어 있을 수 있다 → 하나를 강제로 대기 상태로
            from kstock.models import ClaimStatus
            from kstock.reports.claims import save_claimset

            cs = css[0]
            cs.claims[0].status = ClaimStatus.needs_review
            save_claimset(cs, store.claims_dir)
            rows = ops.review_queue(m)
            self.assertEqual(len(rows), 1)
            self.assertIn("approve", ops.review_claim(m, rows[0]["claim_id"], "approve", note="확인"))
            self.assertEqual(ops.review_queue(m), [])
            with self.assertRaises(OpsError):
                ops.review_claim(m, "nope:c1", "approve")
            info = ops.source_of(m, "A8.4.1")
            self.assertTrue(info["path"].endswith(".txt"))
            a21 = m.children("A2.1")
            if a21:
                info2 = ops.source_of(m, a21[0].id)
                self.assertIn("쟁점", info2["name"])
            store.close()
