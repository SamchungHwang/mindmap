"""리포트 **문장·설명회 Q&A → 촉매 이벤트** (2026-10-31 두산에너빌리티 검토).

전에는 digest 의 촉매 문장 상위 3개를 `event_from_text` 로 하나씩 이벤트로 만들었다 — 한 문장에 든 수주 후보 셋이 하나가 되고,
「2030년 슬롯 … 본계약 전환은 2027년」 이 2030 사건이 되고, 설명회 Q&A(끝맺음 없는 글머리표) 는 통째로 버려졌다.

여기서 하는 것
  · 단위        본문 촉매 문장 + Q&A 항목(질문 — 답) 을 리포트(증권사·날짜·쪽) 와 함께
  · 쪼개기      「A 1조원(…), B 7,000억원(…), C 5,000억원(…)」 은 셋으로; 시점이 둘 이상 든 절은 절마다
  · 시점        `parse_when` 의 역할 판정 — 사건 시점 ↔ 납기·운전 시점(`delivery_period`)
  · 단계·사업   `find_stage`(협의 중 → 조건 합의 → 체결 대기 → 체결) · `event_program`(X-energy · NuScale · 미국 AP1000 …)
  · 등급        Q&A 답과 「목표·계획·추진」 문장은 회사 계획 (리포트 인용), 「기대·전망」 문장은 리포트 전망, 지난 「체결했다」 는 완료 사건
  · 선행조건    Q&A 답 가운데 「필요·절차·조건·확정 후」 가 든 줄은 precheck
  · 수주 목표   「연간 수주 목표 N조 · 잔여 M조」 와 후보 금액 합을 맞춰 미확인 금액을 precheck 에 적는다
  · 합치기      같은 (사업, 주제, 기간) 은 하나 — Q&A 가 본문보다 세고, 증거는 다 남긴다
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from ..models import Event, EventCertainty, EventStatus, Materiality, Source
from .catalysts import STAGES, event_program, event_topic, find_stage, parse_when, time_clause
from .kpi_catalog import _DRIVER_BY_ID, normalize_driver

#: 「SMR 1조원(계약 조건 합의 완료 단계로 연내 체결 가능)」 꼴 — 이름 · 금액 · 괄호 설명
_AMOUNT_ITEM = re.compile(r"([가-힣A-Za-z·/\s]{2,24}?)\s*(?:약\s*)?(\d[\d,]*(?:\.\d+)?)\s*(조원|억원|천억원|조|억)\s*\(([^()]{3,90})\)")
_AMOUNT = re.compile(r"(?:약\s*)?(\d[\d,]*(?:\.\d+)?)\s*(조원|억원|천억원|조|억)(?!\s*\()")
_JO = {"조원": 1.0, "조": 1.0, "천억원": 0.1, "억원": 0.0001, "억": 0.0001}
_COMPANY_WORDS = ("목표", "계획", "추진", "예정", "협의", "협상", "대기", "검토 중", "착수", "체결")
_ANALYST_WORDS = ("기대된다", "전망이다", "예상된다", "판단한다", "전망한다", "예상한다", "기대한다", "기대", "전망", "예상")
_PRECHECK_WORDS = ("필요", "절차", "조건", "확정 후", "확정 이후", "선행", "확보", "승인", "심사", "지연", "충족")
_HIGH_WORDS = ("수주 목표", "본계약 전환", "선발주", "첫 발주", "장주기 기자재", "증설 결정", "FID", "최종투자결정", "기자재 발주", "후속 계약", "계약 전환",
               # 새 제품의 첫 출하·상용화 (중형 가스터빈 2028 하반기) — 금액이 없어도 논제를 바꾸는 사건이다
               "시제품", "상용화", "해외 진출", "첫 출하", "초도 출하", "양산")
MAX_EVENTS_PER_REPORT = 16   # 리포트 한 건의 상한 — A6.1 전체 상한(catalysts.MAX_EVENTS) 과 같다


@dataclass
class TextUnit:
    """이벤트 추출의 입력 한 덩이 — 본문 문장 하나 또는 Q&A 항목 하나."""

    text: str
    broker: str
    date: date | None
    page: int | None
    path: str
    is_qa: bool = False
    question: str = ""
    answers: list[str] | None = None


def amount_jo(text: str) -> float | None:
    """문장의 첫 금액을 조원으로. 「13.3조원」 → 13.3, 「7,000억원」 → 0.7."""
    m = _AMOUNT.search(text or "") or re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(조원|억원|천억원|조|억)", text or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "")) * _JO[m.group(2)]
    except (ValueError, KeyError):
        return None


def _split_parens_aware(text: str) -> list[str]:
    """괄호 밖의 쉼표·세미콜론으로 절을 나눈다."""
    out, buf, depth = [], [], 0
    for ch in text:
        if ch in "([（":
            depth += 1
        elif ch in ")]）":
            depth = max(0, depth - 1)
        if ch in ",，;；" and depth == 0:
            out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf).strip())
    return [c for c in out if c]


def split_candidates(text: str, today: date, anchor: date) -> list[tuple[str, str, str | None]]:
    """한 문장 → 후보들 (제목감, 문맥, 괄호 설명). 금액 항목이 둘 이상이면 항목마다, 시점이 든 절이 둘 이상이면 절마다, 아니면 문장 하나."""
    items = list(_AMOUNT_ITEM.finditer(text))
    if len(items) >= 2:
        out = []
        for m in items:
            name = " ".join(m.group(1).split()).strip(" ·,")
            name = re.sub(r"^(?:세부적으로는|그리고|또한|이외에|및)\s*", "", name)
            title = f"{name} {m.group(2)}{m.group(3)}"
            out.append((title, f"{title} — {m.group(4)}", m.group(4)))
        return out
    clauses = _split_parens_aware(text)
    timed = [c for c in clauses if parse_when(c, today, anchor=anchor) is not None]
    if len(timed) >= 2 and len(clauses) <= 5:
        return [(c, c, None) for c in timed]
    return [(text, text, None)]


def _certainty(unit: TextUnit, context: str, completed: bool) -> EventCertainty:
    if completed or unit.is_qa:
        return EventCertainty.회사계획인용           # 설명회 답 = 회사 발언 (리포트가 전한 것)
    company = any(w in context for w in _COMPANY_WORDS)
    analyst = any(w in context for w in _ANALYST_WORDS)
    return EventCertainty.회사계획인용 if company and not (analyst and not any(w in context for w in ("목표", "계획"))) else EventCertainty.리포트전망


def _driver_label(did: str | None) -> str | None:
    d = _DRIVER_BY_ID.get(did or "")
    return d.label if d else None


def _slug(s: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]+", "_", s).strip("_").lower()[:24] or "x"


_GENERIC_ORDER_WORDS = re.compile(r"연간\s*|신규\s*수주|수주\s*목표|수주잔고|수주\s*가시화|수주\s*모멘텀|목표\s*달성|달성\s*가능성|실현\s*가능성")
PLAN_WORDS = re.compile(r"목표|예정|계획|추진")   #: 납기만 있는 문장이 사건이 되려면 있어야 하는 말 (mapping 도 쓴다)


def _driver_of(text: str, sector: str | None) -> str | None:
    """드라이버 id — 「SMR 수주 목표」 는 수주잔고(backlog) 가 아니라 SMR 이다: 수주·목표 같은 일반 낱말을 빼고 먼저 본다."""
    core = _GENERIC_ORDER_WORDS.sub(" ", text or "")
    return (normalize_driver(core, sector=sector) or normalize_driver(core.replace(" ", ""), sector=sector)
            or normalize_driver(text, sector=sector))


def _candidates(unit: TextUnit, today: date, anchor: date) -> list[tuple[str, str, str | None]]:
    """(제목감, 문맥, 괄호 설명). Q&A 는 시점이 든 답마다 하나 (「2030년분은 내년 · 2031년분은 2028년」 은 둘), 아니면 질문+답 하나."""
    if not unit.is_qa:
        return split_candidates(unit.text, today, anchor)
    wq = parse_when(unit.question, today, anchor=anchor)
    if wq is not None and wq.role == "event":
        return [(unit.question, unit.text, "Q")]          # 「4Q26 SMR 수주 목표 …」 — 시점은 질문에, 답은 단계·선행조건
    timed = [a for a in (unit.answers or []) if parse_when(a, today, anchor=anchor) is not None]
    if timed:
        return [(unit.question, f"{unit.question} — {a}", None) for a in timed]
    return [(unit.question, unit.text, None)]


def events_from_unit(unit: TextUnit, *, today: date, sector: str | None) -> list[Event]:
    """한 덩이(문장·Q&A 항목) → 이벤트들."""
    anchor = unit.date or today
    out: list[Event] = []
    base_text = unit.text
    hint = None
    if not unit.is_qa:
        mh = re.match(r"^\[([^\]]{2,30})\]\s*", base_text)
        if mh and event_program(mh.group(1)):
            hint, base_text = mh.group(1), base_text[mh.end():]              # 프로젝트 표 줄의 「[사업명] 」
        else:
            base_text = re.sub(r"^\[[^\]]{1,6}\]\s*", "", base_text)       # 리포트의 절 표시 「[원전] 」
    for title, context, paren in _candidates(unit, today, anchor):
        if hint and not unit.is_qa:
            context = re.sub(r"^\[([^\]]{2,30})\]\s*", "", context)
            title = re.sub(r"^\[([^\]]{2,30})\]\s*", "", title)
        elif not unit.is_qa:
            # 절 표시 「[원전] 」 는 사실 문장에 남기지 않는다 — 「[NuScale] [원전] 10월 …」 이 되지 않게
            context = re.sub(r"^\[[^\]]{1,6}\]\s*", "", context)
            title = re.sub(r"^\[[^\]]{1,6}\]\s*", "", title)
        if paren == "Q":
            w, paren = parse_when(unit.question, today, anchor=anchor), None
        elif unit.is_qa and " — " in context:
            # 답마다 하나인 후보 — 시점·납기는 **답**에서 먼저 (질문의 「2030년 예약 물량」 이 납기로 끼어들지 않게)
            w = parse_when(context.split(" — ", 1)[1], today, anchor=anchor) or parse_when(context, today, anchor=anchor)
        else:
            w = parse_when(context, today, anchor=anchor)
        if w is None and not unit.is_qa and context != base_text:
            w = parse_when(base_text, today, anchor=anchor)
        if w is None:
            continue
        stage = find_stage(context) or (find_stage(base_text) if not unit.is_qa and context != base_text else None)
        completed = stage == "체결" and w.date <= today + timedelta(days=31) and w.role != "delivery"
        if w.date <= today and not completed:
            continue                                   # 지난 일인데 체결·완료가 아니다 — 촉매가 아니다
        if w.role == "delivery" and not PLAN_WORDS.search(context):
            continue                                   # 「2031년 출하분까지 예약 접수」 — 납기만 있고 사건이 아니다
        # 사업명·드라이버는 **그 후보의 글**에서 — 문장 전체로 가면 「SMR 1조원」 이 옆 항목의 해상풍력을 받는다
        program = hint or event_program(title if unit.is_qa else context) or (event_program(context) if unit.is_qa else None)
        if re.search(r"연간\s*(?:신규\s*)?수주\s*목표", context):
            did = "backlog"                                                    # 전사 수주 목표 — 한 사업이 아니다
        else:
            did = _driver_of(title if unit.is_qa else context, sector) or (_driver_of(context, sector) if unit.is_qa else None) \
                or (None if paren is not None else _driver_of(base_text, sector))
        if did is None and program:
            did = normalize_driver(program, sector=sector)                      # 「불가리아 Kozloduy」 → nuclear_orders
        if did is None and amount_jo(context) is not None:
            did = "backlog"                                                    # 금액이 있는 수주 후보 — 드라이버를 못 찾으면 수주잔고로
        head = program or _driver_label(did)
        if unit.is_qa:
            ans = context.split(" — ", 1)[1] if " — " in context else ""
            body = unit.question + (f" — {ans[:70]}" if ans else "")
        elif paren is not None:
            body = f"{title} ({paren[:60]})"
        else:
            body = context if len(context) <= 90 else time_clause(context, w, limit=80)
        body = " ".join(body.split())
        fact = (f"[{head}] " if head and not body.startswith(head) else "") + body
        fact = fact[:140]
        pre: list[str] = []
        if unit.is_qa:
            pre = [a[:90] for a in (unit.answers or []) if any(k in a for k in _PRECHECK_WORDS) and a[:40] not in fact][:3]
        elif paren and any(k in paren for k in _PRECHECK_WORDS):
            pre = [paren[:90]]
        where = f"{unit.broker} {unit.date.isoformat() if unit.date else ''}".strip() + (f" p.{unit.page}" if unit.page else "")
        evidence = [f"(·) [{unit.broker}] " + " ".join(base_text.split())[:170]]
        amt = amount_jo(title if paren is not None else context)
        high = (amt is not None and amt >= 1.0) or any(k in context for k in _HIGH_WORDS)
        ev = Event(date=min(w.date, today) if completed else w.date, period=w.period if not completed else (w.period or None), fact=fact, affects=[],
                   direction="미정", priced_in="미판단", materiality="H" if high else "M",
                   certainty=_certainty(unit, context, completed), status=EventStatus.completed if completed else EventStatus.scheduled,
                   source=Source(name=where, url=unit.path, retrieved_at=today), evidence=evidence, precheck=pre, project=did,
                   program=program, stage=stage, delivery_period=(w.delivery.period or w.delivery.date.isoformat()[:7]) if w.delivery else None)
        topic = event_topic(ev)
        upd: dict = {}
        if topic == "project_progress" and not unit.is_qa and ev.certainty == EventCertainty.회사계획인용 and not completed:
            upd["certainty"] = EventCertainty.리포트전망           # 대외 프로젝트의 「목표」 는 회사의 계획이 아니다
        key_when = (ev.period or ev.date.isoformat()).lower()
        upd["event_id"] = f"text:{_slug(program or did or head or 'x')}:{topic}:{key_when}"
        out.append(ev.model_copy(update=upd))
    return out


_PROJECT_LINE = re.compile(r"FID|최종투자결정|COL|통합허가|건설허가|EPC\s*계약|발주\s*절차|건설\s*(?:목표|재개)|인허가|기자재\s*(?:구매|발주)|파트너십\s*발표|금융지원\s*발표")


def project_lines(text: str) -> list[str]:
    """프로젝트 표(「V.C. Summer 2·3 / 호기 건설 재개 … / 2028년 상반기 / 최종투자결정(FID) 예상」) 의 줄 — 문장 추출기는 표라고 버린다.
    표의 칸은 줄로 쪼개져 있어, 진행 낱말이 든 줄마다 연도가 보일 때까지 이웃 줄(앞·뒤 1줄) 을 이어 20~220자 조각 하나를 낸다.
    사업명은 그 줄 앞에서 가장 최근에 나온 것을 「[사업명] 」 으로 붙인다 (표의 첫 칸이 사업명이다)."""
    out: list[str] = []
    if not text:
        return out
    lines = [" ".join(ln.split()) for ln in re.split(r"[\n\f]+", text)]
    lines = [ln for ln in lines if ln]
    last_program: str | None = None
    seen: set[str] = set()
    def _is_cell(x: str) -> bool:
        return len(x) <= 25 and not re.search(r"20\d{2}", x) and not _PROJECT_LINE.search(x)

    for i, ln in enumerate(lines):
        if _PROJECT_LINE.search(ln):
            chunk = None
            for lo, hi in ((i, i), (i - 1, i), (i, i + 1), (i - 1, i + 1)):
                if lo < 0 or hi >= len(lines):
                    continue
                c = " ".join(lines[lo:hi + 1])
                if re.search(r"20\d{2}", c) and 20 <= len(c) <= 220 and len(re.findall(r"\d[\d,]*\.?\d*", c)) < 8:
                    chunk = c
                    break
            if chunk is not None:
                prog = event_program(chunk) or last_program     # 사업명 칸은 그 사업의 줄들 **앞**에 온다 — 지금까지 본 마지막 사업명
                key = re.sub(r"\s+", "", chunk)[:50]
                if key not in seen:
                    seen.add(key)
                    out.append((f"[{prog}] " if prog and prog not in chunk else "") + chunk)
        prog_here = event_program(ln)
        if prog_here is None and _is_cell(ln):
            # 사업명 칸이 「미국 / AP1000 / Fleet Project 10기」 처럼 여러 줄로 쪼개진 표 — 짧은 이웃 칸을 이어 본다 (연도·진행 낱말이 든 줄은 칸이 아니다)
            short = []
            for x in lines[i:i + 3]:
                if not _is_cell(x):
                    break
                short.append(x)
            if len(short) >= 2:
                prog_here = event_program(" ".join(short))
        if prog_here:
            last_program = prog_here
    return out[:14]


def attach_untimed_qa(events: list[Event], units: list[TextUnit]) -> int:
    """시점이 없어 이벤트가 안 된 Q&A 답(「주단 소재는 설계 확정 후 제작 준비」) 을 같은 사업의 이벤트에 선행조건(precheck) 으로 붙인다."""
    n = 0
    for u in units:
        if not u.is_qa or not u.answers:
            continue
        prog = event_program(u.question) or event_program(u.text)
        if not prog:
            continue
        targets = [e for e in events if e.program == prog and e.status != EventStatus.completed]
        if not targets:
            continue
        lines = [a[:90] for a in u.answers if any(k in a for k in _PRECHECK_WORDS)]
        if not lines:
            continue
        tgt = min(targets, key=lambda e: e.date)
        add = [x for x in lines if x not in tgt.precheck][:2]
        if add:
            tgt.precheck = [*tgt.precheck, *add]
            n += len(add)
    return n


def _near(c: Event, ev: Event, topic: str) -> bool:
    """같은 사업·주제의 두 사건이 하나인가 — 기간이 겹치거나(2026 ↔ 2026Q4), 수주·계약 주제는 반년 안(「연말~내년 초」 ↔ 「내년」) 이면."""
    from ..units import periods_overlap

    if (c.period or c.date.isoformat()) == (ev.period or ev.date.isoformat()):
        return True
    if c.period and ev.period and periods_overlap(c.period, ev.period):
        return True
    return topic == "orders" and bool(ev.program) and abs((c.date - ev.date).days) <= 200


_STAGE_ORDER = [name for name, _ in STAGES]


def _richness(e: Event) -> tuple[int, int, bool]:
    """같은 확실성이면 더 나아간 단계 · 사전 확인이 많은 것 · 쪽이 적힌(Q&A) 것이 남는다."""
    return (_STAGE_ORDER.index(e.stage) if e.stage in _STAGE_ORDER else -1, len(e.precheck), "p." in (e.source.name if e.source else ""))


def _merge_same(events: list[Event]) -> list[Event]:
    """같은 (사업/드라이버, 주제, 기간) 은 하나 — 확실성이 높은 쪽(Q&A > 본문) 이 남고 증거·사전 확인은 합친다."""
    from .catalysts import certainty_rank

    kept: list[Event] = []
    for ev in events:
        who, topic = ev.program or ev.project or ev.fact[:20], event_topic(ev)
        idx = next((i for i, c in enumerate(kept) if (c.program or c.project or c.fact[:20]) == who and event_topic(c) == topic and _near(c, ev, topic)), None)
        if idx is None:
            kept.append(ev)
            continue
        cur = kept[idx]
        if certainty_rank(ev.certainty) != certainty_rank(cur.certainty):
            keep, gone = (ev, cur) if certainty_rank(ev.certainty) < certainty_rank(cur.certainty) else (cur, ev)
        else:
            keep, gone = (ev, cur) if _richness(ev) > _richness(cur) else (cur, ev)
        kept[idx] = keep.model_copy(update={
            "evidence": keep.evidence + [x for x in gone.evidence if x not in keep.evidence],
            "precheck": keep.precheck + [x for x in gone.precheck if x not in keep.precheck],
            "stage": keep.stage or gone.stage, "delivery_period": keep.delivery_period or gone.delivery_period,
            "materiality": Materiality.H if "H" in (keep.materiality.value, gone.materiality.value) else keep.materiality,
        })
    return kept


def annotate_order_target(events: list[Event]) -> list[str]:
    """「연간 수주 목표 13.3조 · 잔여 3.3조」 이벤트가 있으면 같은 해의 수주 후보 금액을 더해 미확인 금액을 사전 확인에 적는다."""
    notes: list[str] = []
    for tgt in events:
        src = tgt.fact + " " + " ".join(tgt.evidence)
        if not re.search(r"연간\s*(?:신규\s*)?수주\s*목표|잔여\s*수주", src):
            continue
        total = amount_jo(src)
        mr = re.search(r"잔여\s*(?:수주)?\s*\(?\s*(?:약\s*)?(\d[\d,]*(?:\.\d+)?)\s*(조원|억원|천억원|조|억)", src)
        remaining = float(mr.group(1).replace(",", "")) * _JO[mr.group(2)] if mr else None
        if total is None:
            continue
        cands = [(e, amount_jo(e.fact)) for e in events if e is not tgt and event_topic(e) == "orders" and amount_jo(e.fact)
                 and (e.period or "")[:4] == (tgt.period or "")[:4]]
        cands = [(e, a) for e, a in cands if a is not None and a < total]
        s = sum(a for _, a in cands)
        line = f"목표 {total:g}조원" + (f" · 잔여 {remaining:g}조원" if remaining is not None else "") + \
               (f" · 연내 후보 합계 {s:.1f}조원 (" + " · ".join(f"{(e.program or _driver_label(e.project) or e.fact)[:12]} {a:g}조" for e, a in cands) + ")" if cands else "")
        if remaining is not None and cands:
            gap = remaining - s
            line += f" → 후보 밖 미확인 {gap:.1f}조원" if gap > 0.05 else " → 후보로 잔여 목표가 채워진다"
        line += " — 협의 금액은 확정 수주가 아니다; 계약 공시로 확인"
        if line not in tgt.precheck:
            tgt.precheck = [line, *tgt.precheck]
        notes.append(f"수주 목표 점검: {line[:100]}")
    return notes


def report_text_events(digest: Any, *, today: date, sector: str | None = None, texts: dict[str, str] | None = None) -> tuple[list[Event], list[str]]:
    """digest 의 리포트들 → 문장·Q&A·프로젝트 표 기반 촉매 이벤트. texts 는 파일명 → 본문(프로젝트 표 줄용). 돌려주는 것: (이벤트, 메모)."""
    notes: list[str] = []
    events: list[Event] = []
    texts = texts or {}
    for f in getattr(digest, "aggregated", []) or []:
        broker = f.broker or f.path.name
        units: list[TextUnit] = []
        for s in getattr(f, "sentences", []) or []:
            if s.kind == "catalyst":
                units.append(TextUnit(s.text, broker, f.date, None, str(f.path)))
        for q in getattr(f, "qa", []) or []:
            units.append(TextUnit(q.text, broker, f.date, q.page, str(f.path), is_qa=True, question=q.question, answers=list(q.answers)))
        seen = {u.text for u in units}
        for ln in project_lines(texts.get(f.path.name, "")):
            if ln not in seen:
                units.append(TextUnit(ln, broker, f.date, None, str(f.path)))
        per_report: list[Event] = []
        for u in units:
            per_report += events_from_unit(u, today=today, sector=sector)
        per_report = _merge_same(per_report)
        n_pre = attach_untimed_qa(per_report, units)
        if n_pre:
            notes.append(f"{broker}: 시점 없는 Q&A 답 {n_pre}줄을 같은 사업 이벤트의 사전 확인으로")
        if len(per_report) > MAX_EVENTS_PER_REPORT:
            # 중요한 것부터 — H, 회사 계획, 이른 날짜
            per_report.sort(key=lambda e: (e.materiality.value != "H", e.certainty != EventCertainty.회사계획인용, e.date))
            per_report = per_report[:MAX_EVENTS_PER_REPORT]
        if per_report:
            notes.append(f"{broker}: 문장·Q&A 에서 촉매 {len(per_report)}건 (Q&A {sum(1 for u in units if u.is_qa)}항목 · 문장 {sum(1 for u in units if not u.is_qa)}개)")
        events += per_report
    events = _merge_same(events)
    notes += annotate_order_target(events)
    return events, notes


__all__ = ["TextUnit", "events_from_unit", "report_text_events", "split_candidates", "amount_jo", "annotate_order_target", "project_lines", "attach_untimed_qa",
           "MAX_EVENTS_PER_REPORT", "PLAN_WORDS"]
