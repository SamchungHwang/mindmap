"""Claim 추출·검증 (설계 v3 P2): 청킹 · 인용 위치 · LLM 답 → Claim · 중복 제거 · 3단 검증 · 검토 대기함 · 저장 · CLI."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from helpers import D0  # noqa: F401  (sys.path 설정)
from kstock.cli import main as cli_main
from kstock.generate import MockGroupFiller
from kstock.generate.kpi_catalog import normalize_driver
from kstock.models import ClaimStatus, SupportVerdict
from kstock.reports import extract_facts, load_report
from kstock.reports.claims import (
    chunk_report,
    dedupe,
    extract_claims,
    load_claimset,
    locate_quote,
    page_of,
    rules_claims,
    save_claimset,
)
from kstock.reports.loader import ReportDoc, normalize_text
from kstock.reports.validate_claims import check_numbers, review, run_all

FIX = Path(__file__).resolve().parent / "fixtures" / "reports"
HANA = FIX / "hana_005930_20261012.txt"
TODAY = date(2026, 10, 20)


def _hana():
    doc = load_report(HANA)
    return doc, extract_facts(doc, today=TODAY)


class TestChunkAndQuote(unittest.TestCase):
    def test_page_and_chunks(self):
        text = normalize_text("1쪽 투자포인트\n본문 " * 50 + "\n\f2쪽\n리스크 요인\n" + "위험 " * 300)
        doc = ReportDoc(path=Path("x.pdf"), text=text, kind="pdf", extractor="pypdf", pages=2)
        self.assertEqual(page_of(text, 0), 1)
        self.assertEqual(page_of(text, text.find("2쪽")), 2)
        chunks = chunk_report(doc)
        self.assertGreaterEqual(len(chunks), 1)
        self.assertEqual(chunks[0].start, 0)
        # 긴 문서는 max_chars 로 나뉜다
        long_doc = ReportDoc(path=Path("y.pdf"), text=("가나다라 " * 20 + "\n") * 2000, kind="pdf", extractor="pypdf", pages=1)
        cs = chunk_report(long_doc, max_chars=18000, max_chunks=6)
        self.assertTrue(all(c.end - c.start <= 18000 for c in cs))
        self.assertLessEqual(len(cs), 6)

    def test_locate_quote_variants(self):
        doc, _ = _hana()
        sent = "HBM4는 4분기부터 주요 고객사 공급이 본격화"
        loc = locate_quote(doc.text, sent)
        self.assertIsNotNone(loc)
        self.assertEqual(doc.text[loc[0]:loc[1]], sent)
        self.assertIsNotNone(locate_quote(doc.text, "HBM4는  4분기부터\n주요 고객사 공급이 본격화"))   # 공백·줄바꿈 무시
        self.assertIsNone(locate_quote(doc.text, "이 문장은 리포트 어디에도 없다 전혀 없다"))
        self.assertIsNone(locate_quote(doc.text, "짧다"))


class TestExtractAndValidate(unittest.TestCase):
    def _mock(self, claims: list[dict], verdicts: dict[str, str] | None = None) -> MockGroupFiller:
        answers = {"claims": {"claims": claims}}
        if verdicts is not None:
            answers["support"] = {"verdicts": [{"id": k, "verdict": v, "why": "테스트"} for k, v in verdicts.items()]}
        return MockGroupFiller(answers)

    def test_llm_claims_pipeline(self):
        doc, f = _hana()
        good_quote = "HBM4는 4분기부터 주요 고객사 공급이 본격화되어 4Q26 HBM 점유율은 40%까지 상승할 것으로 기대한다"
        claims = [
            {"proposition": "HBM4 공급 본격화로 4Q26 HBM 점유율이 40% 까지 오른다", "epistemic_type": "estimate", "roles": ["thesis"],
             "driver_id": "hbm", "target_metric_id": "op", "mechanism": "공급 본격화 → 점유율", "direction": "positive", "horizon": "4Q26",
             "quant": [{"metric": "hbm_share", "col": "4Q26", "value": 40, "unit": "%"}], "quote": good_quote, "inferred": False},
            {"proposition": "중국 CXMT 의 DDR5 양산 성공 시 범용 DRAM 가격 하락 압력이 커진다", "epistemic_type": "estimate", "roles": ["risk"],
             "driver_label": "CXMT 양산", "direction": "negative", "condition": "DDR5 양산 성공 시",
             "quote": "중국 CXMT의 DDR5 양산 성공 시 범용 DRAM 가격 하락 압력이 커질 수 있으며", "inferred": False},
            {"proposition": "고객사 인증이 완료되어 공급이 확정되었다", "epistemic_type": "fact", "roles": ["thesis"], "direction": "positive",
             "quote": good_quote, "inferred": False},
            {"proposition": "리포트에 없는 인용을 단 주장", "roles": ["thesis"], "direction": "positive", "quote": "이 문장은 리포트에 없다 전혀 없다", "inferred": False},
            {"proposition": "경영진이 2028년 HBM 5 양산을 약속했다 (추론)", "roles": ["thesis"], "direction": "positive", "quote": good_quote, "inferred": True},
            {"proposition": "분기 영업이익이 999조원이라는 주장", "roles": ["thesis"], "direction": "positive",
             "quant": [{"metric": "op", "col": "3Q26", "value": 999, "unit": "조원"}], "quote": good_quote, "inferred": False},
            {"proposition": "HBM4 공급 본격화로 4Q26 HBM 점유율이 40% 까지 오른다 (중복)", "roles": ["thesis"], "direction": "positive", "quote": good_quote},
        ]
        # 중복 제거 전 id 는 추출 순서 — 의미 판정은 id 로 매긴다
        rid = "f_" + "x"
        mock = self._mock(claims)
        cs = extract_claims(doc, f, mock, sector="KR-SEMI", name="삼성전자")
        rid = cs.report_id
        self.assertEqual(cs.quality, "llm")
        self.assertEqual(mock.calls.count("claims"), len(chunk_report(doc)))
        ids = [c.claim_id for c in cs.claims]
        self.assertEqual(len(cs.claims), 6)                                   # 중복 1건 합쳐짐
        def by_prop(prefix: str):
            return next(c for c in cs.claims if c.proposition.startswith(prefix))

        c_hbm = by_prop("HBM4 공급 본격화로")
        self.assertEqual(c_hbm.driver_id, "hbm")
        self.assertEqual(c_hbm.validation.quote, "ok")
        self.assertEqual(cs.span(c_hbm.span_ids[0]).page, 1)
        c_cxmt = by_prop("중국 CXMT")
        self.assertEqual(c_cxmt.driver_id, "china_competition")               # 자유 라벨 → 사전 id
        self.assertEqual(c_cxmt.condition, "DDR5 양산 성공 시")
        c_missing = by_prop("리포트에 없는 인용")
        self.assertEqual(c_missing.validation.quote, "miss")
        # 검증: 의미 판정 mock
        verdicts = {c_hbm.claim_id: "supported", c_cxmt.claim_id: "supported", by_prop("고객사 인증이 완료").claim_id: "contradicted"}
        mock2 = self._mock(claims, verdicts)
        counts = run_all(cs, f, mock2, semantic=True)
        self.assertEqual(c_hbm.status, ClaimStatus.verified)
        self.assertEqual(c_hbm.validation.numbers, "ok")                      # 40 이 인용문 안에 있다
        self.assertEqual(c_cxmt.status, ClaimStatus.verified)
        self.assertEqual(by_prop("고객사 인증이 완료").status, ClaimStatus.rejected)
        self.assertEqual(c_missing.status, ClaimStatus.needs_review)
        self.assertEqual(by_prop("경영진이 2028년").status, ClaimStatus.needs_review)   # inferred
        c_999 = by_prop("분기 영업이익이 999")
        self.assertEqual(c_999.validation.numbers, "mismatch")
        self.assertEqual(c_999.status, ClaimStatus.needs_review)
        self.assertEqual(counts["verified"], 2)
        self.assertEqual(len(cs.usable()), 2)
        self.assertTrue(all(i.startswith(rid) for i in ids))
        # 검토함: 승인 → verified, 반려 → rejected
        review(cs, c_missing.claim_id, "approve", note="원문 p.2 에 있음")
        self.assertEqual(c_missing.status, ClaimStatus.verified)
        self.assertEqual(c_missing.validation.support, SupportVerdict.supported)
        review(cs, c_999.claim_id, "reject")
        self.assertEqual(c_999.status, ClaimStatus.rejected)
        with tempfile.TemporaryDirectory() as t:
            p = save_claimset(cs, Path(t))
            back = load_claimset(p)
            self.assertEqual(len(back.claims), len(cs.claims))
            self.assertEqual(back.usable()[0].claim_id, cs.usable()[0].claim_id)

    def test_numbers_against_table(self):
        doc, f = _hana()
        mock = self._mock([{"proposition": "2027년 영업이익은 표의 값이다", "roles": ["thesis"], "direction": "positive",
                            "quant": [{"metric": "op", "col": "2027E", "value": f.estimates["op"]["2027E"], "unit": "십억원"}],
                            "quote": "HBM4는 4분기부터 주요 고객사 공급이 본격화되어"}])
        cs = extract_claims(doc, f, mock, sector="KR-SEMI")
        self.assertEqual(check_numbers(cs.claims[0], cs, f), "ok")           # 표에 있는 값

    def test_rules_fallback_and_semantic_skip(self):
        doc, f = _hana()
        cs = rules_claims(doc, f, sector="KR-SEMI")
        self.assertEqual(cs.quality, "rules")
        self.assertTrue(cs.claims)
        counts = run_all(cs, f, None, semantic=False)
        self.assertEqual(counts["rejected"], 0)
        self.assertTrue(any(c.status == ClaimStatus.verified for c in cs.claims))   # 규칙 Claim 은 ③ 없이도 verified (저품질 표기)
        self.assertTrue(all(c.extractor.model == "rules" for c in cs.claims))
        # LLM Claim 은 의미 판정 없이는 verified 가 되지 않는다
        mock = self._mock([{"proposition": "HBM4 공급이 본격화된다", "roles": ["thesis"], "direction": "positive",
                            "quote": "HBM4는 4분기부터 주요 고객사 공급이 본격화되어"}])
        cs2 = extract_claims(doc, f, mock, sector="KR-SEMI")
        run_all(cs2, f, None, semantic=False)
        self.assertEqual(cs2.claims[0].status, ClaimStatus.needs_review)

    def test_dedupe_prefers_quoted(self):
        from kstock.models import Claim, Validation

        a = Claim(claim_id="a", report_id="r", proposition="HBM4 공급 확대가 영업이익을 늘린다", validation=Validation(quote="miss"))
        b = Claim(claim_id="b", report_id="r", proposition="HBM4 공급 확대가 영업이익을 늘린다.", validation=Validation(quote="ok"), span_ids=["s"])
        kept = dedupe([a, b])
        self.assertEqual([c.claim_id for c in kept], ["b"])

    def test_normalize_driver(self):
        self.assertEqual(normalize_driver("HBM4 공급량", sector="KR-SEMI"), "hbm")
        self.assertEqual(normalize_driver("가스터빈 수주 확대"), "gas_turbine")
        self.assertIsNone(normalize_driver("전혀 모르는 변수"))

    def test_cli_rules_and_review(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(cli_main(["claims", "--reports", str(HANA), "--rules", "--out", t, "--date", "2026-10-20", "--show"]), 0)
            files = list(Path(t).glob("*.json"))
            self.assertEqual(len(files), 1)
            data = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(data["quality"], "rules")
            rid = files[0].stem
            cid = data["claims"][0]["claim_id"]
            self.assertEqual(cli_main(["claims-review", rid, "--out", t, "--reject", cid]), 0)
            data2 = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(data2["claims"][0]["status"], "rejected")


if __name__ == "__main__":
    unittest.main()


class TestC2Rules(unittest.TestCase):
    """프롬프트 c2: 인용 여럿(quotes) · 명제에 없는 수치는 불일치가 아니라 제외 · 의미 판정에 인용 전부."""

    def test_multiple_quotes_make_multiple_spans(self):
        doc, f = _hana()
        q1 = "HBM4는 4분기부터 주요 고객사 공급이 본격화되어 4Q26 HBM 점유율은 40%까지 상승할 것으로 기대한다"
        q2 = "중국 CXMT의 DDR5 양산 성공 시 범용 DRAM 가격 하락 압력이 커질 수 있으며"
        mock = MockGroupFiller({"claims": {"claims": [
            {"proposition": "HBM 점유율 40% 상승 전망과 CXMT 리스크가 함께 언급된다", "roles": ["thesis"], "direction": "positive",
             "quant": [{"metric": "hbm_share", "col": "4Q26", "value": 40, "unit": "%"}],
             "quotes": [q1, q2, "원문에 없는 세 번째 인용 문장이다 전혀 없다"]},
            {"proposition": "quote 하나만 있는 옛 형식", "roles": ["thesis"], "direction": "positive", "quote": q1},
        ]}})
        cs = extract_claims(doc, f, mock, sector="KR-SEMI", name="삼성전자")
        c = next(x for x in cs.claims if x.proposition.startswith("HBM 점유율 40%"))
        self.assertEqual(len(c.span_ids), 2)                                 # 못 찾은 셋째는 제외
        self.assertEqual(c.validation.quote, "ok")
        self.assertIn("1개는 원문에서 못 찾아 제외", c.validation.note)
        old = next(x for x in cs.claims if x.proposition.startswith("quote 하나만"))
        self.assertEqual(len(old.span_ids), 1)
        # 의미 판정 프롬프트에는 인용 두 개가 ' … ' 로 이어져 들어간다
        from kstock.reports.validate_claims import semantic_check

        class _Spy:
            prompts: list[str] = []

            def complete(self, system: str, user: str) -> str:
                self.prompts.append(user)
                return json.dumps({"verdicts": [{"id": c.claim_id, "verdict": "supported", "why": "ok"}]})

        spy = _Spy()
        semantic_check(cs, spy)
        self.assertTrue(any(" … " in p and "CXMT" in p and "HBM4" in p for p in spy.prompts))

    def test_quant_not_in_proposition_is_dropped_not_mismatch(self):
        doc, f = _hana()
        q1 = "HBM4는 4분기부터 주요 고객사 공급이 본격화되어 4Q26 HBM 점유율은 40%까지 상승할 것으로 기대한다"
        mock = MockGroupFiller({"claims": {"claims": [
            # 명제는 40% 만 말하는데 quant 에 근거 없는 777777 이 끼어 있다 → 제외, 40 은 인용에 있어 ok
            {"proposition": "HBM 점유율이 40% 까지 오른다", "roles": ["thesis"], "direction": "positive",
             "quant": [{"metric": "hbm_share", "col": "4Q26", "value": 40, "unit": "%"}, {"metric": "target_price", "col": None, "value": 777777, "unit": "원"}],
             "quotes": [q1]},
            # 명제에 숫자가 전혀 없는데 근거(인용·표) 에도 없는 quant 만 있다 → 전부 제외 → none
            {"proposition": "고객사 공급 본격화가 점유율 확대로 이어진다", "roles": ["thesis"], "direction": "positive",
             "quant": [{"metric": "hbm_share", "col": "4Q26", "value": 40.7, "unit": "%"}], "quotes": [q1]},
            # 명제에 적힌 숫자가 인용·표에 없다 → 여전히 mismatch
            {"proposition": "경쟁사 대비 점유율 격차가 55%p 로 벌어진다", "roles": ["thesis"], "direction": "positive",
             "quant": [{"metric": "hbm_share", "col": "4Q26", "value": 55, "unit": "%"}], "quotes": [q1]},
        ]}})
        cs = extract_claims(doc, f, mock, sector="KR-SEMI", name="삼성전자")
        a, b, c = cs.claims
        self.assertEqual(check_numbers(a, cs, f), "ok")
        self.assertEqual(len(a.quant_refs), 1)
        self.assertIn("명제에 없는 수치 1개 제외", a.validation.note)
        self.assertEqual(check_numbers(b, cs, f), "none")
        self.assertEqual(b.quant_refs, [])
        self.assertEqual(check_numbers(c, cs, f), "mismatch")
