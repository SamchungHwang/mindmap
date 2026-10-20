"""초안 파이프라인 — 리포트 파일들 → A 맵 초안 (자동 생성 계획 §1 의 ①~③).

    reports ─▶ 읽기(loader) ─▶ 사실(extract) ─▶ 합산(digest) ─▶ 규칙 채움(rules) ─▶ [LLM 그룹 채움] ─▶ 교차검증(check)
            ─▶ 엔진(evaluate_new_map · validate) ─▶ DraftResult

리서치 DB 가 있으면 현재가·52주·추세·DB 컨센서스를 보탠다 (없어도 끝까지 간다). 판단은 항상 WATCH.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..engine import Issue, evaluate_new_map, validate
from ..models import NewMap, Source
from ..reports import ReportDigest, ReportDoc, ReportError, digest_files
from ..symbols import SymbolBook, normalize_code
from ..templates import new_map_skeleton
from .check import cross_check
from .kpi_catalog import guess_sector, sector_name
from .llm_fill import GroupFiller, fill_groups
from .rules import RuleFillResult, fill_from_digest

Progress = Callable[[str, int, int], None]


class DraftError(ValueError):
    """사람이 고칠 수 있는 입력 오류 (종목을 못 정함 등)."""


@dataclass
class DraftResult:
    map: NewMap
    digest: ReportDigest
    docs: list[ReportDoc]
    rules: RuleFillResult
    issues: list[Issue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)       # 생성 과정 메모 (무엇을 어떻게 채웠나)
    flags: list[str] = field(default_factory=list)       # 교차검증 깃발 (사람이 봐야 할 곳)
    llm_used: bool = False
    sector: str | None = None
    db_used: bool = False

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def complete(self) -> bool:
        return not self.errors

    def summary_text(self) -> str:
        m = self.map
        er, rr = m.value("A3.4"), m.value("A3.5")
        w = m["A6.1"].value if isinstance(m["A6.1"].value, dict) else {}
        lines = [f"A 맵 초안 {m.map_id} — {m.name} ({m.ticker}) · 섹터 {sector_name(self.sector)}"
                 + (" · LLM 사용" if self.llm_used else " · LLM 없이 (규칙 채움만)") + (" · 리서치 DB 연결" if self.db_used else "")]
        lines += self.digest.summary_lines()
        lines.append("")
        lines.append(f"현재가 {m.current_price:,.0f} ({self.rules.price_source})" if m.current_price else "현재가 없음")
        for n in m.drivers():
            d = n.driver
            assert d is not None
            lines.append(f"드라이버 {n.id} {n.label}: 현재 {d.current.value:,} / 컨센 {d.consensus.value if d.consensus.value is None else f'{d.consensus.value:,}'} / 내 추정(초안) {d.my_estimate.value:,} · 확인 {d.next_check or '-'}")
        for nm, sc in m.scenarios().items():
            lines.append(f"{nm}: p={sc.probability:.2f} TP={sc.target_price:,.0f} — {sc.valuation_method}")
        if er is not None:
            lines.append(f"기대수익률 {er:+.1%} · 손익비 {rr if rr is None else f'{rr:.2f}'} · 제안 비중 {w.get('suggested', 0):.2%} · 판단 {m.decision.value if m.decision else '-'}")
        kills = m.kill_rules()
        if kills:
            lines.append("Kill: " + " | ".join(k.condition for k in kills))
        lines.append("")
        lines.append(f"검증: 오류 {len(self.errors)} / 경고 {len(self.issues) - len(self.errors)}" + (" → 초안 완료(사람 확정 대기)" if self.complete else " → 비어 있는 노드를 채워야 완료"))
        lines += [f"  {i}" for i in self.issues if i.level == "error"]
        if self.flags:
            lines.append("사람이 볼 곳:")
            lines += [f"  ⚑ {f}" for f in self.flags]
        if self.notes:
            lines.append("생성 메모:")
            lines += [f"  · {n}" for n in self.notes]
        return "\n".join(lines)


# ---------------------------------------------------------------------------- 리서치 DB 보강
def _db_price(code: str, research_db: Path | None, *, as_of_hint: date | None) -> tuple[tuple[float, date, Source] | None, object | None, list[str]]:
    if research_db is None:
        return None, None, []
    try:
        from ..collect.adapters.research_db import ResearchDb

        db = ResearchDb(research_db)
        pf = db.price_facts(code)
    except Exception as e:  # noqa: BLE001 — DB 가 잠겼거나 표가 다르면 리포트 값으로 간다
        return None, None, [f"리서치 DB 를 읽지 못했다 ({type(e).__name__}) — 리포트의 현재주가를 쓴다"]
    if pf is None:
        return None, db, [f"리서치 DB 에 {code} 시세가 없다 — 리포트의 현재주가를 쓴다"]
    notes = []
    if as_of_hint and pf.as_of < as_of_hint:
        notes.append(f"리서치 DB 종가({pf.as_of})가 리포트 기준일({as_of_hint})보다 오래됨 — 리포트의 현재주가를 쓴다")
        return None, db, notes
    return (pf.close, pf.as_of, pf.source(research_db)), db, notes


# ---------------------------------------------------------------------------- 진입점
def draft_from_reports(paths: Sequence[str | Path], *, ticker: str | None = None, name: str | None = None,
                       invest_type: str | None = None, sector: str | None = None, today: date | None = None,
                       symbols: SymbolBook | None = None, filler: GroupFiller | None = None,
                       research_db: Path | None = None, n_drivers: int = 3, progress: Progress | None = None) -> DraftResult:
    """리포트 파일들로 A 맵 초안을 만든다. 저장은 하지 않는다 (ops/CLI 가 한다)."""
    today = today or date.today()
    steps = 6 + (1 if filler else 0)

    def tick(label: str, i: int) -> None:
        if progress:
            progress(label, i, steps)

    tick("리포트 읽기", 1)
    try:
        digest, docs = digest_files(paths, ticker=normalize_code(ticker) if ticker else None, name=name, today=today)
    except ReportError as e:
        raise DraftError(str(e)) from e
    readable = [d for d in docs if not d.is_empty]
    if not readable:
        raise DraftError("읽을 수 있는 리포트가 없습니다 — 스캔 PDF 는 텍스트가 없습니다 (" + ", ".join(d.name for d in docs) + ")")
    # 종목 확정
    code = normalize_code(ticker) or digest.ticker
    if not code:
        raise DraftError("종목을 정하지 못했습니다 — 리포트 본문에 (6자리 코드) 가 없으면 --ticker 로 지정하세요")
    sym = symbols.by_code(code) if symbols else None
    nm = name or (sym.name if sym else None) or digest.name or code
    digest.ticker, digest.name = code, nm
    # 섹터: 지정 > 관심종목 > 본문 추정
    full_text = "\n".join(d.text for d in readable)
    if sector is None and sym and sym.sector and sym.sector.startswith("KR-"):
        sector = sym.sector
    guessed, score = guess_sector(full_text, name=nm)
    notes: list[str] = []
    if sector is None:
        sector = guessed
        notes.append(f"섹터 추정 {sector_name(sector)} (점수 {score})" if sector else "섹터를 추정하지 못했다 — 공통 KPI 만 사용")
    # 스켈레톤
    tick("규칙 채움", 2)
    m = new_map_skeleton(code, nm, invest_type or "growth", today, n_drivers=max(3, min(5, n_drivers)))
    price, db, db_notes = _db_price(code, research_db, as_of_hint=digest.as_of)
    notes += db_notes
    rules = fill_from_digest(m, digest, today=today, sector=sector, price=price)
    notes += rules.notes
    # 리서치 DB 보강: 52주·추세·거래량·DB 컨센서스
    db_used = False
    if db is not None:
        db_used = _enrich_from_db(m, db, code, today=today, research_db=research_db, digest=digest, notes=notes)
    # LLM
    llm_used = False
    if filler is not None:
        tick("LLM 그룹 채움", 3)
        notes += fill_groups(m, digest, filler, today=today, sector=sector, anchors=rules.anchors,
                             progress=(lambda lbl, i, n: progress(lbl, 3, steps)) if progress else None)
        llm_used = True
    # 교차검증 → 엔진
    tick("교차검증", steps - 2)
    flags = cross_check(m, digest, today=today)
    tick("평가·검증", steps - 1)
    ev = evaluate_new_map(m, today=today, enforce_gate=True)
    notes += [f"엔진: {n}" for n in ev.notes]
    issues = validate(m, today=today)
    m.meta["draft"].update({"llm": llm_used, "db": db_used, "flags": flags, "errors": [str(i) for i in issues if i.level == "error"]})
    tick("완료", steps)
    return DraftResult(map=m, digest=digest, docs=docs, rules=rules, issues=issues, notes=notes, flags=flags,
                       llm_used=llm_used, sector=sector, db_used=db_used)


def _enrich_from_db(m: NewMap, db, code: str, *, today: date, research_db: Path | None, digest: ReportDigest, notes: list[str]) -> bool:  # noqa: ANN001
    used = False
    try:
        pf = db.price_facts(code)
    except Exception:  # noqa: BLE001
        pf = None
    if pf is not None:
        src = pf.source(research_db or Path("research_db"))
        if pf.trend and m.value("A5.2.1") is None:
            m["A5.2.1"].set(pf.trend, as_of=pf.as_of, source=src)
            used = True
        if pf.volume_ratio and m.value("A5.2.2") is None:
            m["A5.2.2"].set(pf.volume_ratio, as_of=pf.as_of, source=src)
            used = True
        if pf.high_52w and pf.low_52w:
            m["A5.2"].question = (f"리서치 DB: {pf.as_of} 종가 {pf.close:,.0f} · 52주 {pf.low_52w:,.0f}~{pf.high_52w:,.0f}"
                                  + (f" · 1개월 {pf.ret_1m:+.1%}" if pf.ret_1m is not None else ""))
            used = True
    try:
        targets = db.targets(code, days=90, as_of=today)
    except Exception:  # noqa: BLE001
        targets = []
    if targets:
        tps = [t.tp for t in targets]
        line = f"리서치 DB 최근 90일 목표가 {len(tps)}건 {min(tps):,.0f}~{max(tps):,.0f}, 평균 {sum(tps) / len(tps):,.0f}"
        m["A3.6"].question = (m["A3.6"].question + " / " + line).strip(" /")
        m.meta["draft"]["db_consensus"] = {"n": len(tps), "min": min(tps), "max": max(tps), "mean": sum(tps) / len(tps)}
        if digest.n_tp < 3:
            notes.append(f"리포트 목표가가 {digest.n_tp}건뿐 — {line} 를 참고하라 (앵커는 리포트 기준)")
        used = True
    return used


__all__ = ["DraftResult", "DraftError", "draft_from_reports"]
