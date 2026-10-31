"""두산에너빌리티(034020) A6 「촉매 & 타임라인」 — 본문 문장·설명회 Q&A·프로젝트 표에서 사건을 뽑는 층 A (2026-10-31).

검토문의 핵심 5개가 맞는 **시점·주제·단계** 로 나와야 한다:
  ① 연간 수주 목표 13.3조 (잔여 3.3조 → 후보 밖 미확인 1.1조)   ② X-energy 연내 후속 계약   ③ 미국 AP1000 기자재 발주 (연말~내년 초, 선행조건)
  ④ 가스터빈 슬롯 본계약 전환 2027·2028 (납기 2030·2031 은 사건 시점이 아니다)   ⑤ 중형 가스터빈 2028 하반기 출하
자료: tests/fixtures/reports/doosan/*.txt (유진투자증권 · 신한투자증권 2026-10-30 리포트 본문 — 이미지 PDF 1건은 OCR 없이 못 읽어 뺐다)
"""
from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from kstock.generate.catalysts import (
    MAX_EVENTS,
    event_kind,
    event_subject,
    event_topic,
    parse_when,
    same_event,
)
from kstock.generate.event_text import (
    TextUnit,
    amount_jo,
    annotate_order_target,
    events_from_unit,
    project_lines,
    report_text_events,
    split_candidates,
)
from kstock.models.sub import Event, EventCertainty, EventStatus
from kstock.reports.digest import ReportDigest
from kstock.reports.extract import extract_facts, extract_qa
from kstock.reports.loader import load_report, normalize_text

FIX = Path(__file__).resolve().parent / "fixtures" / "reports" / "doosan"
EUGENE = FIX / "eugene_034020_20261030.txt"
SHINHAN = FIX / "shinhan_034020_20261030.txt"
CLAIMS = Path(__file__).resolve().parent / "fixtures" / "claims" / "doosan"
TODAY = date(2026, 10, 30)
SECTOR = "KR-UTIL"


def _digest() -> tuple[ReportDigest, dict[str, str]]:
    docs = [load_report(p) for p in (EUGENE, SHINHAN)]
    facts = [extract_facts(d, hint_ticker="034020", today=TODAY) for d in docs]
    return ReportDigest.build(facts, ticker="034020", name="두산에너빌리티"), {d.path.name: d.text for d in docs}


def _ev(d: date, fact: str, cert: str = "회사 계획 (리포트 인용)", **kw) -> Event:  # noqa: ANN003
    return Event(date=d, fact=fact, certainty=cert, direction="미정", priced_in="미판단", **kw)


class TestQAAndTable(unittest.TestCase):
    def test_qa_items_with_page(self):
        qa = extract_qa(normalize_text(EUGENE.read_text(encoding="utf-8")))
        self.assertEqual(len(qa), 25)
        q1 = qa[0]
        self.assertEqual((q1.no, q1.page), (1, 2))
        self.assertEqual(q1.question, "4Q26 SMR 수주 목표 약 1 조원의 실현 가능성")
        self.assertTrue(q1.answers[0].startswith("고객사와 GTC 등 주요 계약 조건에 대한 합의를 대부분 완료"))
        q25 = next(q for q in qa if q.no == 25)
        self.assertEqual(q25.question, "2030년 가스터빈 예약 물량의 실제 계약 전환 시점")
        self.assertEqual(len(q25.answers), 2)                                   # 2030년분 → 내년, 2031년분 → 2028년
        self.assertIn(" — ", q25.text)

    def test_project_table_lines_carry_program(self):
        """프로젝트 표(「미국 / AP1000 / Fleet Project 10기」 가 셀로 쪼개진 것) 의 진척 줄에 사업명을 앞세운다 — 문장 추출기는 표라고 버리던 줄."""
        lines = project_lines(normalize_text(SHINHAN.read_text(encoding="utf-8")))
        self.assertEqual(len(lines), 12)
        self.assertIn("[V.C. Summer] 2028년 상반기 최종투자결정(FID) 예상", lines)
        self.assertIn("[불가리아 Kozloduy] 2027~2030년 중 FID·계약 체결·건설 준비 예상", lines)
        self.assertIn("[폴란드 AP1000] 2026년 12월까지 EPC 계약 목표.", lines)
        self.assertTrue(any(x.startswith("[미국 AP1000] ") and "2030년경 건설 목표" in x for x in lines))   # 쪼개진 셀 「미국」+「AP1000」 을 이어 붙인다
        self.assertFalse(any(x.startswith("[미국] ") for x in lines))


class TestTimeRoles(unittest.TestCase):
    """시점 분리 — 사건 시점(계약·발주·결정) 과 납기·운전 시점(출하·운전·건설) 은 다른 칸이다."""

    def test_contract_conversion_vs_delivery(self):
        w = parse_when("2030년 슬롯 예약 물량의 본계약 전환은 2027년, 2031년 슬롯은 2028년으로 예상되며, 수주잔고 증가가 기대된다.", TODAY)
        self.assertEqual((w.period, w.role), ("2027", "event"))
        self.assertEqual(w.delivery.period, "2030")

    def test_ismr_order_before_operation(self):
        w = parse_when("i-SMR은 2035년 운전을 목표로 2028년 이전 발주가 필요해 관련 협의 진행", TODAY)
        self.assertEqual((w.period, w.role, w.delivery.period), ("2028", "event", "2035"))

    def test_shipment_target_is_delivery_role(self):
        w = parse_when("중형 가스터빈은 시제품 제작 및 시험 진행 후 2028년 하반기 출하 목표", TODAY)
        self.assertEqual((w.period, w.role), ("2028H2", "delivery"))

    def test_year_end_to_early_next_year_picks_this_year(self):
        w = parse_when("연말~내년 초 발주 여부는 불확실하나 조기 발주 추진", TODAY)
        self.assertEqual((w.date, w.period), (date(2026, 12, 31), "2026"))

    def test_split_clauses_and_amount_items(self):
        two = split_candidates("2030년 슬롯 예약 물량의 본계약 전환은 2027년, 2031년 슬롯은 2028년으로 예상되며, 수주잔고 증가가 기대된다.", TODAY, TODAY)
        self.assertEqual([c[0] for c in two], ["2030년 슬롯 예약 물량의 본계약 전환은 2027년", "2031년 슬롯은 2028년으로 예상되며"])
        s = ("세부적으로는 SMR 1조원(계약 조건 합의 완료 단계로 연내 체결 가능), 신재생 7,000억원(해상풍력 기자재·EPC 2건 협의 완료 단계), "
             "해외 복합 EPC 5,000억원(국내 1건, 해외 2건)이 기대된다.")
        items = split_candidates(s, TODAY, TODAY)
        self.assertEqual([t for t, _, _ in items], ["SMR 1조원", "신재생 7,000억원", "해외 복합 EPC 5,000억원"])
        self.assertEqual(items[1][2], "해상풍력 기자재·EPC 2건 협의 완료 단계")
        self.assertAlmostEqual(amount_jo("신재생 7,000억원"), 0.7)
        self.assertEqual(amount_jo("연간 수주 목표 13.3조원"), 13.3)


class TestUnitEvents(unittest.TestCase):
    def _qa(self, question: str, answers: list[str], page: int = 5) -> TextUnit:
        return TextUnit(question + " — " + " · ".join(answers), "유진투자증권", TODAY, page, str(EUGENE), is_qa=True, question=question, answers=answers)

    def test_q25_two_conversion_events_with_delivery(self):
        evs = events_from_unit(self._qa("2030년 가스터빈 예약 물량의 실제 계약 전환 시점",
                                        ["2030년 출하 물량은 대부분 내년 실제 계약 여부 결정 전망", "2031년 출하 물량은 2028년경 계약 여부 결정 예상"]),
                               today=TODAY, sector=SECTOR)
        self.assertEqual([(e.period, e.delivery_period, e.program) for e in evs], [("2027", "2030", "가스터빈 슬롯"), ("2028", "2031", "가스터빈 슬롯")])
        self.assertTrue(all(e.certainty == EventCertainty.회사계획인용 for e in evs))     # 설명회 답 = 회사 말
        self.assertTrue(all(event_topic(e) == "orders" and e.materiality.value == "H" for e in evs))
        self.assertEqual(evs[0].event_id, "text:가스터빈_슬롯:orders:2027")
        self.assertIn("p.5", evs[0].source.name)

    def test_q14_mid_gas_turbine_launch(self):
        evs = events_from_unit(self._qa("중형 가스터빈 개발 및 해외 진출 계획", ["중형 가스터빈은 시제품 제작 및 시험 진행 후 2028년 하반기 출하 목표"]),
                               today=TODAY, sector=SECTOR)
        self.assertEqual(len(evs), 1)
        e = evs[0]
        self.assertEqual((e.period, e.program, event_topic(e), e.materiality.value), ("2028H2", "중형 가스터빈", "launch", "H"))
        self.assertIn("2028년 하반기 출하 목표", e.fact)

    def test_q10_us_ap1000_prerequisites(self):
        evs = events_from_unit(self._qa("대미 투자 관련 기자재 발주 시점",
                                        ["미국 부지 확정, 웨스팅하우스 사업제안 및 한국 정부 심사·국회 승인 절차 필요", "연말~내년 초 발주 여부는 불확실하나 조기 발주 추진"]),
                               today=TODAY, sector=SECTOR)
        self.assertEqual(len(evs), 1)
        e = evs[0]
        self.assertEqual((e.period, e.program, e.stage), ("2026", "미국 AP1000", "불확실"))
        self.assertTrue(any("국회 승인" in p for p in e.precheck))                       # 선행조건은 사전 확인으로

    def test_delivery_only_sentence_is_not_an_event(self):
        u = TextUnit("[가스터빈] 2031년 출하분까지 예약 접수", "유진투자증권", TODAY, None, str(EUGENE))
        self.assertEqual(events_from_unit(u, today=TODAY, sector=SECTOR), [])

    def test_completed_contract_in_october_is_kept_as_completed_not_earnings(self):
        u = TextUnit("[원전] 10월 NuScale 1개 모듈 제작 계약을 체결했으며, PPA 지연 여부와 무관하게 완결된 계약으로 실적에 반영될 전망이다.",
                     "유진투자증권", TODAY, None, str(EUGENE))
        evs = events_from_unit(u, today=TODAY, sector=SECTOR)
        self.assertEqual(len(evs), 1)
        e = evs[0]
        self.assertEqual((e.status, e.program, e.stage), (EventStatus.completed, "NuScale", "체결"))
        self.assertTrue(e.fact.startswith("[NuScale] 10월"))                              # 절 표시 「[원전]」 은 남기지 않는다
        self.assertNotEqual(event_kind(e), "earnings")                                     # 「실적에 반영」 은 실적 발표가 아니다


class TestReportTextEvents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d, texts = _digest()
        cls.events, cls.notes = report_text_events(d, today=TODAY, sector=SECTOR, texts=texts)
        cls.by_id = {e.event_id: e for e in cls.events}

    def _one(self, prefix: str) -> Event:
        hits = [e for k, e in self.by_id.items() if k.startswith(prefix)]
        self.assertEqual(len(hits), 1, f"{prefix}: {[k for k in self.by_id if k.startswith(prefix)]}")
        return hits[0]

    def test_key_five_catalysts(self):
        tgt = self._one("text:backlog:orders:2026")
        self.assertIn("13.3조원", tgt.fact)
        self.assertTrue(any("후보 밖 미확인 1.1조원" in p for p in tgt.precheck), tgt.precheck)   # 잔여 3.3 − (SMR 1 + 해상풍력 0.7 + 복합화력 0.5)
        xe = self._one("text:x_energy:orders:2026")
        self.assertEqual((xe.program, xe.stage, xe.certainty), ("X-energy", "협의 중", EventCertainty.회사계획인용))
        self.assertTrue(any("전열관" in p for p in xe.precheck), xe.precheck)                 # 시점 없는 Q&A(Q16) 답이 사전 확인으로 붙는다
        us = self._one("text:미국_ap1000:orders:2026")
        self.assertEqual((us.program, us.stage), ("미국 AP1000", "불확실"))
        self.assertTrue(any("부지 확정" in p for p in us.precheck))
        c27, c28 = self.by_id["text:가스터빈_슬롯:orders:2027"], self.by_id["text:가스터빈_슬롯:orders:2028"]
        self.assertEqual((c27.delivery_period, c28.delivery_period), ("2030", "2031"))
        self.assertTrue(any("본계약 전환은 2027년" in x for x in c27.evidence))               # 본문 문장은 같은 사건의 증거로 합쳐진다
        mid = self._one("text:중형_가스터빈:launch:2028h2")
        self.assertEqual((mid.period, mid.materiality.value), ("2028H2", "H"))

    def test_per_program_candidates_and_stages(self):
        smr = self._one("text:smr:orders:2026q4")
        self.assertEqual(smr.stage, "체결 대기")
        wind = self._one("text:해상풍력:orders:2026q4")
        self.assertEqual((wind.program, wind.stage), ("해상풍력", "조건 합의"))
        ccpp = self._one("text:복합화력_epc:orders:2026q4")
        self.assertEqual((ccpp.program, ccpp.stage), ("복합화력 EPC", "협의 중"))
        pl = self._one("text:폴란드_ap1000:orders:2026")
        self.assertTrue(any("2026년 12월까지 EPC 계약 목표" in x for x in pl.evidence))      # 신한 프로젝트 표 줄이 같은 사건으로 합쳐진다
        ismr = self._one("text:i_smr:orders:2028")
        self.assertEqual(ismr.delivery_period, "2035")
        self.assertEqual(event_kind(self._one("text:nuscale:orders:2026-10")), "target")

    def test_project_table_forecasts(self):
        vc = self._one("text:v_c_summer:project_progress:2028h1")
        self.assertEqual((vc.certainty, vc.period), (EventCertainty.리포트전망, "2028H1"))      # 대외 프로젝트의 「예상」 은 회사 계획이 아니다
        kz = self._one("text:불가리아_kozloduy:orders:2027~2030")
        self.assertEqual((kz.date, kz.period), (date(2027, 12, 31), "2027~2030"))

    def test_order_target_note(self):
        self.assertTrue(any("후보 밖 미확인 1.1조원" in n for n in self.notes), self.notes)
        # 다시 돌려도 사전 확인이 중복되지 않는다
        tgt = self._one("text:backlog:orders:2026")
        before = len(tgt.precheck)
        annotate_order_target(self.events)
        self.assertEqual(len(tgt.precheck), before)


class TestSameEventBySubject(unittest.TestCase):
    """기간 사건끼리는 끝 날짜(12-31) 가 같아도 주체·주제·기간으로 가른다 — 「수주 목표」 낱말로 연말 사건 7개가 하나가 되던 것."""

    def test_distinct_programs_same_period_are_distinct(self):
        a = _ev(date(2026, 12, 31), "[X-energy] 연내 기자재 후속 계약 추진", period="2026", program="X-energy", event_id="text:x_energy:orders:2026")
        b = _ev(date(2026, 12, 31), "[폴란드 AP1000] 장주기 기자재 발주 연내 수주 추진", period="2026", program="폴란드 AP1000", event_id="text:폴란드_ap1000:orders:2026")
        c = _ev(date(2026, 12, 31), "[수주잔고] 연간 수주 목표 13.3조원 잔여 수주 3.3조원", period="2026", event_id="text:backlog:orders:2026")
        d = _ev(date(2026, 12, 31), "[SMR] 4Q26 SMR 수주 목표 약 1조원", period="2026Q4", event_id="text:smr:orders:2026q4")
        self.assertFalse(same_event(a, b))
        self.assertFalse(same_event(c, d))                                                  # 드라이버 조각(backlog vs smr) 이 다르다
        self.assertFalse(same_event(a, c))
        self.assertEqual((event_subject(a), event_subject(c)), ("X-energy", "backlog"))

    def test_same_program_same_topic_overlap_is_same(self):
        a = _ev(date(2026, 12, 31), "[X-energy] 연내 기자재 후속 계약 추진", period="2026", program="X-energy", event_id="text:x_energy:orders:2026")
        b = _ev(date(2026, 12, 31), "[회사 계획] X-energy 후속 계약 연내 체결 목표", period="2026", program="X-energy", event_id="claim:c7")
        self.assertTrue(same_event(a, b))

    def test_claim_without_program_needs_a_specific_shared_word(self):
        text = _ev(date(2026, 12, 31), "[수주잔고] 연간 수주 목표 13.3조원 달성을 위한 잔여 수주 3.3조원", period="2026", event_id="text:backlog:orders:2026")
        claim = _ev(date(2026, 12, 31), "[회사 계획] 연간 수주 목표 13.3조원 달성 전망", period="2026", event_id="claim:c1")
        other = _ev(date(2026, 12, 31), "[회사 계획] 연내 해외 복합화력 수주 목표 달성 추진", period="2026", event_id="claim:c2")
        self.assertTrue(same_event(text, claim))                                             # 「13.3조원」 이 겹친다
        self.assertFalse(same_event(text, other))                                            # 「수주·목표·연내」 만 겹친다 — 일반 낱말

    def test_payout_forecast_still_merges_into_company_plan(self):
        plan = _ev(date(2027, 1, 31), "[회사 계획] 주주환원 규모의 구체적 결정은 27년 1월 이사회 이후", period="2027-01", event_id="claim:c16")
        fc = _ev(date(2027, 3, 31), "1Q27 주주환원계획부터 지속 가능한 정책 확인 기대", "리포트 전망", period="2027Q1", event_id="text:2027-03-31:x")
        self.assertTrue(same_event(plan, fc))


class TestDraftCap(unittest.TestCase):
    def test_draft_keeps_key_catalysts_and_notes_dropped(self):
        from kstock.generate.draft import draft_from_reports

        r = draft_from_reports([EUGENE, SHINHAN], ticker="034020", name="두산에너빌리티", sector=SECTOR, today=TODAY, claims="off", dart_events=False)
        kids = list(r.map.children("A6.1"))
        self.assertEqual(len(kids), MAX_EVENTS)
        ids = {n.event.event_id for n in kids}
        for key in ("text:backlog:orders:2026", "text:x_energy:orders:2026", "text:미국_ap1000:orders:2026",
                    "text:가스터빈_슬롯:orders:2027", "text:가스터빈_슬롯:orders:2028", "text:중형_가스터빈:launch:2028h2", "earnings:4Q26"):
            self.assertIn(key, ids)
        dropped = r.map.meta["draft"]["a6_1_dropped"]
        self.assertTrue(dropped and any("3Q26 확정실적" in x for x in dropped))              # 생략한 것은 적어 둔다
        self.assertTrue(any(n.startswith("A6.1 상한") for n in r.notes))
        self.assertEqual([n.event.date for n in kids], sorted(n.event.date for n in kids))
        # 납기는 사건 시점이 아니다 — 2030·2031 에 노드가 서지 않는다
        self.assertFalse(any(n.event.date.year >= 2030 for n in kids))


class TestClaimMerge(unittest.TestCase):
    """실제 Claim 저장본(hk_97300 신한 · hk_97311 유진, 촉매 역할만 발췌) 과 본문 이벤트가 **사업명 기준으로** 합쳐진다.
    사용자 지도(10-31, 층 A 이전) 는 A6.1 6건 — 「가스터빈은 2031년 출하분까지 예약」 이 2031 사건, SMR 1조가 폴란드 이벤트에 합쳐짐, 본계약 전환이 2030 사건이었다."""

    def test_cached_claims_merge_by_program_and_delivery_only_claim_is_skipped(self):
        import shutil
        import tempfile

        from kstock.generate.draft import draft_from_reports

        with tempfile.TemporaryDirectory() as t:
            rep = Path(t) / "reports"
            rep.mkdir()
            shutil.copy(SHINHAN, rep / "company_97300.txt")          # 파일명의 리포트 번호 → Claim 저장본 hk_97300 과 이어진다
            shutil.copy(EUGENE, rep / "company_97311.txt")
            r = draft_from_reports(sorted(rep.glob("*.txt")), ticker="034020", name="두산에너빌리티", sector=SECTOR, today=date(2026, 10, 31),
                                   claims="cached", claims_dir=CLAIMS, dart_events=False)
        kids = list(r.map.children("A6.1"))
        by_id = {n.event.event_id: n.event for n in kids}
        self.assertTrue(any("저장본 사용" in n for n in r.notes))
        # 신한 Claim c31 「폴란드 … 2026년 12월까지 EPC 계약 목표」 는 본문의 폴란드 이벤트 하나로 (같은 사업·주제·기간)
        pl = by_id["text:폴란드_ap1000:orders:2026"]
        self.assertTrue(any("Lubiatowo" in x for x in pl.evidence), pl.evidence)
        self.assertFalse(any((e.event_id or "").startswith("claim:") and e.program == "폴란드 AP1000" for e in by_id.values()))
        # 신한 Claim c10 「가스터빈은 2031년 출하분까지 예약·협상 진행 중」 — 납기만 있는 문장은 사건이 아니다
        self.assertNotIn("claim:hk_97300:c10", by_id)
        self.assertFalse(any(e.date.year >= 2030 for e in by_id.values()))
        self.assertTrue(any("납기만 있는 Claim" in n and "c10" in n for n in r.notes))
        # 유진 전망 Claim(c17 「내년 발주가 기대」) 은 본문의 미국 AP1000 2027 이벤트의 증거로
        us27 = by_id["text:미국_ap1000:orders:2027"]
        self.assertTrue(any("의회 승인" in x for x in us27.evidence))
        # 핵심 5개는 Claim 을 합쳐도 그대로
        for key in ("text:backlog:orders:2026", "text:x_energy:orders:2026", "text:미국_ap1000:orders:2026",
                    "text:가스터빈_슬롯:orders:2027", "text:가스터빈_슬롯:orders:2028", "text:중형_가스터빈:launch:2028h2"):
            self.assertIn(key, by_id)


if __name__ == "__main__":
    unittest.main()
