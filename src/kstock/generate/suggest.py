"""리포트 정보로 만든 **제안값** (설계 v4 §6 / V13) — 사람 몫 노드의 빈 칸 옆에 근거와 함께 보여 주고, 사용자가 「채우기」 로 받아들여야 값이 된다.

초안이 비워 두는 노드 가운데 리포트가 근거를 줄 수 있는 것만 만든다 (확률·Kill·A7.5 판단에는 제안을 만들지 않는다):

    kind                노드 · 필드                       값                                  근거
    consensus_median    A1.x driver.my_estimate.value     컨센서스 (맵의 컨센 값 = 증권사 평균)   V4 셀 집계 (증권사별 값을 label 에)
    issue_minority      A2.2 value                        핵심 이견의 소수 의견 명제 + 인용        V3 쟁점 표 (display=핵심 이견)
    conditional_claim   A5.1~3 value (비어 있을 때만)       조건부 리스크 Claim 의 조건문 + 인용       V6 kill_candidates
    price_band          A7.2 value {price_range}          Bear 앵커 ~ 현재가                      A3.5.4 분포 · A0.4

A2.3(시장이 틀린 이유) 은 열거형(시간축/정보/심리/구조) 이라 제안을 만들지 않는다 — 목표가 리비전 관측은 A3.5.3 에 이미 있다.

저장 위치 `meta.draft.suggestions = {node_id: [{field, value, label, basis, kind, report_ids, quote?, accepted_at?, prefilled?}]}`.
`field` 는 노드 필드 경로 ("value" | "driver.my_estimate.value"); HTML 편집 폼이 입력 칸 이름으로 바꿔 쓴다.

`apply_prefill` (옵션 `draft --prefill-suggestions`, 기본 꺼짐): 제안값을 값 칸에 넣되 `suggested` 태그 + `pending` 유지 + 출처
"리포트 제안(미확정)". 사용자가 HTML 편집 폼에서 저장하면 `suggested` → `user` (generate/edits.py). 권장하지 않는다 — 빈 칸보다 틀린 숫자가
낫다고 보는 사용자만 켠다 (설계 v4 §6.2).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..models import IssueDisplay, Measurement, NewMap, Source

KIND_LABEL = {"consensus_median": "컨센서스", "issue_minority": "쟁점 소수 의견", "conditional_claim": "조건부 리스크", "price_band": "진입 가격대"}
PREFILL_TAG = "suggested"
PREFILL_SOURCE = "리포트 제안(미확정)"
ACCEPT_SOURCE = "리포트 제안 수락"


def _fmt(v: Any, unit: str | None = None) -> str:
    if isinstance(v, (int, float)):
        s = f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}".rstrip("0").rstrip(".")
        return s + (unit or "")
    return str(v)


def _fmt_amount(v: Any, unit: str | None) -> str:
    """금액·비율은 공통 포맷터(`kstock.units.fmt_amount`) 로 — 억원 원천이 1조를 넘으면 조원으로 보인다. 그 밖의 단위는 예전 표기."""
    from ..units import UNIT_TO_EOK, fmt_amount

    if isinstance(v, (int, float)) and ((unit or "") in UNIT_TO_EOK or unit in ("%", "%p", "원", "주")):
        return fmt_amount(float(v), unit)
    return _fmt(v, unit)


def _sugg(node_id: str, field: str, value: Any, *, label: str, basis: str, kind: str, report_ids: list[str] | None = None,
          quote: str | None = None) -> dict[str, Any]:
    return {"node_id": node_id, "field": field, "value": value, "label": label, "basis": basis, "kind": kind,
            "report_ids": sorted(set(report_ids or [])), "quote": quote}


def _report_ids_by_broker(m: NewMap) -> dict[str, str]:
    out: dict[str, str] = {}
    for rid, info in ((m.meta.get("draft") or {}).get("claims") or {}).items():
        b = (info or {}).get("broker")
        if b:
            out[b] = rid
    return out


def _consensus(m: NewMap) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    meta_drivers = (m.meta.get("draft") or {}).get("drivers") or {}
    by_broker_rid = _report_ids_by_broker(m)
    nodes = list(m.drivers()) + ([n for n in m.children("A3.1") if n.driver is not None] if "A3.1" in m else [])   # 원인(A1) + 결과(A3.1)
    for n in nodes:
        d = n.driver
        if d is None or d.my_estimate.value is not None or d.consensus.value is None:
            continue
        info = meta_drivers.get(n.id) or {}
        by_broker: dict[str, float] = info.get("by_broker") or {}
        unit = n.unit or ""
        # 증권사별 값의 단위는 meta 가 말해 준다 (`by_broker_unit`). 없는 옛 맵: 노드가 조원인데 값이 1,000 을 넘으면 억원 원천이다
        # (2026-10-31 실측 「하나증권 77,599조원」 — 억원 값에 노드 단위를 붙였다. 1,000조원 넘는 한국 기업 수치는 없다)
        bunit = info.get("by_broker_unit") or ("억원" if unit == "조원" and any(abs(v) >= 1000 for v in by_broker.values()) else unit)
        parts = [f"{b} {_fmt_amount(v, bunit)}" for b, v in by_broker.items()]
        label = f"컨센 {_fmt_amount(d.consensus.value, unit)}" + (f" ({' · '.join(parts)})" if parts else "")
        out.append(_sugg(n.id, "driver.my_estimate.value", d.consensus.value, label=label, basis=d.consensus.rationale or "컨센서스",
                         kind="consensus_median", report_ids=[by_broker_rid[b] for b in by_broker if b in by_broker_rid]))
    return out


def _issue_minority(m: NewMap, issue_set: Any, claimsets: list[Any]) -> list[dict[str, Any]]:
    if issue_set is None or "A2.2" not in m or m["A2.2"].value not in (None, ""):
        return []
    claims: dict[str, tuple[Any, Any]] = {}
    for cs in claimsets or []:
        for c in cs.claims:
            claims[c.claim_id] = (c, cs)
    out: list[dict[str, Any]] = []
    for t in issue_set.issues:
        if t.display != IssueDisplay.disagreement or t.level.value != "theme":
            continue
        # 다수 방향(증권사 수 기준) 과 다른 쪽이 소수 의견 — 'mixed'(한 증권사 안에서 섞임) 도 소수로 본다
        from collections import Counter

        votes = Counter(dr for dr in t.directions.values() if dr in ("positive", "negative"))
        top = votes.most_common(2)
        majority = top[0][0] if top and (len(top) == 1 or top[0][1] > top[1][1]) else None
        minority = [b for b, dr in t.directions.items() if dr != majority] if majority else list(t.directions)
        arrow = {"positive": "↑", "negative": "↓", "mixed": "↕", "neutral": "→"}
        others = " · ".join(f"{b}{arrow.get(dr, '')}" for b, dr in t.directions.items() if b not in minority)
        for b in minority:
            cands = [claims[cid] for cid in t.claim_ids if cid in claims and claims[cid][0].broker == b]
            if not cands:
                continue
            c, cs = max(cands, key=lambda x: (len(x[0].quant_refs), len(x[0].proposition)))
            sp = cs.span(c.span_ids[0]) if c.span_ids else None
            text = f"[{c.tag} · 소수 의견] {t.driver_label}: {b} — {c.proposition}"
            out.append(_sugg("A2.2", "value", text, label=f"{t.driver_label} 핵심 이견 — {b}{arrow.get(t.directions.get(b), '')} 가 {others or '다른 증권사'} 와 다르게 본다",
                             basis=f"쟁점 {t.issue_id} ({t.coverage_basis})", kind="issue_minority", report_ids=[c.report_id], quote=(sp.text[:200] if sp else None)))
    return out


def _conditional(m: NewMap) -> list[dict[str, Any]]:
    cands = (m.meta.get("draft") or {}).get("kill_candidates") or []
    out: list[dict[str, Any]] = []
    slots = [nid for nid in ("A5.1", "A5.2", "A5.3") if nid in m and m[nid].value in (None, "")]
    for nid, c in zip(slots, cands, strict=False):
        cond = c.get("condition") or ""
        if not cond:
            continue
        text = f"[조건부] {cond}" + (f" ({c.get('broker')})" if c.get("broker") else "")
        out.append(_sugg(nid, "value", text, label=f"조건부 리스크 — {c.get('broker') or '리포트'}", basis=f"Kill 후보 {c.get('claim_id') or ''}".strip(),
                         kind="conditional_claim", report_ids=[c["report_id"]] if c.get("report_id") else [], quote=(c.get("quote") or None)))
    return out


def _price_band(m: NewMap, anchors: dict[str, float] | None) -> list[dict[str, Any]]:
    if "A7.2" not in m or m["A7.2"].value not in (None, ""):
        return []
    p0 = m.current_price
    if not p0:
        return []
    tp = (m.meta.get("draft") or {}).get("tp_distribution") or {}
    lo: float | None = None
    basis = ""
    for cand, name in (((anchors or {}).get("Bear"), "Bear 앵커"), (tp.get("min"), "목표가 최저")):
        if cand and float(cand) < float(p0):
            lo, basis = float(cand), name
            break
    if lo is None:
        lo, basis = round(p0 * 0.9), "현재가 −10% (현재가 아래 앵커 없음)"
    lo = round(lo, -2) if lo >= 10000 else round(lo)
    value = {"price_range": f"{lo:,.0f}~{p0:,.0f}", "tranches": None}
    return [_sugg("A7.2", "value", value, label=f"{lo:,.0f}~{p0:,.0f}원 ({basis} ~ 현재가) · 분할 횟수는 직접", basis=f"{basis} · A0.4 현재가 {p0:,.0f}",
                  kind="price_band")]


def build_suggestions(m: NewMap, *, issue_set: Any = None, claimsets: list[Any] | None = None,
                      anchors: dict[str, float] | None = None) -> dict[str, list[dict[str, Any]]]:
    """제안값을 만들어 `meta.draft.suggestions` 에 넣고 돌려준다 (빈 칸만; 이미 값이 있으면 만들지 않는다)."""
    rows = _consensus(m) + _issue_minority(m, issue_set, claimsets or []) + _conditional(m) + _price_band(m, anchors)
    # 값 형식이 열거형(A2.3 시간축/정보/심리/구조 등) 인 노드엔 글 제안을 붙이지 않는다 — 선택지가 아닌 값은 들어갈 수 없다
    rows = [r for r in rows if not (m[r["node_id"]].value_type.value == "enum" and r["field"] == "value" and r["value"] not in (m[r["node_id"]].enum_options or []))]
    out: dict[str, list[dict[str, Any]]] = {}
    for s in rows:
        out.setdefault(s["node_id"], []).append(s)
    m.meta.setdefault("draft", {})["suggestions"] = out
    return out


def suggestions_of(m: Any, node_id: str) -> list[dict[str, Any]]:
    return list(((m.meta.get("draft") or {}).get("suggestions") or {}).get(node_id) or [])


def apply_prefill(m: NewMap, *, today: date) -> list[str]:
    """옵션: 제안값을 값 칸에 넣는다 — `suggested` 태그 · pending 유지 · 출처 '리포트 제안(미확정)'. 돌려주는 것은 채운 노드 목록."""
    from .rules import DRAFT_TAG
    from .scenarios import PENDING_TAG

    done: list[str] = []
    for nid, rows in (m.meta.get("draft", {}).get("suggestions") or {}).items():
        if nid not in m or not rows:
            continue
        n = m[nid]
        s = rows[0]
        src = Source(name=f"{PREFILL_SOURCE} — {KIND_LABEL.get(s['kind'], s['kind'])}", retrieved_at=today)
        if s["field"] == "driver.my_estimate.value" and n.driver is not None:
            if n.driver.my_estimate.value is not None:
                continue
            n.driver.my_estimate = Measurement(value=s["value"], as_of=today, source=src, rationale=f"{PREFILL_SOURCE}: {s['label']}")
        elif s["field"] == "value":
            if n.value not in (None, ""):
                continue
            n.value = s["value"]
            n.source = src
            n.as_of = today
        else:
            continue
        n.tags = sorted(set(n.tags) | {PREFILL_TAG, PENDING_TAG, DRAFT_TAG})
        s["prefilled"] = True
        done.append(nid)
    if done:
        m.meta["draft"]["prefilled"] = done
    return done


def record_acceptance(m: Any, node_id: str, accepted: list[dict[str, Any]], *, today: date) -> Source | None:
    """편집 폼이 「채우기」 로 받아들인 제안을 기록하고, history 에 쓸 출처를 돌려준다 (generate/edits 가 부른다)."""
    rows = suggestions_of(m, node_id)
    kinds: list[str] = []
    rids: list[str] = []
    for a in accepted:
        for s in rows:
            if s["field"] == a.get("field") and (a.get("kind") in (None, s["kind"])):
                s["accepted_at"] = today.isoformat()
                kinds.append(KIND_LABEL.get(s["kind"], s["kind"]))
                rids += s.get("report_ids") or []
        m.meta.setdefault("draft", {}).setdefault("suggestion_log", []).append(
            {"node": node_id, "field": a.get("field"), "kind": a.get("kind"), "value": a.get("value"), "date": today.isoformat()})
    if not kinds:
        return None
    return Source(name=f"{ACCEPT_SOURCE} — {', '.join(dict.fromkeys(kinds))}" + (f" ({', '.join(dict.fromkeys(rids))})" if rids else ""),
                  retrieved_at=today)


def suggestion_stats(m: Any) -> dict[str, Any]:
    """제안 수락률 · 수락 뒤 수정률 (설계 v4 §6.3 평가). 수정 = 수락일 이후 그 노드에 '사용자 편집' history 가 더 생긴 것."""
    from .edits import EDIT_SOURCE

    draft = m.meta.get("draft") or {}
    rows = [s for lst in (draft.get("suggestions") or {}).values() for s in lst]
    log = draft.get("suggestion_log") or []
    total = len(rows)
    accepted = [s for s in rows if s.get("accepted_at")]
    prefilled = [s for s in rows if s.get("prefilled")]
    modified = 0
    for s in accepted:
        n = m.nodes.get(s["node_id"])
        if n is None:
            continue
        acc = date.fromisoformat(s["accepted_at"])
        # 수락은 history 에 '리포트 제안 수락' 출처로 남고, 그 뒤 같은 노드를 다시 고치면 '사용자 편집 (HTML)' 출처가 생긴다
        later = [h for h in n.history if h.source is not None and h.source.name.startswith(EDIT_SOURCE) and h.as_of >= acc]
        if later:
            modified += 1
    return {"total": total, "accepted": len(accepted), "prefilled": len(prefilled), "modified_after_accept": modified,
            "accept_rate": (len(accepted) / total) if total else None,
            "modify_rate": (modified / len(accepted)) if accepted else None, "log": log,
            "by_kind": {k: sum(1 for s in rows if s["kind"] == k) for k in KIND_LABEL if any(s["kind"] == k for s in rows)}}


def stats_text(m: Any) -> str:
    st = suggestion_stats(m)
    if not st["total"]:
        return "제안값 없음"
    kinds = " · ".join(f"{KIND_LABEL[k]} {v}" for k, v in st["by_kind"].items())
    line = f"제안 {st['total']}개 ({kinds}) · 수락 {st['accepted']}"
    if st["accept_rate"] is not None:
        line += f" ({st['accept_rate']:.0%})"
    if st["accepted"]:
        line += f" · 수락 뒤 수정 {st['modified_after_accept']} ({st['modify_rate']:.0%})"
        if st["modify_rate"] == 0 and st["accepted"] >= 3:
            line += " ⚑ 앵커링 신호 — 받아들인 값을 한 번도 고치지 않았다"
    if st["prefilled"]:
        line += f" · 미리 채움 {st['prefilled']}"
    return line


__all__ = ["KIND_LABEL", "PREFILL_TAG", "PREFILL_SOURCE", "ACCEPT_SOURCE", "build_suggestions", "suggestions_of", "apply_prefill",
           "record_acceptance", "suggestion_stats", "stats_text"]
