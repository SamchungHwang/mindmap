"""화면이 부르는 조작 계층 — UI 에 의존하지 않는다.

GUI 콘솔(`console.py`)과 텍스트 콘솔(`console_text.py`)이 같은 함수를 부른다. 폼 값은 전부
문자열/dict 로 받아 여기서 숫자·날짜로 바꾸고, 엔진(`engine`)과 저장소(`store`)를 호출한다.
화면 코드에는 계산도 검증도 두지 않는다 — tkinter 가 없는 환경에서도 이 파일은 통째로 테스트된다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .engine import (
    HoldingEval,
    NewMapEval,
    apply_event,
    close_holding,
    evaluate_holding,
    evaluate_new_map,
    reconstruct_holding,
    record_decision,
    rollup,
    set_thesis_state,
    snapshot_to_holding,
    update_driver,
    update_market,
    validate,
)
from .models import (
    Action,
    BiasCheck,
    Driver,
    Event,
    HoldingMap,
    MapKind,
    MarketState,
    Measurement,
    MindMap,
    NewMap,
    NodeStatus,
    Portfolio,
    Position,
    Rule,
    Scenario,
    Source,
)
from .render import to_markdown, write_html
from .store import Store, WatchItem
from .symbols import DEFAULT_CSV, Resolution, Symbol, SymbolBook, default_book, normalize_code, refresh_csv
from .templates import new_map_skeleton

LIGHT_ICON = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}
HORIZONS = ("3M", "1Y", "3Y+")
CONFIDENCES = ("H", "M", "L")
INVEST_TYPES = ("growth", "value", "turnaround", "momentum", "event")
WHY_WRONG = ("시간축", "정보", "심리", "구조")
REVISIONS = ("up", "flat", "down")
DECISIONS = ("BUY", "WATCH", "PASS")
THESIS_STATES = ("강화", "유효", "약화", "무효", "변질")
HOLD_REASONS = ("세금", "유동성", "촉매 임박", "없음")
ACTIONS = ("add", "hold", "trim", "switch", "exit", "hedge")
EVENT_DIRECTIONS = ("강화", "약화", "중립")
PRICED_IN = ("예", "부분", "아니오")
MATERIALITY = ("H", "M", "L")
OPERATORS = ("<", "<=", ">", ">=")
BIAS_FIELDS = (
    ("anchoring", "매입가/본전에 앵커링하고 있나"),
    ("sunk_cost", "매몰비용 때문에 들고 있나"),
    ("disposition", "이익 종목이라 팔기 쉬운 쪽으로 기울었나 (처분효과)"),
    ("endowment", "이 종목을 안 갖고 있었다면 지금 이 뷰가 아닐 것 같나 (보유효과)"),
    ("confirmation", "좋은 뉴스만 찾아 읽었나 (확증편향)"),
    ("breakeven_plan", "'본전 오면 판다'가 계획의 일부인가"),
)


class OpsError(ValueError):
    """사람이 고칠 수 있는 입력 오류. 화면은 이 메시지를 그대로 보여 준다."""


class AmbiguousSymbol(OpsError):
    """이름으로 찾았는데 후보가 여럿. 화면은 `candidates` 로 고르게 한다."""

    def __init__(self, query: str, candidates: list[Symbol]):
        self.query = query
        self.candidates = candidates
        names = ", ".join(c.label() for c in candidates[:6]) + (" …" if len(candidates) > 6 else "")
        super().__init__(f"'{query}' 에 해당하는 종목이 여럿입니다 — {names}")


# ---------------------------------------------------------------------------- 파싱
def num(value: Any, *, name: str = "값", required: bool = False) -> float | None:
    """'1,234', '12.5%', ' 3 ' → float. 비어 있으면 None (required 면 OpsError)."""
    if value is None:
        if required:
            raise OpsError(f"{name}이(가) 필요합니다")
        return None
    s = str(value).strip().replace(",", "").rstrip("%").strip()
    if s == "":
        if required:
            raise OpsError(f"{name}이(가) 필요합니다")
        return None
    try:
        return float(s)
    except ValueError as e:
        raise OpsError(f"{name}: 숫자가 아닙니다 — {value!r}") from e


def pct(value: Any, *, name: str = "비중", required: bool = False) -> float | None:
    """퍼센트 단위 입력('7.2' 또는 '7.2%') → 0.072."""
    v = num(value, name=name, required=required)
    return None if v is None else v / 100.0


def when(value: Any, *, name: str = "날짜", default: date | None = None) -> date | None:
    if value is None or str(value).strip() == "":
        return default
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as e:
        raise OpsError(f"{name}: YYYY-MM-DD 형식이어야 합니다 — {value!r}") from e


def text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def listing(value: Any) -> list[str]:
    """'a, b; c' → ['a', 'b', 'c']"""
    if value is None:
        return []
    return [x.strip() for x in str(value).replace(";", ",").split(",") if x.strip()]


# ---------------------------------------------------------------------------- 컨텍스트
@dataclass
class Ops:
    store: Store
    today: date | None = None
    log_lines: list[str] = field(default_factory=list)
    symbols: SymbolBook | None = None  # 지연 로드. 테스트는 직접 넣는다.

    @property
    def now(self) -> date:
        return self.today or date.today()

    def log(self, line: str) -> None:
        self.log_lines.append(line)

    # ------------------------------------------------------------------ 종목 찾기
    @property
    def book(self) -> SymbolBook:
        if self.symbols is None:
            self.symbols = default_book(self.store)
            self.log(f"종목 목록 {len(self.symbols)}개 ({', '.join(self.symbols.sources) or '출처 없음'})")
        return self.symbols

    def reload_symbols(self) -> SymbolBook:
        self.symbols = None
        return self.book

    def refresh_symbols_csv(self) -> tuple[Path, int]:
        """리서치 DB → data/ref/symbols.csv 스냅숏, 그리고 목록 다시 읽기."""
        p, n = refresh_csv(self.store, csv_path=DEFAULT_CSV)
        self.log(f"종목 목록 스냅숏 {n}개 → {p}")
        self.reload_symbols()
        return p, n

    def resolve_symbol(self, query: str | None) -> Resolution:
        return self.book.resolve(query)

    def complete_symbol(self, ticker: str | None, name: str | None) -> tuple[str, str, Symbol | None]:
        """코드·이름 중 하나만 있어도 나머지를 채운다. 못 찾으면 OpsError, 여럿이면 AmbiguousSymbol."""
        t, n = text(ticker), text(name)
        if t and not normalize_code(t) and not n:
            # 코드 칸에 이름을 적은 경우도 받아 준다
            t, n = None, t
        if t:
            code = normalize_code(t)
            if code is None:
                raise OpsError(f"종목코드는 숫자 6자리여야 합니다 — {t!r}")
            sym = self.book.by_code(code)
            if sym is None:
                w = self.store.get_watch(code)
                if w is not None:
                    sym = Symbol(w.ticker, w.name, w.market, w.sector)
            if n:
                return code, n, sym
            if sym is None:
                raise OpsError(f"{code} 의 종목명을 목록에서 찾지 못했습니다 — 종목명을 함께 적어 주세요")
            return code, sym.name, sym
        if n:
            r = self.book.resolve(n)
            if r.status == "exact" and r.one:
                return r.one.code, r.one.name, r.one
            if r.status == "many":
                raise AmbiguousSymbol(n, r.candidates)
            raise OpsError(f"'{n}' 을(를) 종목 목록에서 찾지 못했습니다 — 종목코드를 함께 적어 주세요")
        raise OpsError("종목코드 또는 종목명을 적어 주세요")

    # ------------------------------------------------------------------ 관심종목
    def add_watch(self, f: dict[str, Any]) -> WatchItem:
        code, name, sym = self.complete_symbol(f.get("ticker"), f.get("name"))
        sector = text(f.get("sector")) or (sym.sector if sym else None)
        market = text(f.get("market")) or (sym.market if sym else None) or "KR"
        it = self.store.add_watch(code, name, market=market, sector=sector,
                                  invest_type=text(f.get("invest_type")) or None, note=text(f.get("note")))
        self.book.extend([Symbol(it.ticker, it.name, it.market, it.sector)], "watchlist")  # 바로 다시 찾히게
        self.log(f"관심종목 추가/갱신 {it.ticker} {it.name}")
        return it

    def update_watch(self, ticker: str, f: dict[str, Any]) -> WatchItem | None:
        return self.store.update_watch(ticker, **{k: text(v) for k, v in f.items() if k in ("name", "market", "sector", "invest_type", "note")})

    # ------------------------------------------------------------------ 표·요약
    def fresh_line(self) -> str:
        s = self.store.summary()
        li = s["lights"]
        nxt = self.next_check()
        parts = [f"관심종목 {s['watchlist']}", f"A 맵 {s['maps'].get('NEW', 0)}",
                 f"B 맵 {s['maps'].get('HOLD', 0)} (🟢{li['GREEN']} 🟡{li['YELLOW']} 🔴{li['RED']})"]
        if nxt:
            d, tk_ = nxt
            delta = (d - self.now).days
            parts.append(f"가장 가까운 확인일 {d.isoformat()} ({'D' + ('+' if delta < 0 else '-') + str(abs(delta))}, {tk_})")
        c = self.store.portfolio()
        parts.append(f"C 맵 {c.updated_at.isoformat()}" if c else "C 맵 없음")
        return " · ".join(parts)

    def next_check(self) -> tuple[date, str] | None:
        best: tuple[date, str] | None = None
        for b in self.store.holdings():
            for n in b.drivers():
                if n.driver and n.driver.next_check and (best is None or n.driver.next_check < best[0]):
                    best = (n.driver.next_check, b.ticker or "")
        return best

    def table_rows(self, *, filter_key: str = "all") -> list[dict[str, Any]]:
        """관심종목 + 맵 상태를 한 줄에. filter_key: all | watch | a | b | attention"""
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        a_rows = {r.ticker: r for r in self.store.list_maps(MapKind.NEW) if r.ticker}
        b_rows = {r.ticker: r for r in self.store.list_maps(MapKind.HOLD) if r.ticker}
        for it in self.store.list_watch():
            seen.add(it.ticker)
            rows.append(self._row(it.ticker, it.name, it, a_rows.get(it.ticker), b_rows.get(it.ticker)))
        for t, r in {**a_rows, **b_rows}.items():  # 관심종목에서 빠졌지만 맵이 있는 종목
            if t not in seen:
                rows.append(self._row(t, r.name or "", None, a_rows.get(t), b_rows.get(t)))
        if filter_key == "watch":
            rows = [r for r in rows if r["watch"]]
        elif filter_key == "a":
            rows = [r for r in rows if r["a_id"]]
        elif filter_key == "b":
            rows = [r for r in rows if r["b_id"]]
        elif filter_key == "attention":
            rows = [r for r in rows if r["light"] in ("YELLOW", "RED")]
        return rows

    def _row(self, ticker: str, name: str, it: WatchItem | None, a: Any, b: Any) -> dict[str, Any]:
        row: dict[str, Any] = {
            "ticker": ticker, "name": name, "market": it.market if it else "", "sector": it.sector if it else "",
            "invest_type": it.invest_type if it else "", "note": it.note if it else "", "watch": it is not None,
            "a_id": a.map_id if a else None, "a_status": a.status if a else "",
            "b_id": b.map_id if b else None, "light": b.status if b else "", "thesis": "", "weight": "",
            "decision": "", "next_check": "",
        }
        if b:
            m = self.store.load_map(b.map_id)
            if isinstance(m, HoldingMap):
                row["thesis"] = m.value("B2.3") or ""
                row["weight"] = f"{m.market.w_now:.1%}" if m.market.w_now is not None else ""
                row["decision"] = m.value("B6") or (f"({m.meta['matrix_default']})" if m.meta.get("matrix_default") else "")
                nxt = sorted(d.driver.next_check for d in m.drivers() if d.driver and d.driver.next_check)
                row["next_check"] = nxt[0].isoformat() if nxt else ""
        return row

    def basis_text(self, m: NewMap) -> str:
        """회계 기준 표 (설계 v6 §11.1 / v7 T1) — 콘솔 「E」."""
        from .engine.valuation import basis_table

        return basis_table((m.meta.get("draft") or {}).get("financials") or {})

    def overview_text(self, ticker: str) -> str:
        it = self.store.get_watch(ticker)
        a = self.store.latest_map(ticker, MapKind.NEW)
        b = self.store.latest_map(ticker, MapKind.HOLD)
        out: list[str] = []
        if it:
            out.append(f"{it.ticker} {it.name} · {it.market} · 섹터 {it.sector or '-'} · 유형 {it.invest_type or '-'}")
            if it.note:
                out.append(f"메모: {it.note}")
        else:
            out.append(f"{ticker} — 관심종목에 없음 (맵만 있음)")
        out.append("")
        if isinstance(a, NewMap):
            out.append(f"[A 신규 투자 맵] {a.map_id} · 판단 {a.decision.value if a.decision else '작성중'}")
            out.append(f"  논제: {a.thesis or '(비어 있음)'}")
            er, rr = a.value("A4.5"), a.value("A4.6")
            w = a["A7.1"].value if isinstance(a["A7.1"].value, dict) else {}
            out.append(f"  기대수익률 {er:+.1%} · 손익비 {rr}" if er is not None else "  기대수익률 미계산")
            if w:
                out.append(f"  비중 제안 {w.get('suggested', 0):.2%} → 확정 {w.get('confirmed', 0) or 0:.2%}")
            issues = validate(a, today=self.now)
            errs = [i for i in issues if i.level == "error"]
            out.append(f"  검증: 오류 {len(errs)} / 경고 {len(issues) - len(errs)}" + (" — " + "; ".join(i.message for i in errs[:3]) if errs else ""))
            fin = (a.meta.get("draft") or {}).get("financials") or {}
            if fin.get("basis"):
                from .engine.valuation import basis_summary

                out.append("  회계 기준: " + basis_summary(fin))
            dm = a.meta.get("draft") or {}
            if dm.get("lights") is not None:
                from .engine.evidence import light_counts_text

                fl = a.meta.get("flags") or {}
                gate = dm.get("claim_gate") or {}
                out.append("  증거·판단(v7): " + light_counts_text(dm.get("lights") or {})
                           + (" · 신규 BUY 보류" if fl.get("hold_new_buy") else "") + (" · 재검토" if fl.get("review") else "")
                           + (" · 증거 없는 판단" if fl.get("no_evidence") else "") + (f" · 제안 대기 {fl.get('pending_proposals')}" if fl.get("pending_proposals") else "")
                           + (" · ⚠ Claim 품질 게이트 미달" if gate.get("status") == "needs_claim_review" else ""))
                sched = dm.get("evidence_schedule") or []
                if sched:
                    out.append("  다음 증거: " + " · ".join(f"{x['date']} {x['what'][:16]}" for x in sched[:3]))
        else:
            out.append("[A 신규 투자 맵] 없음 — 「작업 → A 맵 생성」")
        out.append("")
        if isinstance(b, HoldingMap):
            light = b.value("B0")
            out.append(f"[B 보유 종목 맵] {b.map_id} · {LIGHT_ICON.get(light, '')} {light or '미평가'} · 논제 {b.value('B2.3') or '미판정'} · 복원 {b.value('B1.5')}")
            if b.position:
                out.append(f"  포지션 {b.position.quantity:,.0f}주 · 목표 비중 {b.position.w_target:.1%} · 매수일 {b.position.entry_date}")
            mk = b.market
            out.append(f"  시장: 가격 {mk.price or '-'} · 비중 {f'{mk.w_now:.1%}' if mk.w_now is not None else '-'} · 밴드 {mk.band_percentile or '-'}%ile · 기준일 {mk.as_of or '-'}")
            for n in b.drivers():
                d = n.driver
                assert d is not None
                out.append(f"  - {n.label}: 매수시 {d.at_entry} → 현재 {d.current.value} / 추정 {d.my_estimate.value} ⟨{d.direction.value if d.direction else '-'}⟩ 확인 {d.next_check or '-'}")
            if b.meta.get("matrix_default"):
                out.append(f"  매트릭스 기본 대응: {b.meta['matrix_default']} [{b.meta.get('matrix_rule', '')}] · 확정 {b.value('B6') or '-'}")
            dec = b.value("B2.2")
            if isinstance(dec, dict) and dec.get("consistent"):
                out.append(f"  주가 분해: 총 {dec['r_total']:+.1%} = {dec['metric']} {dec['d_metric']:+.1%} × 멀티플 {dec['d_multiple']:+.1%} — {dec['interpretation']}")
            if b.decision_log:
                last = b.decision_log[-1]
                out.append(f"  마지막 결정: {last.date} {last.decision.value} {last.size or ''} — {last.reason}")
        else:
            out.append("[B 보유 종목 맵] 없음 — A 맵에서 「매수 체결 → B 맵」 또는 「논제 복원 위자드」")
        return "\n".join(out)

    def markdown_of(self, m: MindMap, *, summary: bool = False) -> str:
        return to_markdown(m, summary=summary)

    # ------------------------------------------------------------------ A 맵
    def create_new_map(self, ticker: str, *, n_drivers: int = 3) -> NewMap:
        it = self.store.get_watch(ticker)
        if it is None:
            raise OpsError(f"{ticker} 는 관심종목에 없습니다. 먼저 추가하세요")
        m = new_map_skeleton(it.ticker, it.name, it.invest_type or "growth", self.now, n_drivers=max(3, min(5, n_drivers)))
        if it.sector:
            m["A9.2"].links = [f"S.{it.sector}"]
        m["A9.3"].links = ["M0"]
        self.store.save_map(m)
        self.log(f"A 맵 생성 {m.map_id}")
        return m

    def a_form(self, m: NewMap) -> dict[str, Any]:
        """NewMap → 화면 폼 값 (모두 문자열/리스트)."""
        def s(v: Any) -> str:
            return "" if v is None else str(v)
        w = m["A7.1"].value if isinstance(m["A7.1"].value, dict) else {}
        ep = m.value("A7.2") if isinstance(m.value("A7.2"), dict) else {}
        pr = ep.get("price_range") or [None, None]
        drivers = []
        for n in m.children("A1"):
            d = n.driver
            drivers.append({
                "id": n.id, "label": n.label if d else "", "unit": s(n.unit),
                "current": s(d.current.value) if d else "", "source": s(d.current.source.name) if d and d.current.source else "",
                "consensus": s(d.consensus.value) if d else "", "my_estimate": s(d.my_estimate.value) if d else "",
                "rationale": s(d.my_estimate.rationale) if d else "", "check_indicator": s(d.check_indicator) if d else "",
                "next_check": s(d.next_check) if d and d.next_check else "", "higher_is_better": d.higher_is_better if d else True,
            })
        scen = {}
        for nid, nm in (("A4.2", "Bear"), ("A4.3", "Base"), ("A4.4", "Bull")):
            sc = m[nid].scenario
            scen[nm] = {"probability": s(sc.probability) if sc else "", "target_price": s(sc.target_price) if sc else "",
                        "assumptions": ", ".join(sc.assumptions) if sc else "", "horizon": s(sc.horizon_months) if sc else "12",
                        "method": sc.valuation_method if sc else ""}
        risks = []
        for nid in ("A5.1", "A5.2", "A5.3"):
            v = m.value(nid) if isinstance(m.value(nid), dict) else {}
            risks.append({"text": s(v.get("text")), "probability": s(v.get("probability")), "impact_pct": s(v.get("impact_pct"))})
        kill = m["A5.4.1"].rule
        return {
            "thesis": s(m.value("A0")), "horizon": s(m.value("A0.2")) or "1Y", "confidence": s(m.value("A0.3")) or "M",
            "price": s(m.value("A0.4")), "price_source": s(m["A0.4"].source.name) if m["A0.4"].source else "KRX 종가",
            "drivers": drivers,
            "implied": s(m.value("A2.1")), "mine": s(m.value("A2.2")), "why": s(m.value("A2.3")),
            "scenarios": scen, "band": s(m.value("A3.5.1")), "revision": s(m.value("A3.5.3")) or "flat",
            "risks": risks, "kill_condition": kill.condition if kill else "", "kill_note": s(kill.note) if kill else "",
            "early_warning": s(m.value("A5.5")),
            "w_suggested": s(w.get("suggested")), "w_confirmed": "" if w.get("confirmed") is None else f"{w['confirmed'] * 100:g}",
            "w_reason": s(w.get("reason")), "entry_lo": s(pr[0]), "entry_hi": s(pr[1]), "tranches": s(ep.get("tranches") or 3),
            "stop_dd": _dd_from_rule(m["A7.3"].rule), "tp_price": _price_from_rule(m["A7.4"].rule),
            "tp_size": s(m["A7.4"].rule.size) if m["A7.4"].rule else "50%", "decision": s(m.value("A7.5")) or "WATCH",
        }

    def apply_a_form(self, m: NewMap, f: dict[str, Any], *, evaluate: bool = True) -> list[str]:
        """폼 값 → NewMap. 저장까지 하고 검증 메시지 목록을 돌려준다."""
        t = self.now
        if text(f.get("thesis")):
            m["A0"].set(text(f["thesis"]), as_of=t)
        if text(f.get("horizon")) in HORIZONS:
            m["A0.2"].set(f["horizon"], as_of=t)
        if text(f.get("confidence")) in CONFIDENCES:
            m["A0.3"].set(f["confidence"], as_of=t)
        p = num(f.get("price"), name="현재가")
        if p is not None:
            m["A0.4"].set(p, as_of=t, source=Source(name=text(f.get("price_source")) or "KRX", retrieved_at=t))
        # 드라이버
        for df in f.get("drivers", []):
            nid = df.get("id")
            if not nid or nid not in m:
                continue
            n = m[nid]
            label = text(df.get("label"))
            if not label:
                n.driver = None
                continue
            d = n.driver or Driver()
            n.label = label
            n.unit = text(df.get("unit"))
            cur = num(df.get("current"), name=f"{label} 현재값")
            if cur is not None:
                src = text(df.get("source"))
                d.current = Measurement(value=cur, as_of=d.current.as_of or t,
                                        source=Source(name=src, retrieved_at=t) if src else d.current.source)
            cons = num(df.get("consensus"), name=f"{label} 컨센서스")
            d.consensus = Measurement(value=cons, as_of=t) if cons is not None else Measurement()
            my = num(df.get("my_estimate"), name=f"{label} 내 추정")
            if my is not None:
                d.my_estimate = Measurement(value=my, as_of=t, rationale=text(df.get("rationale")))
            d.check_indicator = text(df.get("check_indicator")) or ""
            d.next_check = when(df.get("next_check"), name=f"{label} 다음 확인일")
            d.higher_is_better = bool(df.get("higher_is_better", True))
            n.driver = d
            n.as_of = t
        # 뷰
        for key, nid in (("implied", "A2.1"), ("mine", "A2.2")):
            if text(f.get(key)):
                m[nid].set(text(f[key]), as_of=t)
        if text(f.get("why")) in WHY_WRONG:
            m["A2.3"].set(f["why"], as_of=t)
        # 시나리오
        for nm, nid in (("Bear", "A4.2"), ("Base", "A4.3"), ("Bull", "A4.4")):
            sf = (f.get("scenarios") or {}).get(nm) or {}
            prob = num(sf.get("probability"), name=f"{nm} 확률")
            tp = num(sf.get("target_price"), name=f"{nm} 목표가")
            if prob is None or tp is None:
                continue
            m[nid].scenario = Scenario(name=nm, probability=prob, target_price=tp, assumptions=listing(sf.get("assumptions")),
                                       horizon_months=int(num(sf.get("horizon"), name="기간") or 12),
                                       valuation_method=text(sf.get("method")) or "")
            m[nid].as_of = t
        band = num(f.get("band"), name="밴드 백분위")
        if band is not None:
            m["A3.5.1"].set(band, as_of=t, source=Source(name="밸류 밴드", retrieved_at=t))
        if text(f.get("revision")) in REVISIONS:
            m["A3.5.3"].set(f["revision"], as_of=t, source=Source(name="컨센서스", retrieved_at=t))
        # 리스크
        for i, nid in enumerate(("A5.1", "A5.2", "A5.3")):
            rf = (f.get("risks") or [{}] * 3)[i] if i < len(f.get("risks") or []) else {}
            rt = text(rf.get("text"))
            if not rt:
                continue
            m[nid].set({"probability": num(rf.get("probability"), name="리스크 확률") or 0.2,
                        "impact_pct": num(rf.get("impact_pct"), name="리스크 영향") or -20, "text": rt}, as_of=t)
        cond = text(f.get("kill_condition"))
        if cond:
            m["A5.4.1"].rule = Rule(trigger_type="fact", condition=cond, action=Action.exit, escalate_to="RED",
                                    note=text(f.get("kill_note")))
            m["A5.4.1"].as_of = t
        if text(f.get("early_warning")):
            m["A5.5"].set(text(f["early_warning"]), as_of=t, source=Source(name="직접 입력", retrieved_at=t))
        # 실행 계획
        wc = pct(f.get("w_confirmed"), name="확정 비중")
        cur_w = m["A7.1"].value if isinstance(m["A7.1"].value, dict) else {}
        if wc is not None:
            m["A7.1"].value = {**cur_w, "confirmed": round(wc, 4), "reason": text(f.get("w_reason"))}
        lo, hi = num(f.get("entry_lo"), name="진입 하단"), num(f.get("entry_hi"), name="진입 상단")
        if lo is not None and hi is not None:
            m["A7.2"].set({"price_range": [lo, hi], "tranches": int(num(f.get("tranches"), name="분할") or 1)}, as_of=t)
        dd = pct(f.get("stop_dd"), name="손절 하락률")
        if dd is not None:
            m["A7.3"].rule = Rule(trigger_type="price", condition=f"drawdown > {dd:g}", action=Action.review, escalate_to="YELLOW")
        tpp = num(f.get("tp_price"), name="익절 가격")
        if tpp is not None:
            m["A7.4"].rule = Rule(trigger_type="price", condition=f"price > {tpp:g}", action=Action.trim, size=text(f.get("tp_size")) or "50%")
        if text(f.get("decision")) in DECISIONS:
            m["A7.5"].set(f["decision"], as_of=t)
        if evaluate:
            evaluate_new_map(m, today=t, enforce_gate=True)
        self.store.save_map(m)
        issues = validate(m, today=t)
        self.log(f"A 맵 저장 {m.map_id} · 검증 오류 {sum(1 for i in issues if i.level == 'error')}")
        return [str(i) for i in issues]

    def build_condition(self, m: MindMap, driver_id: str, op: str, threshold: Any) -> str:
        if op not in OPERATORS:
            raise OpsError(f"연산자 {op!r}")
        thr = num(threshold, name="임계값", required=True)
        n = m.get(driver_id)
        did = driver_id
        if n is not None and n.id.startswith("B2.1.") and n.links:
            did = n.links[0]
        return f"driver[{did}].current {op} {thr:g}"

    def evaluate_a(self, m: NewMap) -> tuple[NewMapEval, list[str]]:
        res = evaluate_new_map(m, today=self.now)
        self.store.save_map(m)
        issues = validate(m, today=self.now)
        return res, [str(i) for i in issues]

    def eval_a_text(self, m: NewMap) -> str:
        res, issues = self.evaluate_a(m)
        lines = []
        lines.append(f"기대수익률 {res.expected_return:+.1%}" if res.expected_return is not None else "기대수익률 계산 불가 — 시나리오 3개와 현재가가 필요")
        lines.append(f"손익비 {res.risk_reward:.2f}" if res.risk_reward is not None else "손익비 계산 불가 (하락위험 0 또는 시나리오 누락)")
        if res.suggested_weight is not None:
            lines.append(f"제안 비중 {res.suggested_weight:.2%}  (w_base × f_conf × f_rr)")
        if res.rr_gate_passed is not None:
            lines.append(f"rr_min 게이트 {'통과' if res.rr_gate_passed else '미통과'} · 판단 {m.decision.value if m.decision else '-'}")
        lines += ["! " + n for n in res.notes]
        if res.checkpoints:
            lines.append("체크포인트: " + ", ".join(d.isoformat() for d in res.checkpoints))
        errs = [i for i in issues if i.startswith("[ERR")]
        lines.append("")
        lines.append(f"검증: 오류 {len(errs)} / 경고 {len(issues) - len(errs)}" + (" → 완료(판단 가능)" if not errs else ""))
        lines += ["  " + i for i in issues]
        return "\n".join(lines)

    def snapshot(self, m: NewMap, f: dict[str, Any]) -> HoldingMap:
        entry_date = when(f.get("entry_date"), name="체결일", default=self.now) or self.now
        price = num(f.get("entry_price"), name="체결가", required=True)
        qty = num(f.get("quantity"), name="수량", required=True)
        w = pct(f.get("w_target"), name="목표 비중")
        if w is None:
            v = m["A7.1"].value if isinstance(m["A7.1"].value, dict) else {}
            w = v.get("confirmed")
        if w is None:
            raise OpsError("목표 비중이 필요합니다 (A7.1 확정 비중 또는 직접 입력)")
        pos = Position(entry_date=entry_date, entry_price=price or 0, quantity=qty or 0, w_target=w,
                       eps_fwd_entry=num(f.get("eps_fwd"), name="forward EPS"))
        b = snapshot_to_holding(m, pos, today=entry_date)
        self.store.save_map(b)
        if m.decision is None or m.decision.value != "BUY":
            m["A7.5"].set("BUY", as_of=entry_date)
            self.store.save_map(m)
        self.log(f"B 맵 생성 {b.map_id} (스냅숏 {b.snapshot_hash})")
        return b

    # ------------------------------------------------------------------ B 맵
    def b_market(self, b: HoldingMap, f: dict[str, Any]) -> None:
        mk = b.market
        ms = MarketState(
            as_of=when(f.get("as_of"), name="기준일", default=self.now),
            price=num(f.get("price"), name="현재가", required=True),
            w_now=pct(f.get("w_now"), name="현재 비중") if text(f.get("w_now")) else mk.w_now,
            eps_fwd=num(f.get("eps_fwd"), name="forward EPS") if text(f.get("eps_fwd")) else mk.eps_fwd,
            band_percentile=num(f.get("band"), name="밴드 백분위") if text(f.get("band")) else mk.band_percentile,
            total_assets=num(f.get("total_assets"), name="총자산") if text(f.get("total_assets")) else mk.total_assets,
            adv_shares=num(f.get("adv"), name="일평균 거래량") if text(f.get("adv")) else mk.adv_shares,
            cash_yield=num(f.get("cash_yield"), name="현금 수익률") if text(f.get("cash_yield")) else (mk.cash_yield if mk.cash_yield is not None else 0.03),
        )
        update_market(b, ms)
        if ms.total_assets:
            self.store.set_setting("total_assets", ms.total_assets)
        self.store.save_map(b)
        self.log(f"시장 상태 갱신 {b.ticker} 가격 {ms.price}")

    def b_driver(self, b: HoldingMap, driver_id: str, f: dict[str, Any]) -> str:
        val = num(f.get("value"), name="현재값", required=True)
        as_of = when(f.get("as_of"), name="기준일", default=self.now) or self.now
        src = text(f.get("source"))
        dirn = update_driver(b, driver_id, val or 0.0, as_of=as_of, source=Source(name=src, retrieved_at=as_of) if src else None)
        n = b[driver_id]
        nc = when(f.get("next_check"), name="다음 확인일")
        if nc and n.driver:
            n.driver.next_check = nc
        my = num(f.get("my_estimate"), name="내 추정")
        if my is not None and n.driver:
            n.driver.my_estimate = Measurement(value=my, as_of=as_of, rationale=n.driver.my_estimate.rationale)
            n.driver.direction = n.driver.compute_direction()
        self.store.save_map(b)
        self.log(f"드라이버 갱신 {b.ticker} {n.label} → {val} ({dirn.value if dirn else '-'})")
        return dirn.value if dirn else "-"

    def b_event(self, b: HoldingMap, f: dict[str, Any]) -> Event:
        fact = text(f.get("fact"))
        if not fact:
            raise OpsError("사실(한 문장)이 필요합니다")
        affects = [a for a in (f.get("affects") or []) if a in b.nodes]
        ev = Event(date=when(f.get("date"), name="날짜", default=self.now) or self.now, fact=fact, affects=affects,
                   direction=f.get("direction") or "중립", priced_in=f.get("priced_in") or "아니오",
                   materiality=f.get("materiality") or "M",
                   source=Source(name=text(f.get("source")) or "공시", url=text(f.get("url")) or "", retrieved_at=self.now))
        apply_event(b, ev)
        self.store.save_map(b)
        self.log(f"사실 로그 {b.ticker}: {fact[:40]}")
        return ev

    def b_thesis(self, b: HoldingMap, f: dict[str, Any]) -> None:
        st = text(f.get("state"))
        if st not in THESIS_STATES:
            raise OpsError("논제 상태를 고르세요")
        set_thesis_state(b, st, today=self.now, note=text(f.get("note")) or "")
        if "buy_again" in f:
            b["B0.1"].set(bool(f["buy_again"]), as_of=self.now)
            if not f["buy_again"] and text(f.get("hold_reason")) in HOLD_REASONS:
                b["B0.2"].set(f["hold_reason"], as_of=self.now)
        self.store.save_map(b)
        self.log(f"논제 판정 {b.ticker}: {st}")

    def b_evaluate(self, b: HoldingMap) -> HoldingEval:
        res = evaluate_holding(b, today=self.now)
        self.store.save_map(b)
        self.log(f"평가 {b.ticker}: {res.light.light.value} / 매트릭스 {res.matrix.action.value}")
        return res

    def b_decide(self, b: HoldingMap, f: dict[str, Any]) -> str:
        act = text(f.get("action"))
        if act not in ACTIONS:
            raise OpsError("대응을 고르세요")
        bc = BiasCheck(**{k: bool(f.get(k)) for k, _ in BIAS_FIELDS}, note=text(f.get("bias_note")))
        try:
            log = record_decision(b, act, today=self.now, reason=text(f.get("reason")) or "", size=text(f.get("size")),
                                  bias_check=bc)
        except ValueError as e:
            raise OpsError(str(e)) from e
        self.store.save_map(b)
        self.log(f"결정 {b.ticker}: {log.decision.value}{' (오버라이드)' if log.override else ''}")
        return f"{log.decision.value}{' (오버라이드)' if log.override else ''} · 편향 {', '.join(bc.flagged()) or '없음'}"

    def b_close(self, b: HoldingMap, f: dict[str, Any]) -> None:
        tc = {"y": True, "n": False}.get(str(f.get("thesis_correct", "p")), None)
        close_holding(b, today=self.now, thesis_correct=tc, wrong_assumption=text(f.get("wrong_assumption")) or "",
                      biases=listing(f.get("biases")), lesson=text(f.get("lesson")) or "")
        self.store.save_map(b)
        self.log(f"종료 {b.ticker}")

    def reconstruct(self, f: dict[str, Any]) -> HoldingMap:
        ticker, name = text(f.get("ticker")), text(f.get("name"))
        if not ticker or not name:
            raise OpsError("종목코드와 종목명이 필요합니다")
        inv = text(f.get("invest_type")) or "growth"
        if self.store.get_watch(ticker) is None:
            self.store.add_watch(ticker, name, invest_type=inv)
        pos = Position(entry_date=when(f.get("entry_date"), name="매수일", default=self.now) or self.now,
                       entry_price=num(f.get("entry_price"), name="매입 단가", required=True) or 0,
                       quantity=num(f.get("quantity"), name="수량", required=True) or 0,
                       w_target=pct(f.get("w_target"), name="목표 비중") or 0.05)
        drivers = []
        for df in f.get("drivers") or []:
            if not text(df.get("label")):
                continue
            drivers.append({"label": text(df["label"]), "current": num(df.get("current"), name="현재값"),
                            "my_estimate": num(df.get("my_estimate"), name="내 추정"),
                            "at_entry": num(df.get("at_entry"), name="매수 당시 값"),
                            "check_indicator": text(df.get("check_indicator")) or "",
                            "next_check": when(df.get("next_check"), name="다음 확인일")})
        kills = [c for c in [text(f.get("kill_condition"))] if c]
        b = reconstruct_holding(ticker, name, pos, invest_type=inv, thesis=text(f.get("thesis")), drivers=drivers,
                                kill_conditions=kills, today=self.now,
                                market=MarketState(as_of=self.now, price=num(f.get("price"), name="현재가"),
                                                   w_now=pct(f.get("w_now"), name="현재 비중")))
        self.store.save_map(b)
        self.log(f"논제 복원 {b.map_id} ({b.value('B1.5')})")
        return b

    # ------------------------------------------------------------------ C / 렌더
    def rollup(self, f: dict[str, Any]) -> Portfolio:
        holds = self.store.holdings()
        if not holds:
            raise OpsError("열린 B 맵이 없습니다")
        cash = num(f.get("cash"), name="현금", required=True) or 0.0
        ta = num(f.get("total_assets"), name="총자산")
        cy = num(f.get("cash_yield"), name="현금 수익률")
        self.store.set_setting("cash", cash)
        if ta:
            self.store.set_setting("total_assets", ta)
        c = rollup(holds, cash=cash, total_assets=ta, cash_yield=cy if cy is not None else 0.03, today=self.now,
                   portfolio=self.store.portfolio())
        self.store.save_map(c)
        self.log("C 롤업 계산")
        return c

    def portfolio_text(self, c: Portfolio) -> str:
        c0 = c.value("C0") or {}
        out = [f"총자산 {c0.get('total_assets', 0):,.0f} · 현금 {c0.get('cash_weight', 0):.1%} · 종목 {c0.get('n_holdings')}개 · 상태 {c0.get('status_dist')} · 기준일 {c0.get('as_of')}"]
        for h in c.value("C1.1") or []:
            er = h.get("expected_return")
            out.append(f"  {LIGHT_ICON.get(h.get('light') or '', '-')} {h.get('ticker')} {h.get('name')}: 비중 {h.get('w_now', 0):.1%} (목표 {h.get('w_target', 0):.1%}) · 논제 {h.get('thesis_state') or '-'} · 결정 {h.get('decision') or ('(' + str(h.get('matrix_default')) + ')' if h.get('matrix_default') else '-')} · ER {f'{er:+.1%}' if er is not None else '-'}")
        c31 = c.value("C3.1") or {}
        out.append(f"순매수 {c31.get('net_buy', 0):,.0f} / 순매도 {c31.get('net_sell', 0):,.0f} → 실행 후 현금 {c.value('C3.2') or 0:.1%}")
        out.append(f"상위3 {c.value('C1.2') or 0:.0%} · HHI {c.value('C1.3') or 0:.3f} · 포트 기대수익률 {c.value('C5.4') or 0:+.1%} · 규칙 커버리지 {c.value('C5.3') if c.value('C5.3') is not None else '-'}")
        for w in c.meta.get("warnings", []):
            out.append("! " + w)
        for cf in c.value("C3.4") or []:
            out.append("⚠ " + cf)
        q = c.value("C5.1") or {}
        if q.get("count"):
            out.append(f"논제 없는 보유 {q['count']}개 ({q.get('weight', 0):.1%}): {', '.join(q.get('tickers', []))}")
        sec = c.value("C2.1") or {}
        if sec:
            out.append("섹터: " + ", ".join(f"{k} {v:.1%}" for k, v in sec.items()))
        return "\n".join(out)

    def render(self, m: MindMap, *, summary: bool = False) -> Path:
        name = m.map_id.replace(":", "_") + ("_summary" if summary else "") + ".html"
        p = write_html(m, self.store.out_path(name), summary=summary)
        self.log(f"렌더링 {p.name}")
        return p

    def render_all(self) -> list[Path]:
        return [self.render(self.store.load_map(r.map_id)) for r in self.store.list_maps()]

    # ------------------------------------------------------------------ HTML 편집 (로컬 편집 서버 · 편집 파일 반영)
    edit_servers: dict[str, Any] = field(default_factory=dict)   # map_id → EditServer (백그라운드 스레드)
    edit_saved: list[str] = field(default_factory=list)          # 서버가 저장한 map_id (화면이 비우며 다시 읽는다)

    def edit_server(self, m: MindMap, *, open_browser: bool = True) -> str:
        """맵 하나의 편집 서버를 띄우고(이미 있으면 재사용) 주소를 돌려준다. 서버는 자기 Store 연결을 쓴다 (SQLite 는 스레드별)."""
        from .render.server import EditServer, StoreTarget

        srv = self.edit_servers.get(m.map_id)
        if srv is None:
            store = self.store

            class _ThreadStore:
                """서버 스레드용 Store — 연산마다 연결을 새로 연다."""

                def __init__(self) -> None:
                    self.versions_dir = store.versions_dir

                def _open(self) -> Store:
                    return Store(store.db_path, maps_dir=store.maps_dir, out_dir=store.out_dir)

                def load_map(self, map_id: str) -> MindMap:
                    with self._open() as st:
                        return st.load_map(map_id)

                def save_map(self, mm: MindMap) -> Path:
                    with self._open() as st:
                        return st.save_map(mm)

                def out_path(self, name: str) -> Path:
                    return store.out_path(name)

            ops = self

            def _log(line: str) -> None:
                if not line.startswith("http "):          # 요청 한 줄 로그는 생략, 저장·오류만
                    ops.log(f"[편집 {m.map_id}] {line}")

            srv = EditServer(StoreTarget(_ThreadStore(), m.map_id, log=_log, on_saved=ops.edit_saved.append), today=self.now, log=_log)
            srv.start()
            self.edit_servers[m.map_id] = srv
            self.log(f"{m.map_id} 편집 서버 {srv.url.split('?')[0]} (브라우저 「저장」 → 맵에 바로 반영; 콘솔을 닫으면 멈춤)")
        if open_browser:
            srv.open_browser()
        return srv.url

    def stop_edit_servers(self) -> int:
        n = 0
        for srv in list(self.edit_servers.values()):
            try:
                srv.shutdown()
                n += 1
            except Exception:  # noqa: BLE001
                pass
        self.edit_servers.clear()
        return n

    def apply_edit_file(self, path: str | Path, *, map_id: str | None = None, force: bool = False) -> dict[str, Any]:
        """file:// 로 연 HTML 이 내려준 *.edits.json 을 저장소의 맵에 반영한다."""
        from .generate.edits import EditError, load_edit_file
        from .render.server import StoreTarget, save_edits

        try:
            payload = load_edit_file(path)
        except (EditError, OSError, ValueError) as e:
            raise OpsError(str(e)) from e
        mid = map_id or payload.get("map_id")
        if not mid:
            raise OpsError("편집 파일에 map_id 가 없다")
        try:
            self.store.load_map(mid)
        except KeyError:
            raise OpsError(f"맵 없음: {mid}") from None
        try:
            out = save_edits(StoreTarget(self.store, mid, log=self.log), payload, today=self.now, check_hash=not force)
        except EditError as e:
            raise OpsError(str(e)) from e
        self.log(f"{mid} 편집 파일 반영 {len(out['applied'])}건 → v{out['version']}: " + "; ".join(out["applied"])[:200])
        for w in out["warnings"]:
            self.log("  ⚑ " + w)
        return out

    def validate_all(self) -> list[str]:
        out = []
        for r in self.store.list_maps(include_closed=True):
            issues = validate(self.store.load_map(r.map_id), today=self.now)
            errs = [i for i in issues if i.level == "error"]
            out.append(f"{r.kind} {r.ticker or '-'} {r.map_id}: 오류 {len(errs)} 경고 {len(issues) - len(errs)}")
            out += ["    " + str(i) for i in errs]
        return out or ["맵이 없습니다"]

    def load_demo(self) -> None:
        from .demo import build_all

        a, b, c, _ = build_all()
        self.store.add_watch("000000", "가온로보틱스", sector="KR-ROBOT", invest_type="growth", note="예시 — 가상 종목")
        for m in (a, b, c):
            self.store.save_map(m)
        self.log("데모 적재: 관심종목 1 + A/B/C")

    # ------------------------------------------------------------------ 리포트 → A 맵 초안
    def peek_reports(self, paths: Sequence[str | Path]) -> dict[str, Any]:
        """파일을 읽어 종목·날짜·증권사만 미리 본다 (대화상자 기본값용). 실패는 OpsError."""
        from .reports import ReportError, digest_files
        from .symbols import research_db_path

        db = None
        try:
            from .collect.adapters.research_db import ResearchDb

            p = research_db_path(self.store)
            db = ResearchDb(p) if p else None
        except Exception:  # noqa: BLE001
            db = None
        try:
            d, docs = digest_files(paths, today=self.now, db=db)
        except ReportError as e:
            raise OpsError(str(e)) from e
        sym = self.book.by_code(d.ticker) if d.ticker else None
        w = self.store.get_watch(d.ticker) if d.ticker else None
        rows: list[str] = []
        for f in sorted(d.reports, key=lambda f: f.date or date.min, reverse=True):
            ok_tbl = "표 ○" if f.estimates.get("op") or f.estimates.get("revenue") else "표 ✗"
            tp = f"TP {f.target_price:,.0f}" if f.target_price else "목표가 ✗"
            px = f"현재가 {f.current_price:,.0f}" if f.current_price else "현재가 ✗"
            mark = "✓" if f.target_price and (f.estimates.get("op") or f.estimates.get("revenue")) else "△"
            tail = " · 집계 제외" if f.path.name in d.excluded else ""
            tail += " · 실적 전" if f.path.name in d.pre_earnings else ""
            rows.append(f"{mark} {f.broker or '증권사?'} {f.date or '날짜?'} · {f.opinion or '의견?'} · {tp} · {px} · {ok_tbl}{tail}  [{f.path.name}]")
        return {"ticker": d.ticker or "", "name": (w.name if w else sym.name if sym else d.name) or "", "rows": rows,
                "invest_type": (w.invest_type if w and w.invest_type else "growth"), "sector": (w.sector if w else sym.sector if sym else "") or "",
                "n_reports": len(docs), "n_readable": sum(1 for x in docs if not x.is_empty), "n_tp": d.n_tp,
                "brokers": d.brokers, "as_of": d.as_of.isoformat(), "warnings": list(d.warnings), "digest": d}

    # ------------------------------------------------------------------ 설계 v3: 시나리오 후보 · 예시 확률 · 검토 대기함 · 쟁점 · 출처
    def scenario_candidates(self, m: NewMap) -> list[dict[str, Any]]:
        """meta.draft.scenario_candidates — 증권사 자체 후보 (리포트 제시 / 산식 재계산 / 목표가)."""
        return list((m.meta.get("draft") or {}).get("scenario_candidates") or [])

    def candidate_labels(self, m: NewMap) -> list[str]:
        from .generate.scenarios import ScenarioCandidate

        out = []
        for i, c in enumerate(self.scenario_candidates(m)):
            cc = ScenarioCandidate(**{k: v for k, v in c.items() if k in ScenarioCandidate.__dataclass_fields__})
            if isinstance(cc.report_date, str):
                cc.report_date = date.fromisoformat(cc.report_date)
            out.append(f"{i + 1}) {cc.label()}")
        return out

    def apply_scenario_candidate(self, m: NewMap, index: int, name: str, *, save: bool = True) -> str:
        """후보 index(0부터) 를 Bear/Base/Bull 로 넣는다. 확률은 그대로 비어 있다."""
        from .generate.scenarios import apply_candidate

        cands = self.scenario_candidates(m)
        if not (0 <= index < len(cands)):
            raise OpsError(f"후보 번호 1~{len(cands)}")
        if name not in ("Bear", "Base", "Bull"):
            raise OpsError("Bear / Base / Bull 중 하나")
        sc = apply_candidate(m, cands[index], name, today=self.now)
        self.log(f"{m.map_id} {name} ← 후보 {index + 1} ({sc.valuation_method}) TP {sc.target_price:,.0f}")
        if sc.valuation_method.startswith("[사용자 재해석]"):
            self.log("  ⚠ 증권사가 이 이름으로 제시한 시나리오가 아니다 — 사용자 재해석으로 기록됨 (meta.draft.scenario_origin)")
        evaluate_new_map(m, today=self.now, enforce_gate=True)
        if save:
            self.store.save_map(m)
        return f"{name} = {sc.target_price:,.0f} ({sc.valuation_method})"

    # ------------------------------------------------------------------ 설계 v6: 평가 모델 · 가정 묶음 시나리오 (계산은 engine; 숫자는 사람)
    def valuation_text(self, m: NewMap, *, recompute: bool = True) -> str:
        """A3.2 기준 · A3.3 평가 모델(PER·정상화·SOTP·역산) · A4.7 매력도 · A3.4 신뢰도 · 시나리오 한 줄씩."""
        from .engine.attractiveness import attractiveness_line, compute_valuation_all, confidence_line
        from .engine.scenario_model import SCENARIO_IDS, scenario_line
        from .engine.valuation import Criteria, basis_table, valuation_table
        from .models import ValuationModel

        if recompute:
            compute_valuation_all(m, today=self.now)
            evaluate_new_map(m, today=self.now, enforce_gate=True)
        draft = m.meta.get("draft") or {}
        c = Criteria.from_map(m)
        rows = ["시나리오 한눈에(A4.1):"] + ["  " + ln for ln in str(m["A4.1"].value or "").split("\n")] if "A4.1" in m and m["A4.1"].value else []
        rows += [f"평가 기준(A3.2): EPS {c.eps_basis} · PER {c.per if c.per is not None else '기본값'} · 주식 수 {c.shares or '유도/공시'} · 순현금 {c.net_cash if c.net_cash is not None else '표'}"
                f" · 배당 {c.dividend_in_return} · SOTP 멀티플 {c.sotp_multiples or '없음 (사용자 입력)'}"]
        rows += basis_table(draft.get("financials") or {}).split("\n")
        rows += valuation_table(ValuationModel.model_validate(draft.get("valuation") or {}), m.current_price).split("\n")
        for name, nid in SCENARIO_IDS.items():
            sc = m[nid].scenario if nid in m else None
            rows.append("  " + (scenario_line(sc) if sc is not None else f"{name}: 공란") + (f" · p={sc.probability:.2f}" if sc is not None and sc.probability is not None else ""))
        rows.append("매력도(A4.7): " + attractiveness_line(draft.get("attractiveness") or {}))
        rows.append("신뢰도(A3.4): " + confidence_line(draft.get("confidence") or {}, len(draft.get("valuation_log") or [])))
        rows.append("  가정 묶음은 HTML 편집의 「밸류 계산기」 또는 `mindmap scenario <맵> --set Bear.A1.1=-30 --set Bear.per=3.5` 로 넣는다")
        return "\n".join(rows)

    def set_valuation_criteria(self, m: NewMap, fields: dict[str, Any], *, save: bool = True) -> str:
        """A3.2 평가 기준의 일부를 바꾼다 (None 이면 그 키를 지움) — 그 뒤 밸류 묶음을 다시 계산한다."""
        from .engine.attractiveness import compute_valuation_all
        from .engine.valuation import ensure_valuation_nodes

        ensure_valuation_nodes(m)
        crit = dict(m["A3.2"].value) if isinstance(m["A3.2"].value, dict) else {}
        for k, v in fields.items():
            if v is None or v == "" or v == {}:
                crit.pop(k, None)
            else:
                crit[k] = v
        m["A3.2"].value = crit or None
        m["A3.2"].as_of = self.now
        m["A3.2"].tags = sorted(set(m["A3.2"].tags) | {"user"})
        notes = compute_valuation_all(m, today=self.now)
        evaluate_new_map(m, today=self.now, enforce_gate=True)
        if save:
            self.store.save_map(m)
        self.log(f"{m.map_id} 평가 기준 갱신 ({', '.join(fields)}) → " + (m["A3.3"].value or "")[:120])
        return "\n".join(n for n in notes if n.startswith(("평가 모델", "매력도", "신뢰도")))

    # ------------------------------------------------------------------ 설계 v7: 성립 조건 · 관측값 · 증거 계획 · 규칙 · Claim 품질
    def conditions_text(self, m: NewMap, *, price: float | None = None, pair_x: str | None = None, recompute: bool = True) -> str:
        """「D」 성립 조건 — 가정 표 · 역산 표(한 변수 해 · 두 변수 조합) · 날짜별 필요 경로 (A2.4)."""
        from .engine.decision import compute_v7_all
        from .engine.path import conditions_text, inversion_table, inversion_table_text

        if recompute:
            compute_v7_all(m, today=self.now, price=price)
        if pair_x:
            inv = inversion_table(m, price=price, pair_x=pair_x, today=self.now)
            return inversion_table_text(inv)
        return conditions_text(m)

    def evidence_text(self, m: NewMap, *, recompute: bool = True) -> str:
        """「G」 증거 계획 · 신호등 · 증거 일정 · 판단 수정 규칙 · 알림."""
        from .engine.decision import compute_v7_all
        from .engine.evidence import (
            evidence_plan,
            evidence_plan_text,
            evidence_schedule,
            evidence_schedule_text,
            light_counts_text,
        )
        from .engine.revision import alerts, rules_text

        if recompute:
            compute_v7_all(m, today=self.now)
        rows = evidence_plan(m, today=self.now, write=True)
        out = ["증거 계획 (A5.5) — 신호등: " + light_counts_text((m.meta.get("draft") or {}).get("lights") or {}), evidence_plan_text(rows), "",
               "증거 일정 (A8.1):", evidence_schedule_text(evidence_schedule(m, today=self.now)), "", rules_text(m), "", "알림:"]
        al = alerts(m, today=self.now)
        out += ["  " + a for a in al] if al else ["  없음"]
        return "\n".join(out)

    def observe(self, m: NewMap, node_id: str, raw: dict[str, Any], *, save: bool = True, entered_by: str = "user") -> str:
        """관측값 입력 (데이터 계약 8칸). 사람·수집기·에이전트 공통. 저장 뒤 신호등·규칙을 다시 돌린다."""
        from .engine.decision import compute_v7_all
        from .engine.evidence import observation_from_dict, observe
        from .engine.revision import log_observation

        if node_id not in m or m[node_id].driver is None:
            raise OpsError(f"{node_id} 는 드라이버 노드가 아니다")
        try:
            obs = observation_from_dict({**raw, "entered_by": raw.get("entered_by") or entered_by})
        except Exception as e:  # noqa: BLE001 — pydantic 메시지를 사람에게
            raise OpsError(f"관측값 칸이 모자라거나 틀리다 — metric_id · period · value_type · basis · value · as_of · compare_at · source · verification: {e}") from e
        warns = observe(m, node_id, obs)
        log_observation(m, self.now, node_id, f"{obs.metric_id} {obs.period} = {obs.value} ({obs.as_of}, {obs.source}, {obs.verification}" + (f", {entered_by}" if entered_by else "") + ")")
        notes = compute_v7_all(m, today=self.now)
        evaluate_new_map(m, today=self.now, enforce_gate=True)
        if save:
            self.store.save_map(m)
        d = m[node_id].driver
        light = d.light.value if d is not None and d.light else "NONE"
        self.log(f"{m.map_id} {node_id} 관측값 {obs.metric_id} {obs.period}={obs.value} → 신호등 {light}")
        return "\n".join([f"저장: {node_id} {obs.metric_id} {obs.period} = {obs.value} ({obs.as_of}, {obs.verification})", f"신호등: {light} — {d.light_reason if d else ''}"]
                         + [f"  ! {w}" for w in warns] + [f"  · {n}" for n in notes if "이벤트" in n or "신호등" in n])

    def observe_file(self, m: NewMap, path: str | Path, *, save: bool = True, entered_by: str = "collector") -> str:
        """JSON 파일(목록: [{node_id, metric_id, …}] 또는 {node_id: [..]}) 로 여러 관측값을 넣는다 — 수집기·에이전트 입력 경로."""
        import json as _json

        data = _json.loads(Path(path).read_text(encoding="utf-8"))
        items: list[tuple[str, dict[str, Any]]] = []
        if isinstance(data, list):
            items = [(str(x.get("node_id")), {k: v for k, v in x.items() if k != "node_id"}) for x in data]
        elif isinstance(data, dict):
            for nid, lst in data.items():
                items += [(nid, dict(x)) for x in (lst if isinstance(lst, list) else [lst])]
        out: list[str] = []
        for nid, raw in items:
            try:
                out.append(self.observe(m, nid, raw, save=False, entered_by=entered_by).split("\n")[0])
            except OpsError as e:
                out.append(f"건너뜀 {nid}: {e}")
        if save:
            self.store.save_map(m)
        return "\n".join(out) if out else "관측값 없음 (파일이 비었다)"

    def set_driver_fields(self, m: NewMap, node_id: str, *, thesis_role: str | None = None, band: float | None = None, bound_bear: float | str | None = None,
                          bound_bull: float | str | None = None, save: bool = True) -> str:
        """역할·띠·경계를 사람이 확정한다 (제안값을 덮어쓴다 → source=user)."""
        from .engine.decision import compute_v7_all

        d = m[node_id].driver if node_id in m else None
        if d is None:
            raise OpsError(f"{node_id} 는 드라이버 노드가 아니다")
        if thesis_role:
            if thesis_role not in ("required", "core", "secondary"):
                raise OpsError("역할은 required · core · secondary 중 하나")
            d.thesis_role, d.thesis_role_source = thesis_role, "user"
        if band is not None or bound_bear is not None or bound_bull is not None:
            if band is not None:
                d.band = band
            if bound_bear is not None:
                d.bound_bear = bound_bear
            if bound_bull is not None:
                d.bound_bull = bound_bull
            d.bounds_source = "user"
        compute_v7_all(m, today=self.now)
        evaluate_new_map(m, today=self.now, enforce_gate=True)
        if save:
            self.store.save_map(m)
        return f"{node_id}: 역할 {d.thesis_role or '—'} ({d.thesis_role_source or '—'}) · 띠 {d.band} · 경계 Bear {d.bound_bear} / Bull {d.bound_bull} ({d.bounds_source or '—'}) · 신호등 {d.light.value if d.light else 'NONE'}"

    def add_rule(self, m: NewMap, *, driver_id: str, grade: str, threshold: float | str, persistence: int = 2, side: str = "bear",
                 pct_points: float | None = None, save: bool = True) -> str:
        from .engine.decision import compute_v7_all
        from .engine.revision import add_rule

        try:
            node = add_rule(m, driver_id=driver_id, grade=grade, threshold=threshold, persistence=persistence, side=side, pct_points=pct_points, today=self.now)
        except ValueError as e:
            raise OpsError(str(e)) from e
        compute_v7_all(m, today=self.now)
        if save:
            self.store.save_map(m)
        self.log(f"{m.map_id} 규칙 {node.id} 추가: {node.label}")
        return f"{node.id} {node.label} — {node.rule.condition if node.rule else ''}"

    def decide_event(self, m: NewMap, event_id: str, *, accept: bool, pct_points: float | None = None, note: str = "", save: bool = True) -> str:
        from .engine.revision import approve_event, compute_flags

        try:
            ev = approve_event(m, event_id, accept=accept, today=self.now, pct_points=pct_points, note=note)
        except ValueError as e:
            raise OpsError(str(e)) from e
        compute_flags(m)
        evaluate_new_map(m, today=self.now, enforce_gate=True)
        if save:
            self.store.save_map(m)
        return f"{ev['id']} {ev['rule_label']}: {ev['status']}" + (f" — {ev.get('applied')}" if ev.get("applied") else "")

    def clear_flag(self, m: NewMap, name: str, *, save: bool = True) -> str:
        from .engine.revision import clear_flag, compute_flags

        clear_flag(m, name, today=self.now)
        compute_flags(m)
        if save:
            self.store.save_map(m)
        return f"플래그 {name} 해제"

    def claim_quality_text(self, claims_dir: str | Path | None = None, ref_dir: str | Path | None = None, report_ids: list[str] | None = None) -> str:
        """「T」 Claim 품질 — 단계별 통계 (+ 기준 폴더가 있으면 비교)."""
        from .reports.claim_stats import (
            claim_compare,
            claim_gate,
            claim_stats,
            compare_text,
            gate_line,
            stats_text,
        )
        from .reports.claims import load_claimsets

        d = Path(claims_dir) if claims_dir else self.store.claims_dir
        sets = load_claimsets(d, report_ids=report_ids)
        if not sets:
            return f"ClaimSet 없음: {d}"
        out = [stats_text(claim_stats(sets))]
        ref = load_claimsets(Path(ref_dir), report_ids=[cs.report_id for cs in sets]) if ref_dir else []
        if ref:
            out += ["", compare_text(claim_compare(ref, sets))]
        out += ["", gate_line(claim_gate(sets, reference=ref or None))]
        return "\n".join(out)

    # ------------------------------------------------------------------ Kill 후보 (설계 v4 V6)
    def kill_candidates(self, m: NewMap) -> list[dict[str, Any]]:
        """meta.draft.kill_candidates — 리포트의 조건부 리스크에서 만든 Risk Trigger 후보. Rule 은 사람이 세 칸을 채워 확정한다."""
        return list((m.meta.get("draft") or {}).get("kill_candidates") or [])

    def kill_candidate_labels(self, m: NewMap) -> list[str]:
        out = []
        for i, c in enumerate(self.kill_candidates(m)):
            thr = f" {c.get('op', '<')} {c['threshold']:g}{c.get('unit') or ''}" if c.get("threshold") is not None else ""
            out.append(f"{i + 1}) {c.get('kpi') or '?'}{thr} — {c.get('condition') or ''} ({c.get('broker') or '?'}, {c.get('claim_id') or ''})")
        return out

    def apply_kill_candidate(self, m: NewMap, index: int, *, observable: str, update_cadence: str, action: str,
                             condition: str | None = None, save: bool = True) -> str:
        """후보 index(0부터) 를 A5.4.x Rule 로 확정한다. 관측 지표(observable)·갱신 주기·위반 시 행동 세 칸이 비면 넣지 않는다.
        condition 은 DSL 조건식 (없으면 후보의 driver_node 와 임계값으로 만들고, 그것도 없으면 사용자가 적어야 한다)."""
        cands = self.kill_candidates(m)
        if not (0 <= index < len(cands)):
            raise OpsError(f"후보 번호 1~{len(cands)}")
        c = cands[index]
        for label, v in (("관측 지표", observable), ("갱신 주기", update_cadence), ("위반 시 행동", action)):
            if not (v or "").strip():
                raise OpsError(f"{label} 가 비어 있다 — 세 칸을 채워야 Kill 조건이 된다 (설계 v4 V6)")
        act = {"exit": Action.exit, "trim": Action.trim, "review": Action.review, "hedge": Action.hedge}.get(action.strip().lower())
        if act is None:
            raise OpsError("위반 시 행동은 exit / trim / review / hedge 중 하나")
        cond = (condition or "").strip()
        if not cond:
            if c.get("driver_node") and c.get("threshold") is not None:
                cond = f"driver[{c['driver_node']}].current {c.get('op', '<')} {c['threshold']:g}"
            else:
                raise OpsError("조건식이 없다 — 예: driver[A1.1].current < 50 처럼 적을 것 (후보의 KPI 가 A1 드라이버에 없음)")
        # 빈 A5.4.x 를 찾는다 (A5.4.1 부터)
        nid = next((f"A5.4.{k}" for k in range(1, 6) if f"A5.4.{k}" in m and m[f"A5.4.{k}"].rule is None), None)
        if nid is None:
            raise OpsError("A5.4.1~5 가 모두 차 있다")
        n = m[nid]
        n.rule = Rule(trigger_type="fact", condition=cond, action=act, escalate_to="RED",
                      note=f"사용자 확정 Kill — 관측: {observable.strip()} · 갱신: {update_cadence.strip()} · 근거: {c.get('broker')} {c.get('claim_id')} '{(c.get('condition') or '')[:60]}'")
        n.as_of = self.now
        n.status = NodeStatus.confirmed
        n.tags = sorted((set(n.tags) | {"user"}) - {"pending", "draft"})
        n.question = (n.question or "").split(" | 후보:")[0]
        draft = m.meta.setdefault("draft", {})
        draft.setdefault("kill_confirmed", []).append({"node": nid, "candidate": index, "origin": "user_confirmed", "observable": observable.strip(),
                                                        "update_cadence": update_cadence.strip(), "action": act.value, "condition": cond,
                                                        "claim_id": c.get("claim_id"), "as_of": str(self.now)})
        self.log(f"{m.map_id} {nid} Kill 확정 ← 후보 {index + 1}: {cond} → {act.value} (관측 {observable.strip()}, {update_cadence.strip()})")
        evaluate_new_map(m, today=self.now, enforce_gate=True)
        if save:
            self.store.save_map(m)
        return f"{nid}: {cond} → {act.value}"

    def apply_example_probs(self, m: NewMap, *, save: bool = True) -> str:
        """「예시 확률 입력」 — 30/45/25 는 통계적 근거 없는 미검증 가정. 세 시나리오가 다 있어야 한다."""
        from .generate.scenarios import apply_example_probs

        try:
            done = apply_example_probs(m, today=self.now)
        except ValueError as e:
            raise OpsError(str(e) + " — 먼저 「시나리오 후보 넣기」 또는 A 탭에서 목표가를 적을 것") from e
        ev = evaluate_new_map(m, today=self.now, enforce_gate=True)
        if save:
            self.store.save_map(m)
        self.log(f"{m.map_id} 예시 확률 입력 (미검증 가정): " + ", ".join(done))
        er = f"ER {ev.expected_return:+.1%}" if ev.expected_return is not None else "ER 계산 불가"
        rr = f"RR {ev.risk_reward:.2f}" if ev.risk_reward is not None else "RR -"
        return f"예시 확률 입력 (미검증 가정) → {er} · {rr}"

    def claim_sets_for(self, m: NewMap) -> list[Any]:
        """이 맵을 만든 리포트들의 ClaimSet (data/claims)."""
        from .reports.claims import load_claimsets

        ids = list(((m.meta.get("draft") or {}).get("claims") or {}).keys())
        return load_claimsets(self.store.claims_dir, report_ids=ids) if ids else []

    def review_queue(self, m: NewMap) -> list[dict[str, Any]]:
        """검토 대기 Claim 목록 (report_id, claim_id, 명제, 검증 상태, 인용)."""
        from .reports.validate_claims import mark_review_required

        rows: list[dict[str, Any]] = []
        sets = self.claim_sets_for(m)
        mark_review_required(sets)          # 증권사 간 수치 충돌은 묶어서 봐야 보인다 (v4 V2)
        for cs in sets:
            for c in cs.review_queue():
                sp = cs.span(c.span_ids[0]) if c.span_ids else None
                rows.append({"report_id": cs.report_id, "broker": cs.broker, "claim_id": c.claim_id, "proposition": c.proposition,
                             "roles": [r.value for r in c.roles], "quote": sp.text if sp else "", "page": sp.page if sp else None,
                             "validation": f"인용 {c.validation.quote} · 숫자 {c.validation.numbers} · 의미 {c.validation.support.value}",
                             "inferred": c.inferred, "path": cs.path,
                             "status": c.status.value, "tag": c.tag,
                             "review_required": c.review_required, "review_reason": c.review_reason,
                             "needs_note": c.validation.support.value == "insufficient"})
        return rows

    def review_claim(self, m: NewMap, claim_id: str, decision: str, *, note: str = "") -> str:
        """검토함에서 승인/반려 → ClaimSet 저장. (맵 반영은 「리포트로 자동 생성」 을 다시 돌리거나 변경안으로.)"""
        from .reports.claims import save_claimset
        from .reports.validate_claims import review

        for cs in self.claim_sets_for(m):
            if any(c.claim_id == claim_id for c in cs.claims):
                try:
                    c = review(cs, claim_id, decision, who="user", note=note)
                except ValueError as e:                  # 근거 불충분 승인에 이유 없음 (v4 V2)
                    raise OpsError(str(e)) from e
                save_claimset(cs, self.store.claims_dir)
                self.log(f"Claim {decision}: {claim_id} — {c.proposition[:60]}")
                return f"{decision}: {c.proposition[:80]}"
        raise OpsError(f"Claim {claim_id} 을 찾지 못함")

    def issues_text(self, m: NewMap) -> str:
        from .generate.issues import build_issues, issues_table

        css = self.claim_sets_for(m)
        if not css:
            return "(Claim 없음 — 리포트로 자동 생성 을 먼저)"
        return issues_table(build_issues(css, ticker=m.ticker))

    def source_of(self, m: NewMap, node_id: str) -> dict[str, Any]:
        """노드의 출처 — 파일 경로·페이지·인용 (PDF 열기용). claim_id 가 source.name 에 있으면 그 인용까지."""
        n = m[node_id]
        out: dict[str, Any] = {"node": node_id, "name": n.source.name if n.source else "", "path": n.source.url if n.source else "", "page": None, "quote": ""}
        import re as _re

        ids = _re.findall(r"(hk_\d+:c\d+|f_[0-9a-f]+:[cr]\d+)", (n.source.name if n.source else "") + " " + (n.question or ""))
        if ids:
            for cs in self.claim_sets_for(m):
                c = next((x for x in cs.claims if x.claim_id == ids[0]), None)
                if c is not None:
                    sp = cs.span(c.span_ids[0]) if c.span_ids else None
                    out.update({"path": cs.path, "page": sp.page if sp else None, "quote": sp.text if sp else "", "claim": c.proposition})
                    break
        return out

    # ------------------------------------------------------------------ 설계 v3 P5: 변경안
    def _report_files_of(self, m: NewMap, new_paths: Sequence[str | Path]) -> list[Path]:
        """기존 맵을 만든 리포트 파일들 — meta.draft.reports[].file 을 새 파일과 같은 폴더(들) 에서 찾는다."""
        names = [r.get("file") for r in ((m.meta.get("draft") or {}).get("reports") or []) if r.get("file")]
        dirs = {Path(p).resolve().parent for p in new_paths}
        found: list[Path] = []
        for nm in names:
            for d in dirs:
                if (d / nm).is_file():
                    found.append(d / nm)
                    break
        return found

    def propose_changes(self, m: NewMap, new_paths: Sequence[str | Path], f: dict[str, Any] | None = None, *,
                        old_paths: Sequence[str | Path] | None = None, progress: Callable[[str, int, int], None] | None = None) -> Any:
        """기존 A 맵 + 새 리포트 → ChangeSet (저장: data/changes). 맵은 바꾸지 않는다."""
        from .generate import DraftError, draft_from_reports, make_filler
        from .generate.changes import propose_changes, save_changeset
        from .models import report_id_for
        from .symbols import research_db_path

        f = f or {}
        new_paths = [Path(p) for p in new_paths]
        if not new_paths:
            raise OpsError("새 리포트 파일을 하나 이상 고르세요")
        olds = [Path(p) for p in old_paths] if old_paths else self._report_files_of(m, new_paths)
        missing = [r.get("file") for r in ((m.meta.get("draft") or {}).get("reports") or []) if r.get("file") and not any(Path(o).name == r.get("file") for o in olds)]
        filler = None
        if f.get("use_llm"):
            try:
                filler = make_filler(text(f.get("llm_kind")) or "auto")
            except RuntimeError as e:
                raise OpsError(f"LLM 을 쓸 수 없습니다 — {e}") from e
        all_paths = [*olds, *[p for p in new_paths if p not in olds]]
        try:
            fresh = draft_from_reports(all_paths, ticker=m.ticker, name=m.name, invest_type=(m.profile.value if m.profile else None), today=self.now,
                                       symbols=self.book, filler=filler, research_db=research_db_path(self.store), progress=progress,
                                       fetch_price=bool(f.get("fetch_price", False)), claims=text(f.get("claims")) or "auto",
                                       claims_dir=self.store.claims_dir, n_drivers=5)
        except DraftError as e:
            raise OpsError(str(e)) from e
        cs = propose_changes(m, fresh.map, new_report_ids=[report_id_for(str(p)) for p in new_paths], today=self.now,
                             all_report_ids=[report_id_for(str(p)) for p in all_paths])
        if missing:
            cs.summary += f" · 기존 리포트 {len(missing)}건을 못 찾아 비교에서 빠짐: {', '.join(missing[:3])}"
        p = save_changeset(cs, self.store.changes_dir)
        self.log(f"변경안 {p.name}: {cs.summary}")
        return cs

    def apply_changes(self, m: NewMap, cs: Any, decisions: dict[str, str], *, save: bool = True) -> list[str]:
        """decisions: change_id → approve | reject. 승인분만 적용, 원본은 maps/versions 에 보존."""
        from .generate.changes import apply_changes, save_changeset
        from .models import ChangeDecision

        for c in cs.changes:
            d = decisions.get(c.change_id)
            if d == "approve":
                c.decision = ChangeDecision.approved
            elif d == "reject":
                c.decision = ChangeDecision.rejected
        from .generate.changes import partial_approval_warnings

        try:
            done = apply_changes(m, cs, today=self.now, versions_dir=self.store.versions_dir)
        except ValueError as e:
            raise OpsError(str(e)) from e
        save_changeset(cs, self.store.changes_dir)
        if done:
            for w in partial_approval_warnings(cs):
                self.log("  ⚑ " + w)
            evaluate_new_map(m, today=self.now, enforce_gate=True)
            if save:
                self.store.save_map(m)
            self.log(f"{m.map_id} 변경안 적용 {len(done)}건 → v{m.meta.get('version')}")
        return done

    def refresh_price(self, m: NewMap, *, fetch: bool = True, fetcher: Callable[[str], Any] | None = None) -> tuple[float, date, str]:
        """A0.4 현재가를 지금 기준으로 갱신 (네이버 실시간 → 리서치 DB 종가) 하고 다시 평가·저장한다."""
        from .generate import PRICE_STALE_DAYS, latest_price
        from .symbols import research_db_path

        pick, notes = latest_price(m.ticker or "", today=self.now, research_db=research_db_path(self.store), fetch=fetch, fetcher=fetcher)
        for n in notes:
            self.log("  · " + n)
        if pick is None:
            raise OpsError("현재가를 가져오지 못했습니다 — 네트워크(네이버 금융)와 리서치 DB 모두 없음. A 탭의 현재가 칸에 직접 적으세요")
        m["A0.4"].set(pick.value, as_of=pick.as_of, source=pick.source)
        m["A0.4"].tags = sorted((set(m["A0.4"].tags) - {"warn"}) | ({"warn"} if pick.age_days(self.now) > PRICE_STALE_DAYS else set()))
        evaluate_new_map(m, today=self.now, enforce_gate=True)
        self.store.save_map(m)
        self.log(f"현재가 갱신 {m.map_id}: {pick.value:,.0f} ({pick.as_of}, {pick.kind})")
        return pick.value, pick.as_of, pick.source.name

    # ------------------------------------------------------------------ 자동 생성 방식 (v4 부록): 새로 만들기(기본) / 기존에 더하기
    DRAFT_MODES = ("replace", "incremental")

    def archive_existing_drafts(self, ticker: str, *, keep_map_id: str | None = None) -> dict[str, Any]:
        """「새로 만들기」: 그 종목의 기존 A 맵 **초안**(meta.draft 가 있는 것) 을 maps/_archive 로 옮긴다. 지우지 않는다.
        B 맵이 스냅숏으로 참조하는 A 맵과 사용자가 처음부터 직접 만든 A 맵(meta.draft 없음) 은 그대로 둔다."""
        out: dict[str, Any] = {"archived": [], "kept": []}
        if not ticker:
            return out
        referenced = {getattr(b, "source_map_id", None) for b in (self.store.load_map(r.map_id) for r in self.store.list_maps(MapKind.HOLD, ticker=ticker, include_closed=True))}
        for row in self.store.list_maps(MapKind.NEW, ticker=ticker):
            if row.map_id == keep_map_id:
                continue
            try:
                m = self.store.load_map(row.map_id)
            except Exception:  # noqa: BLE001
                continue
            if row.map_id in referenced:
                out["kept"].append((row.map_id, "B 맵이 참조"))
                continue
            if not (m.meta.get("draft") or {}):
                out["kept"].append((row.map_id, "사용자가 직접 만든 맵"))
                continue
            dst = self.store.archive_map(row.map_id)
            out["archived"].append((row.map_id, str(dst) if dst else ""))
            self.log(f"기존 초안 보관: {row.map_id} → {dst.name if dst else '(파일 없음)'}")
        for mid, why in out["kept"]:
            self.log(f"기존 A 맵 유지: {mid} ({why})")
        return out

    def archive_claim_cache(self, paths: Sequence[str | Path]) -> dict[str, Any]:
        """「새로 만들기」: 이 리포트들의 저장된 ClaimSet 을 data/claims/_archive/<시각>/ 로 옮긴다 — 다시 뽑는다.
        사람이 승인/반려한 Claim 이 있으면 개수를 알려 준다 (보관 폴더에 그대로 있다)."""
        import shutil
        from datetime import datetime

        from .models import report_id_for
        from .reports import load_report
        from .reports.claims import load_claimset

        out: dict[str, Any] = {"archived": [], "human": 0}
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        for p in paths:
            try:
                rid = report_id_for(str(p), load_report(Path(p)).text)      # 파일명에 번호가 없으면 본문 해시라 읽어야 한다
            except Exception:  # noqa: BLE001
                rid = report_id_for(str(p), "")
            src = self.store.claims_dir / f"{rid}.json"
            if not src.is_file():
                continue
            try:
                cs = load_claimset(src)
                out["human"] += sum(1 for c in cs.claims if c.verified_by == "human")
            except Exception:  # noqa: BLE001
                pass
            dst_dir = self.store.claims_dir / "_archive" / stamp
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst_dir / src.name))
            out["archived"].append(str(dst_dir / src.name))
        if out["archived"]:
            self.log(f"Claim 저장본 {len(out['archived'])}건 보관 → claims/_archive/{stamp}" + (f" (사람이 승인/반려한 Claim {out['human']}건 포함 — 보관본에 남아 있다)" if out["human"] else ""))
        return out

    def draft_target(self, paths: Sequence[str | Path], f: dict[str, Any] | None = None) -> tuple[str | None, Any]:
        """대화상자 뒤, 어떤 방식으로 갈지 정하는 데 필요한 것: (종목코드, 그 종목의 최신 A 맵 또는 None)."""
        f = f or {}
        ticker, name = text(f.get("ticker")), text(f.get("name"))
        if ticker or name:
            try:
                ticker, _, _ = self.complete_symbol(ticker, name)
            except OpsError:
                ticker = normalize_code(ticker) if ticker else None
        if not ticker:
            try:
                ticker = self.peek_reports(paths).get("ticker")
            except OpsError:
                ticker = None
        latest = self.store.latest_map(ticker, MapKind.NEW) if ticker else None
        return ticker, latest

    def draft_from_reports(self, paths: Sequence[str | Path], f: dict[str, Any] | None = None, *,
                           progress: Callable[[str, int, int], None] | None = None) -> Any:
        """리포트 파일들 → A 맵 초안 → (save 면) 관심종목 등록 + 저장. 돌려주는 것은 generate.DraftResult.

        f: {ticker, name, invest_type, use_llm, save, fetch_price, mode} — 전부 선택. 종목은 지정값 > 리포트 본문 코드.
        fetch_price (기본 True): 현재가를 작성 시점 기준으로 네이버에서 조회한다 (실패하면 리서치 DB·리포트 값).
        mode (기본 "replace"): **새로 만들기** — 이 리포트들의 Claim 저장본을 보관 폴더로 옮기고 다시 뽑고(LLM 이 있으면 LLM), 저장할 때
        그 종목의 기존 A 맵 초안을 maps/_archive 로 옮긴다. "incremental": 저장본을 재사용하고 기존 맵은 건드리지 않는다
        (기존 A 맵이 있으면 화면은 이 함수 대신 「변경안」 으로 간다 — draft_target 으로 판단)."""
        from .generate import ClaudeGroupFiller, DraftError, draft_from_reports, llm_status, make_filler
        from .generate.llm_fill import key_problem
        from .symbols import research_db_path

        f = f or {}
        if not paths:
            raise OpsError("리포트 파일을 하나 이상 고르세요")
        ticker, name = text(f.get("ticker")), text(f.get("name"))
        sym: Symbol | None = None
        if ticker or name:
            try:
                ticker, name, sym = self.complete_symbol(ticker, name)
            except OpsError:
                if not ticker or not normalize_code(ticker):
                    raise
                ticker = normalize_code(ticker)
        w = self.store.get_watch(ticker) if ticker else None
        invest_type = text(f.get("invest_type")) or (w.invest_type if w and w.invest_type else None) or "growth"
        if invest_type not in INVEST_TYPES:
            raise OpsError(f"투자유형 {invest_type!r}")
        sector = text(f.get("sector")) or (w.sector if w else None) or (sym.sector if sym and sym.sector and sym.sector.startswith("KR-") else None)
        filler = None
        if f.get("use_llm"):
            kind = text(f.get("llm_kind")) or "auto"
            if kind in ("auto", "claude"):
                prob = key_problem()
                if prob and kind == "claude":
                    raise OpsError(prob + " — 체크를 끄면 규칙 채움만으로 초안을 만듭니다")
            try:
                filler = make_filler(kind)
            except RuntimeError as e:
                raise OpsError(f"LLM 을 쓸 수 없습니다 — {e}. 체크를 끄면 규칙 채움만으로 초안을 만듭니다") from e
            self.log("LLM 그룹 채움: " + (llm_status() if isinstance(filler, ClaudeGroupFiller) else f"로컬 OpenAI 호환 서버 ({getattr(filler, 'model', '')})"))
        fetch = bool(f.get("fetch_price", True))
        mode = text(f.get("mode")) or "replace"
        if mode not in self.DRAFT_MODES:
            raise OpsError(f"생성 방식 {mode!r} — replace | incremental")
        claims_mode = text(f.get("claims")) or "auto"
        if mode == "replace" and f.get("save", True):
            if claims_mode == "cached":
                self.log("새로 만들기 — Claim 저장본은 그대로 재사용(--claims cached), 맵만 다시 만든다")   # 구조만 바뀐 경우(설계 v5) LLM 비용 없이
            else:
                self.archive_claim_cache(paths)                   # 저장본을 치우고 다시 뽑는다
            if not text(f.get("claims")):
                claims_mode = "llm" if filler is not None else "rules"
        try:
            r = draft_from_reports(paths, ticker=ticker, name=name, invest_type=invest_type, sector=sector, today=self.now,
                                   symbols=self.book, filler=filler, research_db=research_db_path(self.store), progress=progress,
                                   fetch_price=fetch, price_fetcher=f.get("price_fetcher"),
                                   claims=claims_mode, claims_dir=self.store.claims_dir, n_drivers=5,
                                   llm_groups=bool(f.get("llm_groups", False)), issue_check=bool(f.get("issue_check", True)),
                                   prefill_suggestions=bool(f.get("prefill_suggestions", False)),
                                   claims_ref=Path(f["claims_ref"]) if f.get("claims_ref") else None, claim_gate_on=bool(f.get("claim_gate", True)),
                                   llm_events=str(f.get("llm_events") or "auto"))
        except DraftError as e:
            raise OpsError(str(e)) from e
        if r.issue_set is not None:
            try:
                self.store.issues_dir.mkdir(parents=True, exist_ok=True)
                (self.store.issues_dir / f"{r.map.ticker}.json").write_text(r.issue_set.model_dump_json(indent=2), encoding="utf-8")
            except OSError:
                pass
        m = r.map
        if f.get("save", True):
            if mode == "replace":
                arch = self.archive_existing_drafts(m.ticker or "", keep_map_id=m.map_id)
                if arch["archived"]:
                    r.notes.append(f"기존 A 맵 초안 {len(arch['archived'])}건을 maps/_archive 로 옮기고 새로 만들었다 (새로 만들기 모드)")
                m.meta.setdefault("draft", {})["generation_mode"] = mode
            if self.store.get_watch(m.ticker or "") is None:
                it = self.store.add_watch(m.ticker or "", m.name or m.ticker or "", sector=r.sector, invest_type=invest_type,
                                          note=f"리포트 {len(r.docs)}건으로 자동 초안 ({self.now})")
                self.book.extend([Symbol(it.ticker, it.name, it.market, it.sector)], "watchlist")
                self.log(f"관심종목 추가 {it.ticker} {it.name} (자동 초안)")
            elif r.sector and not (w and w.sector):
                self.store.update_watch(m.ticker or "", sector=r.sector)
            self.store.save_map(m)
            self.log(f"A 맵 초안 저장 {m.map_id} · 리포트 {len(r.docs)}건 · 검증 오류 {len(r.errors)}" + (" · LLM" if r.llm_used else ""))
        for n in r.notes:
            self.log("  · " + n)
        return r


def _dd_from_rule(r: Rule | None) -> str:
    if r is None or not r.condition.startswith("drawdown >"):
        return ""
    try:
        return f"{float(r.condition.split('>')[1]) * 100:g}"
    except ValueError:
        return ""


def _price_from_rule(r: Rule | None) -> str:
    if r is None or not r.condition.startswith("price >"):
        return ""
    try:
        return f"{float(r.condition.split('>')[1]):g}"
    except ValueError:
        return ""


__all__ = ["Ops", "OpsError", "AmbiguousSymbol", "num", "pct", "when", "text", "listing", "LIGHT_ICON", "HORIZONS", "CONFIDENCES",
           "INVEST_TYPES", "WHY_WRONG", "REVISIONS", "DECISIONS", "THESIS_STATES", "HOLD_REASONS", "ACTIONS",
           "EVENT_DIRECTIONS", "PRICED_IN", "MATERIALITY", "OPERATORS", "BIAS_FIELDS"]
