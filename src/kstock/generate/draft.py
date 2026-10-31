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
from ..models import NewMap, NodeStatus, Source
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
    price: PricePick | None = None

    claimsets: list = field(default_factory=list)        # reports.claims.ClaimSet (설계 v3 P2)
    issue_set: object | None = None                      # models.IssueSet (P3)

    # 의도적으로 비워 둔 노드(pending 태그) 에서 나오는 검증 오류는 '미확정' 이지 '오류' 가 아니다
    PENDING_PREFIXES = ("A1.", "A2.1", "A2.2", "A2.3", "A3", "A4", "A5.4", "A7.")

    def _pending_ids(self) -> set[str]:
        from .scenarios import PENDING_TAG

        ids = {n.id for n in self.map.nodes.values() if PENDING_TAG in n.tags}
        # 드라이버 my_estimate 공란 · 시나리오 공란 · Kill 공란은 pending 노드의 자식/부모 id 로 보고된다
        return ids

    def _is_pending(self, i: Issue) -> bool:
        ids = self._pending_ids()
        nid = i.node_id or ""
        if nid in ids:
            return True
        if nid == "A4" and {"A4.2", "A4.3", "A4.4"} & ids:          # 시나리오 공란 → 'A3: 시나리오 누락' (옛 메시지)
            return True
        if nid in ("A4.2", "A4.3", "A4.4") and "누락" in i.message:   # 설계 v6: 노드별 '시나리오 누락' — 자동 초안은 시나리오를 만들지 않는다
            return True
        if nid == "A5.4" and "A5.4.1" in ids:                         # Kill 공란
            return True
        return "my_estimate" in i.message and nid.startswith("A1.")

    @property
    def errors(self) -> list[Issue]:
        """진짜 오류 — 미확정(pending) 노드에서 나온 것은 뺀다."""
        return [i for i in self.issues if i.level == "error" and not self._is_pending(i)]

    @property
    def pending(self) -> list[Issue]:
        """사람이 채워야 하는 미확정 항목 (초안이 의도적으로 비운 곳)."""
        return [i for i in self.issues if i.level == "error" and self._is_pending(i)]

    @property
    def complete(self) -> bool:
        """초안으로서 완료 — 진짜 오류가 없다. (판단 가능 상태(템플릿 §3.3) 와는 다르다: 그것은 사람이 미확정을 채운 뒤.)
        설계 v7 T3: Claim 품질 게이트 미달(needs_claim_review, 게이트 켬) 이면 완료가 아니다."""
        gate = (self.map.meta.get("draft") or {}).get("claim_gate") or {}
        if gate.get("status") == "needs_claim_review" and gate.get("enabled", True):
            return False
        return not self.errors

    def summary_text(self) -> str:
        m = self.map
        er, rr = m.value("A4.5"), m.value("A4.6")
        w = m["A7.1"].value if isinstance(m["A7.1"].value, dict) else {}
        groups = m.meta.get("draft", {}).get("llm_groups") or {}
        ok = sum(1 for v in groups.values() if v == "ok")
        q = m.meta.get("draft", {}).get("quality")
        if self.claimsets:
            nv = sum(len(cs.usable()) for cs in self.claimsets)
            cv = (m.meta.get("draft") or {}).get("claim_verification") or {}
            detail = (f" · 자동 {cv.get('machine', 0)} · 사람 {cv.get('human', 0)}" + (f" · 확인 권고 ⚑ {cv['review_required']}" if cv.get("review_required") else "")) if cv else ""
            llm_line = f" · Claim {nv}건 ({'LLM' if q == 'llm' else '규칙 분류·저품질'}{detail})"
        else:
            llm_line = f" · LLM {ok}/{len(groups)} 그룹" if groups else " · LLM 없이 (규칙 채움만)"
        lines = [f"A 맵 초안 {m.map_id} — {m.name} ({m.ticker}) · 섹터 {sector_name(self.sector)}" + llm_line
                 + (" · 리서치 DB 연결" if self.db_used else "")]
        gate = (m.meta.get("draft") or {}).get("claim_gate") or {}
        if gate.get("status") == "needs_claim_review":
            from ..reports.claim_stats import gate_line

            lines.insert(0, "⚠ " + gate_line(gate) + (" — 초안을 완료로 표시하지 않는다 (검토함 처리 또는 --no-claim-gate)" if gate.get("enabled", True) else ""))
        lines += self.digest.summary_lines()
        lines.append("")
        if m.current_price:
            age = f" · 작성일 기준 {self.price.age_days(m.created_at)}일 전" if self.price and self.price.age_days(m.created_at) > 0 else ""
            lines.append(f"현재가 {m.current_price:,.0f} ({self.rules.price_source}{age})")
        else:
            lines.append("현재가 없음")
        def fmt(v):  # noqa: ANN001
            return "-" if v is None else f"{v:,}"

        from ..engine.sensitivity import sensitivity_short

        for n in m.drivers():
            d = n.driver
            assert d is not None
            if d.kind == "qualitative":
                lines.append(f"드라이버 {n.id} {n.label}: 정성 — 관측 {d.check_indicator[:50] or '-'} · 무효화 조건 {'있음' if d.invalidation_condition else '사람이 쓴다'}"
                             + (f" · {sensitivity_short(d.sensitivity)}" if d.sensitivity else ""))
                continue
            lines.append(f"드라이버 {n.id} {n.label}: 현재 {fmt(d.current.value)} / 컨센 {fmt(d.consensus.value)} / 내 추정 {fmt(d.my_estimate.value)}"
                         + (" (사람이 쓴다)" if d.my_estimate.value is None else "") + f" · 확인 {d.next_check or '-'}"
                         + (f" · {sensitivity_short(d.sensitivity)}" if d.sensitivity else ""))
        res_nodes = [n for n in m.children("A3.1") if n.driver is not None] if "A3.1" in m else []
        if res_nodes:
            lines.append("결과 지표(A3.1): " + " · ".join(f"{n.label} 컨센 {fmt(n.driver.consensus.value)}{n.unit or ''}" for n in res_nodes))
        if "A3.3" in m and isinstance(m["A3.3"].value, str):
            lines.append("평가 모델(A3.3): " + m["A3.3"].value)
        scs = m.scenarios()
        if scs:
            for nm, sc in scs.items():
                p = f"p={sc.probability:.2f}" if sc.probability is not None else "p=미입력"
                lines.append(f"{nm}: {p} TP={sc.target_price:,.0f} — {sc.valuation_method}")
        else:
            cands = (m.meta.get("draft") or {}).get("scenario_candidates") or []
            lines.append(f"시나리오: 공란 (리포트 제시 없음) · 증권사 자체 후보 {len(cands)}개 — 콘솔 「시나리오 후보 넣기」")
        dist = (m.meta.get("draft") or {}).get("tp_distribution")
        if dist:
            lines.append(f"목표가 분포: {dist['n']}건 {dist['min']:,.0f}~{dist['max']:,.0f} · 중앙 {dist['median']:,.0f} · 분산 {dist['spread']:.0%}")
        if er is not None:
            lines.append(f"기대수익률 {er:+.1%} · 손익비 {rr if rr is None else f'{rr:.2f}'} · 제안 비중 {w.get('suggested', 0):.2%} · 판단 {m.decision.value if m.decision else '-'}")
        else:
            lines.append(f"기대수익률·손익비: 보류 (확률 미입력) · 판단 {m.decision.value if m.decision else '-'}")
        kills = m.kill_rules()
        if kills:
            lines.append("Kill: " + " | ".join(k.condition for k in kills))
        else:
            lines.append("Kill: 공란 — 원문 근거(조건부 리스크) 가 없어 제안하지 않음. 사람이 정한다")
        # 설계 v7: 성립 조건 · 증거 · 판단 상태
        draft_meta = m.meta.get("draft") or {}
        inv = (draft_meta.get("conditions") or {}).get("inversion") or {}
        singles = [r for r in (inv.get("single") or []) if r.get("status") == "ok" and r.get("variable") != "per"]
        if inv.get("price"):
            lines.append(f"성립 조건(A2.4): 현재가 {inv['price']:,.0f} 을 한 변수로 설명하면 " + (" · ".join(f"{r['label']} {r['value']:+.0f}{r['unit']}" for r in singles[:3]) or "설명 가능한 변수 없음")
                         + (f" · 적용 PER 만으로 {next((r['value'] for r in inv.get('single') or [] if r.get('variable') == 'per'), 0):.2f}" if any(r.get('variable') == 'per' for r in inv.get('single') or []) else "")
                         + " (나머지 고정 · 시장의 기대가 아니다 — 두 변수 조합표는 콘솔 「D」)")
        if draft_meta.get("lights") is not None:
            from ..engine.evidence import light_counts_text

            fl = m.meta.get("flags") or {}
            lines.append("증거(A5.5): " + light_counts_text(draft_meta.get("lights") or {}) + (" · ⚪ 과반 → 증거 없는 판단" if fl.get("no_evidence") else "")
                         + (" · 신규 BUY 보류" if fl.get("hold_new_buy") else "") + " — 관측값은 콘솔 「O」 · 규칙은 「G」")
        if self.issue_set is not None:
            from .issues import themes

            ts = themes(self.issue_set)
            lines.append("쟁점: " + " · ".join(f"{t.driver_label}({t.display.value[:2] if t.display else '?'})" for t in ts[:8]) if ts else "쟁점: 없음")
        q = (m.meta.get("draft") or {}).get("quality")
        if q:
            lines.append(f"Claim 품질: {q}" + (" (키워드 분류 — 저품질 · 정성 노드 미생성)" if q == "rules" else ""))
        kc = (m.meta.get("draft") or {}).get("kill_candidates") or []
        if kc:
            lines.append(f"Kill 후보: {len(kc)}건 (조건부 리스크) — 관측 지표·갱신 주기·위반 시 행동을 채워 확정")
        lines.append("")
        lines.append(f"검증: 오류 {len(self.errors)} / 미확정 {len(self.pending)} / 경고 {len(self.issues) - len(self.errors) - len(self.pending)}"
                     + (" → 초안 완료 (미확정 항목은 사람이 채운다)" if self.complete else " → 오류를 고쳐야 한다"))
        lines += [f"  ✗ {i}" for i in self.errors]
        lines += [f"  □ [미확정] {i.node_id}: {i.message}" if i.node_id else f"  □ [미확정] {i.message}" for i in self.pending]
        if self.flags:
            lines.append("사람이 볼 곳:")
            lines += [f"  ⚑ {f}" for f in self.flags]
        if self.notes:
            lines.append("생성 메모:")
            lines += [f"  · {n}" for n in self.notes]
        return "\n".join(lines)


# ---------------------------------------------------------------------------- 현재가 (작성 시점 기준)
PRICE_STALE_DAYS = 3


@dataclass
class PricePick:
    """A0.4 에 들어갈 현재가. 후보(실시간 조회 · 리서치 DB 종가 · 리포트 현재주가) 가운데 날짜가 가장 최근인 것."""

    value: float
    as_of: date
    source: Source
    kind: str                       # live | db | report
    candidates: list[tuple[str, date, float]] = field(default_factory=list)

    def age_days(self, today: date) -> int:
        return (today - self.as_of).days


def _open_db(code: str, research_db: Path | None):  # noqa: ANN202
    if research_db is None:
        return None, None, []
    try:
        from ..collect.adapters.research_db import ResearchDb

        db = ResearchDb(research_db)
        return db, db.price_facts(code), []
    except Exception as e:  # noqa: BLE001 — DB 가 잠겼거나 표가 다르면 없는 것으로
        return None, None, [f"리서치 DB 를 읽지 못했다 ({type(e).__name__})"]


def resolve_price(code: str, digest: ReportDigest | None, *, today: date, research_db: Path | None, fetch: bool,
                  fetcher: Callable[[str], object] | None = None) -> tuple[PricePick | None, object, list[str]]:
    """맵 작성 시점의 현재가. 실시간 조회(네이버) → 리서치 DB 종가 → 리포트 현재주가 를 모아 **가장 최근 날짜** 를 고른다.
    같은 날짜면 live > db > report. 돌려주는 것: (고른 값, 열린 DB, 메모)."""
    notes: list[str] = []
    cands: list[tuple[int, date, float, Source, str]] = []   # (우선순위, 날짜, 값, 출처, 종류)
    if fetch:
        try:
            from ..collect.adapters.naver_quote import fetch_quote

            q = (fetcher or fetch_quote)(code)
            cands.append((0, q.as_of, float(q.price), q.source(), "live"))  # type: ignore[union-attr]
        except Exception as e:  # noqa: BLE001 — 오프라인이면 다음 후보로
            notes.append(f"현재가 실시간 조회 실패 ({str(e)[:120]}) — 리서치 DB/리포트 값으로 대신한다")
    db, pf, db_notes = _open_db(code, research_db)
    notes += db_notes
    if pf is not None:
        cands.append((1, pf.as_of, float(pf.close), pf.source(research_db or Path("research_db")), "db"))
    rp = digest.price if digest is not None else None
    if rp is not None:
        price, as_of, f = rp
        cands.append((2, as_of or f.date or today, float(price),
                      Source(name=f"{f.broker or '리포트'} {f.date or ''} 리포트 현재주가 ({as_of or '기준일 미상'})".strip(), url=str(f.path), retrieved_at=f.date), "report"))
    if not cands:
        notes.append("현재가를 찾지 못했다 — A0.4 를 직접 적어야 한다 (실시간 조회·리서치 DB·리포트 모두 없음)")
        return None, db, notes
    cands.sort(key=lambda c: (-c[1].toordinal(), c[0]))
    prio, as_of, value, src, kind = cands[0]
    pick = PricePick(value=value, as_of=as_of, source=src, kind=kind, candidates=[(c[4], c[1], c[2]) for c in cands])
    label = {"live": "실시간 조회", "db": "리서치 DB 종가", "report": "리포트 현재주가"}[kind]
    others = ", ".join(f"{k} {d} {v:,.0f}" for k, d, v in pick.candidates[1:])
    notes.append(f"현재가 {value:,.0f} ({as_of}, {label})" + (f" — 다른 후보: {others}" if others else ""))
    age = pick.age_days(today)
    if age > PRICE_STALE_DAYS:
        notes.append(f"현재가 기준일이 작성일({today})보다 {age}일 전 — 작성 시점 값이 아니다. 실시간 조회를 켜거나 A0.4 를 갱신하라")
    return pick, db, notes


def latest_price(code: str, *, today: date | None = None, research_db: Path | None = None, fetch: bool = True,
                 fetcher: Callable[[str], object] | None = None) -> tuple[PricePick | None, list[str]]:
    """리포트 없이 지금 기준 현재가만 — 기존 A 맵의 A0.4 갱신용 (실시간 조회 → 리서치 DB 종가)."""
    pick, _db, notes = resolve_price(code, None, today=today or date.today(), research_db=research_db, fetch=fetch, fetcher=fetcher)
    return pick, notes


# ---------------------------------------------------------------------------- 진입점
def draft_from_reports(paths: Sequence[str | Path], *, ticker: str | None = None, name: str | None = None,
                       invest_type: str | None = None, sector: str | None = None, today: date | None = None,
                       symbols: SymbolBook | None = None, filler: GroupFiller | None = None,
                       research_db: Path | None = None, n_drivers: int = 3, progress: Progress | None = None,
                       fetch_price: bool = False, price_fetcher: Callable[[str], object] | None = None,
                       claims: str = "auto", claims_dir: Path | None = None, llm_groups: bool = False,
                       issue_check: bool = True, prefill_suggestions: bool = False, claims_ref: Path | None = None,
                       claim_gate_on: bool = True, dart_events: bool = True, llm_events: str = "auto") -> DraftResult:
    """리포트 파일들로 A 맵 초안을 만든다. 저장은 하지 않는다 (ops/CLI 가 한다).

    issue_check: filler 가 있으면 쟁점마다 기준 명제에 대한 증권사별 판정을 LLM 에 묻는다 (설계 v4 V3). False 면 방향 통계만.
    prefill_suggestions: 리포트 기반 제안값(내 추정=컨센 등) 을 값 칸에 미리 넣는다 — `suggested` 태그·pending 유지 (설계 v4 §6, 기본 꺼짐).
      제안값 자체(`meta.draft.suggestions`) 는 옵션과 무관하게 항상 만들어 HTML 편집 폼의 「채우기」 로 받아들일 수 있다.

    claims: "auto" = filler 가 있으면 LLM Claim 추출(+3단 검증), 없으면 규칙 분류(저품질); "llm" / "rules" / "off" / "cached"(claims_dir 의 저장본만).
    llm_groups: 예전 그룹 채움(노드 직접 채움) 을 보조로 돌릴지 — 기본 끔 (설계 v3: LLM 은 Claim/Issue 만 만든다).

    fetch_price: 작성 시점 현재가를 네이버에서 조회한다 (콘솔·CLI 기본 켬, 라이브러리 기본 끔). 실패하면 DB·리포트 값.
    dart_events: 리서치 DB 공시 목록 + OpenDART 본문으로 **공시확정 촉매**(IR 일시·유상증자 납입일·주총) 를 A6.1 에 넣는다 (2026-10-30).
      키(DART_API_KEY) 가 없으면 건너뛰고 메모만 남긴다. 네트워크를 타므로 시험에서는 끈다.
    llm_events: 층 B (2026-10-31) — "auto": filler 가 있으면 LLM 이 본문 발췌에서 사건을 제안하고 규칙이 검증해 A6.1 에 합친다 (`event_llm`);
      "on": 같되 filler 가 없으면 메모; "off": 안 한다. 층 A(규칙, `event_text`) 와 같은 사건이면 층 A 가 남고 LLM 문장은 증거로 붙는다."""
    today = today or date.today()
    steps = 7 + (1 if (filler and llm_groups) else 0)

    def tick(label: str, i: int) -> None:
        if progress:
            progress(label, i, steps)

    tick("리포트 읽기", 1)
    pre_db = None
    if research_db is not None:
        try:
            from ..collect.adapters.research_db import ResearchDb

            pre_db = ResearchDb(research_db)
        except Exception:  # noqa: BLE001 — 없으면 교차 확인 없이
            pre_db = None
    try:
        digest, docs = digest_files(paths, ticker=normalize_code(ticker) if ticker else None, name=name, today=today, db=pre_db)
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
    pick, db, price_notes = resolve_price(code, digest, today=today, research_db=research_db, fetch=fetch_price, fetcher=price_fetcher)
    notes += price_notes
    price = (pick.value, pick.as_of, pick.source) if pick else None
    texts = {d.path.name: d.text for d in readable}
    m = new_map_skeleton(code, nm, invest_type or "growth", today, n_drivers=max(3, min(5, n_drivers if claims != "off" else n_drivers)))
    # 2026-10-30: 실적 발표일은 그 회사의 과거 공시일(영업(잠정)실적) 로 추정한다 — 「분기 말 +8일」 이 NAVER 에는 한 달 틀렸다
    earnings_history: list[date] = []
    if db is not None:
        try:
            earnings_history = db.earnings_dates(code, as_of=today)
        except Exception:  # noqa: BLE001
            earnings_history = []
    rules = fill_from_digest(m, digest, today=today, sector=sector, price=price, texts=texts, earnings_history=earnings_history)
    notes += rules.notes
    # 층 B (2026-10-31): LLM 이 본문 발췌에서 사건을 제안하고 규칙이 검증해 받는다 — 층 A 와 같은 사건이면 층 A 가 남고 문장은 증거로
    if filler is not None and llm_events in ("auto", "on"):
        from .catalysts import merge_into_map
        from .event_llm import llm_text_events

        b_events, b_notes = llm_text_events(readable, digest, filler, today=today, sector=sector,
                                            progress=(lambda lbl, i, n: progress(lbl, 2, steps)) if progress else None)
        notes += [f"A6.1 LLM: {x}" for x in b_notes]
        if b_events:
            b_merge = merge_into_map(m, b_events, today=today)
            notes.append(f"A6.1 LLM 사건 {len(b_events)}건 (규칙 검증 통과) 합침" + (" · " + "; ".join(b_merge) if b_merge else ""))
    elif llm_events == "on":
        notes.append("A6.1 LLM 사건 추출(층 B): LLM 이 없어 건너뜀 — --llm")
    if pick is not None:
        m.meta["draft"]["price"] = {"value": pick.value, "as_of": pick.as_of.isoformat(), "kind": pick.kind,
                                    "age_days": pick.age_days(today),
                                    "candidates": [{"kind": k, "as_of": d.isoformat(), "value": v} for k, d, v in pick.candidates]}
        if pick.age_days(today) > PRICE_STALE_DAYS:
            m["A0.4"].tags = sorted(set(m["A0.4"].tags) | {"warn"})
    # 리서치 DB 보강: 52주·추세·거래량·DB 컨센서스
    db_used = False
    if db is not None:
        db_used = _enrich_from_db(m, db, code, today=today, research_db=research_db, digest=digest, notes=notes)
    # 공시확정 촉매 (2026-10-30): IR 안내·유상증자·주총 공시 본문의 날짜 — 추정보다 세다
    if dart_events and db is not None:   # 공시 목록은 리서치 DB 가 1차 출처 — DB 없이 corpCode.xml 부터 받지는 않는다
        notes += _fill_dart_events(m, code, db, today=today, research_db=research_db)
    if db is not None:
        notes += [f"촉매 결과: {x}" for x in fill_event_outcomes(m, db, today=today)]
    # Claim → Issue → 맵 (설계 v3 P2~P4)
    claimsets: list = []
    issue_set = None
    if claims != "off":
        tick("주장 추출·검증", 3)
        claimsets, c_notes = build_claimsets(readable, digest, filler, mode=claims, sector=sector, name=nm, ticker=code, today=today,
                                             claims_dir=claims_dir, progress=(lambda lbl, i, n: progress(lbl, 3, steps)) if progress else None)
        notes += c_notes
        if claimsets:
            tick("쟁점 통합", 4)
            from .issues import build_issues
            from .mapping import apply_claims_to_map

            issue_set = build_issues(claimsets, ticker=code, digest=digest, filler=filler if claims != "off" else None, issue_check=issue_check)
            notes += [f"쟁점: {n}" for n in issue_set.notes]
            notes += apply_claims_to_map(m, claimsets, issue_set, digest, today=today, name=nm)
            # 설계 v7 T3: Claim 품질 게이트 — 핵심 Claim 검증률 · (기준 캐시가 있으면) 재현율. 미달이면 초안을 완료로 표시하지 않는다
            from ..reports.claim_stats import claim_gate, gate_line

            ref_sets: list = []
            if claims_ref is not None:
                from ..reports.claims import load_claimsets

                ref_sets = load_claimsets(Path(claims_ref), report_ids=[cs.report_id for cs in claimsets])
            gate = claim_gate(claimsets, reference=ref_sets or None)
            gate["enabled"] = bool(claim_gate_on)
            m.meta["draft"]["claim_gate"] = gate
            if gate["status"] == "needs_claim_review" and claim_gate_on:
                m.meta["draft"]["draft_status"] = "needs_claim_review"
            notes.append(gate_line(gate) + ("" if claim_gate_on else " (게이트 끔 --no-claim-gate — 기록만)"))
    # A1 원인 드라이버 (설계 v5 R2) — 섹터 원인 KPI 순서로, Claim 수치·사업부 표로 채운다. Claim 이 없어도 정성 뼈대는 만든다
    from .cause import fill_cause_drivers

    notes += fill_cause_drivers(m, sector=sector, claimsets=claimsets, issue_set=issue_set, today=today, digest=digest)
    # 촉매 ↔ 드라이버 연결 (2026-10-31): 원인 드라이버가 생긴 뒤에야 실적 이벤트를 A1 전부에, 유상증자를 A3.2/A3.3/A5.4 에,
    # 문장의 드라이버 낱말을 그 A1 노드에 잇고 종류별 상방·하방 초안을 채울 수 있다. 증권사 전망 Claim 은 여기서 증거로 붙는다
    from .catalysts import link_catalysts

    link_notes = link_catalysts(m, sector=sector)
    if link_notes:
        notes.append(f"A6.1 연결 {len(link_notes)}건: " + "; ".join(x[:40] for x in link_notes[:6]))
    if claimsets:
        from .mapping import apply_analyst_catalysts

        notes += apply_analyst_catalysts(m, claimsets, today=today)
        link_catalysts(m, sector=sector)   # 새로 생긴 리포트 전망 이벤트도 잇는다
    from .catalysts import renumber_fresh

    if renumber_fresh(m):   # 새 초안은 날짜순 번호로 — 그 뒤로는 고정 (사람이 고친 노드가 있으면 안 건드린다)
        notes.append("A6.1 번호를 날짜순으로 매겼다 (새 초안)")
    # 민감도 (설계 v5 R3): 표·사업부 표 숫자를 맵에 남기고(편집 뒤 재계산용) 원인 드라이버마다 계산 — 빠진 입력은 "미계산" 으로 드러난다
    from ..engine.sensitivity import compute_all, financial_snapshot, segment_snapshot
    from ..generate.kpi_catalog import cause_kpis

    fin = financial_snapshot(m, digest)
    notes += _align_result_eps(m, fin, today=today)
    if getattr(digest, "unit_fix", None) or getattr(digest, "np_dropped", None) or getattr(digest, "seg_fix", None):
        notes.append("재무 표 정합성 보정: " + " · ".join(
            [f"{k} 단위 ×{v:g}" for k, v in (digest.unit_fix or {}).items()]
            + [f"{b} {y} 순이익 제외" for b, y in sorted(digest.np_dropped or [])]
            + [f"{k} 사업부 표 ×{v:g}" for k, v in (digest.seg_fix or {}).items()]))
    seg_names = [k.segment for k in cause_kpis(sector) if k.segment] + [v.get("segment") for v in (m.meta["draft"].get("drivers") or {}).values() if v.get("segment")]
    seg_names += list(digest.segment_names())   # 설계 v6: SOTP·역산은 사업부 전부를 본다
    cols = [c for c in {fin.get("year"), fin.get("latest_actual_year"), *(v.get("col") for v in (m.meta["draft"].get("drivers") or {}).values())} if c]
    cols = sorted(set(cols) | {k[2] for k in getattr(digest, "segments", {})})   # 설계 v7 T5: 분기 열 전부 — 날짜별 필요 경로의 재료
    segment_snapshot(m, digest, sorted(set(seg_names)), cols)
    notes += [f"민감도: {x}" for x in compute_all(m, today=today)]
    # 밸류에이션 묶음 (설계 v6): A3.2 기준(비어 있음 → 기본값 표시) → A3.3 PER·정상화·역산 (SOTP 는 멀티플 입력 뒤) → A4.7/A3.4. Bear 템플릿은 라벨만
    from ..engine.attractiveness import compute_valuation_all
    from ..engine.scenario_model import apply_bear_template

    apply_bear_template(m, sector, today=today)
    notes += compute_valuation_all(m, today=today)
    # 설계 v7: 성립 조건(가정 표·역산 표·필요 경로) · 역할·띠·경계 제안 · 증거 계획·신호등 · 규칙 평가·플래그
    from ..engine.decision import compute_v7_all

    notes += [f"v7: {x}" for x in compute_v7_all(m, today=today, sector=sector)]
    # A2.1.x 쟁점 노드 ↔ A1 드라이버 링크 (쟁점이 먼저 만들어졌으므로 여기서 잇는다)
    from .cause import driver_node_for

    for iss in m.children("A2.1"):
        if iss.source and iss.source.name.startswith("리포트 쟁점 ") and not iss.links and issue_set is not None:
            iid = iss.source.name.split("리포트 쟁점 ", 1)[1].split(" ")[0]
            t = next((x for x in issue_set.issues if x.issue_id == iid), None)
            link = driver_node_for(m, t.driver_id if t else None, t.driver_label if t else None)
            if link:
                iss.links = [link]
                m[link].links = sorted(set(m[link].links) | {iss.id})
    # (보조) 예전 LLM 그룹 채움 — 노드를 직접 채우는 방식, 기본 끔
    if filler is not None and llm_groups:
        tick("LLM 그룹 채움", 5)
        notes += fill_groups(m, digest, filler, today=today, sector=sector, anchors=rules.anchors,
                             progress=(lambda lbl, i, n: progress(lbl, 5, steps)) if progress else None, groups=("drivers", "thesis", "risks", "catalysts", "evidence"))
    # 리포트 기반 제안값 (설계 v4 §6) — 빈 사람 몫 노드 옆에 보일 값. 옵션이면 값 칸에 미리 넣는다
    from .rules import scenario_anchors
    from .suggest import apply_prefill, build_suggestions

    anchors = rules.anchors or scenario_anchors(digest, m.current_price)[0]   # Bear 앵커 = 진입 가격대 제안의 아래쪽
    sugg = build_suggestions(m, issue_set=issue_set, claimsets=claimsets, anchors=anchors)
    n_sugg = sum(len(v) for v in sugg.values())
    if n_sugg:
        notes.append(f"제안값 {n_sugg}개 ({', '.join(sorted(sugg))}) — HTML 편집 폼의 「채우기」 로 받아들인다")
    if prefill_suggestions and n_sugg:
        filled = apply_prefill(m, today=today)
        notes.append(f"제안값 미리 채움 {len(filled)}개 (suggested 태그 · pending 유지): {', '.join(filled)}")
    # 교차검증 → 엔진
    tick("교차검증", steps - 2)
    flags = cross_check(m, digest, today=today)
    tick("평가·검증", steps - 1)
    ev = evaluate_new_map(m, today=today, enforce_gate=True)
    notes += [f"엔진: {n}" for n in ev.notes]
    issues = validate(m, today=today)
    if pick is not None and pick.age_days(today) > PRICE_STALE_DAYS:
        flags.append(f"A0.4: 현재가 기준일 {pick.as_of} 가 작성일보다 {pick.age_days(today)}일 전 — 갱신 필요")
    groups_status = m.meta["draft"].get("llm_groups") or {}
    llm_ok = sum(1 for v in groups_status.values() if v == "ok")
    if filler is not None and llm_groups and llm_ok == 0:
        first = next((v for v in groups_status.values() if v.startswith("fail")), "")
        flags.append("LLM 그룹 채움 전부 실패" + (f" ({first[6:90]})" if first else ""))
    llm_claims = bool(claimsets) and all(cs.quality == "llm" for cs in claimsets)
    if filler is not None and claims in ("auto", "llm") and not llm_claims:
        flags.append("LLM Claim 추출이 안 됐다 — 규칙 분류(저품질) 로 대체" if claimsets else "Claim 없음")
    m.meta["draft"].update({"llm": llm_claims or llm_ok > 0, "llm_requested": filler is not None, "db": db_used, "flags": flags,
                            "errors": [str(i) for i in issues if i.level == "error"], "notes": list(notes)})
    tick("완료", steps)
    r = DraftResult(map=m, digest=digest, docs=docs, rules=rules, issues=issues, notes=notes, flags=flags,
                    llm_used=llm_claims or llm_ok > 0, sector=sector, db_used=db_used, price=pick, claimsets=claimsets, issue_set=issue_set)
    m.meta["draft"]["pending"] = [str(i) for i in r.pending]
    m.meta["draft"]["errors"] = [str(i) for i in r.errors]
    return r


def build_claimsets(docs, digest: ReportDigest, filler, *, mode: str, sector: str | None, name: str | None, ticker: str | None,  # noqa: ANN001
                    today: date, claims_dir: Path | None, progress=None) -> tuple[list, list[str]]:  # noqa: ANN001
    """리포트 → ClaimSet 들. mode: auto | llm | rules | cached. 저장본(claims_dir) 이 있고 같은 리포트면 다시 뽑지 않는다."""
    from ..models import report_id_for
    from ..reports.claims import extract_claims, load_claimset, rules_claims, save_claimset
    from ..reports.validate_claims import run_all

    notes: list[str] = []
    out: list = []
    facts_by_report: dict = {}
    facts_by_name = {f.path.name: f for f in digest.reports}
    use_llm = filler is not None and mode in ("auto", "llm")
    if mode == "llm" and filler is None:
        notes.append("Claim: LLM 이 없어 규칙 분류로 대체 (저품질)")
    for i, doc in enumerate(docs, start=1):
        f = facts_by_name.get(doc.path.name)
        if f is None:
            continue
        rid = report_id_for(str(doc.path), doc.text)
        cached = (claims_dir / f"{rid}.json") if claims_dir else None
        if cached is not None and cached.is_file() and mode in ("cached", "auto"):
            try:
                cs = load_claimset(cached)
                # 추출이 실패해 비어 있는 저장본(Claim 0 + '실패' 메모) 은 캐시로 치지 않는다 — 다시 뽑는다
                failed_empty = not cs.claims and any("실패" in n for n in cs.notes)
                if failed_empty and mode == "auto" and use_llm:
                    notes.append(f"Claim: {doc.path.name} 저장본은 실패한 빈 결과 — 다시 추출")
                elif cs.quality == "llm" or mode == "cached":
                    out.append(cs)
                    facts_by_report[cs.report_id] = f
                    notes.append(f"Claim: {doc.path.name} 저장본 사용 ({cs.quality}, verified {len(cs.usable())}/{len(cs.claims)})"
                                 + (" ⚠ 비어 있음" if not cs.claims else ""))
                    continue
            except Exception:  # noqa: BLE001
                pass
        if mode == "cached":
            continue
        if progress:
            progress(f"주장 추출 {i}/{len(docs)} {doc.path.name}", i, len(docs))
        if use_llm:
            try:
                cs = extract_claims(doc, f, filler, sector=sector, name=name, ticker=ticker)
                if not cs.claims and any("실패" in n for n in cs.notes):
                    raise RuntimeError(next(n for n in cs.notes if "실패" in n)[:120])
                run_all(cs, f, filler, semantic=True, fulltext=doc.text)
            except Exception as e:  # noqa: BLE001
                notes.append(f"Claim: {doc.path.name} LLM 추출 실패 ({type(e).__name__}: {str(e)[:80]}) → 규칙 분류(저품질)")
                cs = rules_claims(doc, f, sector=sector, ticker=ticker)
                run_all(cs, f, None, semantic=False, fulltext=doc.text)
        else:
            cs = rules_claims(doc, f, sector=sector, ticker=ticker)
            run_all(cs, f, None, semantic=False, fulltext=doc.text)
        facts_by_report[cs.report_id] = f
        if claims_dir is not None:
            save_claimset(cs, claims_dir)
        out.append(cs)
        notes.append(f"Claim: {doc.path.name} {cs.quality} · verified {len(cs.usable())}/{len(cs.claims)}" + (f" · 검토 대기 {len(cs.review_queue(include_required=False))}" if cs.review_queue(include_required=False) else ""))
    if out:
        from ..reports.validate_claims import mark_review_required

        mark_review_required(out, facts_by_report=facts_by_report)   # v4 V2: 증권사 간 수치 충돌은 전부 모아야 보인다 (저장본에도 적용)
        n_req = sum(1 for cs in out for c in cs.usable() if c.review_required)
        if n_req:
            notes.append(f"Claim 확인 권고 ⚑ {n_req}건 — 자동 통과했지만 판단 지표 수치·증권사 간 충돌·조건부 리스크 (검토 대기함에 함께)")
    return out, notes


#: A6.2 수급/기술 시세가 이보다 오래되면 stale — 거래일 기준이 아니라 달력일이라 연휴를 넘길 만큼 넉넉히
FLOW_STALE_DAYS = 7


def _align_result_eps(m: NewMap, fin: dict, *, today: date) -> list[str]:
    """A3.1.3 EPS 컨센을 평가 모델(A3.3) 과 **같은 정규화 EPS** 로 맞춘다 (2026-10-31 삼성전자: A3.1.3 71,314원 ↔ A3.3 30,722,739원).
    정규화(지배 순이익 ÷ 공시 보통주) 가 가능하면 그 값, 아니면 리포트 EPS 그대로. 리포트 EPS 평균은 설명에 남긴다."""
    n = m.nodes.get("A3.1.3")
    e = (fin or {}).get("eps") or {}
    if n is None or n.driver is None or "user" in n.tags or e.get("basis") != "std" or not e.get("n") or e.get("mean") is None:
        return []
    d = n.driver
    if d.consensus.value is None:
        return []
    std, rep = float(e["mean"]), d.consensus.value
    if abs(std - rep) < 1e-6:
        return []
    rep_txt = f"리포트 EPS 평균 {rep:,.0f}원" + (f" ({', '.join(f'{b} {v:,.0f}' for b, v in (e.get('reported') or {}).items())})" if e.get("reported") else "")
    excl = f" · 제외 {', '.join(e['excluded'])}" if e.get("excluded") else ""
    d.consensus = d.consensus.model_copy(update={"value": round(std, 0), "as_of": today,
                                                 "rationale": f"{fin.get('year')} 정규화 EPS (지배 순이익 ÷ 공시 보통주, {e['n']}곳) — A3.3 평가 모델과 같은 값 · {rep_txt}{excl}"})
    if d.my_estimate.value is not None and abs(d.my_estimate.value - rep) < 1e-6 and (d.my_estimate.source and "제안" in (d.my_estimate.source.name or "")):
        d.my_estimate = d.my_estimate.model_copy(update={"value": round(std, 0)})
    if not n.label.endswith("정규화)"):
        n.label = n.label.rstrip(")") + ", 정규화)" if n.label.endswith(")") else n.label + " (정규화)"
    info = m.meta.setdefault("draft", {}).setdefault("drivers", {}).get("A3.1.3")
    if info is not None:
        info["by_broker"] = {b: round(v, 0) for b, v in (e.get("by_broker") or {}).items()}
        info["by_broker_unit"] = "원"
        info["eps_basis"] = "std"
    return [f"A3.1.3 EPS 컨센을 정규화 EPS {std:,.0f}원으로 (리포트 평균 {rep:,.0f}원) — A3.3 과 같은 값"]


def _fill_dart_events(m: NewMap, code: str, db, *, today: date, research_db: Path | None) -> list[str]:  # noqa: ANN001
    """공시확정 촉매를 A6.1 에 합친다. 실패는 전부 메모로 — 초안 생성을 막지 않는다."""
    try:
        from ..collect.adapters.dart import OpenDart, dart_api_key, dart_cache_dir, dart_events
        from .catalysts import merge_into_map

        key = dart_api_key(research_db)
        cache = dart_cache_dir()
        dart = OpenDart(key, cache_dir=cache) if key else None
        op_driver = next((n.id for n in m.drivers() if "영업이익" in n.label), None)
        r = dart_events(code, today=today, db=db, dart=dart, affects_ir=[op_driver] if op_driver else [])
        notes = [f"DART: {x}" for x in r.notes]
        if r.events:
            merged = merge_into_map(m, r.events, today=today)
            notes.append(f"A6.1 공시확정 촉매 {len(r.events)}건 ← DART ({r.source or 'db'})" + (" · " + "; ".join(merged) if merged else ""))
        notes += apply_completions(m, r.completions, today=today)
        m.meta["draft"]["dart_events"] = {"n": len(r.events), "items_seen": r.items_seen, "source": r.source, "notes": r.notes[:10]}
        return notes
    except Exception as e:  # noqa: BLE001
        return [f"DART 촉매 실패 (초안은 계속) — {type(e).__name__}: {str(e)[:80]}"]


def apply_completions(m: NewMap, completions: list[dict], *, today: date) -> list[str]:
    """「일어났다」 를 알리는 공시(증권발행실적보고서 등) 로 이벤트를 completed 로 바꾸고 실제 결과를 적는다 (2026-10-31).
    수익률(outcome 숫자) 과는 별개다 — 납입은 시세가 아니라 공시로 확인된다."""
    from ..models import EventOutcome, EventStatus

    notes: list[str] = []
    for c in completions:
        for n in m.children("A6.1"):
            e = n.event
            if e is None or e.event_id != c["event_id"] or e.status == EventStatus.completed and (e.outcome and e.outcome.actual):
                continue
            prev = e.outcome.model_dump() if e.outcome else {}
            prev["actual"] = c["note"]
            prev["as_of"] = prev.get("as_of") or today
            n.event = e.model_copy(update={"status": EventStatus.completed, "outcome": EventOutcome(**{k: v for k, v in prev.items() if v is not None})})
            n.as_of = today
            notes.append(f"{n.id} {e.fact[:20]}: 완료 — {c['note']}")
    return notes


def fill_event_outcomes(m: NewMap, db, *, today: date) -> list[str]:  # noqa: ANN001
    """지난 촉매의 D+1·D+5 수익률을 리서치 DB 시세로 채운다 (2026-10-30). 판정(verdict) 은 사람 몫 — 읽은 것만 메모에 적는다."""
    notes: list[str] = []
    if db is None:
        return notes
    for n in m.children("A6.1"):
        e = n.event
        if e is None or not e.is_dated() or not e.happened(today):
            continue
        if e.outcome is not None and e.outcome.reaction_d5 is not None:
            continue
        try:
            r = db.reaction(m.ticker, e.date)
        except Exception:  # noqa: BLE001
            r = None
        if not r or r.get("r1") is None:
            continue
        from ..models import EventOutcome

        rel = r.get("vs_market_d5")
        read = ""
        if rel is not None:
            read = "강화로 읽힌다" if rel >= 0.03 else "약화로 읽힌다" if rel <= -0.03 else "시장과 같이 움직였다"
            read = f"D+5 시장 대비 {rel:+.1%} — {read} (자동 · 판정은 사람이)"
        prev = e.outcome.model_dump() if e.outcome else {}
        prev.update({"reaction_d1": r.get("r1"), "reaction_d5": r.get("r5"), "vs_market_d5": rel, "as_of": today,
                     "note": read or prev.get("note"), "measured_from": r.get("measured_from")})
        from ..models import EventStatus

        n.event = e.model_copy(update={"outcome": EventOutcome(**{k: v for k, v in prev.items() if v is not None}), "status": EventStatus.completed})
        n.as_of = today
        notes.append(f"{n.id} {e.when_label()} {e.fact[:20]}: D+1 {r['r1']:+.1%}" + (f" · D+5 {r['r5']:+.1%}" if r.get("r5") is not None else " · D+5 아직"))
    return notes


def _enrich_from_db(m: NewMap, db, code: str, *, today: date, research_db: Path | None, digest: ReportDigest, notes: list[str]) -> bool:  # noqa: ANN001
    used = False
    try:
        pf = db.price_facts(code)
    except Exception:  # noqa: BLE001
        pf = None
    if pf is not None:
        src = pf.source(research_db or Path("research_db"))
        # 2026-10-30: 시세가 낡았으면 낡았다고 적는다 — 10-29 지도가 9-21 시세로 「추세 상승」 이라 말하고 있었다
        age = (today - pf.as_of).days
        stale = age > FLOW_STALE_DAYS
        if stale:
            # 2026-10-31 네 번째 검토: 낡은 시세로 계산한 「상승」 은 지금의 추세가 아니다 — 값 칸을 비우고 「분석 불가」 로 적는다.
            # 당시 계산값은 참고로 질문 칸과 meta.draft.flow_stale 에만 남긴다 (현재 판단엔 안 들어간다)
            was = []
            if pf.trend:
                was.append(f"추세 {pf.trend}")
            if pf.volume_ratio:
                was.append(f"거래량 20일 평균의 {pf.volume_ratio:.2f}배")
            why = f"분석 불가 — 시세가 {pf.as_of} ({age}일 전) 에서 멈춤 · 리서치 「주가 수집」 뒤 재생성"
            for nid in ("A6.2.1", "A6.2.2"):
                if m.value(nid) is None:
                    m[nid].status = NodeStatus.stale
                    m[nid].question = why + (f" · 당시 계산(참고): {' · '.join(was)}" if was else "")
                    m[nid].source, m[nid].as_of = src, pf.as_of
            m.meta.setdefault("draft", {})["flow_stale"] = {"as_of": pf.as_of.isoformat(), "age_days": age, "trend": pf.trend, "volume_ratio": pf.volume_ratio}
            used = True
        else:
            if pf.trend and m.value("A6.2.1") is None:
                m["A6.2.1"].set(pf.trend, as_of=pf.as_of, source=src)
                used = True
            if pf.volume_ratio and m.value("A6.2.2") is None:
                m["A6.2.2"].set(pf.volume_ratio, as_of=pf.as_of, source=src)
                used = True
        if pf.high_52w and pf.low_52w:
            m["A6.2"].question = (f"리서치 DB: {pf.as_of} 종가 {pf.close:,.0f} · 52주 {pf.low_52w:,.0f}~{pf.high_52w:,.0f}"
                                  + (f" · 1개월 {pf.ret_1m:+.1%}" if pf.ret_1m is not None else "")
                                  + (f" ⚠ {age}일 전 시세 — 추세·거래량은 분석 불가, 리서치 「주가 수집」 을 돌릴 것" if stale else ""))
            used = True
        if stale:
            m["A6.2"].tags = sorted(set(m["A6.2"].tags) | {"warn"})
            notes.append(f"A6.2 수급/기술: 리서치 DB 시세가 {age}일 전({pf.as_of}) — 추세·거래량을 비우고 분석 불가로 표시 (당시 계산은 참고로만)")
    try:
        targets = db.targets(code, days=90, as_of=today)
    except Exception:  # noqa: BLE001
        targets = []
    if targets:
        tps = [t.tp for t in targets]
        line = f"리서치 DB 최근 90일 목표가 {len(tps)}건 {min(tps):,.0f}~{max(tps):,.0f}, 평균 {sum(tps) / len(tps):,.0f}"
        m["A3.5"].question = (m["A3.5"].question + " / " + line).strip(" /")
        m.meta["draft"]["db_consensus"] = {"n": len(tps), "min": min(tps), "max": max(tps), "mean": sum(tps) / len(tps)}
        if digest.n_tp < 3:
            notes.append(f"리포트 목표가가 {digest.n_tp}건뿐 — {line} 를 참고하라 (앵커는 리포트 기준)")
        used = True
    return used


__all__ = ["DraftResult", "DraftError", "draft_from_reports", "resolve_price", "latest_price", "PricePick", "PRICE_STALE_DAYS",
           "FLOW_STALE_DAYS", "fill_event_outcomes", "apply_completions"]
