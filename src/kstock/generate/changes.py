"""변경안 (설계 v3 §2.7) — 기존 A 맵 + 새 리포트 → ChangeSet. 자동으로 노드를 바꾸지 않는다.

흐름: 기존 맵을 만든 리포트(+새 리포트) 로 초안을 다시 만들고, 기존 맵과 **비교만** 한다 → 변경 후보 목록(ChangeSet).
사용자가 승인한 후보만 적용하며, 적용 전 원본을 `maps/versions/<map_id>.v<N>.json` 에 보존하고 A8.2 업데이트 로그에 남긴다.
사용자가 손댄 노드(draft 태그가 없거나 사용자 기록이 있는 것) 는 후보로만 올리고(user_touched) 자동 적용 대상에서 뺀다.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..models import (
    Change,
    ChangeDecision,
    ChangeKind,
    ChangeSet,
    HistoryPoint,
    MapKind,
    NewMap,
    Node,
    Source,
)
from .rules import DRAFT_TAG


def map_hash(m: NewMap) -> str:
    """노드 내용(값·드라이버·시나리오·규칙·이벤트·태그) 의 해시 — 변경안을 만든 뒤 맵이 바뀌었는지 확인."""
    import hashlib

    payload = [m.nodes[nid].model_dump(mode="json", include={"id", "value", "driver", "scenario", "rule", "event", "tags", "links"}) for nid in sorted(m.nodes)]
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]

COMPARE_DRIVER = ("A1.1", "A1.2", "A1.3", "A1.4", "A1.5",            # 원인 드라이버 (설계 v5)
                  "A3.1.1", "A3.1.2", "A3.1.3", "A3.1.4")                  # 결과 지표 (매출·영업이익·EPS·OPM — 전에는 A1 이었다)


def _user_touched(n: Node) -> bool:
    if DRAFT_TAG not in n.tags:
        return True
    return any(h.source is not None and "사용자" in (h.source.name or "") for h in n.history)


def _issue_index(m: NewMap) -> dict[str, dict[str, Any]]:
    return {i["id"]: i for i in ((m.meta.get("draft") or {}).get("issues") or [])}


def input_snapshot_id(report_ids: list[str], today: date) -> str:
    """입력 리포트 집합(report_id 정렬) + 기준일의 sha1[:10] (설계 v4 V7) — 다른 입력으로 만든 변경안을 같은 맵에 섞지 않게."""
    import hashlib

    return hashlib.sha1(("|".join(sorted(report_ids)) + "@" + today.isoformat()).encode("utf-8")).hexdigest()[:10]


def _affects(node_id: str, kind: ChangeKind, fresh: NewMap) -> list[str]:
    """이 변경과 함께 바뀌어야 하는 노드 (v4 V7). A1 추정치 → 목표가 분포·그 테마의 A2.1.x 쟁점; TP 리비전 → A3.5.3; 새 리스크 → Kill 후보."""
    out: list[str] = []
    if kind == ChangeKind.estimate and node_id.startswith(("A1.", "A3.1.")):
        out.append("A3.5.4")
        if node_id.startswith("A1."):
            # 원인 드라이버가 바뀌면 그 결과 지표(A3.1) 도 같이 봐야 한다 — 그리고 그 테마의 쟁점
            out += [n.id for n in fresh.children("A3.1") if n.driver is not None] if "A3.1" in fresh.nodes else []
            out += list(fresh[node_id].links) if node_id in fresh.nodes else []
        label = fresh[node_id].label.split(" (")[0] if node_id in fresh.nodes else ""
        out += [n.id for n in fresh.children("A2.1") if label and label in (n.label or "")]
    elif kind == ChangeKind.tp_revision and node_id == "A3.5.4":
        out.append("A3.5.3")
    elif kind == ChangeKind.new_risk:
        out.append("A5.4.1")
    elif kind in (ChangeKind.new_claim, ChangeKind.conflict) and node_id.startswith("A2.1"):
        out.append("A0")
    return [x for x in dict.fromkeys(out) if x != node_id]


def propose_changes(base: NewMap, fresh: NewMap, *, new_report_ids: list[str], today: date,
                    all_report_ids: list[str] | None = None) -> ChangeSet:
    """base(기존, 사용자 수정 포함) 와 fresh(전체 리포트로 다시 만든 초안) 를 비교해 변경 후보를 만든다.
    all_report_ids: fresh 를 만든 전체 입력(기존+새) — input_snapshot_id 에 들어간다 (없으면 new_report_ids 로)."""
    cs = ChangeSet(base_map_id=base.map_id, base_version=int(base.meta.get("version", 1)), base_snapshot_hash=map_hash(base),
                   new_report_ids=list(new_report_ids), input_snapshot_id=input_snapshot_id(list(all_report_ids or new_report_ids), today))
    k = 0

    def add(node_id: str, kind: ChangeKind, before: Any, after: Any, claim_ids: list[str], note: str = "") -> None:
        nonlocal k
        k += 1
        touched = node_id in base.nodes and _user_touched(base[node_id])
        cs.changes.append(Change(change_id=f"ch{k}", node_id=node_id, kind=kind, before=before, after=after, claim_ids=claim_ids,
                                 user_touched=touched, note=note + (" · 사용자가 손댄 노드 — 자동 적용 안 함" if touched else ""),
                                 affects=_affects(node_id, kind, fresh)))

    # 1) 드라이버 컨센서스
    for nid in COMPARE_DRIVER:
        if nid in base.nodes and nid in fresh.nodes and base[nid].driver and fresh[nid].driver:
            b, f = base[nid].driver.consensus.value, fresh[nid].driver.consensus.value
            if b is not None and f is not None and abs(f - b) > 1e-9 and base[nid].label == fresh[nid].label:
                pct = (f - b) / abs(b) if b else 0.0
                add(nid, ChangeKind.estimate, {"consensus": b}, {"consensus": f, "rationale": fresh[nid].driver.consensus.rationale}, [],
                    f"{base[nid].label} 컨센서스 {b:,.2f} → {f:,.2f} ({pct:+.1%})")
    # 2) 쟁점 (A2.1.x)
    bi, fi = _issue_index(base), _issue_index(fresh)
    for iid, info in fi.items():
        if iid not in bi:
            node = next((n for n in fresh.children("A2.1") if n.source and iid in (n.source.name or "")), None)
            add(node.id if node else "A2.1", ChangeKind.new_claim, None,
                {"label": node.label if node else info.get("label"), "value": node.value if node else "", "source": node.source.model_dump(mode="json") if node and node.source else None},
                [], f"새 쟁점: {info.get('label')} ({info.get('display')})")
        elif info.get("display") != bi[iid].get("display") or info.get("direction") != bi[iid].get("direction"):
            node = next((n for n in fresh.children("A2.1") if n.source and iid in (n.source.name or "")), None)
            add(node.id if node else "A2.1", ChangeKind.conflict, {"display": bi[iid].get("display"), "direction": bi[iid].get("direction")},
                {"display": info.get("display"), "direction": info.get("direction"), "label": node.label if node else None, "value": node.value if node else "",
                 "source": node.source.model_dump(mode="json") if node and node.source else None},
                [], f"쟁점 상태 변화: {info.get('label')} {bi[iid].get('display')}/{bi[iid].get('direction')} → {info.get('display')}/{info.get('direction')}")
    # 3) 목표가 분포 · 리비전
    bd, fd = (base.meta.get("draft") or {}).get("tp_distribution"), (fresh.meta.get("draft") or {}).get("tp_distribution")
    if bd and fd and (bd.get("median") != fd.get("median") or bd.get("n") != fd.get("n")):
        add("A3.5.4", ChangeKind.tp_revision, bd, {**fd, "value": fresh.value("A3.5.4")}, [],
            f"목표가 분포 {bd['n']}건 중앙 {bd['median']:,.0f} → {fd['n']}건 중앙 {fd['median']:,.0f}")
    if base.value("A3.5.3") != fresh.value("A3.5.3") and fresh.value("A3.5.3") is not None:
        add("A3.5.3", ChangeKind.tp_revision, base.value("A3.5.3"), {"value": fresh.value("A3.5.3"), "question": fresh["A3.5.3"].question}, [],
            f"리비전 방향 {base.value('A3.5.3')} → {fresh.value('A3.5.3')}")
    # 4) 리스크 · 촉매 (새로 나타난 것)
    base_risks = {str((base[n].value or {}).get("text", ""))[:40] for n in ("A5.1", "A5.2", "A5.3") if n in base.nodes and isinstance(base[n].value, dict)}
    for nid in ("A5.1", "A5.2", "A5.3"):
        if nid in fresh.nodes and isinstance(fresh[nid].value, dict):
            t = str(fresh[nid].value.get("text", ""))
            if t and t[:40] not in base_risks:
                add(nid, ChangeKind.new_risk, base[nid].value if nid in base.nodes else None,
                    {"value": fresh[nid].value, "source": fresh[nid].source.model_dump(mode="json") if fresh[nid].source else None, "question": fresh[nid].question},
                    [], f"새 리스크: {t[:60]}")
    base_events = {(n.event.date, n.event.fact[:25]) for n in base.nodes.values() if n.parent == "A6.1" and n.event}
    for n in fresh.children("A6.1"):
        if n.event and (n.event.date, n.event.fact[:25]) not in base_events:
            add(n.id, ChangeKind.new_catalyst, None, {"node": n.model_dump(mode="json")}, [], f"새 촉매: {n.event.when_label()} [{n.event.certainty.value}] {n.event.fact[:50]}")
    # 5) A9.4 리포트별 주장 (새 리포트)
    for n in fresh.children("A9.4"):
        if n.id not in base.nodes:
            add(n.id, ChangeKind.new_claim, None, {"node": n.model_dump(mode="json")}, [], f"새 리포트 주장 목록: {n.label}")
    kinds = {}
    for c in cs.changes:
        kinds[c.kind.value] = kinds.get(c.kind.value, 0) + 1
    cs.summary = (f"새 리포트 {len(new_report_ids)}건 → 변경 후보 {len(cs.changes)}개: " + ", ".join(f"{k} {v}" for k, v in kinds.items())) if cs.changes else "변경 후보 없음"
    return cs


class ChangeError(ValueError):
    """변경안을 적용할 수 없다 (맵이 바뀜 · 다른 입력 · 적용 결과가 검증 오류)."""


def _atomic_write(path: Path, text: str) -> None:
    """임시 파일에 쓰고 os.replace — 중간에 끊겨도 반쪽 파일이 남지 않는다 (v4 V7)."""
    import os

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def partial_approval_warnings(cs: ChangeSet) -> list[str]:
    """승인된 변경이 함께 바뀌어야 하는 노드(affects) 의 변경은 거절/보류됐을 때 — 분석 시점이 섞인다는 경고 (v4 V7)."""
    by_node: dict[str, Change] = {}
    for c in cs.changes:
        by_node.setdefault(c.node_id, c)
    out: list[str] = []
    for c in cs.approved():
        for nid in c.affects:
            other = by_node.get(nid)
            if other is not None and other.decision != ChangeDecision.approved:
                out.append(f"{c.change_id} {c.node_id} 는 승인했지만 함께 바뀌는 {nid}({other.change_id}) 는 {other.decision.value} — 분석 시점이 섞인다")
    return out


def apply_changes(m: NewMap, cs: ChangeSet, *, today: date, versions_dir: Path | None = None,
                  expected_input_snapshot_id: str | None = None) -> list[str]:
    """승인(approved) 된 변경만 적용. 적용 전 원본을 versions_dir 에 보존하고 meta.version 을 올리고 A8.2 에 남긴다.

    v4 V7: ① 다른 입력(expected_input_snapshot_id) 으로 만든 변경안이면 거절 ② 먼저 **복사본**에 적용해 검증(진짜 오류) 을 통과해야
    원본에 반영 ③ 버전 파일은 임시 파일 → os.replace 로 원자적 ④ 부분 승인(affects 가 거절/보류) 은 A8.2 로그에 ⚑ 로 남긴다."""
    approved = cs.approved()
    if not approved:
        return []
    if map_hash(m) != cs.base_snapshot_hash:
        raise ChangeError("맵이 변경안을 만든 뒤 바뀌었다 — 변경안을 다시 만들 것")
    if expected_input_snapshot_id and cs.input_snapshot_id and expected_input_snapshot_id != cs.input_snapshot_id:
        raise ChangeError(f"이 변경안은 다른 입력 리포트 집합({cs.input_snapshot_id}) 으로 만든 것이다 (기대 {expected_input_snapshot_id})")
    # 복사본에 먼저 적용 → 검증
    trial = m.model_copy(deep=True)
    _apply_approved(trial, cs, approved, today=today, record=False)
    try:
        from ..engine.validate import validate_new_map

        issues = validate_new_map(trial, today=today)
        pending_ids = {n.id for n in trial.nodes.values() if "pending" in n.tags}
        real = [i for i in issues if i.level == "error" and not (i.node_id in pending_ids or (i.node_id or "").startswith(("A1.", "A3", "A4", "A5.4", "A7.")))]
    except ImportError:  # pragma: no cover
        real = []
    if real:
        raise ChangeError("적용하면 검증 오류가 생겨 되돌렸다: " + "; ".join(str(i) for i in real[:3]))
    ver = int(m.meta.get("version", 1))
    if versions_dir is not None:
        versions_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(versions_dir / f"{m.map_id.replace(':', '_')}.v{ver}.json", json.dumps(m.model_dump(mode="json"), ensure_ascii=False, indent=2))
    done = _apply_approved(m, cs, approved, today=today, record=True)
    warns = partial_approval_warnings(cs)
    m.meta["version"] = ver + 1
    m.meta.setdefault("draft", {})["approval_status"] = "승인"
    m.meta["draft"]["input_snapshot_id"] = cs.input_snapshot_id
    log = m["A8.2"].value if isinstance(m["A8.2"].value, list) else []
    log.append({"date": today.isoformat(), "text": f"변경안 승인 {len(done)}건 (새 리포트 {', '.join(cs.new_report_ids)}): " + "; ".join(done)[:300]
                + ((" ⚑ 부분 승인: " + " / ".join(warns)[:300]) if warns else ""), "version": ver + 1})
    m["A8.2"].value = log
    m["A8.2"].as_of = today
    cs.decided_at = datetime.now()
    cs.result_version = ver + 1
    m.touch(today)
    return done


def _apply_approved(m: NewMap, cs: ChangeSet, approved: list[Change], *, today: date, record: bool) -> list[str]:
    done: list[str] = []
    for c in approved:
        after = c.after or {}
        if c.kind == ChangeKind.estimate and c.node_id in m.nodes and m[c.node_id].driver:
            d = m[c.node_id].driver
            d.consensus.value = after.get("consensus")
            d.consensus.as_of = today
            d.consensus.rationale = after.get("rationale")
            m[c.node_id].history.append(HistoryPoint(as_of=today, value=after.get("consensus"), source=Source(name=f"변경안 {c.change_id} 승인", retrieved_at=today)))
        elif "node" in after:
            node = Node.model_validate(after["node"])
            m.add(node)
        elif c.kind in (ChangeKind.new_claim, ChangeKind.conflict) and c.node_id.startswith("A2.1"):
            nid = c.node_id if c.node_id != "A2.1" else f"A2.1.{len(m.children('A2.1')) + 1}"
            src = Source.model_validate(after["source"]) if after.get("source") else None
            if nid in m.nodes:
                m[nid].value, m[nid].label, m[nid].source, m[nid].as_of = after.get("value"), after.get("label") or m[nid].label, src, today
            else:
                m.add(Node(id=nid, map=MapKind.NEW, parent="A2.1", label=after.get("label") or "쟁점", node_type="estimate", value_type="text",
                           value=after.get("value"), as_of=today, source=src, tags=[DRAFT_TAG]))
        elif c.kind == ChangeKind.new_risk and c.node_id in m.nodes:
            n = m[c.node_id]
            n.value, n.as_of = after.get("value"), today
            n.source = Source.model_validate(after["source"]) if after.get("source") else n.source
            n.question = after.get("question") or n.question
        elif c.kind == ChangeKind.tp_revision and c.node_id in m.nodes:
            n = m[c.node_id]
            if c.node_id == "A3.5.4":
                n.value, n.as_of = after.get("value"), today
                m.meta.setdefault("draft", {})["tp_distribution"] = {k: v for k, v in after.items() if k != "value"}
            else:
                n.value, n.as_of = after.get("value"), today
                n.question = after.get("question") or n.question
        if record:
            c.decision = ChangeDecision.approved
        done.append(f"{c.change_id} {c.node_id} {c.kind.value}")
    return done


def save_changeset(cs: ChangeSet, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{cs.base_map_id.replace(':', '_')}_{cs.created_at.strftime('%Y%m%d%H%M%S')}.json"
    _atomic_write(p, cs.model_dump_json(indent=2))
    return p


def load_changeset(path: Path) -> ChangeSet:
    return ChangeSet.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def changeset_table(cs: ChangeSet) -> str:
    lines = [cs.summary + (f" · 입력 {cs.input_snapshot_id}" if cs.input_snapshot_id else "")]
    for c in cs.changes:
        mark = {"pending": "□", "approved": "✓", "rejected": "✗"}[c.decision.value]
        lines.append(f"  {mark} {c.change_id:>4} {c.node_id:<8} {c.kind.value:<10} {c.note[:90]}" + ("  [사용자 수정]" if c.user_touched else "")
                     + (f"  ↔ 함께: {','.join(c.affects)}" if c.affects else ""))
    for w in partial_approval_warnings(cs):
        lines.append("  ⚑ " + w)
    return "\n".join(lines)


__all__ = ["map_hash", "input_snapshot_id", "propose_changes", "apply_changes", "partial_approval_warnings", "ChangeError", "save_changeset", "load_changeset", "changeset_table"]
