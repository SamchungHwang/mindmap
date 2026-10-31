"""촉매 & 타임라인 (A6.1) 보강 — 2026-10-30.

지키는 것:
    시점 읽기      날짜뿐 아니라 상반기·분기·연내 같은 **기간**도 읽는다 (기준일 = 기간 끝)
    실적 일정      회사별 과거 공시일이 있으면 그것으로, 없으면 통상 규칙. 4분기 확정은 사업보고서 90일
    DART 본문      IR 안내·유상증자·주총 본문에서 날짜를 읽고, 정정 공시가 이긴다, 지난 날짜는 버린다
    합치기         같은 사건이면 확실성 높은 출처가 남고 affects 를 넘겨받는다 · 날짜순 번호 · user 노드는 보존
    체크포인트     A8.1 은 next_check 가 있으면 그것
    모델·편집      새 칸이 편집 스키마에 나오고, 빈 outcome 은 저장되지 않는다
    사후 수익률    D-1 종가 대비 D+1·D+5, 같은 시장 평균 대비 — 시세가 모자라면 None (0 이 아니다)
    A6.2 낡음      시세가 오래되면 stale 로 표시
"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from helpers import D0  # noqa: E402, F401  (sys.path 설정)
from kstock.collect.adapters.dart import (  # noqa: E402
    DartItem,
    OpenDart,
    dart_events,
    events_from_items,
    kind_of,
    parse_agm,
    parse_ir_notice,
    parse_rights_offering,
    strip_tags,
)
from kstock.collect.adapters.research_db import ResearchDb  # noqa: E402
from kstock.engine import evaluate_new_map  # noqa: E402
from kstock.generate.catalysts import (  # noqa: E402
    earnings_calendar,
    merge_events,
    merge_into_map,
    parse_when,
    provisional_offset,
    same_event,
    set_catalysts,
)
from kstock.generate.edits import apply_edit_ops, edit_schemas  # noqa: E402
from kstock.models import Event, EventCertainty, EventOutcome  # noqa: E402
from kstock.render.format import event_detail_lines, event_label, event_line  # noqa: E402
from kstock.templates import new_map_skeleton  # noqa: E402

TODAY = date(2026, 10, 30)


# ---------------------------------------------------------------------------- 시점 읽기
class TestParseWhen(unittest.TestCase):
    def test_periods(self):
        cases = {
            "2027년 상반기 55MW 가동": (date(2027, 6, 30), "2027H1"),
            "1H27 가동 목표": (date(2027, 6, 30), "2027H1"),
            "2028년까지 200MW": (date(2028, 12, 31), "2028"),
            "2027년 1분기 가동": (date(2027, 3, 31), "2027Q1"),
            "2027년 3월 출시": (date(2027, 3, 31), "2027-03"),
            "연내 계약 체결": (date(2026, 12, 31), "2026"),
            "내년 출시": (date(2027, 12, 31), "2027"),
        }
        for text, (d, period) in cases.items():
            w = parse_when(text, TODAY)
            self.assertIsNotNone(w, text)
            self.assertEqual((w.date, w.period), (d, period), text)

    def test_dates(self):
        self.assertEqual(parse_when("11월 6일 납입", TODAY).date, date(2026, 11, 6))
        self.assertEqual(parse_when("2026-11-27 상장 예정", TODAY).date, date(2026, 11, 27))
        self.assertEqual(parse_when("2026년 11월 12일 (목) 09:00", TODAY).date, date(2026, 11, 12))
        self.assertIsNone(parse_when("11월 6일 납입", TODAY).period)
        # 지난 날짜도 읽는다 — 버리는 것은 부르는 쪽 (촉매가 아니라 사실 로그)
        self.assertEqual(parse_when("10월 27일 확정됐다", TODAY).date, date(2026, 10, 27))

    def test_relative_ranges_count_from_the_report_date(self):
        anchor = date(2026, 10, 28)
        w = parse_when("향후 6개월간 고객사 계약, GPU 설치 및 상용 서비스 개시가 가시화될 전망", TODAY, anchor=anchor)
        self.assertEqual((w.date, w.period, w.start), (date(2027, 4, 28), "~2027-04", anchor))      # 2027년 전체가 아니다
        self.assertEqual(parse_when("2년 내 흑자 전환", TODAY, anchor=anchor).date, date(2028, 10, 28))
        self.assertIsNone(parse_when("6개월 평균 거래대금", TODAY))                                 # 단위일 뿐 기간이 아니다
        self.assertIsNone(parse_when("12개월 목표주가 293,000원", TODAY))
        from kstock.generate.catalysts import time_clause

        w2 = parse_when("향후 6개월간 고객사 계약, GPU 설치 및 상용 서비스 개시", TODAY, anchor=anchor)
        self.assertEqual(time_clause("향후 6개월간 고객사 계약, GPU 설치 및 상용 서비스 개시", w2), "향후 6개월간 고객사 계약")

    def test_quarter_with_earnings_is_a_release_estimate(self):
        w = parse_when("4Q26 실적 발표", TODAY)
        self.assertEqual(w.date, date(2027, 1, 8))
        self.assertIsNone(w.period)

    def test_nothing(self):
        self.assertIsNone(parse_when("선결조건 충족으로 예정대로 이뤄질 것", TODAY))
        self.assertIsNone(parse_when("", TODAY))


# ---------------------------------------------------------------------------- 실적 일정
class TestEarningsCalendar(unittest.TestCase):
    HIST = [date(2025, 11, 5), date(2026, 2, 6), date(2026, 4, 30), date(2026, 8, 7)]   # NAVER 실측

    def test_company_history_wins(self):
        off, used = provisional_offset(self.HIST)
        self.assertEqual((off, used), (36, 4))
        cal = earnings_calendar(TODAY, self.HIST)
        self.assertEqual(cal[0].date, date(2026, 11, 5))
        self.assertEqual(cal[0].certainty, EventCertainty.과거주기)
        self.assertIn("과거 4분기", cal[0].basis)
        # 4분기 확정은 사업보고서 90일 — 2월 14일이 아니다
        q4_final = next(e for e in cal if "사업보고서" in e.label)
        self.assertEqual(q4_final.date, date(2027, 3, 31))

    def test_weekend_rolls_to_monday(self):
        from kstock.generate.catalysts import business_day

        self.assertEqual(business_day(date(2026, 11, 14)), date(2026, 11, 16))    # 토 → 월
        self.assertEqual(business_day(date(2026, 11, 15)), date(2026, 11, 16))    # 일 → 월
        self.assertEqual(business_day(date(2026, 11, 16)), date(2026, 11, 16))
        cal = earnings_calendar(date(2026, 10, 31), [])
        self.assertTrue(all(e.date.weekday() < 5 for e in cal))

    def test_default_rule_when_no_history(self):
        cal = earnings_calendar(TODAY, [])
        self.assertEqual(cal[0].certainty, EventCertainty.통상추정)
        self.assertTrue(all(e.date > TODAY for e in cal))
        one = earnings_calendar(TODAY, [date(2026, 8, 7)])          # 한 건은 우연일 수 있다
        self.assertEqual(one[0].certainty, EventCertainty.통상추정)


# ---------------------------------------------------------------------------- DART 본문
IR_XML = """<DOCUMENT><TABLE><TR><TD>1. 일시</TD><TD>2026년 11월 12일 (목) 09:00</TD></TR>
<TR><TD>2. 장소</TD><TD>컨퍼런스콜</TD></TR><TR><TD>4. 목적</TD><TD>2026년 3분기 경영실적 발표 및 Q&amp;A</TD></TR></TABLE></DOCUMENT>"""
RIGHTS_XML = """<DOCUMENT><TABLE><TR><TD>1. 신주의 종류와 수</TD><TD>보통주식 (주)</TD><TD>7,005,208</TD></TR>
<TR><TD>6. 신주 발행가액</TD><TD>보통주식 (원)</TD><TD>204,500</TD></TR>
<TR><TD>9. 납입일</TD><TD>2026년 11월 06일</TD></TR>
<TR><TD>10. 신주의 배당기산일</TD><TD>2026년 01월 01일</TD></TR>
<TR><TD>12. 신주의 상장 예정일</TD><TD>2026년 11월 27일</TD></TR>
<TR><TD>- 확정 발행가액 산정 기준일</TD><TD>2026-11-03</TD></TR></TABLE></DOCUMENT>"""
AGM_XML = """<DOCUMENT><TR><TD>1. 일시</TD><TD>2027년 03월 24일 (수) 10:00</TD></TR><TR><TD>2. 장소</TD><TD>경기도 성남시</TD></TR></DOCUMENT>"""


class TestDartParsing(unittest.TestCase):
    def test_kind_of_skips_subsidiaries(self):
        self.assertEqual(kind_of("기업설명회(IR)개최(안내공시)"), "ir")
        self.assertEqual(kind_of("[기재정정]주요사항보고서(유상증자결정)"), "rights")
        self.assertEqual(kind_of("주주총회소집결의"), "agm")
        self.assertIsNone(kind_of("[기재정정]유상증자결정(종속회사의주요경영사항)"))
        self.assertIsNone(kind_of("임원ㆍ주요주주특정증권등소유상황보고서"))

    def test_parsers(self):
        ir = parse_ir_notice(strip_tags(IR_XML))
        self.assertEqual((ir.date, ir.time), (date(2026, 11, 12), "09:00"))
        self.assertIn("실적", ir.label)
        found = {f.kind: f.date for f in parse_rights_offering(strip_tags(RIGHTS_XML))}
        self.assertEqual(found, {"납입일": date(2026, 11, 6), "신주상장예정일": date(2026, 11, 27), "발행가확정일": date(2026, 11, 3)})
        self.assertEqual(parse_agm(strip_tags(AGM_XML)).date, date(2027, 3, 24))

    def test_events_correction_wins_and_past_dropped(self):
        items = [DartItem("20261020800455", date(2026, 10, 20), "기업설명회(IR)개최(안내공시)"),
                 DartItem("20260727000001", date(2026, 7, 27), "주요사항보고서(유상증자결정)"),
                 DartItem("20261030000001", date(2026, 10, 30), "[기재정정]주요사항보고서(유상증자결정)"),
                 DartItem("20260901800215", date(2026, 9, 1), "기업설명회(IR)개최(안내공시)"),
                 DartItem("20260202801571", date(2026, 2, 2), "[기재정정]유상증자결정(종속회사의주요경영사항)")]
        old_rights = strip_tags(RIGHTS_XML.replace("11월 06일", "10월 30일").replace("11월 27일", "11월 20일"))
        old_ir = strip_tags(IR_XML.replace("2026년 11월 12일 (목) 09:00", "2026년 09월 03일 14:00").replace("3분기 경영실적 발표", "해외 컨퍼런스 참가"))
        texts = {"20261020800455": strip_tags(IR_XML), "20260727000001": old_rights, "20261030000001": strip_tags(RIGHTS_XML), "20260901800215": old_ir}
        evs, notes = events_from_items(items, lambda r: texts[r], today=TODAY, affects_ir=["A1.1"])
        self.assertEqual([(e.date, e.certainty.value) for e in evs],
                         [(date(2026, 11, 3), "공시확정"), (date(2026, 11, 6), "공시확정"), (date(2026, 11, 12), "공시확정"), (date(2026, 11, 27), "공시상 예정")])
        ir = next(e for e in evs if e.date == date(2026, 11, 12))
        self.assertEqual(ir.fact, "3Q26 실적발표·IR (09:00)")
        self.assertEqual(ir.affects, ["A1.1"])
        self.assertTrue(all(e.source.url.endswith("rcpNo=20261030000001") for e in evs if "유상증자" in e.source.name))
        self.assertEqual(notes, [])
        self.assertTrue(all(e.direction.value == "미정" and e.priced_in.value == "미판단" for e in evs))

    def test_real_dart_documents(self):
        """실제 DART 원문 (2026-10-31 캐시) — 셀마다 한 줄이고, 정정공시는 「항목 / 사유 / 정정 전 / 정정 후」 가 연달아 온다.
        10-31 초안은 첫 날짜(정정 전) 를 읽어 납입일(10-30, 지남) 을 버리고 상장예정일을 11-20(정정 전) 으로 적었다."""
        fx = Path(__file__).parent / "fixtures" / "dart"
        text = lambda no: (fx / f"{no}.txt").read_text(encoding="utf-8")  # noqa: E731
        corrected = {f.kind: f.date for f in parse_rights_offering(text("20261030000001"))}
        self.assertEqual(corrected, {"납입일": date(2026, 11, 6), "신주상장예정일": date(2026, 11, 27)})      # 정정 후
        original = {f.kind: f.date for f in parse_rights_offering(text("20260727000001"))}
        self.assertEqual(original, {"납입일": date(2026, 10, 30), "신주상장예정일": date(2026, 11, 20)})
        ir = parse_ir_notice(text("20261020800455"))
        self.assertEqual((ir.date, ir.time, ir.label), (date(2026, 11, 12), "09:00", "2026년 3분기 실적발표"))
        ndr = parse_ir_notice(text("20260901800215"))
        self.assertEqual((ndr.date, ndr.time, ndr.label), (date(2026, 9, 9), "", "9월 컨퍼런스 참석"))
        items = [DartItem("20261020800455", date(2026, 10, 20), "기업설명회(IR)개최(안내공시)"),
                 DartItem("20260727000001", date(2026, 7, 27), "주요사항보고서(유상증자결정)"),
                 DartItem("20261030000001", date(2026, 10, 30), "[기재정정]주요사항보고서(유상증자결정)"),
                 DartItem("20260901800215", date(2026, 9, 1), "기업설명회(IR)개최(안내공시)")]
        evs, notes = events_from_items(items, text, today=date(2026, 10, 31))
        self.assertEqual([(e.date, e.fact) for e in evs],
                         [(date(2026, 11, 6), "유상증자 납입"), (date(2026, 11, 12), "3Q26 실적발표·IR (09:00)"), (date(2026, 11, 27), "신주 상장 예정")])
        self.assertTrue(all(e.source.url.endswith("rcpNo=20261030000001") for e in evs if "유상증자" in e.fact or "상장" in e.fact))
        self.assertEqual(notes, [])

    def test_missing_key_item_is_noted(self):
        # 상장예정일만 있고 납입일이 없는 본문 — 조용히 지나가지 않는다
        items = [DartItem("1", date(2026, 10, 30), "주요사항보고서(유상증자결정)")]
        evs, notes = events_from_items(items, lambda r: "12. 신주의 상장 예정일\n2026년 11월 27일", today=TODAY)
        self.assertEqual([e.fact for e in evs], ["신주 상장 예정"])
        self.assertTrue(any("납입일" in n and "원문 확인" in n for n in notes), notes)

    def test_unreadable_body_is_a_note_not_a_crash(self):
        items = [DartItem("1", date(2026, 10, 20), "기업설명회(IR)개최(안내공시)")]
        evs, notes = events_from_items(items, lambda r: "본문에 날짜가 없다", today=TODAY)
        self.assertEqual(evs, [])
        self.assertTrue(notes and "못 읽었다" in notes[0])

    def test_dart_events_without_key_explains(self):
        db = _FakeDb([("20261020800455", date(2026, 10, 20), "기업설명회(IR)개최(안내공시)")])
        r = dart_events("035420", today=TODAY, db=db, dart=None)
        self.assertEqual(r.events, [])
        self.assertTrue(any("DART_API_KEY" in n for n in r.notes))

    def test_dart_events_with_fake_fetch(self):
        import io
        import zipfile

        def fetch(url: str) -> bytes:
            self.assertIn("crtfc_key=k", url)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("20261020800455.xml", IR_XML)
            return buf.getvalue()

        db = _FakeDb([("20261020800455", date(2026, 10, 20), "기업설명회(IR)개최(안내공시)")], until=TODAY)
        r = dart_events("035420", today=TODAY, db=db, dart=OpenDart("k", fetch=fetch))
        self.assertEqual([e.date for e in r.events], [date(2026, 11, 12)])
        self.assertEqual(r.source, "db")


class _FakeDb:
    def __init__(self, rows, until=None):  # noqa: ANN001
        from kstock.collect.adapters.research_db import DbDisclosure

        self.rows = [DbDisclosure(rcept_dt=d, report_nm=nm, rcept_no=no, corp_code="00266961") for no, d, nm in rows]
        self.until = until

    def disclosures(self, code, *, since, until=None):  # noqa: ANN001
        return self.rows

    def corp_code(self, code):  # noqa: ANN001
        return "00266961"


# ---------------------------------------------------------------------------- 합치기 · 맵
def _ev(d: date, fact: str, cert: str = "미정", **kw) -> Event:  # noqa: ANN003
    return Event(date=d, fact=fact, certainty=cert, direction="미정", priced_in="미판단", **kw)


class TestMerge(unittest.TestCase):
    def test_confirmed_replaces_estimate_and_keeps_affects(self):
        est = _ev(date(2026, 11, 5), "3Q26 잠정실적 발표 (과거 주기 추정)", "과거 주기 추정", affects=["A3.1.2"])
        conf = _ev(date(2026, 11, 12), "3Q26 실적발표·IR (09:00)", "공시확정", affects=["A1.1"])
        self.assertTrue(same_event(est, conf))
        out, notes = merge_events([est], [conf])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].certainty, EventCertainty.공시확정)
        self.assertEqual(out[0].affects, ["A1.1", "A3.1.2"])
        self.assertTrue(notes and "공시확정" in notes[0])

    def test_different_quarter_or_report_is_not_same(self):
        a = _ev(date(2026, 11, 5), "3Q26 잠정실적 발표")
        b = _ev(date(2026, 11, 14), "3Q26 확정실적·분기보고서 (법정 시한)")
        self.assertFalse(same_event(a, b))
        c = _ev(date(2027, 2, 5), "4Q26 잠정실적 발표")
        self.assertFalse(same_event(a, c))

    def test_lower_certainty_does_not_replace(self):
        conf = _ev(date(2026, 11, 6), "유상증자 납입", "공시확정")
        rep = _ev(date(2026, 11, 6), "[증권사 전망] 엔비디아 납입 예정 (하나증권)", "리포트 전망")
        out, notes = merge_events([conf], [rep])
        self.assertEqual([e.certainty for e in out], [EventCertainty.공시확정])
        self.assertEqual(notes, [])

    def test_set_catalysts_orders_and_keeps_user_nodes(self):
        m = new_map_skeleton("035420", "NAVER", "growth", TODAY)
        set_catalysts(m, [_ev(date(2027, 1, 8), "늦은 것"), _ev(date(2026, 11, 6), "이른 것", "공시확정")], today=TODAY)
        self.assertEqual([n.event.fact for n in m.children("A6.1")], ["이른 것", "늦은 것"])
        self.assertEqual(m["A6.1.1"].status.value, "confirmed")
        self.assertIn("공시확정", m["A6.1.1"].tags)
        self.assertEqual(m["A6.1.1"].next_check, date(2026, 11, 6))
        # 사람이 고친 노드는 남는다
        m["A6.1.2"].tags = sorted(set(m["A6.1.2"].tags) | {"user"})
        merge_into_map(m, [_ev(date(2026, 12, 1), "새 것")], today=TODAY)
        facts = [n.event.fact for n in m.children("A6.1")]
        self.assertIn("늦은 것", facts)
        self.assertIn("새 것", facts)
        self.assertEqual(facts.count("늦은 것"), 1)

    def test_checkpoints_use_next_check(self):
        m = new_map_skeleton("035420", "NAVER", "growth", TODAY)
        period = Event(date=date(2027, 6, 30), period="2027H1", fact="AI 팩토리 55MW 가동 목표", certainty="회사 목표", materiality="H",
                       next_check=date(2027, 4, 1), direction="미정", priced_in="미판단")
        minor = _ev(date(2026, 12, 1), "주주총회", "공시확정")                      # 중요도 M · affects 없음 → A8.1 에 안 들어간다
        set_catalysts(m, [period, _ev(date(2026, 11, 6), "납입", "공시확정", materiality="H"), minor], today=TODAY)
        evaluate_new_map(m, today=TODAY, enforce_gate=True)
        cps = m.value("A8.1")
        self.assertIn("2027-04-01", cps)
        self.assertIn("2026-11-06", cps)
        self.assertNotIn("2027-06-30", cps)
        self.assertNotIn("2026-12-01", cps)                                       # 판단에 안 걸리는 사건은 체크포인트가 아니다
        # 증거 일정에는 셋 다 있되, 걸리지 않는 것은 matters=False
        from kstock.engine.evidence import evidence_schedule

        rows = {x["what"][:4]: x for x in evidence_schedule(m, today=TODAY) if x["kind"] == "event"}
        self.assertFalse(rows["주주총회"]["matters"])
        self.assertTrue(rows["납입"]["matters"])


# ---------------------------------------------------------------------------- 모델 · 편집 · 표시
class TestModelAndEdits(unittest.TestCase):
    def test_schema_has_new_fields(self):
        keys = {r["key"] for r in edit_schemas()["subs"]["event"]}
        for k in ("period", "certainty", "precheck", "bull", "bear", "rule", "next_check", "outcome.verdict", "outcome.reaction_d5"):
            self.assertIn(k, keys)
        cert = next(r for r in edit_schemas()["subs"]["event"] if r["key"] == "certainty")
        self.assertIn("공시확정", cert["options"])
        self.assertFalse(next(r for r in edit_schemas()["subs"]["event"] if r["key"] == "period")["adv"])

    def test_old_event_json_still_loads(self):
        e = Event.model_validate({"date": "2027-01-08", "fact": "4Q26 잠정실적 발표", "affects": [], "direction": "중립", "priced_in": "아니오",
                                  "materiality": "H", "source": {"name": "통상 일정 기준 추정", "url": "", "retrieved_at": "2026-10-29"}})
        self.assertEqual(e.certainty, EventCertainty.미정)
        self.assertEqual(e.when_label(), "2027-01-08")
        self.assertTrue(e.is_dated())

    def test_edit_add_event_with_new_fields_and_empty_outcome(self):
        m = new_map_skeleton("035420", "NAVER", "growth", TODAY)
        ev = {"date": "2026-11-06", "fact": "NVIDIA 10억달러 유상증자 납입", "affects": [], "direction": "미정", "priced_in": "미판단",
              "materiality": "H", "certainty": "공시확정", "period": None, "precheck": ["납입 완료 공시", "최종 신주 수"],
              "bull": ["납입 완료·협업 진전"], "bear": ["납입 지연·조건 변경"], "rule": "납입만으로 BUY 금지", "next_check": None,
              "outcome": {"actual": None, "reaction_d1": None, "reaction_d5": None, "vs_market_d5": None, "verdict": None, "as_of": None, "note": None},
              "source": {"name": "DART", "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=1", "retrieved_at": "2026-10-30"}}
        res = apply_edit_ops(m, [{"op": "add", "parent": "A6.1", "id": "new:1", "fields": {"label": "납입", "event": ev}}], today=TODAY)
        n = m[res.id_map["new:1"]]
        self.assertEqual(n.event.certainty, EventCertainty.공시확정)
        self.assertEqual(n.event.precheck, ["납입 완료 공시", "최종 신주 수"])
        self.assertIsNone(n.event.outcome)                       # 전부 빈 outcome 은 저장하지 않는다
        self.assertIn("[공시 확인·일정 확정]", event_label(n.event))
        self.assertNotIn("미정", event_line(n.event))             # 사건 전엔 방향을 안 적는다
        lines = event_detail_lines(n.event)
        self.assertTrue(any(ln.startswith("사전 확인:") for ln in lines))
        self.assertTrue(any(ln.startswith("판단 규칙:") for ln in lines))

    def test_outcome_renders(self):
        e = Event(date=date(2026, 11, 6), fact="납입", certainty="공시확정", direction="미정", priced_in="미판단",
                  outcome=EventOutcome(reaction_d1=0.032, reaction_d5=-0.01, vs_market_d5=-0.025, verdict="약화"))
        line = event_line(e)
        self.assertIn("판정 약화", line)
        self.assertIn("D+1 +3.2%", line)
        self.assertIn("시장 대비 -2.5%", line)


# ---------------------------------------------------------------------------- 리서치 DB 어댑터
def _make_db(path: Path, *, last_day: date) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE prices(trade_date TEXT, stock_code TEXT, name TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
                            value REAL, market_cap REAL, shares REAL, market TEXT, sector TEXT);
        CREATE TABLE disclosures(rcept_no TEXT, stock_code TEXT, corp_code TEXT, report_nm TEXT, rcept_dt TEXT, flag_risk INT, flag_event INT);
    """)
    d = last_day - timedelta(days=120)
    closes = {"035420": 100.0, "000001": 50.0, "000002": 80.0}
    while d <= last_day:
        if d.weekday() < 5:
            for code, base in closes.items():
                step = 1.0 if code == "035420" and d >= date(2026, 11, 6) else 0.0   # 사건 뒤 올린다
                closes[code] = base + step if code == "035420" else base
                conn.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (d.isoformat(), code, code, closes[code], closes[code], closes[code], closes[code], 1000, 0, 0, 0, "KOSPI", ""))
        d += timedelta(days=1)
    rows = [("20251105800027", "2025-11-05", "영업(잠정)실적(공정공시)"), ("20260206800015", "2026-02-06", "연결재무제표기준영업(잠정)실적(공정공시)"),
            ("20260430800024", "2026-04-30", "영업(잠정)실적(공정공시)"), ("20260807800028", "2026-08-07", "영업(잠정)실적(공정공시)"),
            ("20260101000001", "2026-01-01", "영업(잠정)실적(공정공시)(자회사의 주요경영사항)"),
            ("20261020800455", "2026-10-20", "기업설명회(IR)개최(안내공시)")]
    for no, dt, nm in rows:
        conn.execute("INSERT INTO disclosures VALUES(?,?,?,?,?,0,0)", (no, "035420", "00266961", nm, dt))
    conn.commit()
    conn.close()


class TestResearchDbAdapter(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / "kstock.sqlite"
        _make_db(self.db_path, last_day=date(2026, 11, 20))
        self.db = ResearchDb(self.db_path)

    def test_earnings_dates_and_disclosures(self):
        self.assertEqual(self.db.earnings_dates("035420"), [date(2025, 11, 5), date(2026, 2, 6), date(2026, 4, 30), date(2026, 8, 7)])   # 자회사 건 제외
        rows = self.db.disclosures("035420", since=date(2026, 10, 1))
        self.assertEqual([r.rcept_no for r in rows], ["20261020800455"])
        self.assertTrue(rows[0].url.endswith("rcpNo=20261020800455"))
        self.assertEqual(self.db.corp_code("035420"), "00266961")

    def test_reaction(self):
        r = self.db.reaction("035420", date(2026, 11, 6))
        self.assertEqual(r["d0"], "2026-11-06")
        self.assertAlmostEqual(r["close_prev"], 100.0)
        self.assertAlmostEqual(r["r1"], 2 / 100)                     # D0 +1, D+1 +1 → 102/100
        self.assertAlmostEqual(r["r5"], 6 / 100)
        self.assertAlmostEqual(r["mkt5"], (0.06 + 0 + 0) / 3)        # 같은 시장 3종목 평균
        self.assertAlmostEqual(r["vs_market_d5"], 0.06 - 0.02)
        # 아직 D+5 가 없으면 None 이지 0 이 아니다
        late = self.db.reaction("035420", date(2026, 11, 19))
        self.assertIsNotNone(late["r1"])
        self.assertIsNone(late["r5"])
        self.assertIsNone(self.db.reaction("035420", date(2027, 1, 1)))

    def test_fill_outcomes_on_map(self):
        from kstock.generate.draft import fill_event_outcomes

        m = new_map_skeleton("035420", "NAVER", "growth", date(2026, 11, 20))
        set_catalysts(m, [_ev(date(2026, 11, 6), "유상증자 납입", "공시확정"),
                          Event(date=date(2027, 6, 30), period="2027H1", fact="55MW", certainty="회사 목표", direction="미정", priced_in="미판단")],
                      today=date(2026, 11, 20))
        notes = fill_event_outcomes(m, self.db, today=date(2026, 11, 20))
        self.assertEqual(len(notes), 1)
        o = m["A6.1.1"].event.outcome
        self.assertAlmostEqual(o.reaction_d5, 0.06)
        self.assertIsNone(o.verdict)                                 # 판정은 사람 몫
        self.assertIn("강화로 읽힌다", o.note)
        self.assertIsNone(m["A6.1.2"].event.outcome)                 # 기간 사건은 안 잰다

    def test_stale_flow_marked(self):
        from kstock.generate.draft import _enrich_from_db
        from kstock.reports.digest import ReportDigest

        m = new_map_skeleton("035420", "NAVER", "growth", date(2026, 12, 15))   # 시세는 11-20 까지 → 25일 전
        notes: list[str] = []
        digest = ReportDigest.build([], ticker="035420", name="NAVER", today=date(2026, 12, 15))
        _enrich_from_db(m, self.db, "035420", today=date(2026, 12, 15), research_db=self.db_path, digest=digest, notes=notes)
        self.assertEqual(m["A6.2.1"].status.value, "stale")
        self.assertIn("⚠", m["A6.2"].question)
        # 2026-10-31 네 번째 검토: 낡은 시세의 「상승」 은 값 칸에 들어가지 않는다 — 분석 불가 + 당시 계산은 참고로만
        self.assertIsNone(m["A6.2.1"].value)
        self.assertIsNone(m["A6.2.2"].value)
        self.assertIn("분석 불가", m["A6.2.1"].question)
        self.assertIn("당시 계산(참고)", m["A6.2.1"].question)
        fs = m.meta["draft"]["flow_stale"]
        self.assertEqual((fs["as_of"], fs["age_days"]), ("2026-11-20", 25))
        self.assertTrue(any("분석 불가" in n for n in notes))
        # 시세가 신선하면 값이 들어간다
        m2 = new_map_skeleton("035420", "NAVER", "growth", date(2026, 11, 23))
        _enrich_from_db(m2, self.db, "035420", today=date(2026, 11, 23), research_db=self.db_path, digest=digest, notes=[])
        self.assertIsNotNone(m2["A6.2.1"].value)
        self.assertNotEqual(m2["A6.2.1"].status.value, "stale")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------- 초안 통합 — 규칙 + 리서치 DB 과거 공시일 + DART 캐시 본문
class TestDraftIntegration(unittest.TestCase):
    """리포트 → 초안에서 A6.1 이 세 출처로 조립되는지. 네트워크 없이: DART 본문은 캐시 폴더(dart_doc) 에 미리 둔다."""

    def test_draft_assembles_a6(self):
        import os
        import tempfile
        from unittest import mock

        from kstock.generate.draft import draft_from_reports

        tmp = Path(tempfile.mkdtemp())
        rdb = tmp / "research" / "data" / "db" / "kstock.sqlite"
        rdb.parent.mkdir(parents=True)
        today = date(2026, 10, 20)
        conn = sqlite3.connect(rdb)
        conn.executescript("""
            CREATE TABLE prices(trade_date TEXT, stock_code TEXT, name TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
                                value REAL, market_cap REAL, shares REAL, market TEXT, sector TEXT);
            CREATE TABLE disclosures(rcept_no TEXT, stock_code TEXT, corp_code TEXT, report_nm TEXT, rcept_dt TEXT, flag_risk INT, flag_event INT);
        """)
        d = today - timedelta(days=100)
        while d <= today:
            if d.weekday() < 5:
                conn.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (d.isoformat(), "005930", "삼성전자", 100, 100, 100, 100, 1000, 0, 0, 0, "KOSPI", ""))
            d += timedelta(days=1)
        # 삼성전자식 잠정 공시 — 분기 말 + 7~8일
        for no, dt in (("1", "2026-01-08"), ("2", "2026-04-07"), ("3", "2026-07-07"), ("4", "2026-10-08")):
            conn.execute("INSERT INTO disclosures VALUES(?,?,?,?,?,0,0)", (no, "005930", "00126380", "연결재무제표기준영업(잠정)실적(공정공시)", dt))
        conn.execute("INSERT INTO disclosures VALUES(?,?,?,?,?,0,0)", ("20261015800001", "005930", "00126380", "기업설명회(IR)개최(안내공시)", "2026-10-15"))
        conn.commit()
        conn.close()
        cache = tmp / "dart_cache"
        cache.mkdir(parents=True)
        (cache / "20261015800001.txt").write_text(strip_tags(IR_XML.replace("2026년 11월 12일 (목) 09:00", "2026년 10월 30일 (금) 10:00")), encoding="utf-8")
        with mock.patch.dict(os.environ, {"DART_API_KEY": "k", "KSTOCK_OFFLINE": "1", "KSTOCK_DART_CACHE": str(cache)}):
            r = draft_from_reports(sorted((Path(__file__).parent / "fixtures" / "reports").glob("*.txt")), today=today, research_db=rdb)
        m = r.map
        cats = m.catalysts()
        confirmed = [e for e in cats if e.certainty == EventCertainty.공시확정]
        self.assertEqual([e.date for e in confirmed], [date(2026, 10, 30)])
        self.assertEqual(confirmed[0].fact, "3Q26 실적발표·IR (10:00)")
        # 과거 주기 추정(4Q26 잠정 ≈ 1월 7~8일) 은 남고, 3Q 잠정 추정(10/8, 지남) 은 없다
        self.assertTrue(any(e.certainty == EventCertainty.과거주기 and e.date.month == 1 for e in cats), [e.fact for e in cats])
        self.assertTrue(all(e.date > today for e in cats))
        self.assertTrue(any("공시확정 촉매 1건" in n for n in r.notes), r.notes)
        self.assertEqual(m.meta["draft"]["dart_events"]["n"], 1)
        # A8.1 체크포인트에 공시 날짜가 들어간다
        self.assertIn("2026-10-30", m.value("A8.1"))
        # 어댑터가 네트워크를 안 탔다 — 캐시로만 (호출 수는 draft 안에서 못 세니, 문서가 캐시에 있었다는 것으로 갈음)
        self.assertTrue((cache / "20261015800001.txt").exists())


# ---------------------------------------------------------------------------- 연결 · 증거 · 영속 ID (2026-10-31)
def _naver_map(today: date = date(2026, 10, 31)):  # noqa: ANN202
    """NAVER 꼴의 맵 — A1 원인 드라이버 다섯 (영업이익 라벨 없음) + 섹터 + driver_id 메타."""
    from kstock.models import Driver
    from kstock.templates.questions import A_SPEC
    from kstock.templates.skeleton import _node_from_spec

    m = new_map_skeleton("035420", "NAVER", "growth", today, n_drivers=5)
    labels = {"A1.1": ("ad_growth", "광고 성장"), "A1.2": ("commerce", "커머스 거래액·테이크레이트"), "A1.3": ("fintech", "핀테크 결제액"),
              "A1.4": ("marketing_cost", "마케팅·인프라 비용"), "A1.5": ("ai_dc", "AI 데이터센터·클라우드")}
    m.meta.setdefault("draft", {})["sector"] = "KR-INTERNET"
    m.meta["draft"]["drivers"] = {}
    for nid, (did, label) in labels.items():
        if nid not in m.nodes:
            m.add(_node_from_spec(m.kind, nid, A_SPEC["A1.1"], label=label))
        n = m[nid]
        n.label = label
        n.driver = Driver(role="cause", kind="qualitative")
        m.meta["draft"]["drivers"][nid] = {"driver_id": did}
    return m


class TestLinking(unittest.TestCase):
    def test_affects_by_kind(self):
        from kstock.generate.catalysts import link_affects, link_catalysts

        m = _naver_map()
        ir = _ev(date(2026, 11, 12), "3Q26 실적발표·IR (09:00)", "공시확정")
        self.assertEqual(link_affects(m, ir), ["A1.1", "A1.2", "A1.3", "A1.4", "A1.5", "A3.1"])
        pay = _ev(date(2026, 11, 6), "유상증자 납입", "공시확정")
        self.assertEqual(link_affects(m, pay), ["A3.2", "A3.3", "A5.4"])
        tgt = Event(date=date(2027, 6, 30), period="2027H1", fact="[기업 계획] 1H27 내 구축 예정인 55MW AI팩토리", certainty="회사 목표",
                    direction="미정", priced_in="미판단")
        self.assertEqual(link_affects(m, tgt), ["A1.5"])                      # 문장의 AI팩토리 → ai_dc → A1.5
        set_catalysts(m, [ir, pay, tgt], today=date(2026, 10, 31))
        notes = link_catalysts(m)
        self.assertEqual(len(notes), 3)
        for n in m.children("A6.1"):
            self.assertTrue(n.event.affects, n.id)
            self.assertTrue(n.event.bull and n.event.bear, n.id)                # 종류별 상방·하방 초안
            self.assertEqual(n.links, [a for a in n.event.affects if a in m.nodes])
        # 사람이 적은 affects·조건은 덮지 않는다
        n = m.children("A6.1")[0]
        n.event = n.event.model_copy(update={"affects": ["A1.2"], "bull": ["내가 쓴 것"]})
        link_catalysts(m)
        self.assertEqual((n.event.affects, n.event.bull), (["A1.2"], ["내가 쓴 것"]))

    def test_analyst_claims_become_evidence_not_events(self):
        from kstock.generate.catalysts import link_catalysts
        from kstock.generate.mapping import apply_analyst_catalysts, apply_claims_to_map
        from kstock.models import Claim, ClaimSet, EvidenceSpan
        from kstock.reports.digest import ReportDigest

        today = date(2026, 10, 31)
        m = _naver_map(today)

        def claim(cid, rid, broker, prop, *, certainty, direction, did="ai_dc", asserted_by="analyst"):  # noqa: ANN001, ANN202
            return Claim(claim_id=cid, report_id=rid, broker=broker, report_date=date(2026, 10, 28), proposition=prop, driver_id=did,
                         roles=["catalyst"], status="verified", certainty=certainty, direction=direction, asserted_by=asserted_by, span_ids=["s1"])

        ls = ClaimSet(report_id="hk_653352", broker="LS증권", path="ls.pdf", spans=[EvidenceSpan(span_id="s1", report_id="r", page=1, start=0, end=2, text="p1")], claims=[
            claim("c5", "hk_653352", "LS증권", "네이버는 1H27 내 55MW 규모 AI 팩토리 구축을 완료할 계획이다", certainty="planned", direction="positive", asserted_by="company"),
            claim("c6", "hk_653352", "LS증권", "AI팩토리 사업의 수익성과 현금흐름은 1H27 내 구축 예정인 55MW에 대한 고객 확보와 구매계약 조건이 장기 지급의무를 상쇄하는지에 달렸다",
                  certainty="conditional", direction="neutral"),
        ])
        hana = ClaimSet(report_id="hk_97166", broker="하나증권", path="hana.pdf", spans=[EvidenceSpan(span_id="s1", report_id="r", page=1, start=0, end=2, text="p1")], claims=[
            claim("c11", "hk_97166", "하나증권", "향후 6개월간 고객사 계약, GPU 설치 및 상용 서비스 개시와 함께 초기 매출 발생이 순차적으로 가시화될 전망이다",
                  certainty="expected", direction="positive"),
            claim("c12", "hk_97166", "하나증권", "2028년 커머스 거래액이 둔화될 위험이 있다", certainty="expected", direction="negative", did="commerce"),
        ])
        digest = ReportDigest.build([], ticker="035420", name="NAVER", today=today)
        apply_claims_to_map(m, [ls, hana], None, digest, today=today, name="NAVER")
        cats = m.catalysts()
        # 회사 계획만 이벤트 — 리포트가 「회사 계획」 이라고 적은 것이라 등급은 「회사 계획 (리포트 인용)」 (회사 원문 미확인 · 2026-10-31 3차 검토)
        self.assertEqual([(e.period, e.certainty.value) for e in cats], [("2027H1", "회사 계획 (리포트 인용)")])
        self.assertTrue(cats[0].fact.startswith("[회사 계획]"))
        self.assertLessEqual(len(cats[0].fact), 80)                                                # 시점이 든 절만
        self.assertEqual(cats[0].event_id, "claim:c5")
        link_catalysts(m)
        tgt0 = m.children("A6.1")[0].event
        self.assertIn("유상 계약", tgt0.rule)                                                      # 회사 계획에도 판단 규칙 초안이 든다
        self.assertTrue({"A3.3", "A4.1"} <= set(tgt0.affects), tgt0.affects)                       # 중요도 H 계획 → 평가 모델·시나리오
        notes = apply_analyst_catalysts(m, [ls, hana], today=today)
        n = m.children("A6.1")
        target = next(x for x in n if x.event.period == "2027H1")
        # 전망은 bull/bear 가 아니라 **관련 전망(evidence)** 에 — 방향 표시를 달고 (2026-10-31 검토: 실적 bull 에 AI 전망이 섞였었다)
        self.assertTrue(any(b.startswith("(+)") and "하나증권" in b and "향후 6개월" in b for b in target.event.evidence))   # 같은 드라이버(A1.5)
        self.assertTrue(any(b.startswith("(·)") and "LS증권" in b and "고객 확보" in b for b in target.event.evidence))     # 같은 기간의 조건부
        self.assertFalse(any("하나증권" in b for b in target.event.bull + target.event.bear))
        others = [x for x in n if x is not target]
        self.assertEqual(len(others), 1)                                                            # 붙일 곳 없는 커머스 전망만 따로
        self.assertEqual(others[0].event.certainty.value, "리포트 전망")
        self.assertEqual(others[0].event.period, "2028")
        self.assertTrue(others[0].event.evidence and others[0].event.evidence[0].startswith("(−)"))   # 문장은 그 이벤트의 관련 전망으로
        self.assertEqual(others[0].event.bear, [])
        self.assertNotIn("둔화될 위험이 있다", others[0].event.fact[:20])
        self.assertTrue(any("증거" in x for x in notes), notes)

    def test_forecasts_do_not_land_on_the_earnings_event(self):
        """실적 이벤트는 A1 전부에 걸려 있어 어떤 드라이버 전망이든 「일치」 했다 — 실적 이벤트엔 기간이 맞고 실적을 말하는 문장만 붙는다."""
        from kstock.generate.catalysts import attach_evidence, link_catalysts, parse_when

        today = date(2026, 10, 31)
        m = _naver_map(today)
        ir = _ev(date(2026, 11, 12), "3Q26 실적발표·IR (09:00)", "공시확정")
        tgt = Event(date=date(2027, 6, 30), period="2027H1", fact="[회사 계획] 1H27 내 55MW AI 팩토리 구축", certainty="회사 계획 (리포트 인용)",
                    direction="미정", priced_in="미판단")
        set_catalysts(m, [ir, tgt], today=today)
        link_catalysts(m)
        ir_node = next(x for x in m.children("A6.1") if x.event.event_id is None and "실적" in x.event.fact)
        # ① 기간이 안 맞는 AI 전망 (드라이버는 A1.5 — 실적 이벤트의 affects 에 들어 있다) → 실적 이벤트엔 안 붙는다
        w = parse_when("향후 6개월간 고객사 계약, GPU 설치", today, anchor=date(2026, 10, 28))
        nid = attach_evidence(m, when=w, driver_node="A1.5", text="[하나증권] 향후 6개월간 고객사 계약, GPU 설치", direction="positive", today=today)
        self.assertNotEqual(nid, ir_node.id)
        self.assertEqual(ir_node.event.evidence, [])
        # ② 기간은 맞지만 실적 말이 없는 문장 → 안 붙는다
        w2 = parse_when("11월 중 글로벌 AI 테넌트 계약 가능성", today, anchor=date(2026, 10, 28))
        nid2 = attach_evidence(m, when=w2, driver_node="A1.5", text="[대신증권] 11월 중 글로벌 AI 테넌트 계약 가능성", direction="positive", today=today)
        self.assertNotEqual(nid2, ir_node.id)
        # ③ 기간도 맞고 실적을 말한다 → 붙는다 (관련 전망에)
        w3 = parse_when("3Q26 실적은 영업이익 컨센 상회가 예상된다", today, anchor=date(2026, 10, 28))
        nid3 = attach_evidence(m, when=w3, driver_node="A1.1", text="[하나증권] 3Q26 실적은 영업이익 컨센 상회가 예상된다", direction="positive", today=today)
        self.assertEqual(nid3, ir_node.id)
        self.assertEqual(len(ir_node.event.evidence), 1)
        self.assertEqual(len(ir_node.event.bull), 2)                                                 # 조건 초안은 그대로, 전망이 섞이지 않았다


class TestStableIds(unittest.TestCase):
    def test_numbers_do_not_move_when_events_are_inserted(self):
        from kstock.generate.catalysts import merge_into_map

        m = _naver_map()
        a = Event(date=date(2027, 1, 8), fact="4Q26 잠정실적 발표", certainty="과거 주기 추정", event_id="earnings:4Q26", direction="미정", priced_in="미판단")
        b = Event(date=date(2027, 3, 31), fact="4Q26 사업보고서 (법정 시한)", certainty="통상 일정 추정", event_id="report:4Q26", direction="미정", priced_in="미판단")
        set_catalysts(m, [a, b], today=date(2026, 10, 31))
        self.assertEqual([(n.id, n.event.event_id) for n in m.children("A6.1")], [("A6.1.1", "earnings:4Q26"), ("A6.1.2", "report:4Q26")])
        # 더 이른 공시 이벤트가 끼어든다 — 기존 번호는 그대로, 새 것은 다음 번호, 보이는 순서는 날짜순
        pay = Event(date=date(2026, 11, 6), fact="유상증자 납입", certainty="공시확정", event_id="rights:납입일", direction="미정", priced_in="미판단")
        merge_into_map(m, [pay], today=date(2026, 10, 31))
        self.assertEqual([(n.id, n.event.event_id) for n in m.children("A6.1")],
                         [("A6.1.3", "rights:납입일"), ("A6.1.1", "earnings:4Q26"), ("A6.1.2", "report:4Q26")])
        # 추정이 공시로 바뀌어도 (같은 사건 · 같은 ID) 번호는 그대로
        ir = Event(date=date(2027, 2, 5), fact="4Q26 실적발표·IR", certainty="공시확정", event_id="earnings:4Q26", direction="미정", priced_in="미판단")
        merge_into_map(m, [ir], today=date(2026, 10, 31))
        n = m["A6.1.1"]
        self.assertEqual((n.event.certainty.value, n.event.date), ("공시확정", date(2027, 2, 5)))
        # Claim ID 만 있는 것이 같은 사건을 대신해도 옛 ID·번호를 잇는다
        c = Event(date=date(2027, 2, 6), fact="[기업 발표] 4Q26 실적 발표 (하나증권)", certainty="회사 목표", event_id="claim:x", direction="미정", priced_in="미판단")
        merge_into_map(m, [c], today=date(2026, 10, 31))
        self.assertEqual(m["A6.1.1"].event.event_id, "earnings:4Q26")
        self.assertEqual(m["A6.1.1"].event.certainty.value, "공시확정")          # 약한 출처는 센 것을 못 밀어낸다

    def test_delayed_status_when_confirmed_date_moves(self):
        from kstock.generate.catalysts import merge_events

        old = Event(date=date(2026, 11, 20), fact="신주 상장 예정", certainty="공시상 예정", event_id="rights:신주상장예정일", direction="미정", priced_in="미판단")
        new = Event(date=date(2026, 11, 27), fact="신주 상장 예정", certainty="공시상 예정", event_id="rights:신주상장예정일", direction="미정", priced_in="미판단")
        out, notes = merge_events([old], [new])
        self.assertEqual((out[0].date, out[0].status.value), (date(2026, 11, 27), "delayed"))

    def test_edit_then_regenerate_keeps_user_edit_on_same_event(self):
        """편집 → 재생성 왕복: 사람이 A6.1.1(납입) 에 적은 상방 조건이, 새 이벤트가 끼어든 뒤에도 같은 사건에 남는다."""
        from kstock.generate.catalysts import merge_into_map
        from kstock.generate.edits import apply_edit_ops

        today = date(2026, 10, 31)
        m = _naver_map(today)
        pay = Event(date=date(2026, 11, 6), fact="유상증자 납입", certainty="공시확정", event_id="rights:납입일", direction="미정", priced_in="미판단")
        set_catalysts(m, [pay], today=today)
        res = apply_edit_ops(m, [{"op": "set", "id": "A6.1.1", "fields": {"event": {**m["A6.1.1"].event.model_dump(mode="json"), "bull": ["납입 + 협업 발표"]}}}], today=today)
        self.assertTrue(res.applied)
        self.assertIn("user", m["A6.1.1"].tags)
        # 재생성: 더 이른 이벤트와 같은 납입 이벤트(정정으로 날짜 유지) 가 들어온다
        ir = Event(date=date(2026, 11, 2), fact="IR·기업설명회 — 컨퍼런스", certainty="공시확정", event_id="ir:2026-11-02", direction="미정", priced_in="미판단")
        merge_into_map(m, [ir, pay], today=today)
        self.assertEqual(m["A6.1.1"].event.bull, ["납입 + 협업 발표"])            # 사람이 고친 노드는 그대로
        self.assertEqual(m["A6.1.1"].event.event_id, "rights:납입일")
        ids = [(n.id, n.event.event_id) for n in m.children("A6.1")]
        self.assertEqual(ids, [("A6.1.2", "ir:2026-11-02"), ("A6.1.1", "rights:납입일")])


# ---------------------------------------------------------------------------- 종단: 원문 → 추출 → 병합 → 생성 → 편집 → 재생성 → 결과 → 재생성
class TestEndToEnd(unittest.TestCase):
    def test_full_cycle_with_real_dart_documents(self):
        import os
        import tempfile
        from unittest import mock

        from kstock.generate.draft import _fill_dart_events, draft_from_reports, fill_event_outcomes
        from kstock.generate.edits import apply_edit_ops

        fx = Path(__file__).parent / "fixtures" / "dart"
        tmp = Path(tempfile.mkdtemp())
        rdb = tmp / "research" / "data" / "db" / "kstock.sqlite"
        rdb.parent.mkdir(parents=True)
        today = date(2026, 10, 31)
        conn = sqlite3.connect(rdb)
        conn.executescript("""
            CREATE TABLE prices(trade_date TEXT, stock_code TEXT, name TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
                                value REAL, market_cap REAL, shares REAL, market TEXT, sector TEXT);
            CREATE TABLE disclosures(rcept_no TEXT, stock_code TEXT, corp_code TEXT, report_nm TEXT, rcept_dt TEXT, flag_risk INT, flag_event INT);
        """)
        d, close = today - timedelta(days=100), 100.0
        while d <= date(2026, 11, 25):
            if d.weekday() < 5:
                if d >= date(2026, 11, 6):
                    close += 1.0   # 사건 뒤 오른다
                for code in ("005930", "000001"):
                    c = close if code == "005930" else 50.0
                    conn.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (d.isoformat(), code, code, c, c, c, c, 1000, 0, 0, 0, "KOSPI", ""))
            d += timedelta(days=1)
        for no, dt, nm in (("1", "2026-01-08", "연결재무제표기준영업(잠정)실적(공정공시)"), ("2", "2026-04-07", "연결재무제표기준영업(잠정)실적(공정공시)"),
                           ("3", "2026-07-07", "연결재무제표기준영업(잠정)실적(공정공시)"), ("4", "2026-10-08", "연결재무제표기준영업(잠정)실적(공정공시)"),
                           ("20260727000001", "2026-07-27", "주요사항보고서(유상증자결정)"), ("20261020800455", "2026-10-20", "기업설명회(IR)개최(안내공시)"),
                           ("20261030000001", "2026-10-30", "[기재정정]주요사항보고서(유상증자결정)")):
            conn.execute("INSERT INTO disclosures VALUES(?,?,?,?,?,0,0)", (no, "005930", "00126380", nm, dt))
        conn.commit()
        conn.close()
        cache = tmp / "dart_cache"
        cache.mkdir()
        for f in fx.glob("*.txt"):
            (cache / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        env = {"DART_API_KEY": "k", "KSTOCK_OFFLINE": "1", "KSTOCK_DART_CACHE": str(cache)}
        with mock.patch.dict(os.environ, env):
            # ① 생성 — 실제 정정공시 원문에서 납입 11-06 · IR 11-12 · 상장 11-27 이 나오고, 드라이버에 이어진다
            r = draft_from_reports(sorted((Path(__file__).parent / "fixtures" / "reports").glob("*.txt")), today=today, research_db=rdb)
            m = r.map
            by_id = {n.event.event_id: n for n in m.children("A6.1")}
            self.assertEqual(by_id["rights:납입일"].event.date, date(2026, 11, 6))
            self.assertEqual(by_id["rights:신주상장예정일"].event.date, date(2026, 11, 27))
            self.assertEqual(by_id["earnings:3Q26"].event.date, date(2026, 11, 12))
            self.assertEqual(by_id["earnings:3Q26"].event.certainty.value, "공시확정")
            key_events = [by_id[k] for k in ("rights:납입일", "rights:신주상장예정일", "earnings:3Q26", "earnings:4Q26")]
            self.assertTrue(all(n.event.affects for n in key_events), {k: v.event.affects for k, v in by_id.items()})
            self.assertTrue(all(n.event.bull and n.event.bear for n in key_events))
            self.assertTrue(all(n.event.event_id for n in m.children("A6.1")))                      # 전부 영속 ID 가 있다
            self.assertIn("A3.2", by_id["rights:납입일"].event.affects)
            self.assertTrue(set(by_id["earnings:3Q26"].event.affects) >= {n.id for n in m.drivers()})
            pay_id = by_id["rights:납입일"].id
            cps = m.value("A8.1")
            self.assertIn("2026-11-06", cps)
            self.assertIn("2026-11-12", cps)
            # ② 사람이 납입 이벤트의 상방 조건을 고친다
            ev = m[pay_id].event.model_dump(mode="json")
            ev["bull"] = ["납입 완료 + 협업 발표 (내가 씀)"]
            apply_edit_ops(m, [{"op": "set", "id": pay_id, "fields": {"event": ev}}], today=today)
            self.assertIn("user", m[pay_id].tags)
            # ③ 다음 날 재생성 (같은 공시 목록) — 중복 없음 · 번호 그대로 · 사람 편집 그대로
            from kstock.collect.adapters.research_db import ResearchDb

            db = ResearchDb(rdb)
            _fill_dart_events(m, "005930", db, today=date(2026, 11, 3), research_db=rdb)
            ids_after = {n.event.event_id: n.id for n in m.children("A6.1")}
            self.assertEqual(ids_after["rights:납입일"], pay_id)
            self.assertEqual(m[pay_id].event.bull, ["납입 완료 + 협업 발표 (내가 씀)"])
            self.assertEqual(len([n for n in m.children("A6.1") if n.event.event_id == "rights:납입일"]), 1)
            # ④ 사건 뒤 — 수익률이 채워지고 상태가 completed, 판정은 비어 있다
            notes = fill_event_outcomes(m, db, today=date(2026, 11, 25))
            self.assertTrue(notes)
            o = m[pay_id].event.outcome
            self.assertIsNotNone(o.reaction_d5)
            self.assertEqual(o.measured_from, date(2026, 11, 5))
            self.assertIsNone(o.verdict)
            self.assertEqual(m[pay_id].event.status.value, "completed")
            # ⑤ 다시 재생성 — 지난 사건은 남고 결과도 남는다 (사실 로그가 된다)
            _fill_dart_events(m, "005930", db, today=date(2026, 11, 25), research_db=rdb)
            self.assertEqual(m[pay_id].event.outcome.reaction_d5, o.reaction_d5)
            self.assertEqual({n.event.event_id: n.id for n in m.children("A6.1")}["rights:납입일"], pay_id)


# ---------------------------------------------------------------------------- 마일스톤 · 번호 · A8.1 본문 · 상태 · 완료 (2026-10-31 두 번째)
class TestMilestonesAndMore(unittest.TestCase):
    def test_capacity_quant_refs_become_milestones_with_project(self):
        from kstock.generate.catalysts import link_catalysts
        from kstock.generate.mapping import apply_analyst_catalysts, milestone_events
        from kstock.models import Claim, ClaimSet, EvidenceSpan, QuantRef

        today = date(2026, 10, 31)
        m = _naver_map(today)
        c9 = Claim(claim_id="c9", report_id="hk_97166", broker="하나증권", report_date=date(2026, 10, 28),
                   proposition="기존 목표인 2027년 상반기 55MW, 2027년 말 100MW, 2028년 말 200MW 공급을 위한 AI DC 임차 공간 확보와 GPU 구매 약정이 마무리된 것으로 파악된다",
                   driver_id="capex", roles=["business", "catalyst"], status="verified", certainty="realized", direction="positive", span_ids=["s1"],
                   quant_refs=[QuantRef(metric="capacity_1h27", col="1H27", value=55.0, unit="MW", source="text"),
                               QuantRef(metric="capacity_2027", col="2027E", value=100.0, unit="MW", source="text"),
                               QuantRef(metric="capacity_2028", col="2028E", value=200.0, unit="MW", source="text")])
        cs = ClaimSet(report_id="hk_97166", broker="하나증권", path="h.pdf", spans=[EvidenceSpan(span_id="s1", report_id="r", page=1, start=0, end=2, text="p")], claims=[c9])
        ms = milestone_events(c9, {"hk_97166": cs}, today=today, sector="KR-INTERNET")
        # 「기존 목표」 는 리포트가 전한 회사 계획 — 회사 원문을 안 봤으니 「회사 목표」 가 아니라 「회사 계획 (리포트 인용)」 (2026-10-31 3차 검토)
        self.assertEqual([(e.period, e.fact) for e in ms],
                         [("2027H1", "[회사 계획] AI 데이터센터·클라우드 55MW (2027H1)"), ("2027", "[회사 계획] AI 데이터센터·클라우드 100MW (2027)"),
                          ("2028", "[회사 계획] AI 데이터센터·클라우드 200MW (2028)")])
        self.assertTrue(all(e.project == "ai_dc" and e.certainty.value == "회사 계획 (리포트 인용)" and e.event_id.startswith("ai_dc:capacity")
                            for e in ms))
        self.assertTrue(all(e.evidence and "하나증권" in e.evidence[0] for e in ms))
        self.assertTrue(all("유상 계약 용량(MW)·계약 단가" in e.precheck and "GPU 가동률" in e.precheck for e in ms))   # 구축 ≠ 수익 — 확인할 것
        notes = apply_analyst_catalysts(m, [cs], today=today)
        link_catalysts(m)
        kids = m.children("A6.1")
        self.assertEqual(len(kids), 3)
        # project → A1.5, 중요도 H 회사 계획 → A3.3 평가 모델 · A4.1 시나리오 (있는 노드만)
        want = sorted({"A1.5"} | {x for x in ("A3.3", "A4.1") if x in m.nodes})
        self.assertTrue(all(n.event.affects == want for n in kids), [n.event.affects for n in kids])
        self.assertTrue(all(n.event.bull and n.event.bear for n in kids))                               # 회사 계획의 조건 초안
        self.assertTrue(all(n.event.rule and "수익성이 입증" in n.event.rule for n in kids))             # 판단 규칙 초안
        self.assertTrue(any("마일스톤" in x for x in notes), notes)

    def test_fresh_draft_numbers_follow_dates_but_edited_maps_keep_numbers(self):
        from kstock.generate.catalysts import renumber_fresh

        m = _naver_map()
        set_catalysts(m, [_ev(date(2027, 1, 8), "4Q26 잠정실적 발표", "과거 주기 추정"), _ev(date(2026, 11, 16), "분기보고서", "통상 일정 추정")], today=TODAY)
        from kstock.generate.catalysts import merge_into_map

        merge_into_map(m, [_ev(date(2026, 11, 6), "유상증자 납입", "공시확정")], today=TODAY)
        self.assertEqual([n.id for n in m.children("A6.1")], ["A6.1.3", "A6.1.1", "A6.1.2"])      # 끼어든 순서대로 번호, 보기는 날짜순
        self.assertTrue(renumber_fresh(m))
        self.assertEqual([(n.id, n.event.fact[:4]) for n in m.children("A6.1")], [("A6.1.1", "유상증자"), ("A6.1.2", "분기보고"), ("A6.1.3", "4Q26")])
        self.assertFalse(renumber_fresh(m))                                                        # 이미 순서대로면 아무것도 안 한다
        m["A6.1.2"].tags = sorted(set(m["A6.1.2"].tags) | {"user"})
        merge_into_map(m, [_ev(date(2026, 11, 2), "IR", "공시확정")], today=TODAY)
        self.assertFalse(renumber_fresh(m))                                                        # 사람이 고친 뒤에는 번호를 안 건드린다
        self.assertEqual([n.id for n in m.children("A6.1")], ["A6.1.4", "A6.1.1", "A6.1.2", "A6.1.3"])

    def test_a81_has_lines_and_status_marks(self):
        from kstock.generate.catalysts import link_catalysts
        from kstock.render.format import event_label

        m = _naver_map()
        set_catalysts(m, [_ev(date(2026, 11, 6), "유상증자 납입", "공시확정", materiality="H"), _ev(date(2026, 11, 12), "3Q26 실적발표·IR", "공시확정"),
                          _ev(date(2026, 12, 1), "브랜드 행사", "리포트 전망")], today=TODAY)
        link_catalysts(m)
        evaluate_new_map(m, today=TODAY, enforce_gate=True)
        q = m["A8.1"].question
        self.assertIn("2026-11-06 유상증자 납입 — 검증:", q)
        self.assertIn("→ 후속:", q)
        self.assertIn("[A3.2, A3.3, A5.4]", q)
        self.assertNotIn("브랜드 행사", q)                                                        # 판단에 안 걸리는 사건(M · affects 없음) 은 본문에도 없다
        self.assertTrue(event_label(m.children("A6.1")[0].event).startswith("📅 예정 2026-11-06"))

    def test_issue_report_marks_payment_completed(self):
        from kstock.collect.adapters.dart import DartItem, completions_from_items
        from kstock.generate.draft import apply_completions

        items = [DartItem("20261106000111", date(2026, 11, 6), "증권발행실적보고서"), DartItem("1", date(2026, 11, 7), "임원ㆍ주요주주특정증권등소유상황보고서"),
                 DartItem("2", date(2026, 11, 8), "증권발행실적보고서(자회사의 주요경영사항)")]
        comps = completions_from_items(items)
        self.assertEqual([(c["event_id"], c["date"]) for c in comps], [("rights:납입일", date(2026, 11, 6))])
        m = _naver_map()
        pay = Event(date=date(2026, 11, 6), fact="유상증자 납입", certainty="공시확정", event_id="rights:납입일", direction="미정", priced_in="미판단")
        set_catalysts(m, [pay], today=TODAY)
        notes = apply_completions(m, comps, today=date(2026, 11, 7))
        e = m.children("A6.1")[0].event
        self.assertEqual(e.status.value, "completed")
        self.assertIn("증권발행실적보고서", e.outcome.actual)
        self.assertTrue(notes)
        self.assertEqual(apply_completions(m, comps, today=date(2026, 11, 8)), [])                   # 두 번 적지 않는다


# ---------------------------------------------------------------------------- 3차 검토 (2026-10-31): 원문 보존 · 낡음 표시 · 세율 가드 · 파서 점검
class TestThirdReview(unittest.TestCase):
    LONG_RULE = ("구축·달성만으로 수익성이 입증됐다고 보지 않는다 — 유상 계약, 가동률, 수익구조와 현금흐름이 확인되면 사업가치와 시나리오를 재평가한다. "
                 "지연되거나 고정 지급의무 부담이 커지면 성장 가정을 하향 검토한다")

    def test_a81_keeps_full_sentences(self):
        """A8.1 본문·증거 일정은 규칙·검증 문장을 자르지 않는다 — 10-31 지도에서 「… 유상 계약, 가」 로 끊겼다."""
        from kstock.engine.evidence import evidence_schedule
        from kstock.generate.catalysts import checkpoint_lines, checkpoint_rows
        from kstock.render.conditions import conditions_html

        m = _naver_map()
        tgt = Event(date=date(2027, 6, 30), period="2027H1", fact="[회사 계획] AI 데이터센터·클라우드 55MW (2027H1) — 유상 고객 확보가 관건인 첫 구축 마일스톤",
                    certainty="회사 계획 (리포트 인용)", materiality="H", direction="미정", priced_in="미판단", rule=self.LONG_RULE,
                    precheck=["실제 가동·공급 개시", "유상 계약 용량(MW)·계약 단가", "GPU 가동률", "EBITDA·FCF 기여"], affects=["A1.5", "A3.3"])
        set_catalysts(m, [tgt], today=TODAY)
        rows = checkpoint_rows(m, today=TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], self.LONG_RULE)
        self.assertEqual(rows[0]["check"], "실제 가동·공급 개시 · 유상 계약 용량(MW)·계약 단가 · GPU 가동률 · EBITDA·FCF 기여")   # 넷 다 — 둘로 자르지 않는다
        line = checkpoint_lines(m, today=TODAY)[0]
        self.assertIn(self.LONG_RULE, line)
        self.assertIn(tgt.fact, line)
        self.assertTrue(line.endswith("[A1.5, A3.3]"))
        evaluate_new_map(m, today=TODAY, enforce_gate=True)
        self.assertIn(self.LONG_RULE, m["A8.1"].question)
        sched = [x for x in evidence_schedule(m, today=TODAY) if x["kind"] == "event"]
        self.assertEqual(sched[0]["action"], self.LONG_RULE)
        self.assertEqual(sched[0]["what"], "2027H1 " + tgt.fact)
        html = conditions_html(m)
        self.assertIn(self.LONG_RULE, html)                                                          # 표에서도 통째로
        self.assertIn('class="sched"', html)
        self.assertIn("EBITDA·FCF 기여", html)

    def test_stale_value_is_marked_in_the_line(self):
        from kstock.models import NodeStatus
        from kstock.render.format import STALE_MARK, node_line

        m = new_map_skeleton("035420", "NAVER", "growth", date(2026, 12, 15))
        m["A6.2.1"].set("상승", as_of=date(2026, 11, 20), status=NodeStatus.stale)
        line = node_line(m["A6.2.1"])
        self.assertIn(STALE_MARK, line)
        self.assertIn("(2026-11-20) 상승", line)                                                    # 언제 자료인지 값 바로 앞에
        m["A6.2.2"].set(1.3, as_of=date(2026, 12, 15))
        self.assertNotIn(STALE_MARK, node_line(m["A6.2.2"]))                                        # 최신 자료는 그대로
        # 값을 비우고 「분석 불가」 만 적은 stale 노드엔 낡음 표시를 겹치지 않는다 (날짜가 두 번 나오지 않게)
        from kstock.models import Node as _N
        empty = _N.model_validate({**m["A6.2.1"].model_dump(mode="json"), "value": None, "question": "분석 불가 — 시세가 2026-11-20 (25일 전) 에서 멈춤"})
        line = node_line(empty)
        self.assertNotIn(STALE_MARK, line)
        self.assertIn("분석 불가", line)

    def test_effective_tax_guard(self):
        """1 − NP/OP 가 세율 띠 밖이면 (영업외손익 때문) 버리고 기본값 — 10-31 NAVER 지도의 t = 0.4%."""
        from kstock.engine.valuation import TAX_BAND, TAX_DEFAULT, common_tail

        def fin_map(np_: float, op: float):  # noqa: ANN202
            m = new_map_skeleton("035420", "NAVER", "growth", TODAY)
            m.meta.setdefault("draft", {})["financials"] = {"year": "2027E", "np": {"mean": np_, "n": 2}, "op": {"mean": op, "n": 2},
                                                             "eps": {"mean": 10_000.0, "n": 2}, "tp_median": 300_000.0,
                                                             "years": {"2027E": {"np": {"mean": np_, "n": 2}, "op": {"mean": op, "n": 2}, "eps": {"mean": 10_000.0, "n": 2}}}}
            return m

        tl = common_tail(fin_map(28_000.0, 28_120.0))                     # NP ≈ OP → 0.4%
        self.assertAlmostEqual(tl.t, TAX_DEFAULT)
        inp = next(i for i in tl.inputs if i.name == "유효세율 t")
        self.assertEqual(inp.source, "default")
        self.assertIn("영업외손익", inp.note)
        self.assertIn("0%", inp.note)
        tl2 = common_tail(fin_map(20_000.0, 26_000.0))                    # 23% → 띠 안, 그대로 유도
        self.assertAlmostEqual(tl2.t, 1 - 20_000 / 26_000)
        self.assertEqual(next(i for i in tl2.inputs if i.name == "유효세율 t").source, "derived")
        tl3 = common_tail(fin_map(28_000.0, 28_120.0), {"tax_rate": 0.25})   # 사람이 준 값이 먼저
        self.assertAlmostEqual(tl3.t, 0.25)
        self.assertEqual(next(i for i in tl3.inputs if i.name == "유효세율 t").source, "user")
        self.assertTrue(TAX_BAND[0] < TAX_DEFAULT < TAX_BAND[1])

    def test_dart_check_counts_parser_hits(self):
        from kstock.collect.adapters.dart import CheckRow, check_items, check_summary

        fx = Path(__file__).parent / "fixtures" / "dart"
        texts = {p.stem: p.read_text(encoding="utf-8") for p in fx.glob("*.txt")}
        texts["9001"] = "12. 신주의 상장 예정일\n2026년 11월 27일"                # 납입일이 없는 유상증자 본문 → 부분
        texts["9002"] = "1. 일시: 추후 확정"                                       # 날짜 없음
        items = [DartItem("20261020800455", date(2026, 10, 20), "기업설명회(IR)개최(안내공시)"),
                 DartItem("20260727000001", date(2026, 7, 27), "주요사항보고서(유상증자결정)"),
                 DartItem("20261030000001", date(2026, 10, 30), "[기재정정]주요사항보고서(유상증자결정)"),
                 DartItem("20260901800215", date(2026, 9, 1), "기업설명회(IR)개최(안내공시)"),
                 DartItem("9001", date(2026, 10, 1), "주요사항보고서(유상증자결정)"),
                 DartItem("9002", date(2026, 10, 2), "기업설명회(IR)개최(안내공시)"),
                 DartItem("9003", date(2026, 10, 3), "기업설명회(IR)개최(안내공시)"),                 # 본문 없음
                 DartItem("9004", date(2026, 10, 4), "영업(잠정)실적(공정공시)"),                     # 읽을 종류가 아니다 → 안 센다
                 DartItem("9005", date(2026, 10, 5), "기업설명회(IR)개최(안내공시)(자회사의 주요경영사항)")]

        def text_of(no: str) -> str:
            if no not in texts:
                raise FileNotFoundError(f"캐시 없음 {no}.txt")
            return texts[no]

        rows = check_items(items, text_of)
        self.assertTrue(all(isinstance(r, CheckRow) for r in rows))
        by = {r.item.rcept_no: r for r in rows}
        self.assertEqual(set(by), {"20261020800455", "20260727000001", "20261030000001", "20260901800215", "9001", "9002", "9003"})
        self.assertTrue(all(by[n].ok for n in ("20261020800455", "20260727000001", "20261030000001", "20260901800215")))   # 실제 원문 넷 전부 OK
        self.assertEqual((by["9001"].status, by["9001"].missing), ("partial", ["납입일"]))
        self.assertEqual(by["9002"].status, "none")
        self.assertEqual(by["9003"].status, "no_text")
        self.assertIn("캐시 없음", by["9003"].error)
        self.assertEqual(check_summary(rows), {"watched": 7, "ok": 4, "partial": 1, "none": 1, "no_text": 1})
        self.assertIn("납입일 2026-11-06", by["20261030000001"].line())
        self.assertIn("못 읽음: 납입일", by["9001"].line())
        self.assertIn("원문 확인", by["9002"].line())

    def test_dart_check_cli_cached_only(self):
        """`mindmap dart-check` — 리서치 DB 공시 목록 + 캐시 본문만으로 돈다 (네트워크·키 없이). 부분·실패가 있으면 종료 코드 1."""
        import contextlib
        import io
        import os
        import shutil
        import tempfile

        from kstock.cli import main as cli_main

        tmp = Path(tempfile.mkdtemp())
        db_path = tmp / "kstock.sqlite"
        _make_db(db_path, last_day=date(2026, 10, 31))
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO disclosures VALUES(?,?,?,?,?,0,0)", ("20261030000001", "035420", "00266961", "[기재정정]주요사항보고서(유상증자결정)", "2026-10-30"))
        conn.execute("INSERT INTO disclosures VALUES(?,?,?,?,?,0,0)", ("20261099000009", "035420", "00266961", "주주총회소집결의", "2026-10-29"))   # 캐시 없음
        conn.commit()
        conn.close()
        cache = tmp / "dart"
        cache.mkdir()
        fx = Path(__file__).parent / "fixtures" / "dart"
        for no in ("20261020800455", "20261030000001"):
            shutil.copy(fx / f"{no}.txt", cache / f"{no}.txt")
        old = {k: os.environ.get(k) for k in ("KSTOCK_DART_CACHE", "DART_API_KEY")}
        os.environ["KSTOCK_DART_CACHE"] = str(cache)
        os.environ.pop("DART_API_KEY", None)
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli_main(["dart-check", "035420", "--research-db", str(db_path), "--cached-only", "--date", "2026-10-31"])
            s = out.getvalue()
            self.assertEqual(rc, 1, s)                                                               # 주총 건의 본문이 없다
            self.assertIn("읽을 것 3건 · OK 2/3", s)
            self.assertIn("본문 없음 1", s)
            self.assertIn("20261099000009", s)
            self.assertNotIn("20261020800455", s)                                                    # OK 인 건은 기본으로 안 보인다
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli_main(["dart-check", "035420", "--research-db", str(db_path), "--cached-only", "--all", "--date", "2026-10-31"])
            self.assertIn("20261020800455", out.getvalue())
            self.assertIn("OK  ir 2026-11-12 09:00", out.getvalue())
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            shutil.rmtree(tmp, ignore_errors=True)
