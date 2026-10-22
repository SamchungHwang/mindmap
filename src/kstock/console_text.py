"""kstock 마인드맵 텍스트 콘솔 — 번호 메뉴로 관심종목·A/B/C 맵을 만들고 관리한다 (GUI 는 console.py).

    mindmap console --text
    mindmap console --text --db data/db/kstock.sqlite

화면은 표준 입출력만 쓴다 (Windows 터미널·PowerShell 에서 그대로 동작, 외부 의존성 없음).
`Console(input_fn=..., print_fn=...)` 로 입력을 스크립트로 넣을 수 있어 테스트가 가능하다.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from .engine import (
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
    InvestType,
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
    ThesisState,
)
from .render import to_markdown, write_html
from .store import DEFAULT_DB, MapRow, Store
from .templates import new_map_skeleton

LIGHT = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}
INVEST_TYPES = [(t.value, t.value) for t in InvestType]


class Quit(Exception):
    """사용자가 종료를 선택했거나 입력이 끊겼다."""


class Console:
    def __init__(self, store: Store, *, input_fn: Callable[[str], str] = input,
                 print_fn: Callable[..., None] = print, open_browser: bool = True, today: date | None = None):
        self.store = store
        self._input = input_fn
        self._print = print_fn
        self.open_browser = open_browser
        self._today = today

    # ====================================================================== I/O helpers
    @property
    def today(self) -> date:
        return self._today or date.today()

    def say(self, *parts: Any) -> None:
        self._print(" ".join(str(p) for p in parts))

    def hr(self, title: str = "") -> None:
        line = "─" * 64
        self.say(f"\n{line}\n{title}\n{line}" if title else line)

    def ask(self, prompt: str, default: str | None = None, *, required: bool = False) -> str | None:
        suffix = f" [{default}]" if default not in (None, "") else ""
        while True:
            try:
                raw = self._input(f"{prompt}{suffix}: ")
            except (EOFError, StopIteration, KeyboardInterrupt) as e:
                raise Quit() from e
            raw = raw.strip()
            if raw.lower() in ("q", "quit", ":q"):
                raise Quit()
            if raw == "":
                if default is not None:
                    return default
                if not required:
                    return None
                self.say("  값이 필요합니다.")
                continue
            return raw

    def ask_float(self, prompt: str, default: float | None = None, *, required: bool = False,
                  scale: float = 1.0) -> float | None:
        while True:
            raw = self.ask(prompt, None if default is None else str(default), required=required)
            if raw is None:
                return None
            try:
                return float(str(raw).replace(",", "").rstrip("%")) * scale
            except ValueError:
                self.say("  숫자를 입력하세요.")

    def ask_int(self, prompt: str, default: int | None = None, *, required: bool = False) -> int | None:
        v = self.ask_float(prompt, default, required=required)
        return None if v is None else int(v)

    def ask_date(self, prompt: str, default: date | None = None) -> date | None:
        while True:
            raw = self.ask(prompt, default.isoformat() if default else None)
            if raw is None:
                return None
            try:
                return date.fromisoformat(raw)
            except ValueError:
                self.say("  YYYY-MM-DD 형식으로 입력하세요.")

    def ask_choice(self, prompt: str, options: Sequence[tuple[str, str]], default: str | None = None) -> str | None:
        """options: [(key, label)]. 번호 또는 key 로 선택."""
        for i, (k, label) in enumerate(options, start=1):
            self.say(f"  {i}) {label}" + ("" if k == label else f"  ({k})"))
        while True:
            raw = self.ask(prompt, default)
            if raw is None:
                return None
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return options[int(raw) - 1][0]
            for k, _ in options:
                if raw == k:
                    return k
            self.say("  목록에서 번호를 고르세요.")

    def ask_yes(self, prompt: str, default: bool = False) -> bool:
        raw = self.ask(f"{prompt} (y/n)", "y" if default else "n")
        return str(raw).lower().startswith(("y", "예", "ㅇ"))

    def table(self, headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
        if not rows:
            self.say("  (없음)")
            return
        cells = [[str(c) if c is not None else "" for c in r] for r in rows]
        widths = [max(len(str(h)), *(self._w(r[i]) for r in cells)) for i, h in enumerate(headers)]
        fmt = lambda r: "  " + "  ".join(self._pad(c, w) for c, w in zip(r, widths, strict=True))  # noqa: E731
        self.say(fmt(list(headers)))
        self.say("  " + "  ".join("-" * w for w in widths))
        for r in cells:
            self.say(fmt(r))

    @staticmethod
    def _w(s: str) -> int:
        return sum(2 if ord(ch) > 0x2E7F else 1 for ch in s)

    def _pad(self, s: str, w: int) -> str:
        return s + " " * max(0, w - self._w(s))

    # ====================================================================== main loop
    def run(self) -> int:
        self.hr("kstock mindmap 콘솔")
        s = self.store.summary()
        self.say(f"DB {s['db']} · 맵 {s['maps_dir']} · 관심종목 {s['watchlist']}개 · 맵 {s['maps']} · 보유 상태등 {s['lights']}")
        self.say("(어느 질문에서든 q 를 치면 상위 메뉴/종료)")
        try:
            while True:
                self.hr("메인")
                self.say("  1) 관심종목          2) 신규 투자 맵 (A)     3) 보유 종목 맵 (B)")
                self.say("  4) 포트폴리오 (C)    5) 렌더링               6) 도구")
                self.say("  0) 종료")
                c = self.ask("선택")
                if c in (None, "0"):
                    break
                try:
                    {"1": self.menu_watch, "2": self.menu_new, "3": self.menu_hold, "4": self.menu_port,
                     "5": self.menu_render, "6": self.menu_tools}.get(c, self._bad)()
                except Quit:
                    continue  # 하위 메뉴에서 q → 메인으로
                except (ValueError, KeyError, AssertionError) as e:
                    self.say(f"  ! {e}")
        except Quit:
            pass
        self.say("종료.")
        return 0

    def _bad(self) -> None:
        self.say("  메뉴 번호를 다시 고르세요.")

    def _drain(self, ops: Any) -> None:
        for line in ops.log_lines:
            self.say("  ·", line)
        ops.log_lines.clear()

    def _submenu(self, title: str, items: Sequence[tuple[str, str, Callable[[], None]]]) -> None:
        while True:
            self.hr(title)
            for key, label, _ in items:
                self.say(f"  {key}) {label}")
            self.say("  0) 뒤로")
            c = self.ask("선택")
            if c in (None, "0"):
                return
            for key, _, fn in items:
                if c.strip().lower() == key.lower():
                    try:
                        fn()
                    except (ValueError, KeyError, AssertionError) as e:
                        self.say(f"  ! {e}")
                    break
            else:
                self._bad()

    # ====================================================================== 1. 관심종목
    def menu_watch(self) -> None:
        self._submenu("관심종목", [
            ("1", "목록 보기", self.watch_list),
            ("2", "추가", self.watch_add),
            ("3", "수정 (이름·섹터·유형·메모)", self.watch_edit),
            ("4", "보관 (목록에서 숨김)", self.watch_archive),
            ("5", "CSV 가져오기 (ticker,name[,market,sector,invest_type,note])", self.watch_import),
            ("6", "CSV 내보내기", self.watch_export),
        ])

    def watch_list(self, include_archived: bool = False) -> None:
        items = self.store.list_watch(include_archived=include_archived)
        rows = []
        for it in items:
            a = self.store.list_maps(MapKind.NEW, ticker=it.ticker)
            b = self.store.list_maps(MapKind.HOLD, ticker=it.ticker)
            a_st = a[-1].status if a else "-"
            b_st = LIGHT.get(b[-1].status or "", b[-1].status) if b else "-"
            rows.append([it.ticker, it.name, it.market, it.sector or "", it.invest_type or "", a_st, b_st, it.note or ""])
        self.table(["종목", "이름", "시장", "섹터", "유형", "A맵", "B맵", "메모"], rows)

    def watch_add(self) -> None:
        from .ops import Ops
        from .symbols import normalize_code

        ops = Ops(self.store, today=self.today)
        raw = self.ask("종목코드 또는 종목명 (하나만 적어도 됩니다)", required=True)
        assert raw is not None
        sym = None
        found_name: str | None = None
        code = normalize_code(raw)
        if code:
            ticker = code
            sym = ops.book.by_code(code)
            found_name = sym.name if sym else None
            if sym is None:
                self.say(f"  {code} 은(는) 종목 목록에 없습니다 — 종목명을 적어 주세요")
        else:
            r = ops.resolve_symbol(raw)
            if r.status == "exact" and r.one:
                sym = r.one
                ticker, found_name = sym.code, sym.name
            elif r.status == "many":
                self.say("  후보가 여럿입니다:")
                pick = self.ask_choice("선택", [(c.code, c.label()) for c in r.candidates])
                if not pick:
                    return
                sym = next(c for c in r.candidates if c.code == pick)
                ticker, found_name = sym.code, sym.name
            else:
                self.say(f"  '{raw}' 을(를) 종목 목록에서 찾지 못했습니다")
                ticker = normalize_code(self.ask("종목코드 (숫자 6자리)", required=True)) or ""
                found_name = raw
        self._drain(ops)
        exist = self.store.get_watch(ticker)
        name = self.ask("종목명", (exist.name if exist else None) or found_name, required=True)
        market = self.ask("시장 (KR/US)", exist.market if exist else "KR")
        sector = self.ask("섹터 코드 (예 KR-ROBOT, 비우면 없음)", (exist.sector if exist else None) or (sym.sector if sym else None))
        self.say("투자 유형:")
        it = self.ask_choice("선택", INVEST_TYPES, exist.invest_type if exist else "growth")
        note = self.ask("메모 (한 줄 — 왜 관심인가)", exist.note if exist else None)
        item = self.store.add_watch(ticker, name or "", market=market or "KR", sector=sector, invest_type=it, note=note)
        self.say(f"  ✓ 관심종목 {'갱신' if exist else '추가'}: {item.ticker} {item.name} ({item.invest_type}, {item.sector or '섹터 없음'})")
        if not exist and self.ask_yes("바로 A 맵(신규 투자 맵) 스켈레톤을 만들까요?", True):
            self._new_from_watch(item.ticker)

    def _pick_watch(self, prompt: str = "종목코드") -> str | None:
        self.watch_list()
        return self.ask(prompt)

    def watch_edit(self) -> None:
        t = self._pick_watch("수정할 종목코드")
        if not t:
            return
        it = self.store.get_watch(t)
        if it is None:
            self.say("  없는 종목입니다.")
            return
        name = self.ask("종목명", it.name)
        sector = self.ask("섹터 코드", it.sector)
        self.say("투자 유형:")
        inv = self.ask_choice("선택", INVEST_TYPES, it.invest_type or "growth")
        note = self.ask("메모", it.note)
        self.store.update_watch(t, name=name, sector=sector, invest_type=inv, note=note)
        self.say("  ✓ 수정됨")

    def watch_archive(self) -> None:
        t = self._pick_watch("보관할 종목코드")
        if t and self.store.archive_watch(t):
            self.say(f"  ✓ {t} 보관 (맵은 그대로 남습니다)")

    def watch_import(self) -> None:
        p = self.ask("CSV 경로", required=True)
        assert p
        n = self.store.import_watch_csv(p)
        self.say(f"  ✓ {n}건 가져옴")

    def watch_export(self) -> None:
        p = self.ask("저장 경로", str(self.store.out_path("watchlist.csv")))
        assert p
        n = self.store.export_watch_csv(p)
        self.say(f"  ✓ {n}건 → {p}")

    # ====================================================================== 2. 신규 투자 맵 (A)
    def menu_new(self) -> None:
        self._submenu("신규 투자 맵 (A) — '살 것인가'", [
            ("1", "A 맵 목록", self.new_list),
            ("2", "관심종목에서 A 맵 스켈레톤 생성", self.new_create),
            ("3", "A 맵 채우기 (논제·드라이버·뷰·시나리오·리스크·실행계획)", self.new_fill),
            ("4", "평가·검증 (기대수익률·손익비·제안 비중·rr_min 게이트)", self.new_eval),
            ("5", "매수 체결 → B 맵 스냅숏", self.new_snapshot),
            ("6", "A 맵 HTML 렌더링", lambda: self._render_pick(MapKind.NEW)),
            ("7", "A 맵 삭제 (인덱스+파일)", lambda: self._delete_pick(MapKind.NEW)),
            ("8", "리포트 파일들로 A 맵 자동 생성 (초안 — 규칙 채움, 선택 LLM)", self.new_draft),
            ("9", "A 맵 현재가 갱신 (지금 기준 — 네이버 실시간 → 리서치 DB 종가)", self.new_refresh_price),
            ("L", "LLM 연결 확인 (ANTHROPIC_API_KEY 동작 — 인증·모델·작은 호출 1회)", self.new_llm_check),
            ("C", "시나리오 후보 넣기 (증권사 자체 후보 → Bear/Base/Bull, 확률은 비움)", self.new_scenario_candidate),
            ("P", "예시 확률 입력 (30/45/25 — 통계적 근거 없는 미검증 가정)", self.new_example_probs),
            ("K", "Kill 후보 확정 (리포트 조건부 리스크 → 관측 지표·갱신 주기·행동을 채워 Rule)", self.new_kill_candidate),
            ("I", "쟁점 표 (공통 견해·핵심 이견·독립 주장·미해결)", self.new_issues),
            ("R", "Claim 검토 대기함 (승인/반려)", self.new_review_queue),
            ("S", "노드 출처 보기 (리포트·페이지·인용)", self.new_source),
            ("N", "새 리포트 → 변경안 (승인한 것만 적용, 원본 버전 보존)", self.new_changes),
        ])

    def _rows(self, kind: MapKind, include_closed: bool = False) -> list[MapRow]:
        return self.store.list_maps(kind, include_closed=include_closed)

    def _pick_map(self, kind: MapKind, prompt: str = "번호", include_closed: bool = False) -> MindMap | None:
        rows = self._rows(kind, include_closed)
        if not rows:
            self.say("  맵이 없습니다.")
            return None
        for i, r in enumerate(rows, start=1):
            st = LIGHT.get(r.status or "", r.status or "")
            self.say(f"  {i}) {r.ticker or '-'} {r.name or ''}  [{st}]  {r.map_id}  (갱신 {r.updated_at})")
        raw = self.ask(prompt)
        if raw is None:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(rows):
            return self.store.load_map(rows[int(raw) - 1].map_id)
        for r in rows:
            if raw in (r.map_id, r.ticker):
                return self.store.load_map(r.map_id)
        self.say("  목록에서 고르세요.")
        return None

    def new_list(self) -> None:
        rows = self._rows(MapKind.NEW)
        self.table(["종목", "이름", "판단", "map_id", "갱신"],
                   [[r.ticker, r.name, r.status, r.map_id, r.updated_at] for r in rows])

    def new_create(self) -> None:
        t = self._pick_watch("종목코드 (관심종목에 없으면 먼저 추가됩니다)")
        if not t:
            return
        self._new_from_watch(t)

    def _new_from_watch(self, ticker: str) -> NewMap:
        it = self.store.get_watch(ticker)
        if it is None:
            name = self.ask("종목명", required=True)
            self.say("투자 유형:")
            inv = self.ask_choice("선택", INVEST_TYPES, "growth")
            it = self.store.add_watch(ticker, name or "", invest_type=inv)
        inv = it.invest_type or "growth"
        n = self.ask_int("핵심 드라이버 슬롯 수 (3~5)", 3) or 3
        m = new_map_skeleton(it.ticker, it.name, inv, self.today, n_drivers=max(3, min(5, n)))
        if it.sector:
            m["A8.2"].links = [f"S.{it.sector}"]
        m["A8.3"].links = ["M0"]
        p = self.store.save_map(m)
        self.say(f"  ✓ A 맵 생성: {m.map_id} → {p}")
        if self.ask_yes("지금 바로 채울까요?", True):
            self._fill_all(m)
        return m

    def new_draft(self) -> None:
        """증권사 리포트 여러 건 → A 맵 초안. 경로는 ';' 로 구분 (따옴표 허용)."""
        from .ops import Ops, OpsError

        raw = self.ask("리포트 파일 경로들 (';' 로 구분, PDF/TXT/MD/HTML/DOCX)", required=True)
        if not raw:
            return
        paths = [p.strip().strip('"').strip("'") for p in raw.replace("\n", ";").split(";") if p.strip()]
        ops = Ops(self.store, today=self.today)
        try:
            pk = ops.peek_reports(paths)
        except OpsError as e:
            self.say(f"  ! {e}")
            return
        self.say(f"  파일 {pk['n_reports']}개 (읽힘 {pk['n_readable']}) · 목표주가 {pk['n_tp']}건 · {', '.join(pk['brokers']) or '증권사 미상'} · 최신 {pk['as_of']}")
        for row in pk.get("rows", []):
            self.say("   " + row)
        for w in pk["warnings"][:5]:
            self.say(f"  ! {w}")
        ticker = self.ask("종목코드", pk["ticker"] or None)
        name = self.ask("종목명", pk["name"] or None)
        self.say("투자 유형:")
        inv = self.ask_choice("선택", INVEST_TYPES, pk["invest_type"] or "growth")
        from .generate import llm_available, llm_status

        self.say(f"  LLM: {llm_status()}")
        use_llm = self.ask_yes("LLM 그룹 채움을 쓸까요?", llm_available())
        fetch = self.ask_yes("현재가를 작성 시점 기준으로 네이버에서 조회할까요?", True)
        try:
            r = ops.draft_from_reports(paths, {"ticker": ticker, "name": name, "invest_type": inv, "use_llm": use_llm, "save": True,
                                               "fetch_price": fetch},
                                       progress=lambda label, i, n: self.say(f"  [{i}/{n}] {label}"))
        except OpsError as e:
            self.say(f"  ! {e}")
            return
        self._drain(ops)
        self.hr("초안 요약")
        self.say(r.summary_text())
        if self.ask_yes("HTML 로 열까요?", True):
            self._render(r.map)

    def new_refresh_price(self) -> None:
        from .ops import Ops, OpsError

        m = self._pick_map(MapKind.NEW)
        if not isinstance(m, NewMap):
            return
        ops = Ops(self.store, today=self.today)
        try:
            price, as_of, src = ops.refresh_price(m)
        except OpsError as e:
            self.say(f"  ! {e}")
            return
        self._drain(ops)
        self.say(f"  ✓ 현재가 {price:,.0f} ({as_of}) — {src}")
        self._print_new_eval(m)

    def _ops(self):  # noqa: ANN202
        from .ops import Ops

        return Ops(self.store, today=self.today)

    def new_scenario_candidate(self) -> None:
        from .ops import OpsError

        m = self._pick_map(MapKind.NEW)
        if not isinstance(m, NewMap):
            return
        ops = self._ops()
        labels = ops.candidate_labels(m)
        if not labels:
            self.say("  후보가 없습니다 (리포트로 자동 생성한 맵이 아니거나 목표가를 못 읽음)")
            return
        self.say("  증권사 자체 후보 — 한 증권사 안의 가정만 쓴다 (서로 섞지 않는다):")
        for ln in labels:
            self.say("   " + ln)
        for name in ("Bear", "Base", "Bull"):
            raw = self.ask(f"{name} 에 넣을 후보 번호 (건너뛰려면 빈칸)")
            if not raw or not raw.isdigit():
                continue
            try:
                self.say("  ✓ " + ops.apply_scenario_candidate(m, int(raw) - 1, name))
            except OpsError as e:
                self.say(f"  ! {e}")
        self._drain(ops)
        self._print_new_eval(m)

    def new_kill_candidate(self) -> None:
        from .ops import OpsError

        m = self._pick_map(MapKind.NEW)
        if not isinstance(m, NewMap):
            return
        ops = self._ops()
        labels = ops.kill_candidate_labels(m)
        if not labels:
            self.say("  Kill 후보가 없습니다 (리포트에 조건부 리스크 Claim 이 없음) — A4.4.x 는 직접 적을 것")
            return
        self.say("  Kill 후보 — 리포트의 조건부 리스크. '수익성 개선 지연' 은 '논제 붕괴' 가 아니므로 자동으로 넣지 않는다:")
        for ln in labels:
            self.say("   " + ln)
        raw = self.ask("확정할 후보 번호 (빈칸이면 취소)")
        if not raw or not raw.isdigit():
            return
        observable = self.ask("관측 지표 (어디서 무엇을 보나 — 예: 분기 실적 발표의 HBM 수율 코멘트)") or ""
        cadence = self.ask("갱신 주기 (예: 분기 / 월 / 공시 즉시)") or ""
        action = self.ask("위반 시 행동 (exit / trim / review / hedge)") or ""
        cond = self.ask("조건식 (빈칸이면 후보의 임계값으로 — 예: driver[A1.1].current < 50)") or ""
        try:
            self.say("  ✓ " + ops.apply_kill_candidate(m, int(raw) - 1, observable=observable, update_cadence=cadence, action=action, condition=cond or None))
        except OpsError as e:
            self.say(f"  ! {e}")
        self._drain(ops)
        self._print_new_eval(m)

    def new_example_probs(self) -> None:
        from .ops import OpsError

        m = self._pick_map(MapKind.NEW)
        if not isinstance(m, NewMap):
            return
        if not self.ask_yes("30/45/25 는 통계적 근거가 없는 예시값입니다. 그래도 넣을까요?", False):
            return
        ops = self._ops()
        try:
            self.say("  ✓ " + ops.apply_example_probs(m))
        except OpsError as e:
            self.say(f"  ! {e}")
        self._drain(ops)
        self._print_new_eval(m)

    def new_issues(self) -> None:
        m = self._pick_map(MapKind.NEW)
        if not isinstance(m, NewMap):
            return
        for ln in self._ops().issues_text(m).split("\n"):
            self.say("  " + ln)

    def new_review_queue(self) -> None:
        from .ops import OpsError

        m = self._pick_map(MapKind.NEW)
        if not isinstance(m, NewMap):
            return
        ops = self._ops()
        rows = ops.review_queue(m)
        if not rows:
            self.say("  검토 대기 Claim 이 없습니다")
            return
        for i, r in enumerate(rows, start=1):
            flag = f" ⚑ {r['review_reason']}" if r.get("review_required") else ""
            self.say(f"  {i}) [{r['broker']}|{r.get('tag', '')}|{','.join(r['roles']) or '-'}] {r['proposition'][:100]}{flag}")
            self.say(f"       {r['validation']}" + (f" · p.{r['page']} 인용: {r['quote'][:100]!r}" if r["quote"] else "") + (" · 추론" if r["inferred"] else "")
                     + (" · 승인하려면 이유 필요" if r.get("needs_note") else ""))
        raw = self.ask("승인할 번호 (쉼표, 빈칸이면 없음)")
        rej = self.ask("반려할 번호 (쉼표, 빈칸이면 없음)")
        note = ""
        picked = [int(t) for t in (raw or "").split(",") if t.strip().isdigit()]
        if any(1 <= k <= len(rows) and rows[k - 1].get("needs_note") for k in picked):
            note = self.ask("승인 이유 (근거 불충분 판정을 뒤집는 근거 — 비우면 그 항목은 보류)") or ""
        for txt, decision in ((raw, "approve"), (rej, "reject")):
            for tok in (txt or "").split(","):
                tok = tok.strip()
                if tok.isdigit() and 1 <= int(tok) <= len(rows):
                    try:
                        self.say("  ✓ " + ops.review_claim(m, rows[int(tok) - 1]["claim_id"], decision, note=note if decision == "approve" else ""))
                    except (OpsError, ValueError) as e:
                        self.say(f"  ! {e}")
        self._drain(ops)

    def new_source(self) -> None:
        m = self._pick_map(MapKind.NEW)
        if not isinstance(m, NewMap):
            return
        nid = self.ask("노드 id (예: A2.1.1, A4.1)")
        if not nid or nid not in m:
            self.say("  ! 그런 노드가 없습니다")
            return
        info = self._ops().source_of(m, nid)
        self.say(f"  출처: {info['name']}")
        if info.get("path"):
            self.say(f"  파일: {info['path']}" + (f" p.{info['page']}" if info.get("page") else ""))
        if info.get("quote"):
            self.say(f"  인용: {info['quote'][:300]}")

    def new_changes(self) -> None:
        from .generate.changes import changeset_table
        from .ops import OpsError

        m = self._pick_map(MapKind.NEW)
        if not isinstance(m, NewMap):
            return
        raw = self.ask("새 리포트 파일 경로 (쉼표로 구분)")
        if not raw:
            return
        paths = [p.strip() for p in raw.split(",") if p.strip()]
        ops = self._ops()
        try:
            cs = ops.propose_changes(m, paths, {"fetch_price": False})
        except OpsError as e:
            self.say(f"  ! {e}")
            return
        for ln in changeset_table(cs).split("\n"):
            self.say("  " + ln)
        if not cs.changes:
            return
        ap = self.ask("승인할 변경 id (쉼표, 예: ch1,ch2 — 빈칸이면 없음)")
        rj = self.ask("반려할 변경 id (쉼표)")
        decisions = {x.strip(): "approve" for x in (ap or "").split(",") if x.strip()}
        decisions.update({x.strip(): "reject" for x in (rj or "").split(",") if x.strip()})
        try:
            done = ops.apply_changes(m, cs, decisions)
        except OpsError as e:
            self.say(f"  ! {e}")
            return
        self._drain(ops)
        self.say(f"  ✓ 적용 {len(done)}건 → v{m.meta.get('version')}")

    def new_llm_check(self) -> None:
        from .generate import llm_check

        for ln in llm_check().lines():
            self.say("  " + ln)

    # ---------------------------------------------------------------- 채우기
    def new_fill(self) -> None:
        m = self._pick_map(MapKind.NEW)
        if not isinstance(m, NewMap):
            return
        self.hr(f"채우기 — {m.name} ({m.map_id})")
        sec = self.ask_choice("섹션", [
            ("0", "전체 순서대로"), ("1", "논제 (A0)"), ("2", "핵심 드라이버 (A1)"), ("3", "컨센서스 vs 내 뷰 (A2)"),
            ("4", "밸류·시나리오 (A3)"), ("5", "리스크·무효화 조건 (A4)"), ("6", "실행 계획 (A6)"),
        ], "0")
        fn = {"0": self._fill_all, "1": self._fill_thesis, "2": self._fill_drivers, "3": self._fill_view,
              "4": self._fill_scenarios, "5": self._fill_risks, "6": self._fill_exec}.get(sec or "0", self._fill_all)
        fn(m)

    def _save_and_check(self, m: MindMap, *, evaluate: bool = True) -> None:
        if isinstance(m, NewMap) and evaluate:
            evaluate_new_map(m, today=self.today, enforce_gate=True)
        self.store.save_map(m)
        issues = validate(m, today=self.today)
        errs = [i for i in issues if i.level == "error"]
        self.say(f"  저장됨 · 검증: 오류 {len(errs)} / 경고 {len(issues) - len(errs)}")
        for i in errs[:8]:
            self.say("   ", i)

    def _fill_all(self, m: NewMap) -> None:
        for fn in (self._fill_thesis, self._fill_drivers, self._fill_view, self._fill_scenarios,
                   self._fill_risks, self._fill_exec):
            fn(m, save=False)
        self._save_and_check(m)
        self._print_new_eval(m)

    def _fill_thesis(self, m: NewMap, save: bool = True) -> None:
        self.hr("A0 투자 논제 — 한 문장")
        self.say("  형식: ○○은 [핵심 드라이버] 덕분에 [기간] 내 [목표가]. 시장은 아직 [X]를 반영하지 않음")
        v = self.ask("논제", m.value("A0"))
        if v:
            m["A0"].set(v, as_of=self.today)
        self.say("투자 기간:")
        h = self.ask_choice("선택", [("3M", "3M"), ("1Y", "1Y"), ("3Y+", "3Y+")], m.value("A0.2") or "1Y")
        if h:
            m["A0.2"].set(h, as_of=self.today)
        self.say("확신도:")
        c = self.ask_choice("선택", [("H", "H 높음"), ("M", "M 보통"), ("L", "L 낮음")], m.value("A0.3") or "M")
        if c:
            m["A0.3"].set(c, as_of=self.today)
        p = self.ask_float("현재가 (원)", m.value("A0.4"))
        if p:
            m["A0.4"].set(p, as_of=self.today, source=Source(name=self.ask("가격 출처", "KRX 종가") or "KRX", retrieved_at=self.today))
        if save:
            self._save_and_check(m, evaluate=False)

    def _fill_drivers(self, m: NewMap, save: bool = True) -> None:
        self.hr("A1 핵심 드라이버 — 이 변수가 틀리면 논제가 무너지는가? (아니면 빼라)")
        slots = [n for n in m.children("A1") if n.id.startswith("A1.")]
        for n in slots:
            d = n.driver or Driver()
            self.say(f"\n  [{n.id}] 현재 라벨: {n.label}  (비우고 Enter 면 건너뜀, '-' 면 슬롯 비움)")
            label = self.ask("변수 이름 (예 영업이익률)", n.label if n.driver else None)
            if label is None:
                continue
            if label == "-":
                n.driver = None
                continue
            n.label = label
            n.unit = self.ask("단위 (%, 억원, 배 …)", n.unit) or n.unit
            cur = self.ask_float("현재값", d.current.value)
            if cur is not None:
                d.current = Measurement(value=cur, as_of=self.today,
                                        source=Source(name=self.ask("현재값 출처", d.current.source.name if d.current.source else "분기보고서") or "", retrieved_at=self.today))
            cons = self.ask_float("컨센서스 (없으면 Enter)", d.consensus.value)
            if cons is not None:
                d.consensus = Measurement(value=cons, as_of=self.today)
            my = self.ask_float("내 추정", d.my_estimate.value)
            if my is not None:
                d.my_estimate = Measurement(value=my, as_of=self.today, rationale=self.ask("추정 근거 (한 줄)", d.my_estimate.rationale))
            d.check_indicator = self.ask("확인 지표·출처 (어디서 확인?)", d.check_indicator) or d.check_indicator
            d.next_check = self.ask_date("다음 확인일", d.next_check)
            d.higher_is_better = self.ask_yes("값이 클수록 좋은 변수인가", d.higher_is_better)
            n.driver = d
            n.as_of = self.today
        if save:
            self._save_and_check(m, evaluate=False)

    def _fill_view(self, m: NewMap, save: bool = True) -> None:
        self.hr("A2 컨센서스 vs 내 뷰 — 기대수익의 원천")
        for nid in ("A2.1", "A2.2"):
            v = self.ask(m[nid].question, m.value(nid))
            if v:
                m[nid].set(v, as_of=self.today)
        self.say("시장이 틀린 이유:")
        w = self.ask_choice("선택", [("시간축", "시간축 — 시장은 더 짧게 본다"), ("정보", "정보 — 시장이 아직 모른다"),
                                  ("심리", "심리 — 과잉 반응"), ("구조", "구조 — 패시브/규제/유동성 제약")], m.value("A2.3"))
        if w:
            m["A2.3"].set(w, as_of=self.today)
        if save:
            self._save_and_check(m, evaluate=False)

    def _fill_scenarios(self, m: NewMap, save: bool = True) -> None:
        self.hr("A3 시나리오 — 확률 합 = 1.0, 목표가 Bear < Base < Bull")
        for nid, nm, dp in (("A3.1", "Bear", 0.25), ("A3.2", "Base", 0.50), ("A3.3", "Bull", 0.25)):
            cur = m[nid].scenario
            self.say(f"\n  [{nm}]")
            prob = self.ask_float("확률 (0~1)", cur.probability if cur else dp)
            tp = self.ask_float("목표가 (원)", cur.target_price if cur else None)
            if prob is None or tp is None:
                continue
            assumptions = self.ask("핵심 가정 (쉼표로 구분)", "; ".join(cur.assumptions) if cur else None)
            horizon = self.ask_int("기간 (개월)", cur.horizon_months if cur else 12) or 12
            method = self.ask("밸류 방법 (예 PER 18배 × 2027E EPS)", cur.valuation_method if cur else None)
            m[nid].scenario = Scenario(name=nm, probability=prob, target_price=tp,
                                       assumptions=[a.strip() for a in (assumptions or "").replace(";", ",").split(",") if a.strip()],
                                       horizon_months=horizon, valuation_method=method or "")
            m[nid].as_of = self.today
        band = self.ask_float("현재 멀티플의 역사 밴드 백분위 (0~100)", m.value("A3.6.1"))
        if band is not None:
            m["A3.6.1"].set(band, as_of=self.today, source=Source(name="밸류 밴드", retrieved_at=self.today))
        self.say("최근 3개월 EPS 리비전 방향:")
        rv = self.ask_choice("선택", [("up", "up"), ("flat", "flat"), ("down", "down")], m.value("A3.6.3") or "flat")
        if rv:
            m["A3.6.3"].set(rv, as_of=self.today, source=Source(name="컨센서스", retrieved_at=self.today))
        if save:
            self._save_and_check(m)

    def _fill_risks(self, m: NewMap, save: bool = True) -> None:
        self.hr("A4 리스크 & 무효화 조건 (Kill criteria)")
        for nid in ("A4.1", "A4.2", "A4.3"):
            cur = m.value(nid) if isinstance(m.value(nid), dict) else {}
            text = self.ask(f"{m[nid].label}: 무엇이 잘못될 수 있나 (Enter 면 건너뜀)", cur.get("text"))
            if not text:
                continue
            prob = self.ask_float("발생 확률 (0~1)", cur.get("probability", 0.2))
            imp = self.ask_float("발생 시 주가 영향 % (예 -25)", cur.get("impact_pct", -20))
            m[nid].set({"probability": prob, "impact_pct": imp, "text": text}, as_of=self.today)
        self.say("\n  무효화 조건 — '이 사실이 확인되면 손실 크기와 무관하게 논제를 버린다' (가격 아닌 사실 기준)")
        cond = self._build_condition(m, m["A4.4.1"].rule.condition if m["A4.4.1"].rule else None)
        if cond:
            m["A4.4.1"].rule = Rule(trigger_type="fact", condition=cond, action=Action.exit, escalate_to="RED",
                                    note=self.ask("설명 (한 줄)", m["A4.4.1"].rule.note if m["A4.4.1"].rule else None))
            m["A4.4.1"].as_of = self.today
        ew = self.ask("조기 경보 지표 (무엇을, 어디서, 얼마나 자주)", m.value("A4.5"))
        if ew:
            m["A4.5"].set(ew, as_of=self.today, source=Source(name="직접 입력", retrieved_at=self.today))
        if save:
            self._save_and_check(m)

    def _build_condition(self, m: MindMap, current: str | None) -> str | None:
        """드라이버 번호 + 연산자 + 임계값 → `driver[A1.i].current < X`. 직접 입력도 허용."""
        drivers = [n for n in m.nodes.values() if n.driver is not None]
        if drivers:
            self.say("  드라이버:")
            for i, n in enumerate(drivers, start=1):
                self.say(f"    {i}) {n.id} {n.label} (현재 {n.driver.current.value})")  # type: ignore[union-attr]
        self.say("  입력 방법: 번호 (예 2) → 연산자·임계값 묻기 / 또는 조건식 직접 (예 driver[A1.2].current < 3500)")
        raw = self.ask("드라이버 번호 또는 조건식", current)
        if not raw:
            return None
        if raw.isdigit() and drivers and 1 <= int(raw) <= len(drivers):
            n = drivers[int(raw) - 1]
            did = n.links[0] if (n.id.startswith("B2.1.") and n.links) else n.id
            op = self.ask_choice("연산자", [("<", "< 미만"), ("<=", "<= 이하"), (">", "> 초과"), (">=", ">= 이상")], "<")
            thr = self.ask_float("임계값", required=True)
            return f"driver[{did}].current {op} {thr:g}"
        return raw

    def _fill_exec(self, m: NewMap, save: bool = True) -> None:
        self.hr("A6 실행 계획")
        evaluate_new_map(m, today=self.today, enforce_gate=False)
        sug = (m["A6.1"].value or {}).get("suggested") if isinstance(m["A6.1"].value, dict) else None
        if sug is not None:
            self.say(f"  제안 비중 {sug:.2%}  (= w_base × f_conf × f_rr)")
        conf = self.ask_float("확정 비중 % (예 5)", None if sug is None else round(sug * 100, 2), scale=0.01)
        if conf is not None:
            reason = self.ask("비중 확정 사유", (m["A6.1"].value or {}).get("reason") if isinstance(m["A6.1"].value, dict) else None)
            m["A6.1"].value = {**(m["A6.1"].value or {}), "confirmed": round(conf, 4), "reason": reason}
        cur = m.value("A6.2") if isinstance(m.value("A6.2"), dict) else {}
        lo = self.ask_float("진입 가격대 하단", (cur.get("price_range") or [None, None])[0])
        hi = self.ask_float("진입 가격대 상단", (cur.get("price_range") or [None, None])[1])
        tr = self.ask_int("분할 횟수", cur.get("tranches", 3))
        if lo and hi:
            m["A6.2"].set({"price_range": [lo, hi], "tranches": tr or 1}, as_of=self.today)
        dd = self.ask_float("손절(재검토) 기준: 고점 대비 하락률 % (예 25)", 25.0, scale=0.01)
        if dd is not None:
            m["A6.3"].rule = Rule(trigger_type="price", condition=f"drawdown > {dd:g}", action=Action.review, escalate_to="YELLOW")
        tp = self.ask_float("익절/축소 기준 가격 (없으면 Enter)", None)
        if tp:
            size = self.ask("축소 비율 (예 50%)", "50%")
            m["A6.4"].rule = Rule(trigger_type="price", condition=f"price > {tp:g}", action=Action.trim, size=size)
        self.say("판단:")
        dec = self.ask_choice("선택", [("BUY", "BUY 매수"), ("WATCH", "WATCH 관찰"), ("PASS", "PASS 패스")], m.value("A6.5") or "WATCH")
        if dec:
            m["A6.5"].set(dec, as_of=self.today)
        if save:
            self._save_and_check(m)
            self._print_new_eval(m)

    # ---------------------------------------------------------------- 평가 / 스냅숏
    def _print_new_eval(self, m: NewMap) -> None:
        res = evaluate_new_map(m, today=self.today)
        self.say(f"  기대수익률 {res.expected_return:+.1%}" if res.expected_return is not None else "  기대수익률 계산 불가 (시나리오·현재가 확인)")
        self.say(f"  손익비 {res.risk_reward:.2f}" if res.risk_reward is not None else "  손익비 계산 불가")
        if res.suggested_weight is not None:
            self.say(f"  제안 비중 {res.suggested_weight:.2%}")
        if res.rr_gate_passed is not None:
            self.say(f"  rr_min 게이트: {'통과' if res.rr_gate_passed else '미통과'} · 판단 {m.decision.value if m.decision else '-'}")
        for n in res.notes:
            self.say("  !", n)
        self.store.save_map(m)

    def new_eval(self) -> None:
        m = self._pick_map(MapKind.NEW)
        if isinstance(m, NewMap):
            self._print_new_eval(m)
            issues = validate(m, today=self.today)
            for i in issues:
                self.say("   ", i)
            self.say(f"  → {'완료(판단 가능)' if not any(i.level == 'error' for i in issues) else '미완료'}")

    def new_snapshot(self) -> None:
        m = self._pick_map(MapKind.NEW)
        if not isinstance(m, NewMap):
            return
        issues = [i for i in validate(m, today=self.today) if i.level == "error"]
        if issues:
            self.say("  A 맵이 완료 상태가 아닙니다:")
            for i in issues:
                self.say("   ", i)
            if not self.ask_yes("그래도 스냅숏을 만들까요?", False):
                return
        self.hr("매수 체결 → B 맵")
        entry_date = self.ask_date("체결일", self.today) or self.today
        price = self.ask_float("체결가 (원)", m.current_price, required=True)
        qty = self.ask_float("수량 (주)", required=True)
        conf = (m["A6.1"].value or {}).get("confirmed") if isinstance(m["A6.1"].value, dict) else None
        w = self.ask_float("목표 비중 % (예 5)", None if conf is None else round(conf * 100, 2), required=True, scale=0.01)
        eps = self.ask_float("매수 시점 12M forward EPS (주가 분해용, 없으면 Enter)")
        pos = Position(entry_date=entry_date, entry_price=price or 0, quantity=qty or 0, w_target=w or 0, eps_fwd_entry=eps)
        b = snapshot_to_holding(m, pos, today=entry_date)
        self.store.save_map(b)
        if m.decision is None or m.decision.value != "BUY":
            m["A6.5"].set("BUY", as_of=entry_date)
            self.store.save_map(m)
        self.say(f"  ✓ B 맵 생성: {b.map_id}  (스냅숏 해시 {b.snapshot_hash}, 규칙 {len(b.rules())}개)")
        self.say("  다음: 메인 3) 보유 종목 맵 → 2) 시장 상태 갱신 → 6) 평가")

    # ====================================================================== 3. 보유 종목 맵 (B)
    def menu_hold(self) -> None:
        self._submenu("보유 종목 맵 (B) — '계속 들고 있을 자격이 있는가'", [
            ("1", "목록 (상태등)", self.hold_list),
            ("2", "시장 상태 갱신 (가격·비중·EPS·밸류 밴드)", self.hold_market),
            ("3", "드라이버 값 갱신", self.hold_driver),
            ("4", "사실 로그 추가 (공시·뉴스 → Event)", self.hold_event),
            ("5", "논제 상태 판정 (강화/유효/약화/무효/변질)", self.hold_thesis),
            ("6", "평가 → 상태등·매트릭스 → 결정 기록 (편향 체크)", self.hold_evaluate),
            ("7", "논제 복원 위자드 (맵 없이 보유 중인 종목)", self.hold_reconstruct),
            ("8", "종료 (전량 매도 완료) + 사후 복기", self.hold_close),
            ("9", "B 맵 HTML 렌더링", lambda: self._render_pick(MapKind.HOLD)),
        ])

    def hold_list(self) -> None:
        rows = []
        for r in self._rows(MapKind.HOLD, include_closed=True):
            m = self.store.load_map(r.map_id)
            if not isinstance(m, HoldingMap):
                continue
            nxt = sorted(d.driver.next_check for d in m.drivers() if d.driver and d.driver.next_check)
            rows.append([LIGHT.get(r.status or "", r.status or "-"), r.ticker, r.name, m.value("B2.3") or "-",
                         f"{m.market.w_now:.1%}" if m.market.w_now is not None else "-",
                         m.value("B6") or (m.meta.get("matrix_default") and f"({m.meta['matrix_default']})") or "-",
                         nxt[0].isoformat() if nxt else "-", "종료" if r.closed else ""])
        self.table(["등", "종목", "이름", "논제", "비중", "결정", "다음확인", ""], rows)

    def _pick_hold(self) -> HoldingMap | None:
        m = self._pick_map(MapKind.HOLD)
        return m if isinstance(m, HoldingMap) else None

    def hold_market(self) -> None:
        b = self._pick_hold()
        if b is None:
            return
        mk = b.market
        self.hr(f"시장 상태 — {b.name}")
        as_of = self.ask_date("기준일", self.today) or self.today
        price = self.ask_float("현재가 (원)", mk.price, required=True)
        w = self.ask_float("현재 비중 % (예 7.2)", None if mk.w_now is None else round(mk.w_now * 100, 2), scale=0.01)
        eps = self.ask_float("12M forward EPS", mk.eps_fwd)
        band = self.ask_float("밸류 밴드 백분위 (0~100)", mk.band_percentile)
        ta = self.ask_float("총자산 (원)", mk.total_assets or self.store.get_setting("total_assets"))
        adv = self.ask_float("일평균 거래량 (주)", mk.adv_shares)
        cy = self.ask_float("현금 대안 수익률 (예 0.03)", mk.cash_yield if mk.cash_yield is not None else 0.03)
        update_market(b, MarketState(as_of=as_of, price=price, w_now=w, eps_fwd=eps, band_percentile=band,
                                     total_assets=ta, adv_shares=adv, cash_yield=cy))
        if ta:
            self.store.set_setting("total_assets", ta)
        self.store.save_map(b)
        self.say("  ✓ 갱신됨. 6) 평가 로 상태등을 다시 계산하세요.")

    def hold_driver(self) -> None:
        b = self._pick_hold()
        if b is None:
            return
        drivers = b.drivers()
        for i, n in enumerate(drivers, start=1):
            d = n.driver
            assert d is not None
            self.say(f"  {i}) {n.label}: 매수시 {d.at_entry} → 현재 {d.current.value} / 내 추정 {d.my_estimate.value}  ⟨{d.direction.value if d.direction else '-'}⟩")
        raw = self.ask("갱신할 드라이버 번호")
        if not raw or not raw.isdigit() or not (1 <= int(raw) <= len(drivers)):
            return
        n = drivers[int(raw) - 1]
        val = self.ask_float("새 현재값", required=True)
        as_of = self.ask_date("기준일", self.today) or self.today
        src = self.ask("출처", "분기보고서")
        dirn = update_driver(b, n.id, val or 0.0, as_of=as_of, source=Source(name=src or "", retrieved_at=as_of))
        nc = self.ask_date("다음 확인일", n.driver.next_check)  # type: ignore[union-attr]
        if nc and n.driver:
            n.driver.next_check = nc
        self.store.save_map(b)
        self.say(f"  ✓ {n.label} → {val}  방향 {dirn.value if dirn else '-'}")

    def hold_event(self) -> None:
        b = self._pick_hold()
        if b is None:
            return
        d = self.ask_date("사건 날짜", self.today) or self.today
        fact = self.ask("사실 (한 문장, 해석 말고 사실만)", required=True)
        drivers = b.drivers()
        for i, n in enumerate(drivers, start=1):
            self.say(f"  {i}) {n.id} {n.label}")
        aff = self.ask("영향받는 드라이버 번호 (쉼표, 없으면 Enter)")
        affects = [drivers[int(x) - 1].id for x in (aff or "").split(",") if x.strip().isdigit() and 1 <= int(x) <= len(drivers)]
        self.say("방향 (논제 기준):")
        direction = self.ask_choice("선택", [("강화", "강화"), ("약화", "약화"), ("중립", "중립")], "중립")
        self.say("주가 반영 여부:")
        priced = self.ask_choice("선택", [("예", "예"), ("부분", "부분"), ("아니오", "아니오")], "아니오")
        self.say("중요도:")
        mat = self.ask_choice("선택", [("H", "H"), ("M", "M"), ("L", "L")], "M")
        src = self.ask("출처", "공시")
        ev = Event(date=d, fact=fact or "", affects=affects, direction=direction or "중립", priced_in=priced or "아니오",
                   materiality=mat or "M", source=Source(name=src or "", retrieved_at=d))
        apply_event(b, ev)
        self.store.save_map(b)
        self.say(f"  ✓ 로그 추가 ({'노이즈' if ev.is_noise() else '알림 대상'})")

    def hold_thesis(self) -> None:
        b = self._pick_hold()
        if b is None:
            return
        self.say(f"  원래 논제: {b.value('B1.1')}")
        for n in b.drivers():
            d = n.driver
            assert d is not None
            self.say(f"   - {n.label}: 매수시 {d.at_entry} → 현재 {d.current.value} / 추정 {d.my_estimate.value} ⟨{d.direction.value if d.direction else '-'}⟩")
        self.say("논제 상태:")
        st = self.ask_choice("선택", [(s.value, s.value) for s in ThesisState], b.value("B2.3") or "유효")
        if not st:
            return
        note = self.ask("판정 근거 (한 줄)")
        set_thesis_state(b, st, today=self.today, note=note or "")
        q = self.ask_yes("오늘 현금이 있다면 이 가격·이 비중에 새로 사겠는가?", bool(b.value("B0.1")))
        b["B0.1"].set(q, as_of=self.today)
        if not q:
            self.say("보유 이유:")
            r = self.ask_choice("선택", [("세금", "세금"), ("유동성", "유동성"), ("촉매 임박", "촉매 임박"), ("없음", "없음 — 정당한 이유가 아님")], "없음")
            if r:
                b["B0.2"].set(r, as_of=self.today)
        self.store.save_map(b)
        self.say("  ✓ 저장")

    def hold_evaluate(self) -> None:
        b = self._pick_hold()
        if b is None:
            return
        res = evaluate_holding(b, today=self.today)
        self.hr(f"평가 — {b.name}")
        self.say(res.summary())
        self.store.save_map(b)
        if not self.ask_yes("대응을 지금 결정·기록할까요?", True):
            return
        default = res.matrix.action.value
        self.say("대응:")
        act = self.ask_choice("선택", [(a.value, a.value) for a in Action if a != Action.review], default) or default
        reason = self.ask("사유" + (" (매트릭스와 다르므로 필수)" if act != default else ""), required=act != default)
        size = self.ask("규모 (예 7.2% → 5%, 전량)")
        self.say("편향 체크 — 솔직하게:")
        bc = BiasCheck(
            anchoring=self.ask_yes("매입가/본전에 앵커링하고 있나"),
            sunk_cost=self.ask_yes("매몰비용 때문에 들고 있나"),
            disposition=self.ask_yes("이익 종목이라 팔기 쉬운 쪽으로 기울었나 (처분효과)"),
            endowment=self.ask_yes("이 종목을 안 갖고 있어도 지금 이 뷰일까 → 아니면 y"),
            confirmation=self.ask_yes("좋은 뉴스만 찾아 읽었나"),
            breakeven_plan=self.ask_yes("'본전 오면 판다'가 계획의 일부인가"),
        )
        log = record_decision(b, act, today=self.today, reason=reason or "", size=size, bias_check=bc)
        self.store.save_map(b)
        self.say(f"  ✓ 결정 {log.decision.value}{' (오버라이드)' if log.override else ''} 기록 · 편향 {bc.flagged() or '없음'}")

    def hold_reconstruct(self) -> None:
        self.hr("논제 복원 위자드 — 맵 없이 보유 중인 종목")
        t = self.ask("종목코드", required=True)
        assert t
        it = self.store.get_watch(t)
        name = self.ask("종목명", it.name if it else None, required=True)
        self.say("투자 유형:")
        inv = self.ask_choice("선택", INVEST_TYPES, (it.invest_type if it else None) or "growth")
        if it is None:
            self.store.add_watch(t, name or "", invest_type=inv)
        entry_date = self.ask_date("매수일 (대략)", self.today) or self.today
        price = self.ask_float("매입 단가 (원) — 세금·복기용, 판단 입력 아님", required=True)
        qty = self.ask_float("수량", required=True)
        w = self.ask_float("목표 비중 % (예 5)", 5.0, scale=0.01)
        pos = Position(entry_date=entry_date, entry_price=price or 0, quantity=qty or 0, w_target=w or 0.05)
        self.say("  왜 샀는지 한 문장으로 복원해 보세요. 정말 기억이 없으면 Enter — '논제 없는 보유' 로 표시됩니다 (그 자체가 신호).")
        thesis = self.ask("원래 논제")
        drivers: list[dict[str, Any]] = []
        while len(drivers) < 5 and self.ask_yes(f"핵심 드라이버 {len(drivers) + 1} 입력?", len(drivers) < 2 and bool(thesis)):
            label = self.ask("변수 이름", required=True)
            drivers.append({"label": label, "current": self.ask_float("현재값"), "my_estimate": self.ask_float("내 추정"),
                            "at_entry": self.ask_float("매수 당시 값 (모르면 Enter)"),
                            "check_indicator": self.ask("확인 지표") or "", "next_check": self.ask_date("다음 확인일")})
        kills: list[str] = []
        if drivers and self.ask_yes("무효화 조건(사실 기준)을 하나 넣을까요?", True):
            tmp = reconstruct_holding(t, name or "", pos, invest_type=inv, thesis=thesis, drivers=drivers, today=self.today)
            cond = self._build_condition(tmp, None)
            if cond:
                kills.append(cond)
        price_now = self.ask_float("현재가 (원)")
        w_now = self.ask_float("현재 비중 % (예 7.2)", scale=0.01)
        b = reconstruct_holding(t, name or "", pos, invest_type=inv, thesis=thesis, drivers=drivers, kill_conditions=kills,
                                today=self.today, market=MarketState(as_of=self.today, price=price_now, w_now=w_now))
        self.store.save_map(b)
        self.say(f"  ✓ B 맵 생성: {b.map_id}  복원 품질 {b.value('B1.5')}")
        if self.ask_yes("바로 평가할까요?", True):
            res = evaluate_holding(b, today=self.today)
            self.say(res.summary())
            self.store.save_map(b)

    def hold_close(self) -> None:
        b = self._pick_hold()
        if b is None:
            return
        self.hr(f"종료 & 복기 — {b.name}")
        ok = self.ask_choice("논제가 맞았나", [("y", "맞았다"), ("n", "틀렸다"), ("p", "부분")], "p")
        wrong = self.ask("틀렸다면 어느 가정이 틀렸나")
        biases = self.ask("반복된 편향 (쉼표)")
        lesson = self.ask("다음에 다르게 할 것 (한 줄)")
        close_holding(b, today=self.today, thesis_correct={"y": True, "n": False}.get(ok or "p"),
                      wrong_assumption=wrong or "", biases=[x.strip() for x in (biases or "").split(",") if x.strip()],
                      lesson=lesson or "")
        self.store.save_map(b)
        self.say("  ✓ 종료. 복기는 B8.5 와 C 맵 복기 통합(C6)에 남습니다.")

    # ====================================================================== 4. 포트폴리오 (C)
    def menu_port(self) -> None:
        self._submenu("포트폴리오 (C)", [
            ("1", "롤업 계산 (현금 입력 → C 맵 생성/갱신)", self.port_rollup),
            ("2", "요약 보기", self.port_show),
            ("3", "C 맵 HTML 렌더링", lambda: self._render_pick(MapKind.PORT)),
        ])

    def port_rollup(self) -> None:
        holds = self.store.holdings()
        if not holds:
            self.say("  열린 B 맵이 없습니다.")
            return
        cash = self.ask_float("현금 (원)", self.store.get_setting("cash"), required=True)
        ta = self.ask_float("총자산 (원, 비우면 평가금액+현금으로 계산)", self.store.get_setting("total_assets"))
        cy = self.ask_float("현금 수익률", 0.03) or 0.03
        self.store.set_setting("cash", cash)
        if ta:
            self.store.set_setting("total_assets", ta)
        c = rollup(holds, cash=cash or 0, total_assets=ta, cash_yield=cy, today=self.today, portfolio=self.store.portfolio())
        self.store.save_map(c)
        self._print_port(c)

    def _print_port(self, c: Portfolio) -> None:
        c0 = c.value("C0") or {}
        self.say(f"  총자산 {c0.get('total_assets', 0):,.0f} · 현금 {c0.get('cash_weight', 0):.1%} · 종목 {c0.get('n_holdings')}개 · 상태 {c0.get('status_dist')}")
        rows = [[LIGHT.get(h.get("light") or "", h.get("light") or "-"), h.get("ticker"), h.get("name"),
                 f"{h.get('w_now', 0):.1%}", f"{h.get('w_target', 0):.1%}", h.get("thesis_state") or "-",
                 h.get("decision") or f"({h.get('matrix_default')})" if (h.get("decision") or h.get("matrix_default")) else "-",
                 f"{h['expected_return']:+.1%}" if h.get("expected_return") is not None else "-"]
                for h in (c.value("C1.1") or [])]
        self.table(["등", "종목", "이름", "비중", "목표", "논제", "결정", "기대수익"], rows)
        c31 = c.value("C3.1") or {}
        self.say(f"  순매수 {c31.get('net_buy', 0):,.0f} / 순매도 {c31.get('net_sell', 0):,.0f} → 실행 후 현금 {c.value('C3.2') or 0:.1%}"
                 f" · 상위3 {c.value('C1.2') or 0:.0%} · HHI {c.value('C1.3') or 0:.3f} · 포트 ER {c.value('C5.4') or 0:+.1%}")
        for w in c.meta.get("warnings", []):
            self.say("  !", w)
        for cf in c.value("C3.4") or []:
            self.say("  ⚠", cf)
        q = c.value("C5.1") or {}
        if q.get("count"):
            self.say(f"  논제 없는 보유 {q['count']}개 ({q.get('weight', 0):.1%}): {', '.join(q.get('tickers', []))}")

    def port_show(self) -> None:
        c = self.store.portfolio()
        if c is None:
            self.say("  C 맵이 없습니다. 1) 롤업 계산을 먼저.")
            return
        self._print_port(c)

    # ====================================================================== 5. 렌더링
    def menu_render(self) -> None:
        self._submenu("렌더링 (HTML — 양쪽 균형/방사형, 오프라인)", [
            ("1", "A 맵", lambda: self._render_pick(MapKind.NEW)),
            ("2", "B 맵", lambda: self._render_pick(MapKind.HOLD)),
            ("3", "B 맵 요약 뷰 (🟢 종목용)", lambda: self._render_pick(MapKind.HOLD, summary=True)),
            ("4", "C 맵", lambda: self._render_pick(MapKind.PORT)),
            ("5", "모든 맵 한 번에", self.render_all),
            ("6", "맵을 마크다운으로 화면에 출력", self.render_print),
        ])

    def _render(self, m: MindMap, *, summary: bool = False, open_it: bool | None = None) -> Path:
        name = m.map_id.replace(":", "_") + ("_summary" if summary else "") + ".html"
        p = write_html(m, self.store.out_path(name), summary=summary)
        self.say(f"  ✓ {p}")
        if (self.open_browser if open_it is None else open_it):
            try:
                webbrowser.open(p.resolve().as_uri())
            except Exception as e:  # noqa: BLE001
                self.say(f"  (브라우저 열기 실패: {e})")
        return p

    def _render_pick(self, kind: MapKind, summary: bool = False) -> None:
        m = self._pick_map(kind)
        if m is not None:
            self._render(m, summary=summary)

    def render_all(self) -> None:
        n = 0
        for r in self.store.list_maps():
            self._render(self.store.load_map(r.map_id), open_it=False)
            n += 1
        self.say(f"  {n}개 → {self.store.out_dir}")

    def render_print(self) -> None:
        kind = self.ask_choice("종류", [("NEW", "A"), ("HOLD", "B"), ("PORT", "C")], "HOLD")
        m = self._pick_map(MapKind(kind or "HOLD"))
        if m is not None:
            self.say(to_markdown(m))

    def _delete_pick(self, kind: MapKind) -> None:
        m = self._pick_map(kind, "삭제할 번호", include_closed=True)
        if m is not None and self.ask_yes(f"{m.map_id} 를 삭제할까요 (파일도)?", False):
            self.store.delete_map(m.map_id, remove_file=True)
            self.say("  ✓ 삭제")

    # ====================================================================== 6. 도구
    def menu_tools(self) -> None:
        self._submenu("도구", [
            ("1", "모든 맵 검증", self.tools_validate),
            ("2", "데모 데이터 적재 (가온로보틱스 A/B/C + 관심종목)", self.tools_demo),
            ("3", "맵 인덱스 재구성 (maps/*.json 다시 읽기)", self.tools_reindex),
            ("4", "경로·설정 보기", self.tools_info),
        ])

    def tools_validate(self) -> None:
        for r in self.store.list_maps(include_closed=True):
            m = self.store.load_map(r.map_id)
            issues = validate(m, today=self.today)
            errs = [i for i in issues if i.level == "error"]
            self.say(f"  {r.kind} {r.ticker or '-'} {r.map_id}: 오류 {len(errs)} 경고 {len(issues) - len(errs)}")
            for i in errs:
                self.say("     ", i)

    def tools_demo(self) -> None:
        from .demo import build_all

        a, b, c, _ = build_all()
        self.store.add_watch("000000", "가온로보틱스", sector="KR-ROBOT", invest_type="growth", note="예시 — 가상 종목")
        for m in (a, b, c):
            self.store.save_map(m)
        self.say("  ✓ 데모 적재: 관심종목 1 + A/B/C 맵")

    def tools_reindex(self) -> None:
        self.say(f"  ✓ {self.store.reindex()}개 재구성")

    def tools_info(self) -> None:
        s = self.store.summary()
        for k, v in s.items():
            self.say(f"  {k}: {v}")
        self.say(f"  out: {self.store.out_dir}")
        for key in ("cash", "total_assets"):
            self.say(f"  setting.{key}: {self.store.get_setting(key)}")


# ========================================================================== entry
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mindmap console --text", description="kstock 마인드맵 콘솔")
    p.add_argument("--db", default=str(DEFAULT_DB), help="SQLite 경로 (기본 data/db/kstock.sqlite)")
    p.add_argument("--maps", default=None, help="맵 JSON 폴더 (기본 <db 상위>/maps)")
    p.add_argument("--out", default=None, help="렌더링 출력 폴더 (기본 <db 상위>/out)")
    p.add_argument("--no-browser", action="store_true", help="렌더링 후 브라우저를 열지 않음")
    return p


def main(argv: list[str] | None = None) -> int:
    from .env import load_dotenv

    load_dotenv()  # 프로젝트 루트 .env — ANTHROPIC_API_KEY 등. 셸 환경변수가 우선
    args = build_parser().parse_args(argv)
    if sys.platform == "win32":  # 콘솔 한글·이모지 출력
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    with Store(args.db, args.maps, args.out) as store:
        return Console(store, open_browser=not args.no_browser).run()


__all__ = ["Console", "Quit", "main", "build_parser"]
