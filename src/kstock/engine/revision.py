"""판단 수정 규칙 (설계 v7 §3.5·§3.6 — T8): 감지 → 검증 → 영향 → 제안 → 사람 승인. 판단 상태 플래그 · 로그 · 알림.

- 규칙(A5.6.x, `Rule.trigger_type="evidence"`) 은 대상 드라이버 · 경계값 · 지속성 · 등급(shift/review/warn) · 제안을 가진다. 조건에 지표·경계·지속성이 없으면 저장되지 않는다(검증 오류).
- 감지: 드라이버 신호등이 🔴(경계 초과). 검증: 확인됨 등급 · 같은 자 · 연속 n 회. 영향: 드라이버를 가정에 쓰는 시나리오와 A0 논제. 제안: 역할별.
- 자동으로 바뀌는 것은 **신규 BUY 보류 플래그**(필수 조건 위반이 검증됐을 때) 하나뿐. 확률 이동·재검토는 제안이고 사람이 승인한다.
- 판단 상태 층: A 맵 분석 판단(BUY/WATCH/PASS + 플래그) / B 맵 보유 대응(매트릭스 제안) / 주문(시스템 밖). 주문은 막지 않는다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..models import Action, Light, NewMap, Node, Rule
from .evidence import LIGHT_ICON, ROLE_LABEL, comparable, evidence_plan, no_evidence, period_key

GRADE_LABEL = {"shift": "확률 이동", "review": "논제 재검토", "warn": "경고", "kill": "폐기"}
ROLE_DEFAULT_GRADE = {"required": "review", "core": "shift", "secondary": "warn"}


# --------------------------------------------------------------------------- 규칙 만들기 · 검증
def rule_nodes(m: NewMap) -> list[Node]:
    if "A5.6" not in m:
        return []
    return [n for n in m.children("A5.6") if n.rule is not None]


def rule_problems(r: Rule) -> list[str]:
    """v7 규칙(evidence) 의 필수 요소 — 없으면 검증 오류."""
    if r.trigger_type != "evidence":
        return []
    out: list[str] = []
    if not r.driver_id:
        out.append("대상 드라이버(driver_id) 가 없다")
    if r.threshold is None or r.threshold == "":
        out.append("경계값(threshold) 이 없다")
    if r.persistence < 1:
        out.append("지속성(persistence) 은 1 이상")
    if not r.grade:
        out.append("등급(grade: shift·review·warn) 이 없다")
    if r.grade == "shift" and not r.proposal.get("pct_points"):
        out.append("확률 이동 규칙엔 제안 %p(proposal.pct_points) 가 있어야 한다")
    return out


def add_rule(m: NewMap, *, driver_id: str, grade: str, threshold: float | str, persistence: int = 2, side: str = "bear",
             pct_points: float | None = None, scenario_from: str = "Base", scenario_to: str = "Bear", condition: str | None = None,
             note: str | None = None, today: date | None = None) -> Node:
    """A5.6 아래에 규칙 노드를 더한다 (콘솔·CLI·편집 폼). 드라이버의 bound_bear 가 비어 있으면 경계값을 거기에도 적는다."""
    from ..models import NodeType, ValueType
    from .path import ensure_v7_nodes

    today = today or date.today()
    ensure_v7_nodes(m)
    d = m[driver_id].driver
    if d is None:
        raise ValueError(f"{driver_id} 는 드라이버가 아니다")
    kind = {"shift": "hold_new_buy" if (d.thesis_role == "required") else "shift", "review": "review", "warn": "warn", "kill": "kill"}[grade]
    if grade == "review" and d.thesis_role == "required":
        kind = "hold_new_buy"
    proposal: dict[str, Any] = {"kind": kind}
    if grade == "shift":
        proposal.update({"scenario_from": scenario_from, "scenario_to": scenario_to, "pct_points": pct_points or 10.0})
    label = m[driver_id].label.split(" (")[0]
    cond = condition or f"{label} 가 {'아래로' if side == 'bear' else '위로'} {threshold} 를 넘음 (연속 {persistence}회, 확인된 관측값만)"
    rule = Rule(trigger_type="evidence", condition=cond, action=Action.review, grade=grade, driver_id=driver_id, threshold=threshold, side=side,
                persistence=persistence, proposal=proposal, note=note)
    probs = rule_problems(rule)
    if probs:
        raise ValueError("규칙에 빠진 것: " + "; ".join(probs))
    n_children = len(m.children("A5.6"))
    nid = f"A5.6.{n_children + 1}"
    while nid in m:
        n_children += 1
        nid = f"A5.6.{n_children + 1}"
    node = Node(id=nid, map=m.kind, parent="A5.6", label=f"{GRADE_LABEL[grade]} — {label}", node_type=NodeType.rule, value_type=ValueType.text,
                question="증거 계획의 지표·경계값·지속성이 들어간 규칙. 발동 → 검증 → 제안 → 사람 승인", rule=rule, as_of=today)
    m.add(node)
    if d.bound_bear is None and side == "bear":
        d.bound_bear = threshold
        d.bounds_source = d.bounds_source or "user"
    if d.bound_bull is None and side == "bull":
        d.bound_bull = threshold
    return node


# --------------------------------------------------------------------------- 감지 → 검증 → 영향 → 제안
def _events(m: NewMap) -> list[dict[str, Any]]:
    return m.meta.setdefault("draft", {}).setdefault("rule_events", [])


def _impact(m: NewMap, driver_id: str) -> dict[str, Any]:
    scen = [name for name, n in m.scenario_nodes().items() if n.scenario is not None and any(da.node_id == driver_id for da in n.scenario.driver_assumptions)]
    label = m[driver_id].label.split(" (")[0]
    thesis = m.thesis or ""
    return {"scenarios": scen, "thesis_mentions": label in thesis, "driver": label}


def evaluate_rules(m: NewMap, *, today: date | None = None) -> list[dict[str, Any]]:
    """A5.6 규칙을 평가해 이벤트를 만든다/갱신한다. 돌려주는 것은 이번에 새로 생기거나 상태가 바뀐 이벤트."""
    today = today or date.today()
    rows = evidence_plan(m, today=today, write=True)
    lights = {r.node_id: r for r in rows}
    changed: list[dict[str, Any]] = []
    events = _events(m)
    for n in rule_nodes(m):
        r = n.rule
        assert r is not None
        if r.trigger_type != "evidence" or r.status == "disabled" or not r.driver_id or r.driver_id not in m:
            continue
        d = m[r.driver_id].driver
        lr = lights.get(r.driver_id)
        if d is None or lr is None:
            continue
        crossed = lr.light == Light.RED if r.side == "bear" else bool(lr.light is not None and "Bull 쪽 이탈" in (lr.reason or ""))
        existing = next((e for e in events if e["rule"] == n.id and e["status"] in ("detected", "verified", "proposed")), None)
        if not crossed:
            if existing is not None and existing["status"] == "detected":
                existing["status"], existing["closed_at"] = "cleared", today.isoformat()
                changed.append(existing)
            continue
        if existing is None:
            existing = {"id": f"ev{len(events) + 1}", "rule": n.id, "rule_label": n.label, "driver": r.driver_id, "grade": r.grade, "status": "detected",
                        "detected_at": today.isoformat(), "reason": lr.reason}
            events.append(existing)
            changed.append(existing)
        # 검증: 확인됨 등급 · 같은 자 · 연속 n 회
        good = [o for o in d.observations if comparable(o, d.required)[0]]
        good.sort(key=lambda o: (period_key(o.period) or (0, 0), o.as_of))
        streak = 0
        bb = r.threshold
        for o in reversed(good):
            v = o.numeric
            if isinstance(bb, (int, float)) and v is not None:
                hit = (v < bb) if (r.side == "bear") == d.higher_is_better else (v > bb)
            else:
                hit = isinstance(o.value, str) and isinstance(bb, str) and bb in o.value
            if hit:
                streak += 1
            else:
                break
        existing["streak"] = streak
        if streak >= r.persistence and existing["status"] == "detected":
            existing["status"], existing["verified_at"] = "verified", today.isoformat()
            existing["impact"] = _impact(m, r.driver_id)
            existing["proposal"] = dict(r.proposal)
            existing["status"] = "proposed"
            existing["proposed_at"] = today.isoformat()
            existing.pop("note", None)
            changed.append(existing)
            if existing["proposal"].get("kind") == "hold_new_buy":
                m.meta.setdefault("flags", {})["hold_new_buy"] = True        # 자동으로 바뀌는 유일한 것
            _log(m, today, "rule", f"{n.label}: 검증 통과(연속 {streak}회) → 제안 {existing['proposal'].get('kind')} — 승인 대기")
            if r.status == "armed":
                r.status, r.fired_at = "fired", today
        elif existing["status"] == "detected":
            existing["note"] = f"감지만 — 지속성 {streak}/{r.persistence} (검증 전)"
    compute_flags(m, rows)
    return changed


def approve_event(m: NewMap, event_id: str, *, accept: bool, today: date | None = None, pct_points: float | None = None, note: str = "") -> dict[str, Any]:
    """사람이 제안을 승인/거절한다. 승인하면 제안을 적용하고(확률 이동 · 재검토 플래그), 로그에 남긴다."""
    today = today or date.today()
    ev = next((e for e in _events(m) if e["id"] == event_id), None)
    if ev is None:
        raise ValueError(f"이벤트 {event_id} 없음")
    if ev["status"] != "proposed":
        raise ValueError(f"이벤트 {event_id} 는 승인 대기가 아니다 ({ev['status']})")
    pr = ev.get("proposal") or {}
    if not accept:
        ev["status"], ev["decided_at"], ev["decision_note"] = "rejected", today.isoformat(), note
        if pr.get("kind") == "hold_new_buy":
            m.meta.setdefault("flags", {})["hold_new_buy"] = False
        _log(m, today, "approval", f"{ev['rule_label']}: 제안 거절" + (f" — {note}" if note else ""))
        compute_flags(m)
        return ev
    kind = pr.get("kind")
    applied = ""
    if kind == "shift":
        pp = float(pct_points if pct_points is not None else pr.get("pct_points") or 0.0) / 100.0
        scs = m.scenario_nodes()
        a, b = scs.get(pr.get("scenario_from", "Base")), scs.get(pr.get("scenario_to", "Bear"))
        if a is not None and b is not None and a.scenario is not None and b.scenario is not None and a.scenario.probability is not None and b.scenario.probability is not None:
            move = min(pp, a.scenario.probability)
            a.scenario.probability = round(a.scenario.probability - move, 4)
            b.scenario.probability = round(b.scenario.probability + move, 4)
            applied = f"확률 {pr.get('scenario_from')}→{pr.get('scenario_to')} {move * 100:.0f}%p 이동"
            _log(m, today, "probability", applied)
        else:
            applied = "확률이 비어 있어 이동하지 못함 — 확률을 먼저 넣을 것"
    elif kind in ("review", "hold_new_buy"):
        m.meta.setdefault("flags", {})["review"] = True
        applied = "재검토 — A2.2 내 뷰와 A0 논제를 다시 쓸 것 (신규 BUY 보류)"
        _log(m, today, "thesis", applied)
    elif kind == "warn":
        applied = "경고 확인 — 모니터링 주기 단축 권고"
    ev["status"], ev["decided_at"], ev["applied"], ev["decision_note"] = "approved", today.isoformat(), applied, note
    _log(m, today, "approval", f"{ev['rule_label']}: 제안 승인 — {applied}")
    compute_flags(m)
    return ev


def clear_flag(m: NewMap, name: str, *, today: date | None = None, note: str = "") -> None:
    today = today or date.today()
    m.meta.setdefault("flags", {})[name] = False
    _log(m, today, "flag", f"플래그 {name} 해제" + (f" — {note}" if note else ""))


# --------------------------------------------------------------------------- 플래그 · 로그
def compute_flags(m: NewMap, rows: list[Any] | None = None) -> dict[str, Any]:
    """{hold_new_buy, review, no_evidence} — hold 는 이벤트(hold_new_buy 제안 대기/승인) 또는 review 플래그에서, no_evidence 는 ⚪ 과반."""
    flags = m.meta.setdefault("flags", {})
    events = _events(m)
    hold_ev = any(e["status"] in ("proposed", "approved") and (e.get("proposal") or {}).get("kind") == "hold_new_buy" and not e.get("cleared") for e in events)
    flags["hold_new_buy"] = bool(hold_ev or flags.get("review"))
    flags["review"] = bool(flags.get("review"))
    if rows is None:
        rows = evidence_plan(m, write=False)
    flags["no_evidence"] = no_evidence(rows)
    flags["pending_proposals"] = sum(1 for e in events if e["status"] == "proposed")
    return flags


def _log(m: NewMap, today: date, kind: str, text: str) -> None:
    if "A8.2" not in m:
        return
    log = m["A8.2"].value if isinstance(m["A8.2"].value, list) else []
    log.append({"date": today.isoformat(), "kind": kind, "text": text})
    m["A8.2"].value = log
    m["A8.2"].as_of = today


def log_observation(m: NewMap, today: date, node_id: str, text: str) -> None:
    _log(m, today, "observe", f"{node_id} 관측값: {text}")


# --------------------------------------------------------------------------- 텍스트 · 알림
def rules_text(m: NewMap) -> str:
    rows = ["판단 수정 규칙 (A5.6) — 등급 · 대상 · 경계 · 지속성 · 제안 · 상태"]
    ns = rule_nodes(m)
    if not ns:
        rows.append("  없음 — 콘솔 「G」 또는 `mindmap rule add` 로 만든다 (Kill 은 A5.4)")
    for n in ns:
        r = n.rule
        assert r is not None
        probs = rule_problems(r)
        rows.append(f"  {n.id} {GRADE_LABEL.get(r.grade, r.grade or '—'):<6} {m[r.driver_id].label.split(' (')[0] if r.driver_id in m else r.driver_id!s:<12} "
                    f"{r.side} {r.threshold!s:<14} 연속 {r.persistence}회  제안 {r.proposal.get('kind', '—')}"
                    + (f" {r.proposal.get('pct_points')}%p" if r.proposal.get("pct_points") else "") + f"  [{r.status}]" + (f"  ⚠ {'; '.join(probs)}" if probs else ""))
    evs = _events(m)
    if evs:
        rows.append("  이벤트:")
        for e in evs[-10:]:
            rows.append(f"    {e['id']} {e['rule_label'][:20]:<20} {e['status']:<9} 감지 {e.get('detected_at', '')}" + (f" · 지속 {e.get('streak')}" if e.get("streak") is not None else "")
                        + (f" · 제안 {(e.get('proposal') or {}).get('kind')}" if e.get("proposal") else "") + (f" · {e.get('applied')}" if e.get("applied") else "") + (f" · {e.get('note')}" if e.get("note") else ""))
    fl = m.meta.get("flags") or {}
    rows.append(f"  플래그: 신규 BUY 보류 {'예' if fl.get('hold_new_buy') else '아니오'} · 재검토 {'예' if fl.get('review') else '아니오'} · 증거 없는 판단 {'예' if fl.get('no_evidence') else '아니오'}"
                + (f" · 승인 대기 {fl.get('pending_proposals')}" if fl.get("pending_proposals") else ""))
    return "\n".join(rows)


def alerts(m: NewMap, *, today: date | None = None, horizon_days: int = 7) -> list[str]:
    """알림 텍스트 — 규칙 이벤트(감지/검증/제안) · 증거 일정 도래 · 관측값 노후 · 🔴/🟡. 전송은 에이전트 몫."""
    from .evidence import evidence_schedule

    today = today or date.today()
    out: list[str] = []
    rows = evidence_plan(m, today=today, write=False)
    for e in _events(m):
        if e["status"] == "proposed":
            out.append(f"[제안 대기] {e['rule_label']} — {(e.get('proposal') or {}).get('kind')} (감지 {e.get('detected_at')}, 지속 {e.get('streak')}) → 승인/거절 필요")
        elif e["status"] == "detected":
            out.append(f"[감지] {e['rule_label']} — {e.get('note') or e.get('reason')}")
    for r in rows:
        if r.light == Light.RED:
            out.append(f"[🔴] {r.label} ({ROLE_LABEL.get(r.role)}) — {r.reason}")
        elif r.light == Light.YELLOW and r.trend == "drifting":
            out.append(f"[🟡 추세] {r.label} — {r.reason}")
    for x in evidence_schedule(m, today=today):
        try:
            dt = date.fromisoformat(x["date"])
        except ValueError:
            continue
        if today <= dt <= today + timedelta(days=horizon_days):
            out.append(f"[일정 {x['date']}] {x['what']} — {x.get('where') or ''}" + (f" vs {x['compare_at']} {x['required']}" if x.get("compare_at") else ""))
    for n in m.drivers():
        d = n.driver
        if d is None or d.role == "result" or not d.observations:
            continue
        last = max(d.observations, key=lambda o: o.as_of)
        from .evidence import cadence_days

        if (today - last.as_of).days > 2 * cadence_days(last.period):
            out.append(f"[노후] {n.label.split(' (')[0]} 관측값 {last.as_of} — {(today - last.as_of).days}일 전")
    fl = m.meta.get("flags") or {}
    if fl.get("hold_new_buy"):
        out.append("[상태] 신규 BUY 보류 중")
    if fl.get("no_evidence"):
        out.append("[상태] 증거 없는 판단 — ⚪ 가 드라이버 과반")
    return out


def lights_line(m: NewMap) -> str:
    rows = evidence_plan(m, write=False)
    return " · ".join(f"{LIGHT_ICON.get(r.light)} {r.label}" for r in rows) or "드라이버 없음"


__all__ = ["rule_nodes", "rule_problems", "add_rule", "evaluate_rules", "approve_event", "clear_flag", "compute_flags", "rules_text", "alerts", "lights_line",
           "log_observation", "GRADE_LABEL"]
