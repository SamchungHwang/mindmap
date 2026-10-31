"""층 B — LLM 이 리포트 본문에서 촉매 사건을 **제안**하고, 규칙이 **검증**해 받는다 (2026-10-31, 두산에너빌리티 검토 뒤).

층 A(`event_text.py`, 규칙) 가 기준이다. LLM 은 규칙이 놓치는 문장 — 시점이 흩어졌거나(「내년 상반기 … 그 뒤 하반기에」), 조건 속에 들었거나,
Q&A 가 아닌 서술에 묻힌 것 — 을 같은 `Event` 스키마로 내놓는다. 받는 조건은 전부 규칙이다:
  ① 인용(quote) 이 원문에 있어야 한다 (`claims.locate_quote`) — 없는 문장은 버린다
  ② 시점은 `catalysts.parse_when` 이 다시 읽는다 — LLM 의 날짜를 믿지 않는다; 납기·운전 시점만 있는 문장은 사건이 아니다
  ③ 사업명은 `catalysts.PROGRAMS` 사전에 있는 것만, 단계는 `STAGES` 에 있는 것만 — 사전 밖 이름은 메모로 남겨 사전을 키울 후보가 된다
  ④ 지난 일은 「체결·완료」 가 아니면 버린다
  ⑤ 등급은 화자(speaker) 로 — 회사 발언·계획이면 「회사 계획 (리포트 인용)」, 증권사 전망이면 「리포트 전망」. 공시 등급은 LLM 이 못 준다
받은 사건은 `event_text._merge_same` 으로 겹침을 정리하고, 지도에는 `catalysts.merge_into_map` 으로 들어가 층 A 사건과 `same_event`
(주체·주제·기간) 로 합쳐진다 — 같은 사건이면 층 A 의 ID 가 남고 LLM 문장은 증거로 붙는다.

토큰을 아끼기 위해 본문 전체가 아니라 **시점 표현이 든 줄과 그 이웃, Q&A 블록** 만 보낸다 (`candidate_passages`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from ..models.common import Source
from ..models.sub import Event, EventCertainty, EventStatus, Materiality
from .catalysts import PROGRAMS, STAGES, event_program, event_topic, find_stage, parse_when, time_clause
from .event_text import _HIGH_WORDS, PLAN_WORDS, _driver_label, _driver_of, _merge_same, _slug, amount_jo
from .kpi_catalog import normalize_driver

MAX_PASSAGE_CHARS = 9000
MAX_EVENTS_PER_REPORT = 20
_QA_LINE = re.compile(r"^\s*Q\s*\d{1,2}\s*[.)]")
_ANSWER_LINE = re.compile(r"^\s*[-–•]\s+")
_PROGRAM_NAMES = [name for name, _ in PROGRAMS]
_STAGE_NAMES = [name for name, _ in STAGES]

EVENT_SYSTEM = (
    "너는 한국 증권사 리포트에서 **앞으로 일어날 사건(촉매)** 을 뽑는 분석 보조다. 사건이란 날짜·기간이 정해질 수 있는 일 — 계약 체결·발주·"
    "본계약 전환·FID·착공·출하 시작·증설 결정·실적 발표·정책 발표 — 이다. 전망 문장의 숫자(매출·이익 추정) 는 사건이 아니다.\n"
    "규칙: (1) quote 는 본문에서 **글자 그대로 복사**한 한 문장(또는 Q&A 의 답 한 줄) 이다 — 고치거나 요약하지 않는다. (2) 사건 시점(when_text) 과 "
    "납기·운전·출하 시점(delivery_text) 을 가른다: 「2030년 슬롯 예약 물량의 본계약 전환은 2027년」 이면 when_text=2027년, delivery_text=2030년. "
    "출하·운전·건설 목표 시점만 있고 그 전의 결정·계약 시점이 없으면 when_role 은 delivery 다. (3) 사업명(program) 은 주어진 목록에서만 고른다; "
    "없으면 null 로 두고 raw_program 에 본문의 이름을 적는다. (4) speaker 는 회사(설명회 답·회사 목표·계획) 면 company, 증권사의 예상이면 analyst. "
    "(5) 선행조건(preconditions) 은 본문에 있는 것만. (6) 응답은 코드펜스 없이 JSON 객체 하나 — {\"events\": [...]} — 설명 없이."
)


def event_prompt(passage: str, *, broker: str, report_date: str, name: str, ticker: str, today: date) -> str:
    schema = {
        "events": [{
            "quote": "본문 문장 그대로", "what": "사건 한 줄 (무엇이 언제)", "when_text": "사건 시점 표현 그대로 (예: 2028년 하반기 · 연내 · 4Q26 · 내년 상반기)",
            "when_role": "event | delivery", "delivery_text": "납기·운전·출하 시점 표현 (없으면 null)", "program": "목록의 사업명 또는 null",
            "raw_program": "본문의 사업·프로젝트 이름 (program 이 null 일 때)", "stage": " | ".join(_STAGE_NAMES) + " | null",
            "speaker": "company | analyst", "preconditions": ["선행조건 (본문에 있는 것만)"], "amount": "금액 표현 (없으면 null)", "page": 1,
        }],
    }
    return (
        f"## 리포트: {broker} {report_date} · {name}({ticker}) · 기준일 {today.isoformat()}\n"
        f"## 사업명 목록: {' · '.join(_PROGRAM_NAMES)}\n"
        f"## 단계 목록: {' → '.join(_STAGE_NAMES)}\n"
        f"## 출력 JSON 꼴\n{json.dumps(schema, ensure_ascii=False)}\n"
        "## 본문 발췌 (「[p.N]」 은 쪽 번호 · 「Q1.」 은 설명회 질문, 「- 」 는 답)\n"
        f"{passage}\n"
        "## 지시\n위 발췌에서 앞으로의 사건을 전부 뽑아라. 한 문장에 시점이 둘이면(2030년분은 2027년 · 2031년분은 2028년) 사건 둘로. "
        "이미 지난 일은 「체결·완료」 로 적힌 것만. 숫자 전망(매출·이익) 과 밸류에이션 문장은 빼라."
    )


def _mostly_numbers(s: str) -> bool:
    body = re.sub(r"\s", "", s)
    return bool(body) and sum(ch.isdigit() or ch in ".,()%-" for ch in body) / len(body) > 0.55


def candidate_passages(text: str, today: date, *, max_chars: int = MAX_PASSAGE_CHARS) -> str:
    """시점 표현이 든 줄(+이웃 줄) 과 Q&A 블록만 모은다. 쪽 경계('\\f') 는 「[p.N]」 으로."""
    lines = text.split("\n")
    keep = [False] * len(lines)
    in_qa = False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s == "\f":
            in_qa = False
            continue
        if _QA_LINE.match(s):
            in_qa = True
            keep[i] = True
            continue
        if in_qa and (_ANSWER_LINE.match(s) or not s):
            keep[i] = bool(s)
            if not s:
                in_qa = False
            continue
        in_qa = False
        if _mostly_numbers(s):
            continue                                   # 표 행(「2024 2025 2026F 2027F」) — 연도는 있지만 사건이 아니다
        if len(s) >= 12 and parse_when(s, today) is not None:
            keep[i] = True
            if i > 0 and lines[i - 1].strip() not in ("", "\f"):
                keep[i - 1] = True
            if i + 1 < len(lines) and lines[i + 1].strip() not in ("", "\f"):
                keep[i + 1] = True
    out: list[str] = []
    page = 1
    marked = False
    n = 0
    for i, ln in enumerate(lines):
        if ln.strip() == "\f":
            page += 1
            marked = False
            continue
        if not keep[i]:
            continue
        if not marked:
            out.append(f"[p.{page}]")
            marked = True
        s = " ".join(ln.split())
        out.append(s)
        n += len(s) + 1
        if n >= max_chars:
            out.append("… (길이 제한)")
            break
    return "\n".join(out)


@dataclass
class Validation:
    accepted: list[Event] = field(default_factory=list)
    rejected: dict[str, int] = field(default_factory=dict)
    unknown_programs: list[str] = field(default_factory=list)

    def reject(self, why: str) -> None:
        self.rejected[why] = self.rejected.get(why, 0) + 1

    def summary(self) -> str:
        bits = [f"받음 {len(self.accepted)}"]
        if self.rejected:
            bits.append("버림 " + " · ".join(f"{k} {v}" for k, v in sorted(self.rejected.items(), key=lambda kv: -kv[1])))
        if self.unknown_programs:
            bits.append("사전 밖 사업명: " + ", ".join(sorted(set(self.unknown_programs))[:6]))
        return " / ".join(bits)


def _s(x: Any) -> str:
    return " ".join(str(x).split()) if isinstance(x, str) else ""


def _squash(x: str) -> str:
    return re.sub(r"\s+", "", x or "")


def validate_proposals(items: list[dict[str, Any]], *, text: str, broker: str, report_date: date | None, path: str, today: date,
                       sector: str | None) -> Validation:
    """LLM 제안 → 규칙 검증을 통과한 Event. 모든 판정 이유는 Validation.rejected 에 남는다."""
    from ..reports.claims import locate_quote, page_of

    v = Validation()
    anchor = report_date or today
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            v.reject("형식")
            continue
        quote = _s(it.get("quote"))
        if len(quote) < 20:
            v.reject("인용 짧음")
            continue
        loc = locate_quote(text, quote) if quote else None
        if loc is None:
            v.reject("인용 없음")
            continue
        page = page_of(text, loc[0])
        # 시점은 **인용 문장을 규칙이 읽은 것**이 기준이다 (역할·납기 포함). LLM 의 when_text 는 인용에 시점이 없을 때만 —
        # 그것도 인용 근처(같은 Q&A 항목: 질문의 「4Q26」) 에 실제로 있는 표현일 때만 받는다. 밖에서 가져온 날짜는 버린다
        wq = parse_when(quote, today, anchor=anchor)
        when_text = _s(it.get("when_text"))
        near = _squash(text[max(0, loc[0] - 400): loc[1] + 100])
        wt = parse_when(when_text, today, anchor=anchor) if when_text and _squash(when_text) in near else None
        w = wq or wt
        if w is None:
            v.reject("시점 못 읽음")
            continue
        stage_raw = _s(it.get("stage"))
        stage = stage_raw if stage_raw in _STAGE_NAMES else find_stage(quote)
        completed = stage == "체결" and w.date <= today + timedelta(days=31) and w.role != "delivery"
        if w.date <= today and not completed:
            v.reject("지난 일")
            continue
        if w.role == "delivery" and not PLAN_WORDS.search(quote):
            v.reject("납기만 있음")
            continue
        prog_raw = _s(it.get("program"))
        program = prog_raw if prog_raw in _PROGRAM_NAMES else event_program(quote)
        if prog_raw and prog_raw not in _PROGRAM_NAMES:
            v.unknown_programs.append(prog_raw)
        elif not program and _s(it.get("raw_program")):
            v.unknown_programs.append(_s(it.get("raw_program")))
        did = _driver_of(quote, sector) or (normalize_driver(program, sector=sector) if program else None)
        if did is None and amount_jo(quote) is not None:
            did = "backlog"
        head = program or _driver_label(did)
        what = _s(it.get("what")) or time_clause(quote, w, limit=80)
        fact = ((f"[{head}] " if head and not what.startswith(head) else "") + what)[:140]
        speaker = _s(it.get("speaker"))
        cert = EventCertainty.회사계획인용 if (speaker == "company" or completed) else EventCertainty.리포트전망
        pre = [_s(p)[:90] for p in (it.get("preconditions") or []) if _s(p) and _s(p)[:30] in text][:3]   # 본문에 있는 것만
        delivery = None
        dt = _s(it.get("delivery_text"))
        if w.delivery is not None:
            delivery = w.delivery.period or w.delivery.date.isoformat()[:7]
        elif dt:
            wd = parse_when(dt, today, anchor=anchor)
            if wd is not None and wd.date > w.date:
                delivery = wd.period or wd.date.isoformat()[:7]
        amt = amount_jo(_s(it.get("amount")) or quote)
        high = (amt is not None and amt >= 1.0) or any(k in quote for k in _HIGH_WORDS)
        where = f"{broker} {report_date.isoformat() if report_date else ''}".strip() + f" p.{page}"
        ev = Event(date=min(w.date, today) if completed else w.date, period=w.period, fact=fact, affects=[], direction="미정", priced_in="미판단",
                   materiality=Materiality.H if high else Materiality.M, certainty=cert,
                   status=EventStatus.completed if completed else EventStatus.scheduled,
                   source=Source(name=where, url=path, retrieved_at=today), evidence=[f"(·) [{broker}] {quote[:170]}"], precheck=pre,
                   project=did, program=program, stage=stage, delivery_period=delivery)
        topic = event_topic(ev)
        key_when = (ev.period or ev.date.isoformat()).lower()
        v.accepted.append(ev.model_copy(update={"event_id": f"llm:{_slug(program or did or head or 'x')}:{topic}:{key_when}"}))
    return v


def llm_text_events(docs, digest, filler, *, today: date, sector: str | None = None, progress=None) -> tuple[list[Event], list[str]]:  # noqa: ANN001
    """읽을 수 있는 리포트마다 LLM 에 한 번 묻고(발췌만), 검증 통과분을 돌려준다. 실패한 리포트는 메모만 남기고 넘어간다."""
    from .llm_fill import complete_json

    facts_by_name = {f.path.name: f for f in digest.reports}
    events: list[Event] = []
    notes: list[str] = []
    for i, doc in enumerate(docs, start=1):
        f = facts_by_name.get(doc.path.name)
        if f is None or doc.is_empty:
            continue
        passage = candidate_passages(doc.text, today)
        if len(passage) < 80:
            notes.append(f"{doc.path.name}: 시점이 든 문장이 없어 LLM 에 묻지 않음")
            continue
        if progress:
            progress(f"LLM 사건 추출 {i}/{len(docs)} {doc.path.name}", i, len(docs))
        prompt = event_prompt(passage, broker=f.broker or "?", report_date=str(f.date or "?"), name=f.name or "?", ticker=f.ticker or "?", today=today)
        try:
            ans = complete_json(filler, EVENT_SYSTEM, prompt, retries=1)
        except Exception as e:  # noqa: BLE001 — 한 리포트 실패는 메모로
            notes.append(f"{doc.path.name}: LLM 사건 추출 실패 ({type(e).__name__}: {str(e)[:80]})")
            continue
        v = validate_proposals((ans or {}).get("events") or [], text=doc.text, broker=f.broker or "?", report_date=f.date, path=str(doc.path),
                               today=today, sector=sector)
        kept = _merge_same(v.accepted)[:MAX_EVENTS_PER_REPORT]
        notes.append(f"{f.broker or doc.path.name}: LLM 제안 {len((ans or {}).get('events') or [])}건 → {v.summary()}"
                     + (f" → 겹침 정리 {len(kept)}건" if len(kept) != len(v.accepted) else ""))
        events += kept
    return _merge_same(events), notes


__all__ = ["EVENT_SYSTEM", "MAX_PASSAGE_CHARS", "Validation", "candidate_passages", "event_prompt", "llm_text_events", "validate_proposals"]
