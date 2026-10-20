"""LLM 그룹 채움 — 규칙 채움 뒤에, 노드의 question 에 답하는 부분을 LLM 이 쓴다 (자동 생성 계획 §3b).

- `GroupFiller`      : `complete(system, user) -> str` 하나면 된다.
- `MockGroupFiller`  : 테스트용. 그룹 이름 → 미리 준 dict.
- `ClaudeGroupFiller`: anthropic SDK (`pip install kstock[llm]`, ANTHROPIC_API_KEY).
- `fill_groups()`    : 그룹 순서대로 묻고, 답을 검증해 맵에 적용한다. 숫자 환각 방어:
    * 드라이버 current/consensus 는 evidence 문장이 자료 안에 있어야 받는다. 규칙 채움 드라이버(keep_id) 의 숫자는 LLM 이 못 바꾼다.
    * 시나리오 목표가는 앵커 ±25% 로 자른다. 확률은 합 1 로 정규화.
    * Kill 조건의 driver_id 는 맵에 있는 드라이버만, threshold 는 숫자만.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date
from typing import Any, Protocol

from ..models import (
    Action,
    Driver,
    Event,
    MapKind,
    Measurement,
    NewMap,
    Node,
    NodeStatus,
    Rule,
    Scenario,
    Source,
)
from ..reports.digest import ReportDigest
from .prompts import GROUPS, SYSTEM_GROUP, group_prompt, parse_json
from .rules import DRAFT, mark_draft

Progress = Callable[[str, int, int], None]


class GroupFiller(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class MockGroupFiller:
    """answers: {group: dict}. 그룹은 프롬프트 첫 줄 '## 그룹: xxx' 로 읽는다."""

    def __init__(self, answers: dict[str, dict[str, Any]] | None = None):
        self.answers = answers or {}
        self.calls: list[str] = []

    def complete(self, system: str, user: str) -> str:
        m = re.search(r"## 그룹: (\w+)", user)
        group = m[1] if m else "?"
        self.calls.append(group)
        return json.dumps(self.answers.get(group, {}), ensure_ascii=False)


class ClaudeGroupFiller:
    def __init__(self, model: str | None = None, *, api_key: str | None = None, max_tokens: int = 2000):
        from ..llm.filler import ClaudeFiller

        self._inner = ClaudeFiller(model, api_key=api_key, max_tokens=max_tokens)

    def complete(self, system: str, user: str) -> str:
        return self._inner._complete(system, user)


def llm_available() -> bool:
    """anthropic 패키지 + ANTHROPIC_API_KEY (환경변수 또는 프로젝트 .env)."""
    return anthropic_installed() and has_key()


def anthropic_installed() -> bool:
    try:
        import anthropic  # type: ignore  # noqa: F401
    except ImportError:
        return False
    return True


def has_key() -> bool:
    from ..env import has

    return has("ANTHROPIC_API_KEY")


def llm_status() -> str:
    """콘솔·CLI 가 보여 주는 한 줄 — 값은 절대 안 보인다."""
    from ..env import describe

    pkg = "anthropic 설치됨" if anthropic_installed() else "anthropic 미설치 (pip install -e \".[llm]\")"
    return f"{describe('ANTHROPIC_API_KEY')} · {pkg}"


# ---------------------------------------------------------------------------- 도우미
def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").rstrip("%").strip())
    except ValueError:
        return None


def _date(v: Any) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _txt(v: Any, n: int = 400) -> str | None:
    if v is None:
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s[:n] or None


def evidence_in(evidence: str | None, materials: str) -> bool:
    """인용 문장이 자료에 있나 — 공백·구두점을 지우고 토큰의 70% 이상이 이어져 나오면 인정."""
    if not evidence:
        return False
    norm = lambda s: re.sub(r"[\s,\.\(\)%원·\-–—:;\"'“”]+", "", s)  # noqa: E731
    e, mat = norm(evidence), norm(materials)
    if len(e) < 6:
        return False
    if e in mat:
        return True
    # 느슨하게: 6글자 조각들의 비율
    chunks = [e[i:i + 6] for i in range(0, max(1, len(e) - 5), 6)]
    hit = sum(1 for c in chunks if c in mat)
    return hit / max(1, len(chunks)) >= 0.7


# ---------------------------------------------------------------------------- 그룹별 적용
def apply_drivers(m: NewMap, data: dict[str, Any], d: ReportDigest, *, today: date, materials: str) -> list[str]:
    notes: list[str] = []
    items = data.get("drivers") or []
    if not isinstance(items, list) or not items:
        return ["LLM 드라이버 응답 없음 — 규칙 채움 유지"]
    existing = {n.id: n for n in m.drivers()}
    src_llm = Source(name="리포트 인용 (LLM 추출)", url=d.source_all().url, retrieved_at=d.as_of)
    chosen: list[Node] = []
    new_nodes: list[Node] = []
    for it in items[:5]:
        if not isinstance(it, dict):
            continue
        keep = it.get("keep_id")
        label = _txt(it.get("label"), 60)
        if keep and keep in existing:
            n = existing[keep]
            dr = n.driver
            assert dr is not None
            # 숫자는 규칙 채움 그대로. 라벨·근거·확인지표·내 추정만 받는다
            if label:
                n.label = label
            my = _num(it.get("my_estimate"))
            if my is not None:
                dr.my_estimate = Measurement(value=my, as_of=today, rationale=f"{_txt(it.get('rationale'), 200) or 'LLM'} — {DRAFT}")
            if _txt(it.get("check_indicator")):
                dr.check_indicator = _txt(it.get("check_indicator"), 120) or dr.check_indicator
            nc = _date(it.get("next_check"))
            if nc and nc > today:
                dr.next_check = nc
            chosen.append(n)
            continue
        cur = _num(it.get("current"))
        if cur is None or not label:
            notes.append(f"LLM 드라이버 '{label or '?'}' 제외 — current 없음")
            continue
        if not evidence_in(_txt(it.get("evidence"), 300), materials):
            notes.append(f"LLM 드라이버 '{label}' 제외 — 인용(evidence) 이 자료에 없다 (숫자 환각 방어)")
            continue
        cons = _num(it.get("consensus"))
        my = _num(it.get("my_estimate"))
        if my is None:
            my = cons if cons is not None else cur
        drv = Driver(current=Measurement(value=cur, as_of=_date(it.get("current_as_of")) or d.as_of, source=src_llm,
                                         rationale=_txt(it.get("evidence"), 200)),
                     consensus=Measurement(value=cons, as_of=d.as_of, source=src_llm if cons is not None else None) if cons is not None else Measurement(),
                     my_estimate=Measurement(value=my, as_of=today, rationale=f"{_txt(it.get('rationale'), 200) or 'LLM'} — {DRAFT}"),
                     check_indicator=_txt(it.get("check_indicator"), 120) or "", next_check=_date(it.get("next_check")),
                     higher_is_better=bool(it.get("higher_is_better", True)))
        node = Node(id="A1.tmp", map=MapKind.NEW, parent="A1", label=label, node_type="estimate", value_type="number",
                    unit=_txt(it.get("unit"), 12), as_of=today, driver=drv, confidence="L",
                    question="이 변수가 틀리면 논제가 무너지는가?")
        new_nodes.append(node)
    if not chosen and not new_nodes:
        return notes + ["LLM 드라이버 전부 제외 — 규칙 채움 유지"]
    # 슬롯 재배치: 유지된 것 먼저, 새 것은 빈 슬롯 또는 A1.4/A1.5
    slot_ids = [n.id for n in m.children("A1")]
    keep_ids = {n.id for n in chosen}
    free = [sid for sid in slot_ids if sid not in keep_ids]
    for node in new_nodes:
        if free:
            sid = free.pop(0)
            tgt = m[sid]
            tgt.label, tgt.unit, tgt.driver, tgt.as_of, tgt.confidence = node.label, node.unit, node.driver, today, node.confidence
            mark_draft(tgt)
            chosen.append(tgt)
        else:
            if len(chosen) >= 5:
                break
            nid = f"A1.{len(slot_ids) + 1}"
            slot_ids.append(nid)
            node.id = nid
            mark_draft(node)
            m.add(node)
            chosen.append(node)
    # LLM 이 고르지 않은 규칙 드라이버는 비운다 (3개 미만이 되면 남긴다)
    for sid in free:
        if len(chosen) >= 3:
            m[sid].driver = None
            m[sid].label = f"드라이버 {sid.split('.')[1]}"
            m[sid].unit = None
    notes.append(f"LLM 드라이버 {len(chosen)}개 (유지 {len(keep_ids)}, 신규 {len(new_nodes)})")
    return notes


def apply_thesis(m: NewMap, data: dict[str, Any], *, today: date) -> list[str]:
    notes: list[str] = []
    t = _txt(data.get("thesis"), 600)
    if t:
        m["A0"].set(f"[LLM 초안] {t}", as_of=today, status=NodeStatus.unverified)
        mark_draft(m["A0"])
    if data.get("horizon") in ("3M", "1Y", "3Y+"):
        m["A0.2"].set(data["horizon"], as_of=today, status=NodeStatus.unverified)
    if data.get("confidence") in ("H", "M", "L"):
        m["A0.3"].set(data["confidence"], as_of=today, status=NodeStatus.unverified)
    if _txt(data.get("implied")):
        m["A2.1"].set(_txt(data["implied"], 500), as_of=today, source=m["A2.1"].source or Source(name="LLM (리포트 발췌 기반)", retrieved_at=today))
        mark_draft(m["A2.1"])
    if _txt(data.get("mine")):
        m["A2.2"].set(f"[LLM 초안] {_txt(data['mine'], 500)}", as_of=today, status=NodeStatus.unverified)
        mark_draft(m["A2.2"])
    if data.get("why_market_wrong") in ("시간축", "정보", "심리", "구조"):
        m["A2.3"].set(data["why_market_wrong"], as_of=today, status=NodeStatus.unverified)
        mark_draft(m["A2.3"])
    if data.get("invest_type") in ("growth", "value", "turnaround", "momentum", "event") and data["invest_type"] != (m.profile.value if m.profile else None):
        notes.append(f"LLM 이 제안한 투자유형 {data['invest_type']} ≠ 현재 프로파일 {m.profile.value if m.profile else '-'} — 바꾸려면 새 맵을 만든다")
    return notes


def apply_scenarios(m: NewMap, data: dict[str, Any], *, today: date, anchors: dict[str, float]) -> list[str]:
    notes: list[str] = []
    probs: dict[str, float] = {}
    tps: dict[str, float] = {}
    for nm in ("Bear", "Base", "Bull"):
        sc = data.get(nm)
        if not isinstance(sc, dict):
            return ["LLM 시나리오 응답 불완전 — 규칙 앵커 유지"]
        p, tp = _num(sc.get("probability")), _num(sc.get("target_price"))
        if p is None or tp is None or tp <= 0:
            return ["LLM 시나리오에 확률/목표가 누락 — 규칙 앵커 유지"]
        anchor = anchors.get(nm)
        if anchor:
            lo, hi = anchor * 0.75, anchor * 1.25
            if not (lo <= tp <= hi):
                notes.append(f"{nm} 목표가 {tp:,.0f} 가 앵커 {anchor:,.0f} ±25% 밖 → {min(max(tp, lo), hi):,.0f} 로 자름")
                tp = min(max(tp, lo), hi)
        probs[nm], tps[nm] = p, tp
    s = sum(probs.values())
    if s <= 0:
        return ["LLM 시나리오 확률 합 0 — 규칙 앵커 유지"]
    if abs(s - 1.0) > 0.01:
        notes.append(f"확률 합 {s:.2f} → 1.0 으로 정규화")
        probs = {k: v / s for k, v in probs.items()}
    # 순서 보정
    if not (tps["Bear"] < tps["Base"] < tps["Bull"]):
        notes.append("목표가 순서 Bear<Base<Bull 위반 → 정렬")
        vals = sorted(tps.values())
        tps = dict(zip(("Bear", "Base", "Bull"), vals, strict=True))
    for nid, nm in (("A3.1", "Bear"), ("A3.2", "Base"), ("A3.3", "Bull")):
        sc = data[nm]
        assumptions = [str(a)[:120] for a in (sc.get("assumptions") or []) if str(a).strip()][:6]
        if not assumptions and m[nid].scenario is not None:
            assumptions = list(m[nid].scenario.assumptions)
        m[nid].scenario = Scenario(name=nm, probability=round(probs[nm], 3), target_price=round(tps[nm], -2) if tps[nm] >= 10000 else round(tps[nm]),
                                   assumptions=assumptions, horizon_months=12,
                                   valuation_method=(_txt(sc.get("method"), 160) or "") + " (LLM 초안)")
        m[nid].as_of = today
        mark_draft(m[nid])
    # 마지막 확률을 맞춰 합이 정확히 1 이 되게
    tot = sum(m[n].scenario.probability for n in ("A3.1", "A3.2", "A3.3"))
    if abs(tot - 1.0) > 1e-6:
        m["A3.2"].scenario.probability = round(m["A3.2"].scenario.probability + (1.0 - tot), 3)
    return notes


def apply_risks(m: NewMap, data: dict[str, Any], *, today: date) -> list[str]:
    notes: list[str] = []
    src = Source(name="LLM (리포트 발췌 기반)", retrieved_at=today)
    risks = [r for r in (data.get("risks") or []) if isinstance(r, dict) and _txt(r.get("text"))][:3]
    for nid, r in zip(("A4.1", "A4.2", "A4.3"), risks, strict=False):
        p = _num(r.get("probability"))
        imp = _num(r.get("impact_pct"))
        m[nid].set({"probability": min(max(p if p is not None else 0.25, 0.0), 1.0), "impact_pct": -abs(imp) if imp is not None else -20,
                    "text": _txt(r["text"], 200) + f" ({DRAFT})"}, as_of=today, source=src)
        mark_draft(m[nid])
    driver_ids = {n.id for n in m.drivers()}
    kills = [k for k in (data.get("kill") or []) if isinstance(k, dict)][:2]
    made = 0
    for k in kills:
        did, op, thr = k.get("driver_id"), k.get("op"), _num(k.get("threshold"))
        if did not in driver_ids or op not in ("<", "<=", ">", ">=") or thr is None:
            notes.append(f"LLM Kill 조건 제외 — driver_id/op/threshold 가 맞지 않음: {k}")
            continue
        made += 1
        nid = "A4.4.1" if made == 1 else "A4.4.2"
        rule = Rule(trigger_type="fact", condition=f"driver[{did}].current {op} {thr:g}", action=Action.exit, escalate_to="RED",
                    note=(_txt(k.get("note"), 200) or "") + f" — {DRAFT}")
        if nid in m:
            m[nid].rule = rule
            m[nid].as_of = today
            mark_draft(m[nid])
        else:
            node = Node(id=nid, map=MapKind.NEW, parent="A4.4", label=f"무효화 조건 {made}", node_type="rule", value_type="text", as_of=today, rule=rule)
            mark_draft(node)
            m.add(node)
    if _txt(data.get("early_warning")):
        m["A4.5"].set(_txt(data["early_warning"], 300), as_of=today, source=src)
        mark_draft(m["A4.5"])
    return notes


def apply_catalysts(m: NewMap, data: dict[str, Any], *, today: date) -> list[str]:
    events = [e for e in (data.get("events") or []) if isinstance(e, dict)]
    good: list[Event] = []
    driver_ids = {n.id for n in m.drivers()}
    for e in events:
        dt = _date(e.get("date"))
        fact = _txt(e.get("fact"), 160)
        if dt is None or not fact or dt < today:
            continue
        est = bool(e.get("estimated"))
        good.append(Event(date=dt, fact=fact + (" (예정일 추정)" if est and "추정" not in fact else ""),
                          affects=[a for a in (e.get("affects") or []) if a in driver_ids],
                          direction=e.get("direction") if e.get("direction") in ("강화", "약화", "중립") else "중립",
                          priced_in="아니오", materiality=e.get("materiality") if e.get("materiality") in ("H", "M", "L") else "M",
                          source=Source(name="LLM (리포트 발췌 기반)" + (" · 통상 일정 추정" if est else ""), retrieved_at=today)))
    if not good:
        return ["LLM 촉매 응답 없음 — 규칙 일정 유지"]
    for n in list(m.children("A5.1")):
        del m.nodes[n.id]
    for i, ev in enumerate(sorted(good, key=lambda e: e.date)[:5], start=1):
        node = Node(id=f"A5.1.{i}", map=MapKind.NEW, parent="A5.1", label=ev.fact[:30], node_type="event", value_type="text", as_of=today, event=ev)
        mark_draft(node)
        m.add(node)
    return [f"LLM 촉매 {min(len(good), 5)}개"]


def apply_evidence(m: NewMap, data: dict[str, Any], *, today: date) -> list[str]:
    src = Source(name="LLM (리포트 발췌 요약)", retrieved_at=today)
    for key, nid in (("business_model", "A8.1.1"), ("moat", "A8.1.2"), ("governance_payout", "A8.1.3"), ("peers", "A3.6.2")):
        t = _txt(data.get(key), 400)
        if t and t.lower() != "null":
            m[nid].set(t, as_of=today, source=src)
            mark_draft(m[nid])
    return []


APPLY = {"drivers": apply_drivers, "thesis": apply_thesis, "scenarios": apply_scenarios, "risks": apply_risks,
         "catalysts": apply_catalysts, "evidence": apply_evidence}


# ---------------------------------------------------------------------------- 진입점
def fill_groups(m: NewMap, d: ReportDigest, filler: GroupFiller, *, today: date, sector: str | None,
                anchors: dict[str, float], groups: tuple[str, ...] = GROUPS, progress: Progress | None = None) -> list[str]:
    notes: list[str] = []
    materials = d.materials()
    for i, g in enumerate(groups, start=1):
        if progress:
            progress(f"LLM {g}", i, len(groups))
        try:
            raw = filler.complete(SYSTEM_GROUP, group_prompt(g, m, d, sector=sector, anchors=anchors))
            data = parse_json(raw)
        except Exception as e:  # noqa: BLE001 — 한 그룹이 실패해도 나머지는 간다
            notes.append(f"LLM {g} 실패: {type(e).__name__}: {str(e)[:120]} — 규칙 채움 유지")
            continue
        if g == "drivers":
            notes += apply_drivers(m, data, d, today=today, materials=materials)
        elif g == "scenarios":
            notes += apply_scenarios(m, data, today=today, anchors=anchors)
        else:
            notes += APPLY[g](m, data, today=today)  # type: ignore[operator]
    m.meta.setdefault("draft", {})["llm"] = True
    return notes


__all__ = ["GroupFiller", "MockGroupFiller", "ClaudeGroupFiller", "llm_available", "anthropic_installed", "has_key", "llm_status",
           "fill_groups", "evidence_in", "GROUPS"]
