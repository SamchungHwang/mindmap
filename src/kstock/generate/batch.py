"""일괄 실행 (설계 v7 T10 / v6 §10 D1) — 에이전트(OpenClaw heartbeat) 가 콘솔 없이 부른다.

리포트 폴더의 종목별 하위 폴더마다: 맵이 없으면 `draft`, 있으면 전체 리포트로 다시 만든 초안과 비교한 변경안(적용은 하지 않는다 — 사람 승인).
결과는 요약 한 장(Markdown/JSON) 과 종료 코드. 판단·확률·내 추정은 건드리지 않는다.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

REPORT_EXTS = (".pdf", ".txt", ".md", ".html", ".htm", ".docx")


def _report_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in REPORT_EXTS)


def _find_map(maps_dir: Path, ticker: str | None, folder_name: str) -> Path | None:
    from ..models import load_map_file

    if not maps_dir.is_dir():
        return None
    for p in sorted(maps_dir.glob("A_*.json")):
        try:
            m = load_map_file(p)
        except Exception:  # noqa: BLE001
            continue
        if ticker and getattr(m, "ticker", None) == ticker:
            return p
        if folder_name and (folder_name in p.stem):
            return p
    return None


def run_batch(reports_dir: Path, *, maps_dir: Path, db: str | None = None, today: date | None = None, claims: str = "auto", llm: bool = False,
              llm_kind: str = "auto", apply_changes: bool = False, summary_path: Path | None = None, json_path: Path | None = None) -> dict[str, Any]:
    from ..generate import DraftError, draft_from_reports, make_filler
    from ..generate.changes import propose_changes, save_changeset
    from ..models import load_map_file, report_id_for
    from ..render.native import write_html

    today = today or date.today()
    maps_dir.mkdir(parents=True, exist_ok=True)
    filler = None
    if llm:
        try:
            filler = make_filler(llm_kind)
        except RuntimeError as e:
            return {"ok": False, "text": f"LLM 을 쓸 수 없다 — {e}", "items": []}
    items: list[dict[str, Any]] = []
    for folder in sorted(p for p in reports_dir.iterdir() if p.is_dir()):
        files = _report_files(folder)
        if not files:
            continue
        item: dict[str, Any] = {"folder": folder.name, "reports": len(files), "kind": None, "ok": False}
        existing = _find_map(maps_dir, None, folder.name)
        base = None
        if existing is not None:
            try:
                base = load_map_file(existing)
                item["map"] = str(existing)
            except Exception as e:  # noqa: BLE001
                item["note"] = f"기존 맵 읽기 실패: {e}"
        try:
            r = draft_from_reports(files, ticker=(getattr(base, "ticker", None) if base is not None else None), today=today, filler=filler, claims=claims,
                                   claims_dir=maps_dir.parent / "claims" if (maps_dir.parent / "claims").is_dir() else None, n_drivers=5, fetch_price=False)
        except DraftError as e:
            item["note"] = f"초안 실패: {e}"
            items.append(item)
            continue
        m = r.map
        draft = m.meta.get("draft") or {}
        item.update({"ticker": m.ticker, "name": m.name, "claims": sum(len(cs.claims) for cs in r.claimsets), "verified": sum(len(cs.usable()) for cs in r.claimsets),
                     "errors": len(r.errors), "pending": len(r.pending), "gate": (draft.get("claim_gate") or {}).get("status"),
                     "lights": draft.get("lights"), "flags": m.meta.get("flags") or {}, "valuation": m["A3.3"].value if "A3.3" in m else None})
        if base is None:
            out = maps_dir / f"A_{m.ticker or folder.name}_draft.json"
            m.save(out)
            write_html(m, out.with_suffix(".html"))
            item.update({"kind": "draft", "map": str(out), "ok": r.complete})
        else:
            cs = propose_changes(base, m, new_report_ids=[report_id_for(str(p)) for p in files], today=today)
            cdir = maps_dir.parent / "changes"
            p = save_changeset(cs, cdir)
            item.update({"kind": "changes", "changeset": str(p), "n_changes": len(cs.changes), "summary": cs.summary, "ok": True})
        items.append(item)
    lines = [f"# 일괄 실행 {today} — {reports_dir}", ""]
    for it in items:
        if it.get("kind") == "draft":
            lines.append(f"- **{it.get('name') or it['folder']}** ({it.get('ticker') or '-'}) 초안 {'완료' if it['ok'] else '미완료'} — 리포트 {it['reports']} · Claim {it.get('claims', 0)}(verified {it.get('verified', 0)})"
                         f" · 오류 {it.get('errors')} · 미확정 {it.get('pending')}" + (f" · 게이트 {it['gate']}" if it.get("gate") else "") + f" → {it.get('map')}")
            if it.get("valuation"):
                lines.append(f"  - {it['valuation'][:200]}")
        elif it.get("kind") == "changes":
            lines.append(f"- **{it.get('name') or it['folder']}** 변경안 {it.get('n_changes')}건 — {it.get('summary')} → {it.get('changeset')} (승인은 사람: 콘솔 「N」)")
        else:
            lines.append(f"- {it['folder']}: {it.get('note')}")
    text = "\n".join(lines)
    if summary_path:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(text + "\n", encoding="utf-8")
    if json_path:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps({"date": today.isoformat(), "items": items}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"ok": all(it.get("ok") for it in items) and bool(items), "text": text, "items": items}


__all__ = ["run_batch"]
