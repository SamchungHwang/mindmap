"""지식 내보내기 (설계 v7 T10 / v6 §10 D3) — 맵(+Claim·쟁점) → 종목별 Markdown 한 장. OpenClaw workspace 의 memory_search 가 읽는 2층 지식.

판단 필드(A0·A2.2·A7)는 그대로 적되 "사람 판단" 표시를 붙인다. 비밀(.env·토큰) 과 리포트 원문 전문은 넣지 않는다 — 요약·Claim 만.
"""

from __future__ import annotations

from pathlib import Path


def map_to_markdown(m, *, claimsets=None) -> str:  # noqa: ANN001
    from ..engine.evidence import LIGHT_ICON, ROLE_LABEL

    d = m.meta.get("draft") or {}
    lines = [f"# {m.name or ''} ({m.ticker or ''}) — 투자 의사결정 맵 {m.map_id}", "",
             f"- 작성 {m.created_at} · 갱신 {m.updated_at if hasattr(m, 'updated_at') else ''} · 판단(사람) **{m.decision.value if m.decision else '작성중'}** · 현재가 {m.current_price or '-'}", ""]
    lines += ["## 논제 (사람 판단)", "", (m.thesis or "(비어 있음)"), ""]
    lines += ["## 원인 드라이버 (A1) · 증거"]
    for n in m.drivers():
        dr = n.driver
        if dr is None or dr.role == "result":
            continue
        obs = f"{dr.observations[-1].value} ({dr.observations[-1].as_of})" if dr.observations else "관측값 없음"
        lines.append(f"- {LIGHT_ICON.get(dr.light)} **{n.label.split(' (')[0]}** [{ROLE_LABEL.get(dr.thesis_role)}] 컨센 {dr.consensus.value if dr.consensus.value is not None else '-'} · 관측 {obs}"
                     + (f" · 필요 경로 {dr.required.ruler.label()} {', '.join(p.at + ' ' + str(p.value) for p in dr.required.points[:4])}" if dr.required else "")
                     + (f" · 경계 Bear {dr.bound_bear}" if dr.bound_bear is not None else "") + (f" — {dr.light_reason}" if dr.light_reason else ""))
    lines.append("")
    if "A2.4" in m and isinstance(m["A2.4"].value, str):
        lines += ["## 가격이 성립하기 위한 조건 (A2.4, 코드)", "", "```", m["A2.4"].value[:3000], "```", ""]
    if "A3.3" in m and isinstance(m["A3.3"].value, str):
        lines += ["## 평가 모델 (A3.3)", "", m["A3.3"].value, ""]
    scs = m.scenario_nodes()
    if scs:
        lines += ["## 시나리오 (A4.2~4)"]
        for name, n in scs.items():
            sc = n.scenario
            if sc is None:
                continue
            lines.append(f"- {name}: 목표가 {sc.target_price or '미계산'} · 확률 {sc.probability if sc.probability is not None else '미입력 (사람)'} · 가정 {', '.join(f'{a.node_id} {a.delta_pct:+.0f}%' for a in sc.driver_assumptions if a.delta_pct is not None) or '-'}")
        lines.append("")
    kills = m.kill_rules()
    if kills:
        lines += ["## Kill (A5.4, 사실 기준)"] + [f"- {k.condition}" for k in kills] + [""]
    if "A5.6" in m:
        rs = [n for n in m.children("A5.6") if n.rule is not None]
        if rs:
            lines += ["## 판단 수정 규칙 (A5.6)"] + [f"- {n.label}: {n.rule.condition} → 제안 {n.rule.proposal.get('kind')} [{n.rule.status}]" for n in rs] + [""]
    if "A5.5" in m and isinstance(m["A5.5"].value, str):
        lines += ["## 증거 계획 (A5.5)", "", "```", m["A5.5"].value[:2500], "```", ""]
    sched = d.get("evidence_schedule") or []
    if sched:
        lines += ["## 증거 일정 (A8.1)"] + [f"- {x['date']} {x['what']} — {x.get('where') or ''}" for x in sched[:12]] + [""]
    iss = m.children("A2.1")
    if iss:
        lines += ["## 쟁점 (A2.1.x)"] + [f"- {n.label}: {str(n.value)[:160] if n.value else ''}" for n in iss[:12]] + [""]
    if claimsets:
        lines += ["## Claim (리포트 주장, verified 만)"]
        for cs in claimsets:
            for c in cs.usable()[:15]:
                lines.append(f"- [{cs.broker or cs.report_id}] {c.tag}: {c.proposition}")
        lines.append("")
    fl = m.meta.get("flags") or {}
    lines += ["## 상태 플래그", "", f"- 신규 BUY 보류 {fl.get('hold_new_buy', False)} · 재검토 {fl.get('review', False)} · 증거 없는 판단 {fl.get('no_evidence', False)}", "",
              "> 이 문서는 kstock-mindmap 이 만든 지식 요약이다. 판단·확률·내 추정은 사람 몫이고, 밸류 숫자는 코드가 계산했다. 투자 조언이 아니다."]
    return "\n".join(lines)


def export_knowledge(maps: list[Path], *, out_dir: Path, claims_dir: Path | None = None) -> list[Path]:
    from ..models import load_map_file
    from ..reports.claims import load_claimsets

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for p in maps:
        try:
            m = load_map_file(p)
        except Exception:  # noqa: BLE001
            continue
        sets = None
        if claims_dir is not None and claims_dir.is_dir():
            ids = [r.get("file", "").rsplit(".", 1)[0] for r in ((m.meta.get("draft") or {}).get("reports") or [])]
            sets = load_claimsets(claims_dir)
            sets = [cs for cs in sets if cs.ticker == m.ticker or cs.report_id in ids] or None
        txt = map_to_markdown(m, claimsets=sets)
        dest = out_dir / f"{m.ticker or p.stem}.md"
        dest.write_text(txt + "\n", encoding="utf-8")
        written.append(dest)
    return written


__all__ = ["map_to_markdown", "export_knowledge"]
