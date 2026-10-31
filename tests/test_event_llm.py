"""층 B — LLM 사건 제안 + 규칙 검증 (2026-10-31). LLM 은 가짜(고정 JSON) 다: 검증 규칙과 층 A 와의 합치기만 본다.

받는 조건: 인용이 원문에 있다 · 시점을 규칙이 다시 읽는다 · 납기만 있는 문장은 아니다 · 지난 일은 체결·완료만 · 사업명·단계는 사전에 있는 것만.
"""
from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from kstock.generate.catalysts import event_subject, merge_events, same_event
from kstock.generate.event_llm import (
    EVENT_SYSTEM,
    candidate_passages,
    event_prompt,
    llm_text_events,
    validate_proposals,
)
from kstock.generate.event_text import report_text_events
from kstock.models.sub import EventCertainty, EventStatus
from kstock.reports.digest import ReportDigest
from kstock.reports.extract import extract_facts
from kstock.reports.loader import load_report

FIX = Path(__file__).resolve().parent / "fixtures" / "reports" / "doosan"
EUGENE = FIX / "eugene_034020_20261030.txt"
SHINHAN = FIX / "shinhan_034020_20261030.txt"
TODAY = date(2026, 10, 30)
SECTOR = "KR-UTIL"

EUGENE_ANSWER = {"events": [
    # 받음 — 층 A 가 상한에서 생략한 Q18 답: 주체(사업명) 는 사전에 없다 → program None, raw_program 은 메모
    {"quote": "지분 투자는 내년, 기자재 계약은 연내 또는 내년 초 목표로 시점이 일치할지는 불확실", "what": "웨스팅하우스 지분 투자 집행 (내년)",
     "when_text": "내년", "when_role": "event", "delivery_text": None, "program": None, "raw_program": "웨스팅하우스 지분 투자",
     "stage": "불확실", "speaker": "company", "preconditions": [], "amount": None, "page": 5},
    # 받음 — 사건 2028 · 납기 2035 (LLM 의 when_text 가 아니라 규칙이 다시 읽는다)
    {"quote": "i-SMR은 2035년 운전을 목표로 2028년 이전 발주가 필요해 관련 협의 진행", "what": "i-SMR 발주 (2028년 이전)",
     "when_text": "2028년 이전", "when_role": "event", "delivery_text": "2035년 운전", "program": "i-SMR", "stage": "협의 중",
     "speaker": "company", "preconditions": [], "amount": None, "page": 5},
    # 받음 — 층 A 의 중형 가스터빈 사건과 같은 사건 → 합쳐지고 문장은 증거로
    {"quote": "중형 가스터빈은 시제품 제작 및 시험 진행 후 2028년 하반기 출하 목표", "what": "중형 가스터빈 첫 출하 (2028년 하반기)",
     "when_text": "2028년 하반기", "when_role": "delivery", "delivery_text": None, "program": "중형 가스터빈", "stage": None,
     "speaker": "company", "preconditions": ["시제품 제작 및 시험 진행"], "amount": None, "page": 4},
    # 버림 — 납기만 있는 문장
    {"quote": "현재 생산능력 증설 중이며 2031년 출하분까지 예약 접수", "what": "가스터빈 2031년 출하분 예약", "when_text": "2031년",
     "when_role": "delivery", "delivery_text": None, "program": "가스터빈 슬롯", "stage": None, "speaker": "company", "preconditions": [], "amount": None, "page": 5},
    # 버림 — 원문에 없는 문장 (LLM 이 지어낸 것)
    {"quote": "체코 두코바니 본계약은 2029년 상반기에 체결될 것이다", "what": "체코 본계약 2029H1", "when_text": "2029년 상반기",
     "when_role": "event", "delivery_text": None, "program": "체코 원전", "stage": None, "speaker": "analyst", "preconditions": [], "amount": None, "page": 2},
    # 버림 — 지난 일 (체결·완료가 아니다)
    {"quote": "3Q26 미국 빅테크 데이터센터향 가스터빈 첫 출하를 달성했으며", "what": "가스터빈 첫 출하 (3Q26)", "when_text": "3Q26",
     "when_role": "event", "delivery_text": None, "program": None, "raw_program": "데이터센터향 가스터빈", "stage": None, "speaker": "company",
     "preconditions": [], "amount": None, "page": 1},
    # 버림 — 인용에도 근처에도 시점이 없다 (LLM 의 「곧」 은 못 읽는다)
    {"quote": "TerraPower 주기기 3종 중 2종 계약이 완료되어 제작에 착수했으며", "what": "TerraPower 나머지 1종 계약", "when_text": "곧",
     "when_role": "event", "delivery_text": None, "program": "TerraPower", "stage": None, "speaker": "company", "preconditions": [], "amount": None, "page": 1},
    # 버림 — 인용이 너무 짧다 (문장이 아니다)
    {"quote": "X-energy는 연내 기자", "what": "?", "when_text": "연내", "when_role": "event", "delivery_text": None, "program": "X-energy",
     "stage": None, "speaker": "company", "preconditions": [], "amount": None, "page": 1},
]}


class FakeFiller:
    """프롬프트의 증권사 이름으로 답을 고른다. 모르는 리포트엔 빈 목록."""

    def __init__(self, answers: dict[str, dict]):
        self.answers = answers
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        for key, ans in self.answers.items():
            if key in user.split("\n", 1)[0]:
                return json.dumps(ans, ensure_ascii=False)
        return json.dumps({"events": []}, ensure_ascii=False)


def _docs_and_digest():
    docs = [load_report(p) for p in (EUGENE, SHINHAN)]
    facts = [extract_facts(d, hint_ticker="034020", today=TODAY) for d in docs]
    return docs, ReportDigest.build(facts, ticker="034020", name="두산에너빌리티")


class TestPassagesAndPrompt(unittest.TestCase):
    def test_passages_keep_timed_lines_and_qa_but_drop_tables(self):
        doc = load_report(EUGENE)
        p = candidate_passages(doc.text, TODAY)
        self.assertLess(len(p), len(doc.text) * 0.6)
        self.assertIn("Q25.", p)                                              # Q&A 블록은 통째로
        self.assertIn("2028년 하반기 출하 목표", p)
        self.assertIn("[p.", p)                                               # 쪽 표시
        self.assertNotIn("1Q25 2Q25 3Q25 4Q25", p)                            # 표 행은 뺀다
        prompt = event_prompt(p, broker="유진투자증권", report_date="2026-10-30", name="두산에너빌리티", ticker="034020", today=TODAY)
        self.assertIn("X-energy · NuScale", prompt)
        self.assertIn("불확실 → 협의 중", prompt)
        self.assertIn("JSON", EVENT_SYSTEM)


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.doc = load_report(EUGENE)
        self.v = validate_proposals(EUGENE_ANSWER["events"], text=self.doc.text, broker="유진투자증권", report_date=date(2026, 10, 30),
                                    path=str(EUGENE), today=TODAY, sector=SECTOR)

    def test_accept_and_reject_counts(self):
        self.assertEqual(len(self.v.accepted), 3)
        self.assertEqual(self.v.rejected, {"납기만 있음": 1, "인용 없음": 1, "지난 일": 1, "시점 못 읽음": 1, "인용 짧음": 1})
        self.assertIn("웨스팅하우스 지분 투자", self.v.unknown_programs)
        self.assertIn("받음 3", self.v.summary())

    def test_accepted_fields(self):
        by_prog = {e.program: e for e in self.v.accepted}
        ismr = by_prog["i-SMR"]
        self.assertEqual((ismr.period, ismr.delivery_period, ismr.stage), ("2028", "2035", "협의 중"))
        self.assertEqual(ismr.certainty, EventCertainty.회사계획인용)
        self.assertTrue(ismr.event_id.startswith("llm:i_smr:"))
        self.assertIn("p.4", ismr.source.name)                                      # 쪽은 LLM 의 말(5) 이 아니라 인용 위치로 센다
        self.assertTrue(ismr.evidence[0].startswith("(·) [유진투자증권] i-SMR은 2035년"))
        mid = by_prog["중형 가스터빈"]
        self.assertEqual((mid.period, mid.materiality.value), ("2028H2", "H"))
        self.assertEqual(mid.precheck, ["시제품 제작 및 시험 진행"])                   # 본문에 있는 선행조건만
        wh = by_prog[None]
        self.assertEqual((wh.period, wh.stage, wh.status), ("2027", "불확실", EventStatus.scheduled))
        self.assertEqual(event_subject(wh), "ma_investment")                      # 사전 밖 사업명 → 인용의 드라이버 조각(지분 투자) 이 주체

    def test_merges_with_layer_a_by_subject(self):
        _docs, d = _docs_and_digest()
        texts = {p.name: load_report(p).text for p in (EUGENE, SHINHAN)}
        a_events, _ = report_text_events(d, today=TODAY, sector=SECTOR, texts=texts)
        mid_a = next(e for e in a_events if e.event_id == "text:중형_가스터빈:launch:2028h2")
        mid_b = next(e for e in self.v.accepted if e.program == "중형 가스터빈")
        self.assertTrue(same_event(mid_a, mid_b))
        merged, _notes = merge_events(a_events, self.v.accepted)
        # i-SMR · 중형 가스터빈은 층 A 에 같은 사건이 있어 합쳐지고, 「지분 투자」 는 주체(ma_investment) 가 층 A 의 [원전 수주](nuclear_orders) 와 달라 따로 남는다
        self.assertEqual(len(merged), len(a_events) + 1)
        self.assertEqual(sum(1 for e in merged if e.program == "i-SMR"), 1)
        mid = next(e for e in merged if e.program == "중형 가스터빈")
        self.assertEqual(mid.event_id, "text:중형_가스터빈:launch:2028h2")           # 층 A 의 ID 가 남는다
        self.assertTrue(any("시제품 제작 및 시험 진행 후 2028년 하반기 출하 목표" in x for x in mid.evidence))   # LLM 문장은 증거로


class TestLlmTextEvents(unittest.TestCase):
    def test_per_report_call_and_notes(self):
        docs, d = _docs_and_digest()
        filler = FakeFiller({"유진투자증권": EUGENE_ANSWER})
        events, notes = llm_text_events(docs, d, filler, today=TODAY, sector=SECTOR)
        self.assertEqual(len(filler.calls), 2)                                       # 리포트마다 한 번
        self.assertTrue(all(len(u) < 7000 for _s, u in filler.calls))                 # 발췌만 보낸다
        self.assertEqual(len(events), 3)
        self.assertTrue(any("유진투자증권: LLM 제안 8건 → 받음 3" in n for n in notes), notes)
        self.assertTrue(any("신한투자증권: LLM 제안 0건" in n for n in notes), notes)

    def test_draft_merges_layer_b_into_a6(self):
        from kstock.generate.draft import draft_from_reports

        filler = FakeFiller({"유진투자증권": EUGENE_ANSWER})
        r = draft_from_reports([EUGENE, SHINHAN], ticker="034020", name="두산에너빌리티", sector=SECTOR, today=TODAY, claims="off",
                               dart_events=False, filler=filler, llm_events="auto")
        kids = {n.event.event_id: n.event for n in r.map.children("A6.1")}
        self.assertTrue(any(n.startswith("A6.1 LLM 사건 3건") for n in r.notes), [n for n in r.notes if "LLM" in n])
        mid = kids["text:중형_가스터빈:launch:2028h2"]
        self.assertTrue(any("시제품 제작 및 시험 진행 후" in x for x in mid.evidence))
        self.assertEqual(sum(1 for e in kids.values() if e.program == "중형 가스터빈"), 1)   # 같은 사건이 두 줄로 서지 않는다
        self.assertFalse(any(e.date.year >= 2030 for e in kids.values()))
        # 끄면 묻지 않는다
        filler2 = FakeFiller({"유진투자증권": EUGENE_ANSWER})
        draft_from_reports([EUGENE], ticker="034020", name="두산에너빌리티", sector=SECTOR, today=TODAY, claims="off", dart_events=False,
                           filler=filler2, llm_events="off")
        self.assertEqual(filler2.calls, [])
        # 켰는데 LLM 이 없으면 메모
        r3 = draft_from_reports([EUGENE], ticker="034020", name="두산에너빌리티", sector=SECTOR, today=TODAY, claims="off", dart_events=False,
                                llm_events="on")
        self.assertTrue(any("LLM 이 없어 건너뜀" in n for n in r3.notes))


if __name__ == "__main__":
    unittest.main()
