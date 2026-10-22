"""P2.5 최소 실험 — 삼성전자 3건: 사람이 쓴 Claim 답안(samsung_claims_answers.json) 을 실제 파이프라인(인용 위치 → 3단 검증) 에 통과시켜
data/claims/ 에 저장하고, 정답표(tests/golden/extract) 의 required_claims/required_risks 를 채운다.

    python examples/claims_experiment/run_experiment.py [--claims-dir data/claims] [--golden tests/golden/extract]
실제 Claude 로 뽑은 결과와 비교: mindmap claims --reports data/reports/삼성전자/*.pdf --show --out data/claims_llm
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from kstock.models import ClaimRole, ClaimSet, report_id_for  # noqa: E402
from kstock.reports import extract_facts, load_report  # noqa: E402
from kstock.reports.claims import extract_claims, save_claimset  # noqa: E402
from kstock.reports.validate_claims import run_all  # noqa: E402

ANSWERS = Path(__file__).with_name("samsung_claims_answers.json")
PDFS = sorted((ROOT / "data/reports/삼성전자").glob("*.pdf"))


class FixedFiller:
    """리포트별 답안을 돌려주는 가짜 LLM — '## 그룹: claims' 면 Claim 답, '## 그룹: support' 면 판정."""

    model = "human-2026-10-21"

    def __init__(self, answers: dict):
        self.answers = answers
        self.current: str | None = None

    def complete(self, system: str, user: str) -> str:
        if "## 그룹: claims" in user:
            return json.dumps({"claims": self.answers[self.current]["claims"]}, ensure_ascii=False)
        if "## 그룹: support" in user:
            cfg = self.answers[self.current].get("verdicts", {})
            default, overrides = cfg.get("default", "supported"), cfg.get("overrides", {})
            verdicts = []
            for block in user.split("- id: ")[1:]:
                cid = block.split("\n", 1)[0].strip()
                prop = block.split("명제: ", 1)[1].split("\n", 1)[0] if "명제: " in block else ""
                v = next((val for key, val in overrides.items() if prop.startswith(key)), default)
                verdicts.append({"id": cid, "verdict": v, "why": "사람 판정"})
            return json.dumps({"verdicts": verdicts}, ensure_ascii=False)
        return "{}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--claims-dir", default=str(ROOT / "data/claims"))
    ap.add_argument("--golden", default=str(ROOT / "tests/golden/extract"))
    a = ap.parse_args()
    answers = json.loads(ANSWERS.read_text(encoding="utf-8"))
    filler = FixedFiller(answers)
    results: list[ClaimSet] = []
    for pdf in PDFS:
        rid = report_id_for(str(pdf))
        if rid not in answers:
            continue
        doc = load_report(pdf)
        f = extract_facts(doc, today=date(2026, 10, 21))
        filler.current = rid
        cs = extract_claims(doc, f, filler, sector="KR-SEMI", name="삼성전자", ticker="005930", max_chunks=1)
        counts = run_all(cs, f, filler, semantic=True)
        p = save_claimset(cs, Path(a.claims_dir))
        results.append(cs)
        print(f"{rid} {f.broker} · Claim {len(cs.claims)} verified {counts['verified']} / 검토 {counts['needs_review']} / 반려 {counts['rejected']} → {p}")
        for c in cs.claims:
            mark = {"verified": "✓", "needs_review": "?", "rejected": "✗"}[c.status.value]
            print(f"   {mark} 인용{c.validation.quote} 숫자{c.validation.numbers} 의미{c.validation.support.value[:4]} [{','.join(r.value for r in c.roles)}|{c.direction.value[:3]}|{c.driver_id}] {c.proposition[:90]}")
        # 정답표 채우기 (필수 주장 5 + 리스크 3, 인용 포함)
        gp = Path(a.golden) / f"{rid}.json"
        if gp.is_file():
            g = json.loads(gp.read_text(encoding="utf-8"))
            usable = cs.usable()
            def row(c, cs=cs):  # noqa: ANN001
                sp = cs.span(c.span_ids[0]) if c.span_ids else None
                return {"id": c.claim_id, "proposition": c.proposition, "roles": [r.value for r in c.roles], "direction": c.direction.value,
                        "driver_id": c.driver_id, "quote": (sp.text if sp else "")[:200]}
            thesis = [c for c in usable if c.has_role(ClaimRole.thesis) or c.has_role(ClaimRole.catalyst)][:5]
            risks = [c for c in usable if c.has_role(ClaimRole.risk)][:3]
            g["required_claims"] = [row(c) for c in thesis]
            g["required_risks"] = [row(c) for c in risks]
            g["candidate_review"] = [{"claim_id": c.claim_id, "supported": c.status.value == "verified", "note": "사람 작성 답안"} for c in cs.claims]
            gp.write_text(json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"   정답표 갱신: {gp.name} (필수 주장 {len(thesis)} · 리스크 {len(risks)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
