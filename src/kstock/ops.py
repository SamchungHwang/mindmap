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
            er, rr = a.value("A3.4"), a.value("A3.5")
            w = a["A6.1"].value if isinstance(a["A6.1"].value, dict) else {}
            out.append(f"  기대수익률 {er:+.1%} · 손익비 {rr}" if er is not None else "  기대수익률 미계산")
            if w:
                out.append(f"  비중 제안 {w.get('suggested', 0):.2%} → 확정 {w.get('confirmed', 0) or 0:.2%}")
            issues = validate(a, today=self.now)
            errs = [i for i in issues if i.level == "error"]
            out.append(f"  검증: 오류 {len(errs)} / 경고 {len(issues) - len(errs)}" + (" — " + "; ".join(i.message for i in errs[:3]) if errs else ""))
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
            m["A8.2"].links = [f"S.{it.sector}"]
        m["A8.3"].links = ["M0"]
        self.store.save_map(m)
        self.log(f"A 맵 생성 {m.map_id}")
        return m

    def a_form(self, m: NewMap) -> dict[str, Any]:
        """NewMap → 화면 폼 값 (모두 문자열/리스트)."""
        def s(v: Any) -> str:
            return "" if v is None else str(v)
        w = m["A6.1"].value if isinstance(m["A6.1"].value, dict) else {}
        ep = m.value("A6.2") if isinstance(m.value("A6.2"), dict) else {}
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
        for nid, nm in (("A3.1", "Bear"), ("A3.2", "Base"), ("A3.3", "Bull")):
            sc = m[nid].scenario
            scen[nm] = {"probability": s(sc.probability) if sc else "", "target_price": s(sc.target_price) if sc else "",
                        "assumptions": ", ".join(sc.assumptions) if sc else "", "horizon": s(sc.horizon_months) if sc else "12",
                        "method": sc.valuation_method if sc else ""}
        risks = []
        for nid in ("A4.1", "A4.2", "A4.3"):
            v = m.value(nid) if isinstance(m.value(nid), dict) else {}
            risks.append({"text": s(v.get("text")), "probability": s(v.get("probability")), "impact_pct": s(v.get("impact_pct"))})
        kill = m["A4.4.1"].rule
        return {
            "thesis": s(m.value("A0")), "horizon": s(m.value("A0.2")) or "1Y", "confidence": s(m.value("A0.3")) or "M",
            "price": s(m.value("A0.4")), "price_source": s(m["A0.4"].source.name) if m["A0.4"].source else "KRX 종가",
            "drivers": drivers,
            "implied": s(m.value("A2.1")), "mine": s(m.value("A2.2")), "why": s(m.value("A2.3")),
            "scenarios": scen, "band": s(m.value("A3.6.1")), "revision": s(m.value("A3.6.3")) or "flat",
            "risks": risks, "kill_condition": kill.condition if kill else "", "kill_note": s(kill.note) if kill else "",
            "early_warning": s(m.value("A4.5")),
            "w_suggested": s(w.get("suggested")), "w_confirmed": "" if w.get("confirmed") is None else f"{w['confirmed'] * 100:g}",
            "w_reason": s(w.get("reason")), "entry_lo": s(pr[0]), "entry_hi": s(pr[1]), "tranches": s(ep.get("tranches") or 3),
            "stop_dd": _dd_from_rule(m["A6.3"].rule), "tp_price": _price_from_rule(m["A6.4"].rule),
            "tp_size": s(m["A6.4"].rule.size) if m["A6.4"].rule else "50%", "decision": s(m.value("A6.5")) or "WATCH",
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
        for nm, nid in (("Bear", "A3.1"), ("Base", "A3.2"), ("Bull", "A3.3")):
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
            m["A3.6.1"].set(band, as_of=t, source=Source(name="밸류 밴드", retrieved_at=t))
        if text(f.get("revision")) in REVISIONS:
            m["A3.6.3"].set(f["revision"], as_of=t, source=Source(name="컨센서스", retrieved_at=t))
        # 리스크
        for i, nid in enumerate(("A4.1", "A4.2", "A4.3")):
            rf = (f.get("risks") or [{}] * 3)[i] if i < len(f.get("risks") or []) else {}
            rt = text(rf.get("text"))
            if not rt:
                continue
            m[nid].set({"probability": num(rf.get("probability"), name="리스크 확률") or 0.2,
                        "impact_pct": num(rf.get("impact_pct"), name="리스크 영향") or -20, "text": rt}, as_of=t)
        cond = text(f.get("kill_condition"))
        if cond:
            m["A4.4.1"].rule = Rule(trigger_type="fact", condition=cond, action=Action.exit, escalate_to="RED",
                                    note=text(f.get("kill_note")))
            m["A4.4.1"].as_of = t
        if text(f.get("early_warning")):
            m["A4.5"].set(text(f["early_warning"]), as_of=t, source=Source(name="직접 입력", retrieved_at=t))
        # 실행 계획
        wc = pct(f.get("w_confirmed"), name="확정 비중")
        cur_w = m["A6.1"].value if isinstance(m["A6.1"].value, dict) else {}
        if wc is not None:
            m["A6.1"].value = {**cur_w, "confirmed": round(wc, 4), "reason": text(f.get("w_reason"))}
        lo, hi = num(f.get("entry_lo"), name="진입 하단"), num(f.get("entry_hi"), name="진입 상단")
        if lo is not None and hi is not None:
            m["A6.2"].set({"price_range": [lo, hi], "tranches": int(num(f.get("tranches"), name="분할") or 1)}, as_of=t)
        dd = pct(f.get("stop_dd"), name="손절 하락률")
        if dd is not None:
            m["A6.3"].rule = Rule(trigger_type="price", condition=f"drawdown > {dd:g}", action=Action.review, escalate_to="YELLOW")
        tpp = num(f.get("tp_price"), name="익절 가격")
        if tpp is not None:
            m["A6.4"].rule = Rule(trigger_type="price", condition=f"price > {tpp:g}", action=Action.trim, size=text(f.get("tp_size")) or "50%")
        if text(f.get("decision")) in DECISIONS:
            m["A6.5"].set(f["decision"], as_of=t)
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
            v = m["A6.1"].value if isinstance(m["A6.1"].value, dict) else {}
            w = v.get("confirmed")
        if w is None:
            raise OpsError("목표 비중이 필요합니다 (A6.1 확정 비중 또는 직접 입력)")
        pos = Position(entry_date=entry_date, entry_price=price or 0, quantity=qty or 0, w_target=w,
                       eps_fwd_entry=num(f.get("eps_fwd"), name="forward EPS"))
        b = snapshot_to_holding(m, pos, today=entry_date)
        self.store.save_map(b)
        if m.decision is None or m.decision.value != "BUY":
            m["A6.5"].set("BUY", as_of=entry_date)
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

        try:
            d, docs = digest_files(paths, today=self.now)
        except ReportError as e:
            raise OpsError(str(e)) from e
        sym = self.book.by_code(d.ticker) if d.ticker else None
        w = self.store.get_watch(d.ticker) if d.ticker else None
        return {"ticker": d.ticker or "", "name": (w.name if w else sym.name if sym else d.name) or "",
                "invest_type": (w.invest_type if w and w.invest_type else "growth"), "sector": (w.sector if w else sym.sector if sym else "") or "",
                "n_reports": len(docs), "n_readable": sum(1 for x in docs if not x.is_empty), "n_tp": d.n_tp,
                "brokers": d.brokers, "as_of": d.as_of.isoformat(), "warnings": list(d.warnings), "digest": d}

    def draft_from_reports(self, paths: Sequence[str | Path], f: dict[str, Any] | None = None, *,
                           progress: Callable[[str, int, int], None] | None = None) -> Any:
        """리포트 파일들 → A 맵 초안 → (save 면) 관심종목 등록 + 저장. 돌려주는 것은 generate.DraftResult.

        f: {ticker, name, invest_type, use_llm, save} — 전부 선택. 종목은 지정값 > 리포트 본문 코드."""
        from .generate import ClaudeGroupFiller, DraftError, draft_from_reports, llm_available, llm_status
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
            if not llm_available():
                raise OpsError("LLM 을 쓸 수 없습니다 — " + llm_status()
                               + ". 프로젝트 루트 .env 에 ANTHROPIC_API_KEY=… 를 적고 `pip install -e \".[llm]\"` 하세요. 체크를 끄면 규칙 채움만으로 초안을 만듭니다")
            filler = ClaudeGroupFiller()
            self.log("LLM 그룹 채움: " + llm_status())
        try:
            r = draft_from_reports(paths, ticker=ticker, name=name, invest_type=invest_type, sector=sector, today=self.now,
                                   symbols=self.book, filler=filler, research_db=research_db_path(self.store), progress=progress)
        except DraftError as e:
            raise OpsError(str(e)) from e
        m = r.map
        if f.get("save", True):
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
