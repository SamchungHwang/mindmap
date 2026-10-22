"""P2.5 최소 실험 요약 HTML — 삼성전자 3건: 리포트별 Claim(인용·페이지) · 증권사 간 쟁점 표 · TP/OP/EPS 비교표 · A 맵 초안 요약.
    python examples/claims_experiment/make_summary.py   → examples/claims_experiment/samsung_experiment.html
"""

from __future__ import annotations

import glob
import html
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from kstock.generate import draft_from_reports  # noqa: E402
from kstock.generate.issues import build_issues, issues_table  # noqa: E402
from kstock.reports.claims import load_claimsets  # noqa: E402

OUT = Path(__file__).with_name("samsung_experiment.html")


def esc(x) -> str:  # noqa: ANN001
    return html.escape(str(x))


def main() -> int:
    css = load_claimsets(ROOT / "data/claims")
    css = [c for c in css if c.ticker == "005930"]
    paths = sorted(glob.glob(str(ROOT / "data/reports/삼성전자/*.pdf")))
    r = draft_from_reports(paths, ticker="005930", name="삼성전자", today=date(2026, 10, 21), claims="cached", claims_dir=ROOT / "data/claims")
    d = r.digest
    iss = build_issues(css, ticker="005930")
    parts = ["<h1>P2.5 최소 실험 — 삼성전자 리포트 3건 (2026-10-21)</h1>",
             "<p>질문 하나: <b>키워드 분류보다 증권사들의 투자 논리를 더 정확하고 유용하게 비교하는가?</b> 아래를 보고 판단한다. "
             "Claim 은 사람이 LLM 역할로 작성한 답안(examples/claims_experiment/samsung_claims_answers.json) 을 실제 파이프라인(인용 위치 → 3단 검증) 에 통과시킨 것이다.</p>"]
    # 1. 비교표
    parts.append("<h2>1. 증권사별 비교표</h2><table><tr><th>증권사</th><th>날짜</th><th>의견</th><th>목표주가</th><th>현재가</th><th>2026E OP(조원)</th><th>2027E OP(조원)</th><th>2027E EPS</th><th>제목</th></tr>")
    for f in sorted(d.reports, key=lambda f: f.date or date.min, reverse=True):
        op26 = f.estimates.get("op", {}).get("2026E")
        op27 = f.estimates.get("op", {}).get("2027E")
        eps27 = f.estimates.get("eps", {}).get("2027E")
        parts.append(f"<tr><td>{esc(f.broker)}</td><td>{f.date}</td><td>{esc(f.opinion)}</td><td>{f.target_price:,.0f}</td><td>{f.current_price:,.0f}</td>"
                     f"<td>{op26 / 1000:,.1f}</td><td>{op27 / 1000:,.1f}</td><td>{eps27:,.0f}</td><td>{esc(f.title)}</td></tr>")
    sp = d.spread("op", "2027E")
    parts.append(f"</table><p>2027E 영업이익 분산: {sp['min'] / 10000:,.1f}~{sp['max'] / 10000:,.1f}조 (상대 {sp['rel']:.0%}) · 목표주가 {d.tp_min:,.0f}~{d.tp_max:,.0f}</p>")
    # 2. 쟁점 표
    parts.append("<h2>2. 증권사 간 쟁점 (테마 → 메커니즘)</h2><pre>" + esc(issues_table(iss)) + "</pre>")
    # 3. 리포트별 Claim
    parts.append("<h2>3. 리포트별 Claim (verified 만, 인용·페이지)</h2>")
    for cs in css:
        parts.append(f"<h3>{esc(cs.broker)} {cs.report_date} — verified {len(cs.usable())}/{len(cs.claims)}</h3><ol>")
        for c in cs.claims:
            sp_ = cs.span(c.span_ids[0]) if c.span_ids else None
            mark = {"verified": "✓", "needs_review": "?", "rejected": "✗"}[c.status.value]
            parts.append(f"<li><b>{mark} [{esc(','.join(x.value for x in c.roles))} | {esc(c.direction.value)} | {esc(c.driver_id or c.driver_label or '-')}]</b> {esc(c.proposition)}"
                         + (f"<br><small>p.{sp_.page} “{esc(sp_.text[:160])}”</small>" if sp_ else "")
                         + (f"<br><small>조건: {esc(c.condition)}</small>" if c.condition else "")
                         + (f"<br><small>검증: 인용 {c.validation.quote} · 숫자 {c.validation.numbers} · 의미 {c.validation.support.value}</small>") + "</li>")
        parts.append("</ol>")
    # 4. 초안 요약
    parts.append("<h2>4. A 맵 초안 요약 (규칙 + Claim/Issue 매핑)</h2><pre>" + esc(r.summary_text()) + "</pre>")
    parts.append("<h2>5. A2.1 자식 (쟁점 노드)</h2>")
    for n in r.map.children("A2.1"):
        parts.append(f"<h4>{esc(n.id)} {esc(n.label)}</h4><pre>{esc(n.value)}</pre><small>출처: {esc(n.source.name if n.source else '')}</small>")
    parts.append("<p>맵 파일: A_005930_v3_draft.json / .html (같은 폴더)</p>")
    style = "<style>body{font-family:'Malgun Gothic',sans-serif;max-width:1100px;margin:24px auto;line-height:1.5}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px;font-size:13px}pre{background:#f6f6f6;padding:10px;white-space:pre-wrap;font-size:12px}small{color:#555}</style>"
    OUT.write_text("<!doctype html><meta charset='utf-8'><title>P2.5 실험 — 삼성전자</title>" + style + "\n".join(parts), encoding="utf-8")
    print(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
