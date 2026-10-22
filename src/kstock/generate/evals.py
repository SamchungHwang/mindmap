"""평가 루프 (설계 v3 P0/P6) — 정답표 대비 추출·Claim·Issue 지표.

정답표 위치: `tests/golden/extract/<report_id>.json` (수치·제목·필수 주장), `tests/golden/issues/<ticker>.json` (쟁점).
PDF 는 저장소 밖(`data/reports/…`) 이고 정답표에는 경로만 적는다. 정답표는 AI 초안(`verified: false`) 으로 시작해 사람이 확인하면 true 로.

    mindmap golden-draft --reports data/reports/삼성전자/*.pdf      # 추출기 결과로 정답표 초안 생성 (검수 대기)
    mindmap eval-extract                                           # 정답표 vs 추출기 — 필드별 정확도 표
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ..models import ClaimSet, report_id_for

GOLDEN_DIR = Path("tests/golden")
FACT_FIELDS = ("broker", "date", "ticker", "opinion", "target_price", "prev_target_price", "current_price", "high_52w", "low_52w", "title")
EST_METRICS = ("revenue", "op", "np", "eps")
EST_TOL = 0.005          # 추정치 표 값 허용 오차 (상대)
PRICE_TOL = 0.0          # 가격류는 정확히


# --------------------------------------------------------------------------- 정답표 초안
def golden_draft_from_facts(f: Any, *, root: Path | None = None) -> dict[str, Any]:
    """ReportFacts → 정답표 초안 dict (verified: false). 경로는 root 기준 상대경로."""
    path = Path(f.path)
    try:
        rel = str(path.resolve().relative_to((root or Path.cwd()).resolve()))
    except ValueError:
        rel = str(path)
    est = {m: {c: v for c, v in sorted(f.estimates.get(m, {}).items())} for m in EST_METRICS if f.estimates.get(m)}
    return {
        "report_id": report_id_for(str(path)),
        "file": rel.replace("\\", "/"),
        "verified": False,
        "source": "extractor draft — 사람이 확인하면 verified: true",
        "facts": {
            "broker": f.broker, "date": f.date.isoformat() if f.date else None, "ticker": f.ticker, "name": f.name,
            "opinion": f.opinion, "target_price": f.target_price, "prev_target_price": f.prev_target_price,
            "current_price": f.current_price, "price_date": f.price_date.isoformat() if f.price_date else None,
            "high_52w": f.high_52w, "low_52w": f.low_52w, "title": f.title,
        },
        "estimates": {"unit": f.unit, **est},
        "required_claims": [],   # [{"id": "rc1", "proposition": "...", "roles": ["thesis"], "direction": "positive", "quote": "원문 일부"}]
        "required_risks": [],    # 같은 모양, roles ["risk"]
        "candidate_review": [],  # [{"claim_id": "...", "supported": true, "note": ""}] — AI 가 뽑은 주장의 지지 여부 (정밀도)
    }


def write_golden_drafts(paths: list[str | Path], out_dir: Path, *, today: date | None = None, root: Path | None = None,
                        overwrite: bool = False) -> list[Path]:
    from ..reports import extract_facts, load_report

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for p in paths:
        doc = load_report(p)
        f = extract_facts(doc, today=today)
        g = golden_draft_from_facts(f, root=root)
        dst = out_dir / f"{g['report_id']}.json"
        if dst.exists() and not overwrite:
            old = json.loads(dst.read_text(encoding="utf-8"))
            if old.get("verified"):
                continue   # 사람이 확인한 정답표는 덮지 않는다
        dst.write_text(json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(dst)
    return written


# --------------------------------------------------------------------------- 추출 평가
@dataclass
class FieldScore:
    field: str
    n: int = 0
    ok: int = 0
    misses: list[str] = field(default_factory=list)   # "hk_96588: got X / want Y"

    @property
    def acc(self) -> float:
        return self.ok / self.n if self.n else 1.0


@dataclass
class ExtractEval:
    reports: int = 0
    verified_only: bool = False
    fields: dict[str, FieldScore] = field(default_factory=dict)
    missing_files: list[str] = field(default_factory=list)

    def score(self, name: str) -> FieldScore:
        return self.fields.setdefault(name, FieldScore(name))

    @property
    def overall(self) -> float:
        n = sum(s.n for s in self.fields.values())
        return sum(s.ok for s in self.fields.values()) / n if n else 1.0

    def table(self) -> str:
        rows = [f"정답표 {self.reports}건" + (" (verified 만)" if self.verified_only else " (초안 포함)") + f" · 전체 정확도 {self.overall:.1%}"]
        for s in self.fields.values():
            rows.append(f"  {s.field:<16} {s.ok:>3}/{s.n:<3} {s.acc:6.1%}" + (f"   예: {s.misses[0]}" if s.misses else ""))
        for m in self.missing_files:
            rows.append(f"  ! 파일 없음: {m}")
        return "\n".join(rows)


def _same(a: Any, b: Any, tol: float = 0.0) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if tol == 0:
            return abs(float(a) - float(b)) < 1e-6
        return abs(float(a) - float(b)) <= tol * max(abs(float(b)), 1e-9)
    return str(a).strip() == str(b).strip()


def eval_extract(golden_dir: Path = GOLDEN_DIR / "extract", *, root: Path | None = None, today: date | None = None,
                 verified_only: bool = False) -> ExtractEval:
    from ..reports import extract_facts, load_report

    root = root or Path.cwd()
    ev = ExtractEval(verified_only=verified_only)
    for gp in sorted(golden_dir.glob("*.json")):
        g = json.loads(gp.read_text(encoding="utf-8"))
        if verified_only and not g.get("verified"):
            continue
        fpath = Path(g["file"])
        if not fpath.is_absolute():
            fpath = root / fpath
        if not fpath.is_file():
            ev.missing_files.append(str(fpath))
            continue
        ev.reports += 1
        f = extract_facts(load_report(fpath), today=today)
        rid = g.get("report_id", gp.stem)
        got_facts = {
            "broker": f.broker, "date": f.date.isoformat() if f.date else None, "ticker": f.ticker, "opinion": f.opinion,
            "target_price": f.target_price, "prev_target_price": f.prev_target_price, "current_price": f.current_price,
            "high_52w": f.high_52w, "low_52w": f.low_52w, "title": f.title,
        }
        for name in FACT_FIELDS:
            want = g.get("facts", {}).get(name)
            s = ev.score(name)
            s.n += 1
            if _same(got_facts.get(name), want, PRICE_TOL):
                s.ok += 1
            else:
                s.misses.append(f"{rid}: got {got_facts.get(name)!r} / want {want!r}")
        gest = g.get("estimates", {})
        for m in EST_METRICS:
            want_cols = gest.get(m) or {}
            if not want_cols:
                continue
            s = ev.score(f"est.{m}")
            for col, want in want_cols.items():
                s.n += 1
                got = f.estimates.get(m, {}).get(col)
                if _same(got, want, EST_TOL):
                    s.ok += 1
                else:
                    s.misses.append(f"{rid} {col}: got {got!r} / want {want!r}")
    return ev


# --------------------------------------------------------------------------- Claim 평가 (P2)
@dataclass
class ClaimEval:
    report_id: str
    required: int = 0
    found: int = 0               # 필수 주장 중 추출된 것 (명제 유사도 또는 인용 겹침)
    candidates: int = 0          # AI 가 뽑은 usable Claim 수
    reviewed: int = 0            # candidate_review 에 판정이 있는 것
    supported: int = 0           # 그중 사람이 '지지됨' 으로 표기
    mismatched: int = 0          # verified 인데 사람이 '지지 안 됨' — 근거 불일치
    found_pending: int = 0       # 필수 주장이 뽑히긴 했는데 검토 대기(needs_review) 라 usable 이 아닌 것
    misses: list[str] = field(default_factory=list)   # 못 찾은 필수 주장 (설명용)
    machine_mismatched: int = 0  # v4 V9: 사람이 '지지 안 됨' 인데 기계가 verified 로 통과시킨 것 (잘못된 Claim 자동 승인)
    human_mismatched: int = 0    # 사람이 승인했는데 검토표에서는 '지지 안 됨' (사람 승인 오류 — 검토표가 더 최신일 때)
    machine_verified: int = 0

    @property
    def recall(self) -> float:
        return self.found / self.required if self.required else 1.0

    @property
    def mismatch_rate(self) -> float:
        return self.mismatched / self.reviewed if self.reviewed else 0.0

    @property
    def approved_invalid_rate(self) -> float:
        """기계가 verified 로 통과시킨 것 중 사람이 '지지 안 됨' 으로 표기한 비율 (v4 V9)."""
        return self.machine_mismatched / self.machine_verified if self.machine_verified else 0.0


def _overlap(a: str, b: str) -> float:
    import difflib

    return difflib.SequenceMatcher(None, " ".join(a.split()), " ".join(b.split())).ratio()


def _quote_hits(quote: str, spans: list[str]) -> bool:
    """정답표 인용과 Claim 의 span 이 겹치나 — 앞 40자 포함(양방향) 또는 유사도 ≥ 0.6."""
    q = " ".join(quote.split())
    if not q:
        return False
    for s in spans:
        t = " ".join(s.split())
        if not t:
            continue
        if q[:40] in t or t[:40] in q or _overlap(q, t) >= 0.6:
            return True
    return False


def eval_claims(golden: dict[str, Any], cs: ClaimSet, *, threshold: float = 0.6) -> ClaimEval:
    """필수 주장 재현율 + 후보 검토표 기반 근거 불일치율.

    매칭은 명제 유사도 ≥ threshold 또는 인용(quote) 겹침. 검토표(candidate_review) 행에 proposition 이 있으면 그 명제와 맞는 Claim 에
    붙이고(다른 추출기의 Claim 과 id 가 우연히 같아도 섞이지 않게), 없으면 옛 방식대로 claim_id 로 붙인다."""
    ev = ClaimEval(report_id=cs.report_id)
    usable = cs.usable()
    ev.candidates = len(usable)
    span_text = {s.span_id: s.text for s in cs.spans}

    def _match(req: dict[str, Any], pool: list) -> bool:  # noqa: ANN001
        prop = req.get("proposition", "")
        quote = req.get("quote", "")
        for c in pool:
            if _overlap(prop, c.proposition) >= threshold or _quote_hits(quote, [span_text.get(sid, "") for sid in c.span_ids]):
                return True
        return False

    for req in golden.get("required_claims", []) + golden.get("required_risks", []):
        ev.required += 1
        if _match(req, usable):
            ev.found += 1
        else:
            pending = _match(req, [c for c in cs.claims if c not in usable])
            ev.found_pending += int(pending)
            ev.misses.append(("[검토 대기] " if pending else "[미추출] ") + req.get("proposition", "")[:70])
    by_id = {c.claim_id: c for c in cs.claims}
    for row in golden.get("candidate_review", []):
        if "supported" not in row:
            continue
        c = None
        if row.get("proposition"):
            c = next((x for x in cs.claims if _overlap(row["proposition"], x.proposition) >= threshold), None)
        elif row.get("claim_id"):
            c = by_id.get(row["claim_id"])
        if c is None:
            continue
        ev.reviewed += 1
        if c.status.value == "verified" and c.verified_by != "human":
            ev.machine_verified += 1
        if row["supported"]:
            ev.supported += 1
        elif c.status.value == "verified":
            ev.mismatched += 1
            if c.verified_by == "human":
                ev.human_mismatched += 1
            else:
                ev.machine_mismatched += 1
    return ev


# --------------------------------------------------------------------------- Issue 평가 (v4 V9)
@dataclass
class IssueEval:
    ticker: str
    required: int = 0
    found: int = 0                 # 테마가 있다 (driver_id 또는 라벨)
    display_ok: int = 0            # 분류(공통/이견/독립/미해결) 도 같다
    missed_disagreements: int = 0  # 정답은 핵심 이견인데 결과는 아니다 — 가장 비싼 실수
    misses: list[str] = field(default_factory=list)

    @property
    def theme_recall(self) -> float:
        return self.found / self.required if self.required else 1.0

    @property
    def display_accuracy(self) -> float:
        return self.display_ok / self.required if self.required else 1.0


def eval_issues(golden: dict[str, Any], issue_set) -> IssueEval:  # noqa: ANN001
    """`tests/golden/issues/<ticker>.json` 의 required_issues 와 IssueSet 테마를 맞춘다.
    행: {"driver_id": "mobile_demand", "label": "모바일·세트 수요", "display": "핵심 이견", "direction": "mixed", "brokers_disagree": [...]}"""
    from .issues import themes

    ev = IssueEval(ticker=str(golden.get("ticker") or issue_set.ticker or ""))
    ts = themes(issue_set)
    for req in golden.get("required_issues", []):
        ev.required += 1
        did, label = req.get("driver_id"), req.get("label", "")
        t = next((x for x in ts if (did and x.driver_id == did) or (label and _overlap(label, x.driver_label) >= 0.7)), None)
        want = req.get("display")
        if t is None:
            ev.misses.append(f"[테마 없음] {label or did} ({want})")
            if want == "핵심 이견":
                ev.missed_disagreements += 1
            continue
        ev.found += 1
        got = t.display.value if t.display else None
        if not want or got == want:
            ev.display_ok += 1
        else:
            ev.misses.append(f"[분류 다름] {t.driver_label}: 정답 {want} / 결과 {got}")
            if want == "핵심 이견":
                ev.missed_disagreements += 1
        bd = req.get("brokers_disagree") or []
        if bd and t.display and t.display.value == "핵심 이견":
            got_dis = {b for b, v in t.coverage.items() if v.value == "disagree"} | {b for b, v in t.coverage.items() if v.value == "agree"}
            if not set(bd) <= got_dis:
                ev.misses.append(f"[이견 증권사 다름] {t.driver_label}: 정답 {bd} / 결과 coverage {dict((b, v.value) for b, v in t.coverage.items())}")
    return ev


__all__ = ["GOLDEN_DIR", "FACT_FIELDS", "EST_METRICS", "golden_draft_from_facts", "write_golden_drafts", "ExtractEval", "FieldScore",
           "eval_extract", "ClaimEval", "eval_claims", "IssueEval", "eval_issues"]
