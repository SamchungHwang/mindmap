"""kstock 마인드맵 콘솔. 관심종목을 올리고, 맵을 만들고, 보유 종목의 상태등을 본다.

    python tools/console.py
    python tools/console.py --db data/db/kstock.sqlite

## 이 화면이 하는 일

리서치 콘솔이 "어떤 종목이 **왜** 떴는지" 를 보는 발굴 도구라면, 여기는 **판단과 대응**의
기록 도구다. 묻는 것이 셋이다.

    살 것인가            A 맵 — 논제·드라이버·시나리오·무효화 조건을 숫자로 적고 손익비 게이트를 통과하는가
    계속 들고 있을 자격이 있는가   B 맵 — 매수 당시 논제에서 무엇이 바뀌었나, 상태등(🟢🟡🔴)과 매트릭스가 무엇을 말하나
    전체로 보면 무엇을 해야 하나   C 맵 — 비중·현금·순매수/순매도

## 색은 하나만 말한다 — 주의가 필요한가

줄의 색은 **상태등**이다. 🟡(재검토)·🔴(즉시 대응)만 칠하고 🟢은 칠하지 않는다. 손익의 높낮이,
오를지 내릴지를 색으로 말하지 않는다. 평가손익은 세금·복기용 메타데이터일 뿐 판단 입력이 아니다
(템플릿 §0 "매입가 제거").

## 일은 화면이 하지 않는다

계산·검증·상태등·매트릭스는 전부 `engine` 에 있고, 폼 값을 맵으로 옮기는 것은 `ops` 가 한다.
이 파일은 **위젯을 놓고 값을 모아 `ops` 에 넘기는 일**만 한다. 그래서 tkinter 가 없는 환경에서도
`ops` 는 통째로 테스트되고, 화면은 얇다.

## 표준 라이브러리만으로 돈다

tkinter·sqlite3 뿐이다. pydantic 은 엔진 쪽 의존성이고, 그마저 없으면 화면은 뜨되 「엔진을 못 불러왔다」
고 적는다 — 빈 화면과 고장 난 화면은 다르다.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tkinter as tk
import traceback
import webbrowser
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Literal

from .env import load_dotenv
from .models import HoldingMap, MapKind, NewMap
from .ops import (
    ACTIONS,
    BIAS_FIELDS,
    CONFIDENCES,
    DECISIONS,
    EVENT_DIRECTIONS,
    HOLD_REASONS,
    HORIZONS,
    INVEST_TYPES,
    LIGHT_ICON,
    MATERIALITY,
    OPERATORS,
    PRICED_IN,
    REVISIONS,
    THESIS_STATES,
    WHY_WRONG,
    AmbiguousSymbol,
    Ops,
    OpsError,
)
from .store import DEFAULT_DB, Store

ROOT = Path(__file__).resolve().parents[2]

#: 줄 색. **상태등 하나만 말한다.**
COLORS = {
    "plain": "#ffffff",
    "yellow": "#fff4cc",
    "red": "#fbe0e0",
    "muted": "#666666",
    "warn": "#8a4700",
    "ok": "#3d5f42",
}
TABLE_ROWS = 9
FILTERS: tuple[tuple[str, str], ...] = (
    ("전체", "all"),
    ("관심종목만", "watch"),
    ("A 맵 있는 것", "a"),
    ("B 맵 있는 것", "b"),
    ("🟡🔴 주의만", "attention"),
)
FONT = "맑은 고딕"


def force_utf8() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass


# ============================================================================ 공용 대화상자
FieldSpec = tuple[str, str, str, Any, Sequence[str] | None]
"""(key, label, kind, default, values) — kind: entry | combo | check | text | heading"""


class FormDialog:
    """필드 목록으로 만드는 모달 대화상자. 확인을 누르면 `result` 에 dict, 취소면 None."""

    def __init__(self, root: tk.Misc, title: str, fields: Sequence[FieldSpec], *, width: int = 46,
                 note: str = "") -> None:
        self.result: dict[str, Any] | None = None
        self.vars: dict[str, tk.Variable] = {}
        self.texts: dict[str, tk.Text] = {}
        self.top = tk.Toplevel(root)
        self.top.title(title)
        self.top.transient(root)
        self.top.resizable(False, False)
        body = ttk.Frame(self.top, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        row = 0
        if note:
            ttk.Label(body, text=note, foreground=COLORS["muted"], wraplength=520, justify="left").grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
            row += 1
        for key, label, kind, default, values in fields:
            if kind == "heading":
                ttk.Label(body, text=label, font=(FONT, 10, "bold")).grid(
                    row=row, column=0, columnspan=2, sticky="w", pady=(10, 2))
                row += 1
                continue
            if kind == "check":
                var = tk.BooleanVar(value=bool(default))
                self.vars[key] = var
                ttk.Checkbutton(body, text=label, variable=var).grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
                row += 1
                continue
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 10), pady=3)
            if kind == "text":
                txt = tk.Text(body, height=3, width=width, font=(FONT, 10), wrap="word")
                txt.insert("1.0", "" if default is None else str(default))
                txt.grid(row=row, column=1, sticky="ew", pady=3)
                self.texts[key] = txt
            else:
                var = tk.StringVar(value="" if default is None else str(default))
                self.vars[key] = var
                if kind == "combo":
                    ttk.Combobox(body, textvariable=var, values=list(values or []), state="readonly", width=width - 4).grid(
                        row=row, column=1, sticky="w", pady=3)
                else:
                    ttk.Entry(body, textvariable=var, width=width).grid(row=row, column=1, sticky="ew", pady=3)
            row += 1
        bar = ttk.Frame(body)
        bar.grid(row=row, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(bar, text="취소", width=8, command=self._cancel).pack(side="right")
        ttk.Button(bar, text="확인", width=8, command=self._ok).pack(side="right", padx=(0, 6))
        self.top.bind("<Escape>", lambda _e: self._cancel())
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        self.top.grab_set()
        self.top.focus_set()
        self.top.wait_window()

    def _ok(self) -> None:
        out: dict[str, Any] = {k: v.get() for k, v in self.vars.items()}
        for k, t in self.texts.items():
            out[k] = t.get("1.0", "end-1c")
        self.result = out
        self.top.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.top.destroy()


# ============================================================================ 콘솔
class Console:
    """창 하나. 상태를 인스턴스에 둔다."""

    def __init__(self, root: tk.Tk, *, db: Path, maps: Path | None = None, out: Path | None = None,
                 open_browser: bool = True, debug: bool = False) -> None:
        self.root = root
        self.db = Path(db)
        self.store = Store(self.db, maps, out)
        self.ops = Ops(self.store)
        self.open_browser = open_browser
        self.debug = debug or bool(os.environ.get("KSTOCK_DEBUG"))
        self.sash_placed = False

        self.rows: dict[str, dict[str, Any]] = {}   # ticker → 표 한 줄
        self.selected: str | None = None            # 고른 종목
        self.a_map: NewMap | None = None
        self.b_map: HoldingMap | None = None
        self.log_lines = 0

        self.db_var = tk.StringVar(value=str(self.db))
        self.status_var = tk.StringVar(value="준비됨")
        self.fresh_var = tk.StringVar(value="")
        self.count_var = tk.StringVar(value="아직 안 읽었습니다.")
        self.pick_var = tk.StringVar(value="줄을 누르면 아래에 그 종목의 맵이 나옵니다.")
        self.filter_var = tk.StringVar(value=FILTERS[0][0])
        self.log_count_var = tk.StringVar(value="로그 0줄")
        # 관심종목 추가 줄
        self.w_ticker = tk.StringVar()
        self.w_name = tk.StringVar()
        self.w_market = tk.StringVar(value="KR")
        self.w_sector = tk.StringVar()
        self.w_type = tk.StringVar(value="growth")
        self.w_note = tk.StringVar()
        # A 폼 / B 폼 변수는 탭을 만들 때 채운다
        self.a_vars: dict[str, tk.Variable] = {}
        self.a_drivers: list[dict[str, tk.Variable]] = []
        self.a_scen: dict[str, dict[str, tk.Variable]] = {}
        self.a_risks: list[dict[str, tk.Variable]] = []
        self.b_vars: dict[str, tk.Variable] = {}
        self.b_bias: dict[str, tk.BooleanVar] = {}
        self.p_vars: dict[str, tk.Variable] = {}

        self._configure()
        self._build()
        self.read_db()

    # ---------- 창 ----------

    def _configure(self) -> None:
        self.root.title("kstock — 마인드맵 콘솔")
        self.root.geometry("1280x900")
        self.root.minsize(1024, 680)
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=(FONT, 16, "bold"))
        style.configure("Status.TLabel", foreground=COLORS["ok"])
        style.configure("Fresh.TLabel", font=(FONT, 10, "bold"))
        style.configure("Section.TLabel", font=(FONT, 10, "bold"))
        style.configure("Treeview", rowheight=26, font=(FONT, 10))
        style.configure("Treeview.Heading", font=(FONT, 10, "bold"))

    def _build(self) -> None:
        """위에서 아래로 넷. 표는 높이를 고정하고 탭이 남는 자리를 나눠 갖는다."""
        self._menu()
        box = ttk.Frame(self.root, padding=12)
        box.pack(fill="both", expand=True)
        self._head(box)
        self._source(box)
        self.split = ttk.PanedWindow(box, orient="vertical")
        self.split.pack(fill="both", expand=True, pady=(0, 4))
        self._table(self.split)
        self._tabs(self.split)
        self._foot(box)
        self.split.bind("<Configure>", self._place_sash)

    def _place_sash(self, _event: Any = None) -> None:
        """딱 한 번만 옮긴다. 사람이 끌어 놓은 자리를 되돌리지 않는다."""
        if self.sash_placed:
            return
        height = self.split.winfo_height()
        if height < 300:
            return
        self.sash_placed = True
        try:
            self.split.sashpos(0, int(height * 0.38))
        except tk.TclError:
            pass

    def _menu(self) -> None:
        bar = tk.Menu(self.root, tearoff=False)
        work = tk.Menu(bar, tearoff=False)
        work.add_command(label="관심종목 추가 (입력 줄로)", command=self.focus_watch)
        work.add_command(label="관심종목 CSV 가져오기…", command=self.watch_import)
        work.add_command(label="관심종목 CSV 내보내기…", command=self.watch_export)
        work.add_command(label="종목 목록 갱신 (리서치 DB → data/ref/symbols.csv)", command=lambda: self._guard(self.symbols_refresh))
        work.add_separator()
        work.add_command(label="A 맵 생성 (고른 종목)", command=lambda: self._guard(self.a_create))
        work.add_command(label="리포트로 A 맵 자동 생성 (초안)…", command=lambda: self._guard(self.a_draft_reports))
        work.add_command(label="A 맵 현재가 갱신 (지금 기준 — 네이버/리서치 DB)", command=lambda: self._guard(self.a_refresh_price))
        work.add_command(label="LLM 연결 확인 (ANTHROPIC_API_KEY 동작)", command=lambda: self._guard(self.llm_check))
        work.add_separator()
        work.add_command(label="시나리오 후보 넣기… (증권사 자체 후보 → Bear/Base/Bull)", command=lambda: self._guard(self.a_scenario_candidates))
        work.add_command(label="예시 확률 입력 (30/45/25 — 미검증 가정)", command=lambda: self._guard(self.a_example_probs))
        work.add_command(label="Kill 후보 확정… (조건부 리스크 → 관측·주기·행동 채워 Rule)", command=lambda: self._guard(self.a_kill_candidates))
        work.add_command(label="쟁점 표 (공통 견해·핵심 이견·독립 주장·미해결)", command=lambda: self._guard(self.a_issues))
        work.add_command(label="Claim 검토 대기함…", command=lambda: self._guard(self.a_review_queue))
        work.add_command(label="노드 출처 보기…", command=lambda: self._guard(self.a_source))
        work.add_command(label="새 리포트 → 변경안… (승인한 것만 적용)", command=lambda: self._guard(self.a_changes))
        work.add_command(label="A 맵 저장·검증", command=lambda: self._guard(self.a_save))
        work.add_command(label="A 맵 평가", command=lambda: self._guard(self.a_eval))
        work.add_command(label="매수 체결 → B 맵…", command=lambda: self._guard(self.a_snapshot))
        work.add_separator()
        work.add_command(label="B 맵 평가 (상태등·매트릭스)", command=lambda: self._guard(self.b_evaluate))
        work.add_command(label="논제 복원 위자드… (맵 없이 보유 중)", command=lambda: self._guard(self.b_reconstruct))
        work.add_command(label="종료·복기… (전량 매도 완료)", command=lambda: self._guard(self.b_close))
        work.add_separator()
        work.add_command(label="C 롤업 계산", command=lambda: self._guard(self.port_rollup))
        work.add_command(label="모든 맵 렌더링", command=lambda: self._guard(self.render_all))
        work.add_command(label="모든 맵 검증", command=lambda: self._guard(self.validate_all))
        work.add_separator()
        work.add_command(label="데모 데이터 적재", command=lambda: self._guard(self.load_demo))
        work.add_command(label="맵 인덱스 재구성", command=lambda: self._guard(self.reindex))
        work.add_separator()
        work.add_command(label="닫기", command=self.close)
        bar.add_cascade(label="작업", menu=work)

        view = tk.Menu(bar, tearoff=False)
        view.add_command(label="다시 읽기", command=self.read_db)
        view.add_command(label="고른 맵 HTML 열기", command=lambda: self._guard(self.open_html))
        view.add_command(label="출력 폴더 열기", command=self.open_out_dir)
        view.add_separator()
        view.add_command(label="실행 로그 보기", command=self.to_log)
        view.add_command(label="로그 복사", command=self.copy_log)
        view.add_command(label="로그 지우기", command=self.clear_log)
        bar.add_cascade(label="보기", menu=view)
        self.root.configure(menu=bar)

    def _head(self, parent: tk.Misc) -> None:
        head = ttk.Frame(parent)
        head.pack(fill="x", pady=(0, 6))
        ttk.Label(head, text="KSTOCK", font=("Arial", 13, "bold"), foreground="#ffffff",
                  background="#1f4e79", padding=(12, 7)).pack(side="left")
        ttk.Label(head, text="마인드맵 콘솔", style="Title.TLabel").pack(side="left", padx=12)
        ttk.Label(head, textvariable=self.status_var, style="Status.TLabel").pack(side="right")

        state = ttk.Frame(parent)
        state.pack(fill="x", pady=(0, 8))
        ttk.Label(state, textvariable=self.fresh_var, style="Fresh.TLabel").pack(side="left")
        ttk.Label(state, text="판단 보조 도구입니다 · 매입가·평가손익은 판단 입력이 아닙니다",
                  foreground=COLORS["muted"]).pack(side="right")

    def _source(self, parent: tk.Misc) -> None:
        box = ttk.LabelFrame(parent, text="자료와 실행", padding=10)
        box.pack(fill="x", pady=(0, 8))
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="DB").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(box, textvariable=self.db_var, state="readonly").grid(row=0, column=1, columnspan=11, sticky="ew")
        ttk.Button(box, text="다시 읽기", width=10, command=self.read_db).grid(row=0, column=12, padx=(8, 0))

        # 관심종목 추가 — **한 줄에** 둔다. 세로로 쌓을수록 아래 탭이 좁아진다.
        ttk.Label(box, text="관심종목").grid(row=1, column=0, sticky="w", pady=(8, 0))
        row = ttk.Frame(box)
        row.grid(row=1, column=1, columnspan=12, sticky="ew", pady=(8, 0))
        self.w_ticker_entry = ttk.Entry(row, textvariable=self.w_ticker, width=9)
        self._labeled(row, "종목코드", self.w_ticker_entry)
        self.w_name_entry = ttk.Entry(row, textvariable=self.w_name, width=14)
        self._labeled(row, "종목명", self.w_name_entry)
        # 둘 중 하나만 적고 Enter — 나머지는 종목 목록에서 채운다
        for entry in (self.w_ticker_entry, self.w_name_entry):
            entry.bind("<Return>", lambda _e: self._guard(self.watch_lookup))
        self._labeled(row, "시장", ttk.Combobox(row, textvariable=self.w_market, values=["KR", "US"], state="readonly", width=4))
        self._labeled(row, "섹터", ttk.Entry(row, textvariable=self.w_sector, width=11))
        self._labeled(row, "유형", ttk.Combobox(row, textvariable=self.w_type, values=list(INVEST_TYPES), state="readonly", width=10))
        self._labeled(row, "메모", ttk.Entry(row, textvariable=self.w_note, width=22))
        ttk.Button(row, text="찾기", width=6, command=lambda: self._guard(self.watch_lookup)).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="추가", width=6, command=lambda: self._guard(self.watch_add)).pack(side="left", padx=(4, 0))
        ttk.Button(row, text="추가 + A 맵", width=11, command=lambda: self._guard(self.watch_add_then_a)).pack(side="left", padx=(4, 0))
        self.symbols_note = tk.StringVar(value="종목코드나 종목명 하나만 적고 Enter(또는 찾기) — 나머지는 종목 목록에서 채웁니다. "
                                               "섹터 코드는 섹터 맵 이름(예 KR-ROBOT)과 같게, 유형은 프로파일을 정합니다")
        ttk.Label(box, textvariable=self.symbols_note, foreground=COLORS["muted"]).grid(
            row=2, column=1, columnspan=12, sticky="w", pady=(2, 0))

        ttk.Label(box, text="보기").grid(row=3, column=0, sticky="w", pady=(8, 0))
        row2 = ttk.Frame(box)
        row2.grid(row=3, column=1, columnspan=12, sticky="ew", pady=(8, 0))
        pick = ttk.Combobox(row2, textvariable=self.filter_var, values=[n for n, _ in FILTERS], state="readonly", width=12)
        pick.pack(side="left")
        pick.bind("<<ComboboxSelected>>", lambda _e: self.refresh_table())
        ttk.Label(row2, textvariable=self.count_var, foreground=COLORS["muted"]).pack(side="left", padx=12)
        self.progress = ttk.Progressbar(row2, mode="determinate", length=140)
        self.progress.pack(side="right")
        ttk.Button(row2, text="모든 맵 검증", width=11, command=lambda: self._guard(self.validate_all)).pack(side="right", padx=(0, 8))
        ttk.Button(row2, text="모든 맵 렌더링", width=12, command=lambda: self._guard(self.render_all)).pack(side="right", padx=(0, 6))
        ttk.Button(row2, text="C 롤업", width=8, command=lambda: self._guard(self.port_rollup)).pack(side="right", padx=(0, 6))

    @staticmethod
    def _labeled(parent: tk.Misc, label: str, widget: tk.Widget) -> None:
        ttk.Label(parent, text=label).pack(side="left", padx=(0, 4))
        widget.pack(side="left", padx=(0, 10))

    def _table(self, parent: ttk.PanedWindow) -> None:
        listing = ttk.LabelFrame(parent, text="관심종목과 맵", padding=8)
        listing.rowconfigure(0, weight=1)
        listing.columnconfigure(0, weight=1)
        columns = ("ticker", "name", "market", "sector", "type", "a", "light", "thesis", "weight", "decision", "next", "note")
        heads = {"ticker": "종목", "name": "이름", "market": "시장", "sector": "섹터", "type": "유형", "a": "A 맵",
                 "light": "B 맵", "thesis": "논제", "weight": "비중", "decision": "결정", "next": "다음 확인", "note": "메모"}
        widths = {"ticker": 70, "name": 120, "market": 44, "sector": 90, "type": 80, "a": 64, "light": 64,
                  "thesis": 56, "weight": 60, "decision": 70, "next": 92, "note": 220}
        self.tree = ttk.Treeview(listing, columns=columns, show="headings", selectmode="browse", height=TABLE_ROWS)
        for name in columns:
            self.tree.heading(name, text=heads[name])
            anchor: Literal["w", "e", "center"] = "w" if name in {"name", "note", "sector"} else "e" if name == "weight" else "center"
            self.tree.column(name, width=widths[name], minwidth=40, anchor=anchor)
        self.tree.tag_configure("plain", background=COLORS["plain"])
        self.tree.tag_configure("YELLOW", background=COLORS["yellow"])
        self.tree.tag_configure("RED", background=COLORS["red"])
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-Button-1>", lambda _e: self._guard(self.open_html))
        bar = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns")
        parent.add(listing, weight=2)

    def _tabs(self, parent: ttk.PanedWindow) -> None:
        detail = ttk.LabelFrame(parent, text="자세히", padding=8)
        parent.add(detail, weight=3)
        self.book = ttk.Notebook(detail)
        self.book.pack(fill="both", expand=True)
        self.over_text = self._text_tab("개요")
        self._a_tab()
        self._b_tab()
        self.map_text = self._text_tab("마인드맵", tools=self._map_tools)
        self._port_tab()
        self.log_text = self._text_tab("실행 로그", tools=self._log_tools)

    def _text_tab(self, title: str, *, tools: Callable[[ttk.Frame], None] | None = None) -> tk.Text:
        frame = ttk.Frame(self.book, padding=6)
        self.book.add(frame, text=f" {title} ")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        text = tk.Text(frame, wrap="word", height=12, font=(FONT, 10), padx=8, pady=8)
        bar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=bar.set, state="disabled")
        text.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns")
        if tools:
            row = ttk.Frame(frame)
            row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
            tools(row)
        return text

    def _log_tools(self, row: ttk.Frame) -> None:
        ttk.Label(row, textvariable=self.log_count_var, foreground=COLORS["muted"]).pack(side="left")
        ttk.Button(row, text="지우기", width=8, command=self.clear_log).pack(side="right")
        ttk.Button(row, text="복사", width=8, command=self.copy_log).pack(side="right", padx=(0, 6))

    def _map_tools(self, row: ttk.Frame) -> None:
        ttk.Label(row, text="고른 종목의 맵을 마크다운으로. HTML 은 중심에서 양쪽으로 펼쳐지며(방사형 전환 가능) 접고 펼 수 있습니다",
                  foreground=COLORS["muted"]).pack(side="left")
        ttk.Button(row, text="B 요약 HTML", width=11, command=lambda: self._guard(lambda: self.open_html("B", summary=True))).pack(side="right")
        ttk.Button(row, text="B HTML", width=8, command=lambda: self._guard(lambda: self.open_html("B"))).pack(side="right", padx=(0, 6))
        ttk.Button(row, text="A HTML", width=8, command=lambda: self._guard(lambda: self.open_html("A"))).pack(side="right", padx=(0, 6))
        ttk.Button(row, text="B 보기", width=8, command=lambda: self.show_markdown("B")).pack(side="right", padx=(0, 12))
        ttk.Button(row, text="A 보기", width=8, command=lambda: self.show_markdown("A")).pack(side="right", padx=(0, 6))

    def _scrollable(self, parent: ttk.Frame) -> ttk.Frame:
        """캔버스 안에 프레임. 폼이 길어도 창을 키우지 않는다."""
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        canvas = tk.Canvas(parent, highlightthickness=0)
        bar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas, padding=6)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=bar.set)
        canvas.grid(row=1, column=0, sticky="nsew")
        bar.grid(row=1, column=1, sticky="ns")

        def _wheel(event: Any) -> None:
            delta = -1 if event.delta > 0 else 1
            canvas.yview_scroll(delta, "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        return inner

    # ---------- A 탭 ----------

    def _a_tab(self) -> None:
        frame = ttk.Frame(self.book, padding=6)
        self.book.add(frame, text=" A 맵 채우기 ")
        bar = ttk.Frame(frame)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.a_title = tk.StringVar(value="종목을 고르면 그 종목의 A 맵이 여기에 옵니다. 없으면 「A 맵 생성」.")
        ttk.Label(bar, textvariable=self.a_title, foreground=COLORS["muted"]).pack(side="left")
        ttk.Button(bar, text="매수 체결 → B 맵…", command=lambda: self._guard(self.a_snapshot)).pack(side="right")
        ttk.Button(bar, text="HTML", width=7, command=lambda: self._guard(lambda: self.open_html("A"))).pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="평가", width=7, command=lambda: self._guard(self.a_eval)).pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="저장·검증", width=10, command=lambda: self._guard(self.a_save)).pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="A 맵 생성", width=10, command=lambda: self._guard(self.a_create)).pack(side="right", padx=(0, 12))
        ttk.Button(bar, text="리포트로 자동 생성…", width=16, command=lambda: self._guard(self.a_draft_reports)).pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="현재가 갱신", width=10, command=lambda: self._guard(self.a_refresh_price)).pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="시나리오 후보…", width=12, command=lambda: self._guard(self.a_scenario_candidates)).pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="쟁점", width=6, command=lambda: self._guard(self.a_issues)).pack(side="right", padx=(0, 6))

        f = self._scrollable(frame)
        f.columnconfigure(1, weight=1)
        r = 0
        r = self._section(f, r, "A0 투자 논제 — 한 문장. ○○은 [핵심 드라이버] 덕분에 [기간] 내 [목표가]. 시장은 아직 [X]를 반영하지 않음")
        r = self._field(f, r, "논제", "thesis", width=100)
        row = ttk.Frame(f)
        row.grid(row=r, column=0, columnspan=2, sticky="w", pady=2)
        self._inline(row, "투자기간", "horizon", "combo", HORIZONS, 6)
        self._inline(row, "확신도", "confidence", "combo", CONFIDENCES, 4)
        self._inline(row, "현재가(원)", "price", "entry", None, 10)
        self._inline(row, "가격 출처", "price_source", "entry", None, 14)
        r += 1

        r = self._section(f, r, "A1 핵심 드라이버 (3~5개) — 이 변수가 틀리면 논제가 무너지는가? 아니면 빼라. 라벨을 비우면 슬롯을 비웁니다")
        head = ("변수", "단위", "현재값", "출처", "컨센서스", "내 추정", "확인 지표", "다음 확인일", "↑좋음")
        for c, h in enumerate(head):
            ttk.Label(f, text=h, foreground=COLORS["muted"]).grid(row=r, column=c, sticky="w", padx=2)
        r += 1
        widths = (14, 6, 9, 10, 9, 9, 16, 11)
        self.a_drivers = []
        for i in range(5):
            vars_: dict[str, tk.Variable] = {"id": tk.StringVar(value=f"A1.{i + 1}")}
            for c, key in enumerate(("label", "unit", "current", "source", "consensus", "my_estimate", "check_indicator", "next_check")):
                v = tk.StringVar()
                vars_[key] = v
                ttk.Entry(f, textvariable=v, width=widths[c]).grid(row=r, column=c, sticky="w", padx=2, pady=1)
            hb = tk.BooleanVar(value=True)
            vars_["higher_is_better"] = hb
            ttk.Checkbutton(f, variable=hb).grid(row=r, column=8, sticky="w")
            self.a_drivers.append(vars_)
            r += 1

        r = self._section(f, r, "A2 컨센서스 vs 내 뷰 — 기대수익의 원천")
        r = self._field(f, r, "주가에 내재된 가정", "implied", width=100)
        r = self._field(f, r, "내가 다르게 보는 것", "mine", width=100)
        row = ttk.Frame(f)
        row.grid(row=r, column=0, columnspan=2, sticky="w", pady=2)
        self._inline(row, "시장이 틀린 이유", "why", "combo", WHY_WRONG, 8)
        r += 1

        r = self._section(f, r, "A3 시나리오 — 확률 합 1.0, 목표가 Bear < Base < Bull")
        for c, h in enumerate(("", "확률(0~1)", "목표가(원)", "핵심 가정 (쉼표)", "기간(월)", "밸류 방법")):
            ttk.Label(f, text=h, foreground=COLORS["muted"]).grid(row=r, column=c, sticky="w", padx=2)
        r += 1
        self.a_scen = {}
        for nm in ("Bear", "Base", "Bull"):
            ttk.Label(f, text=nm, font=(FONT, 10, "bold")).grid(row=r, column=0, sticky="w", padx=2)
            sv: dict[str, tk.Variable] = {}
            for c, (key, w) in enumerate((("probability", 8), ("target_price", 10), ("assumptions", 30), ("horizon", 6), ("method", 18)), start=1):
                v = tk.StringVar()
                sv[key] = v
                ttk.Entry(f, textvariable=v, width=w).grid(row=r, column=c, sticky="w", padx=2, pady=1)
            self.a_scen[nm] = sv
            r += 1
        row = ttk.Frame(f)
        row.grid(row=r, column=0, columnspan=6, sticky="w", pady=2)
        self._inline(row, "역사 밴드 백분위(0~100)", "band", "entry", None, 6)
        self._inline(row, "EPS 리비전", "revision", "combo", REVISIONS, 6)
        r += 1

        r = self._section(f, r, "A4 리스크 & 무효화 조건 — 어떤 '사실'이 확인되면 손실 크기와 무관하게 논제를 버리나")
        for c, h in enumerate(("하락 리스크", "확률(0~1)", "영향 %")):
            ttk.Label(f, text=h, foreground=COLORS["muted"]).grid(row=r, column=c, sticky="w", padx=2)
        r += 1
        self.a_risks = []
        for _ in range(3):
            rv: dict[str, tk.Variable] = {}
            for c, (key, w) in enumerate((("text", 40), ("probability", 8), ("impact_pct", 8))):
                v = tk.StringVar()
                rv[key] = v
                ttk.Entry(f, textvariable=v, width=w).grid(row=r, column=c, sticky="w", padx=2, pady=1)
            self.a_risks.append(rv)
            r += 1
        row = ttk.Frame(f)
        row.grid(row=r, column=0, columnspan=6, sticky="w", pady=2)
        self.a_vars["kill_driver"] = tk.StringVar()
        self.kill_driver_box = ttk.Combobox(row, textvariable=self.a_vars["kill_driver"], values=[], state="readonly", width=16)
        ttk.Label(row, text="무효화 조건 만들기: 드라이버").pack(side="left", padx=(0, 4))
        self.kill_driver_box.pack(side="left", padx=(0, 6))
        self._inline(row, "", "kill_op", "combo", OPERATORS, 3)
        self._inline(row, "임계값", "kill_threshold", "entry", None, 8)
        ttk.Button(row, text="조건식 넣기", command=lambda: self._guard(self.a_build_kill)).pack(side="left")
        r += 1
        r = self._field(f, r, "조건식 (fact 기준)", "kill_condition", width=50)
        r = self._field(f, r, "조건 설명", "kill_note", width=50)
        r = self._field(f, r, "조기 경보 지표", "early_warning", width=50)

        r = self._section(f, r, "A6 실행 계획")
        row = ttk.Frame(f)
        row.grid(row=r, column=0, columnspan=6, sticky="w", pady=2)
        self.a_vars["w_suggested"] = tk.StringVar()
        ttk.Label(row, text="제안 비중").pack(side="left", padx=(0, 4))
        ttk.Label(row, textvariable=self.a_vars["w_suggested"], foreground=COLORS["muted"], width=8).pack(side="left", padx=(0, 10))
        self._inline(row, "확정 비중 %", "w_confirmed", "entry", None, 6)
        self._inline(row, "사유", "w_reason", "entry", None, 20)
        r += 1
        row = ttk.Frame(f)
        row.grid(row=r, column=0, columnspan=6, sticky="w", pady=2)
        self._inline(row, "진입 가격대", "entry_lo", "entry", None, 9)
        self._inline(row, "~", "entry_hi", "entry", None, 9)
        self._inline(row, "분할", "tranches", "entry", None, 3)
        self._inline(row, "손절(재검토) 고점대비 %", "stop_dd", "entry", None, 5)
        self._inline(row, "익절 가격", "tp_price", "entry", None, 9)
        self._inline(row, "축소", "tp_size", "entry", None, 5)
        self._inline(row, "판단", "decision", "combo", DECISIONS, 7)
        r += 1

    def _section(self, parent: ttk.Frame, row: int, title: str) -> int:
        ttk.Label(parent, text=title, style="Section.TLabel").grid(row=row, column=0, columnspan=9, sticky="w", pady=(10, 2))
        return row + 1

    def _field(self, parent: ttk.Frame, row: int, label: str, key: str, *, width: int = 40) -> int:
        var = tk.StringVar()
        self.a_vars[key] = var
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=2)
        ttk.Entry(parent, textvariable=var, width=width).grid(row=row, column=1, columnspan=8, sticky="w", padx=2, pady=1)
        return row + 1

    def _inline(self, row: ttk.Frame, label: str, key: str, kind: str, values: Sequence[str] | None, width: int,
                store: dict[str, tk.Variable] | None = None) -> None:
        var = tk.StringVar()
        (store if store is not None else self.a_vars)[key] = var
        if label:
            ttk.Label(row, text=label).pack(side="left", padx=(0, 4))
        if kind == "combo":
            ttk.Combobox(row, textvariable=var, values=list(values or []), state="readonly", width=width).pack(side="left", padx=(0, 10))
        else:
            ttk.Entry(row, textvariable=var, width=width).pack(side="left", padx=(0, 10))

    # ---------- B 탭 ----------

    def _b_tab(self) -> None:
        frame = ttk.Frame(self.book, padding=6)
        self.book.add(frame, text=" B 맵 운영 ")
        bar = ttk.Frame(frame)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.b_title = tk.StringVar(value="종목을 고르면 그 종목의 B 맵이 여기에 옵니다. 없으면 A 맵에서 「매수 체결 → B 맵」 또는 「논제 복원 위자드」.")
        ttk.Label(bar, textvariable=self.b_title, foreground=COLORS["muted"]).pack(side="left")
        ttk.Button(bar, text="종료·복기…", command=lambda: self._guard(self.b_close)).pack(side="right")
        ttk.Button(bar, text="HTML", width=7, command=lambda: self._guard(lambda: self.open_html("B"))).pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="논제 복원 위자드…", command=lambda: self._guard(self.b_reconstruct)).pack(side="right", padx=(0, 12))

        f = self._scrollable(frame)
        f.columnconfigure(0, weight=1)

        # 시장 상태
        mk = ttk.LabelFrame(f, text="시장 상태 — 수집기가 없을 때는 여기에 손으로", padding=8)
        mk.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        row = ttk.Frame(mk)
        row.pack(fill="x")
        for label, key, w in (("기준일", "as_of", 11), ("현재가(원)", "price", 10), ("비중 %", "w_now", 6), ("fwd EPS", "eps_fwd", 8),
                              ("밴드 %ile", "band", 6), ("총자산(원)", "total_assets", 13), ("일평균 거래량", "adv", 10), ("현금수익률", "cash_yield", 6)):
            self._inline(row, label, key, "entry", None, w, store=self.b_vars)
        ttk.Button(row, text="갱신", width=7, command=lambda: self._guard(self.b_market_apply)).pack(side="left")

        # 드라이버
        dv = ttk.LabelFrame(f, text="핵심 드라이버 — 매수 당시 → 현재 → 내 추정. 값이 바뀌면 「값 갱신」 (이력·방향이 남습니다)", padding=8)
        dv.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        cols = ("id", "label", "at_entry", "current", "consensus", "my_estimate", "direction", "next_check")
        heads = {"id": "id", "label": "변수", "at_entry": "매수시", "current": "현재", "consensus": "컨센", "my_estimate": "내 추정",
                 "direction": "방향", "next_check": "다음 확인"}
        self.drv_tree = ttk.Treeview(dv, columns=cols, show="headings", selectmode="browse", height=4)
        for c in cols:
            self.drv_tree.heading(c, text=heads[c])
            self.drv_tree.column(c, width=70 if c != "label" else 160, anchor="w" if c == "label" else "center")
        self.drv_tree.pack(side="left", fill="x", expand=True)
        side = ttk.Frame(dv)
        side.pack(side="left", padx=(8, 0), fill="y")
        ttk.Button(side, text="값 갱신…", width=10, command=lambda: self._guard(self.b_driver_edit)).pack(pady=(0, 4))
        ttk.Button(side, text="사실 로그…", width=10, command=lambda: self._guard(self.b_event_add)).pack()
        self.drv_tree.bind("<Double-Button-1>", lambda _e: self._guard(self.b_driver_edit))

        # 논제 판정
        th = ttk.LabelFrame(f, text="논제 판정 — 처음 산 이유가 아직 유효한가 (변질 = 다른 이유로 들고 있음)", padding=8)
        th.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        row = ttk.Frame(th)
        row.pack(fill="x")
        self._inline(row, "논제 상태", "state", "combo", THESIS_STATES, 6, store=self.b_vars)
        self._inline(row, "근거", "note", "entry", None, 40, store=self.b_vars)
        self.b_vars["buy_again"] = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="오늘 현금으로 이 가격·비중에 새로 사겠는가", variable=self.b_vars["buy_again"]).pack(side="left", padx=(0, 10))
        self._inline(row, "아니오면 보유 이유", "hold_reason", "combo", HOLD_REASONS, 9, store=self.b_vars)
        ttk.Button(row, text="저장", width=7, command=lambda: self._guard(self.b_thesis_save)).pack(side="left")

        # 평가·결정
        ev = ttk.LabelFrame(f, text="평가 → 상태등·매트릭스 → 결정 기록. 매트릭스와 다른 결정에는 사유가 필요합니다", padding=8)
        ev.grid(row=3, column=0, sticky="ew")
        ev.columnconfigure(0, weight=1)
        self.eval_text = tk.Text(ev, height=6, font=(FONT, 10), wrap="word", padx=6, pady=4, state="disabled")
        self.eval_text.grid(row=0, column=0, sticky="ew")
        ttk.Button(ev, text="평가", width=8, command=lambda: self._guard(self.b_evaluate)).grid(row=0, column=1, sticky="n", padx=(8, 0))
        row = ttk.Frame(ev)
        row.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 2))
        self._inline(row, "대응", "action", "combo", ACTIONS, 7, store=self.b_vars)
        self._inline(row, "사유", "reason", "entry", None, 36, store=self.b_vars)
        self._inline(row, "규모", "size", "entry", None, 14, store=self.b_vars)
        ttk.Button(row, text="결정 기록", width=10, command=lambda: self._guard(self.b_decide)).pack(side="left")
        row = ttk.Frame(ev)
        row.grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(row, text="편향 체크 (솔직하게):", foreground=COLORS["muted"]).pack(side="left", padx=(0, 6))
        self.b_bias = {}
        for key, label in BIAS_FIELDS:
            var = tk.BooleanVar(value=False)
            self.b_bias[key] = var
            ttk.Checkbutton(row, text=label.split(" (")[0], variable=var).pack(side="left", padx=(0, 6))

    # ---------- C 탭 ----------

    def _port_tab(self) -> None:
        frame = ttk.Frame(self.book, padding=6)
        self.book.add(frame, text=" 포트폴리오 ")
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)
        row = ttk.Frame(frame)
        row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self._inline(row, "현금(원)", "cash", "entry", None, 14, store=self.p_vars)
        self._inline(row, "총자산(원, 비우면 평가금액+현금)", "total_assets", "entry", None, 14, store=self.p_vars)
        self._inline(row, "현금 수익률", "cash_yield", "entry", None, 6, store=self.p_vars)
        ttk.Button(row, text="롤업 계산", width=10, command=lambda: self._guard(self.port_rollup)).pack(side="left")
        ttk.Button(row, text="C HTML", width=8, command=lambda: self._guard(lambda: self.open_html("C"))).pack(side="left", padx=(6, 0))
        self.port_text = tk.Text(frame, wrap="word", font=(FONT, 10), padx=8, pady=8, state="disabled")
        bar = ttk.Scrollbar(frame, orient="vertical", command=self.port_text.yview)
        self.port_text.configure(yscrollcommand=bar.set)
        self.port_text.grid(row=1, column=0, sticky="nsew")
        bar.grid(row=1, column=1, sticky="ns")

    def _foot(self, parent: tk.Misc) -> None:
        foot = ttk.Frame(parent)
        foot.pack(fill="x", pady=(8, 0))
        ttk.Label(foot, textvariable=self.pick_var, foreground=COLORS["muted"]).pack(side="left")
        ttk.Button(foot, text="닫기", command=self.close).pack(side="right")
        ttk.Button(foot, text="로그 지우기", command=self.clear_log).pack(side="right", padx=6)
        ttk.Button(foot, text="실행 로그 보기", command=self.to_log).pack(side="right", padx=6)
        ttk.Button(foot, text="고른 맵 HTML 열기", command=lambda: self._guard(self.open_html)).pack(side="right")

    # ---------- 공통 ----------

    def _guard(self, fn: Callable[[], Any]) -> None:
        """입력 오류는 경고창으로, 그 밖의 예외는 로그에 트레이스백을 남기고 오류창으로."""
        try:
            fn()
        except OpsError as error:
            self.status(f"입력을 확인하세요 — {error}")
            if self.debug:
                raise
            messagebox.showwarning("입력", str(error), parent=self.root)
        except Exception as error:  # noqa: BLE001
            self.log(traceback.format_exc().rstrip())
            self.status(f"오류 — {error}")
            if self.debug:
                raise
            hint = ""
            if isinstance(error, ImportError) and "kstock" in str(error):
                # 콘솔이 떠 있는 동안 코드가 갱신되면, 시작 때 읽어 둔 모듈(models 등) 은 옛것이고 나중에 처음 읽는 모듈은 새것이라
                # 둘이 안 맞아 이런 ImportError 가 난다. 파일이 틀린 게 아니다 — 콘솔을 다시 시작하면 된다.
                hint = "\n\n코드가 갱신된 뒤 콘솔을 다시 시작하지 않은 것 같습니다 — 콘솔을 닫고 다시 실행하세요."
                self.log("힌트: 코드 갱신 뒤 콘솔 재시작 필요 (시작 때 읽은 옛 모듈과 새 모듈이 섞임)")
            messagebox.showerror("오류", f"{error}{hint}\n\n자세한 것은 실행 로그에 있습니다.", parent=self.root)

    def status(self, line: str) -> None:
        self.status_var.set(line)

    def _set(self, widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def log(self, line: str) -> None:
        stamp = datetime.now().astimezone().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{stamp}  {line}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self.log_lines += 1
        self.log_count_var.set(f"로그 {self.log_lines}줄")
        self.root.update_idletasks()

    def _drain_ops_log(self) -> None:
        for line in self.ops.log_lines:
            self.log(line)
        self.ops.log_lines.clear()

    def to_log(self) -> None:
        self.book.select(self.book.index("end") - 1)

    def copy_log(self) -> None:
        text = self.log_text.get("1.0", "end-1c")
        if not text.strip():
            self.status("로그가 비어 있습니다 — 복사할 것이 없습니다")
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
        except tk.TclError as error:
            self.status(f"복사하지 못했습니다 — {error}")
            return
        self.status(f"로그 {len(text.splitlines())}줄을 복사했습니다")

    def clear_log(self) -> None:
        self._set(self.log_text, "")
        self.log_lines = 0
        self.log_count_var.set("로그 0줄")

    def _open_path(self, path: Path) -> None:
        if not self.open_browser:
            return
        try:
            webbrowser.open(path.resolve().as_uri())
        except Exception as error:  # noqa: BLE001
            self.status(f"브라우저를 열지 못했습니다 — {error}")

    def open_out_dir(self, *, only_if_browser: bool = False) -> None:
        if only_if_browser and not self.open_browser:
            return
        p = self.store.out_dir
        try:
            if sys.platform == "win32":
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception as error:  # noqa: BLE001
            self.status(f"폴더를 열지 못했습니다 — {error}")

    def _need(self, which: str) -> Any:
        if which == "ticker":
            if not self.selected:
                raise OpsError("표에서 종목을 먼저 고르세요")
            return self.selected
        if which == "A":
            if self.a_map is None:
                raise OpsError("고른 종목에 A 맵이 없습니다 — 「A 맵 생성」")
            return self.a_map
        if which == "B":
            if self.b_map is None:
                raise OpsError("고른 종목에 B 맵이 없습니다 — 「매수 체결 → B 맵」 또는 「논제 복원 위자드」")
            return self.b_map
        raise KeyError(which)

    # ---------- 읽기 ----------

    def read_db(self) -> None:
        # 다른 도구(예시 빌더·배치)가 data/maps 에 넣어 둔 맵을 인덱스에 줍는다
        try:
            added = self.store.sync_index()
            if added:
                self.log(f"인덱스에 없던 맵 {added}개 등록")
        except Exception as error:  # noqa: BLE001
            self.log(f"인덱스 동기화 실패 — {error}")
        try:
            self.fresh_var.set(self.ops.fresh_line())
        except Exception as error:  # noqa: BLE001
            self.fresh_var.set(f"▲ 요약을 읽지 못했습니다 — {error}")
        self.refresh_table()
        c = self.store.portfolio()
        if c is not None:
            self._set(self.port_text, self.ops.portfolio_text(c))
        cash = self.store.get_setting("cash")
        ta = self.store.get_setting("total_assets")
        if cash is not None and not self.p_vars["cash"].get():
            self.p_vars["cash"].set(f"{cash:g}")
        if ta is not None and not self.p_vars["total_assets"].get():
            self.p_vars["total_assets"].set(f"{ta:g}")
        if not self.p_vars["cash_yield"].get():
            self.p_vars["cash_yield"].set("0.03")
        self.status("읽었습니다")

    def _filter_key(self) -> str:
        for name, key in FILTERS:
            if name == self.filter_var.get():
                return key
        return "all"

    def refresh_table(self) -> None:
        keep = self.selected
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.rows = {}
        rows = self.ops.table_rows(filter_key=self._filter_key())
        for r in rows:
            self.rows[r["ticker"]] = r
            light = r["light"] or ""
            tag = light if light in ("YELLOW", "RED") else "plain"
            self.tree.insert("", "end", iid=r["ticker"], tags=(tag,), values=(
                r["ticker"], r["name"], r["market"], r["sector"] or "", r["invest_type"] or "", r["a_status"] or "",
                f"{LIGHT_ICON.get(light, '')} {light}".strip() if light else "", r["thesis"], r["weight"], r["decision"],
                r["next_check"], r["note"] or ""))
        n_att = sum(1 for r in rows if r["light"] in ("YELLOW", "RED"))
        self.count_var.set(f"{len(rows)}종목 · 주의 {n_att}")
        if keep and keep in self.rows:
            self._select(keep)
        else:
            self.selected = None

    def _select(self, ticker: str | None) -> None:
        """표에 있는 줄만 고른다. 필터로 가려진 종목을 고르면 Tk 가 오류를 낸다."""
        if ticker and ticker in self.rows:
            self.tree.selection_set(ticker)
            self.tree.see(ticker)

    def on_select(self, _event: Any = None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        ticker = sel[0]
        self.selected = ticker
        row = self.rows.get(ticker, {})
        self.pick_var.set(f"{ticker} {row.get('name', '')} — A {row.get('a_status') or '없음'} · B {row.get('light') or '없음'}")
        a = self.store.latest_map(ticker, MapKind.NEW)
        b = self.store.latest_map(ticker, MapKind.HOLD)
        self.a_map = a if isinstance(a, NewMap) else None
        self.b_map = b if isinstance(b, HoldingMap) else None
        self._set(self.over_text, self.ops.overview_text(ticker))
        self.a_load()
        self.b_load()
        self.show_markdown("B" if self.b_map else "A")
        self.status(f"{ticker} 선택")

    # ---------- 관심종목 ----------

    def focus_watch(self) -> None:
        self.w_ticker_entry.focus_set()

    def _watch_fields(self) -> dict[str, Any]:
        return {"ticker": self.w_ticker.get(), "name": self.w_name.get(), "market": self.w_market.get(),
                "sector": self.w_sector.get(), "invest_type": self.w_type.get(), "note": self.w_note.get()}

    def _pick_symbol(self, error: AmbiguousSymbol) -> bool:
        """후보가 여럿이면 고르게 한다. 골랐으면 입력 줄을 채우고 True."""
        labels = [c.label() for c in error.candidates]
        dlg = FormDialog(self.root, f"'{error.query}' — 어느 종목입니까?", [
            ("pick", "종목", "combo", labels[0], labels),
        ], width=48, note=f"후보 {len(labels)}개 (시가총액 큰 순). 찾는 종목이 없으면 취소하고 종목코드를 적어 주세요.")
        if dlg.result is None:
            return False
        chosen = next((c for c in error.candidates if c.label() == dlg.result["pick"]), None)
        if chosen is None:
            return False
        self.w_ticker.set(chosen.code)
        self.w_name.set(chosen.name)
        if chosen.sector and not self.w_sector.get():
            self.w_sector.set(chosen.sector)
        return True

    def watch_lookup(self) -> bool:
        """입력 줄의 코드·이름 중 하나로 나머지를 채운다. 채웠으면 True."""
        try:
            code, name, sym = self.ops.complete_symbol(self.w_ticker.get(), self.w_name.get())
        except AmbiguousSymbol as error:
            return self._pick_symbol(error)
        self.w_ticker.set(code)
        self.w_name.set(name)
        if sym and sym.sector and not self.w_sector.get():
            self.w_sector.set(sym.sector)
        self._drain_ops_log()
        self.status(f"{code} {name}" + (f" ({sym.market})" if sym else " — 목록에 없는 종목, 적은 그대로 씁니다"))
        return True

    def symbols_refresh(self) -> None:
        p, n = self.ops.refresh_symbols_csv()
        self._drain_ops_log()
        self.status(f"종목 목록 {n}개 → {p.name}")
        self.symbols_note.set(f"종목 목록 {len(self.ops.book)}개 ({', '.join(self.ops.book.sources)}) · 코드나 이름 하나만 적고 Enter")

    def watch_add(self) -> str:
        try:
            it = self.ops.add_watch(self._watch_fields())
        except AmbiguousSymbol as error:
            if not self._pick_symbol(error):
                raise OpsError("종목을 고르지 않았습니다") from error
            it = self.ops.add_watch(self._watch_fields())
        self._drain_ops_log()
        for v in (self.w_ticker, self.w_name, self.w_sector, self.w_note):
            v.set("")
        self.read_db()
        self.selected = it.ticker
        self.refresh_table()
        self._select(it.ticker)
        self.status(f"관심종목 {it.ticker} {it.name} 추가")
        return it.ticker

    def watch_add_then_a(self) -> None:
        self.watch_add()
        self.a_create()

    def watch_import(self) -> None:
        p = filedialog.askopenfilename(parent=self.root, title="관심종목 CSV", filetypes=[("CSV", "*.csv"), ("모두", "*.*")])
        if not p:
            return
        n = self.store.import_watch_csv(p)
        self.log(f"CSV 가져오기 {n}건 ← {p}")
        self.read_db()

    def watch_export(self) -> None:
        p = filedialog.asksaveasfilename(parent=self.root, title="관심종목 CSV 저장", defaultextension=".csv",
                                         initialfile="watchlist.csv", filetypes=[("CSV", "*.csv")])
        if not p:
            return
        n = self.store.export_watch_csv(p)
        self.log(f"CSV 내보내기 {n}건 → {p}")
        self.status(f"{n}건 저장")

    # ---------- A ----------

    def a_create(self) -> None:
        ticker = self._need("ticker")
        existing = self.store.latest_map(ticker, MapKind.NEW)
        if existing is not None and not messagebox.askyesno(
                "A 맵", f"{ticker} 에 A 맵이 이미 있습니다. 새로 하나 더 만들까요?\n(기존 맵은 남습니다)", parent=self.root):
            return
        m = self.ops.create_new_map(ticker)
        self._drain_ops_log()
        self.a_map = m
        self.read_db()
        self._select(ticker)
        self.a_load()
        self.book.select(1)
        self.status(f"A 맵 생성 {m.map_id} — 논제부터 채우세요")

    def a_draft_reports(self, paths: Sequence[str] | None = None) -> None:
        """증권사 리포트 파일 여러 개 → A 맵 초안. 종목은 본문 (코드) 에서 읽고 대화상자에서 고친다."""
        if paths is None:
            picked = filedialog.askopenfilenames(
                parent=self.root, title="증권사 리포트 파일 — 같은 종목의 리포트를 여러 개 고르세요",
                filetypes=[("리포트 (PDF·텍스트·HTML·DOCX)", "*.pdf *.txt *.md *.html *.htm *.docx"), ("PDF", "*.pdf"), ("모두", "*.*")])
            paths = list(picked) if picked else []
        if not paths:
            return
        from .generate import llm_available, llm_status

        self.status(f"리포트 {len(paths)}개 읽는 중…")
        self.root.update_idletasks()
        pk = self.ops.peek_reports(paths)
        llm_ok = llm_available()
        note = (f"파일 {pk['n_reports']}개 (글자 읽힘 {pk['n_readable']}) · 목표주가 {pk['n_tp']}건 · "
                f"{', '.join(pk['brokers']) or '증권사 미상'} · 최신 리포트 {pk['as_of']}\n"
                "숫자(현재가·컨센서스·추정치 표)는 규칙으로 채우고 판단은 WATCH 로 둡니다. 채운 값에는 전부 draft 태그가 붙습니다.\n"
                f"LLM: {llm_status()}")
        if pk.get("rows"):
            note += "\n" + "\n".join(pk["rows"][:8]) + ("\n…" if len(pk["rows"]) > 8 else "")
        if pk["warnings"]:
            note += "\n! " + "\n! ".join(pk["warnings"][:4])
        dlg = FormDialog(self.root, "리포트로 A 맵 자동 생성 (초안)", [
            ("ticker", "종목코드", "entry", pk["ticker"], None),
            ("name", "종목명", "entry", pk["name"], None),
            ("invest_type", "투자유형", "combo", pk["invest_type"], INVEST_TYPES),
            ("fetch_price", "현재가를 작성 시점 기준으로 조회 (네이버 금융) — 끄면 리서치 DB 종가·리포트 현재주가", "check", True, None),
            ("use_llm", "LLM 그룹 채움 — 논제·뷰·시나리오 가정·리스크·촉매를 LLM 이 쓴다 (.env 의 ANTHROPIC_API_KEY)", "check", llm_ok, None),
            ("open_html", "끝나면 마인드맵 HTML 열기", "check", True, None),
        ], note=note)
        f = dlg.result
        if f is None:
            return
        if not f.get("ticker") and not f.get("name"):
            raise OpsError("종목코드 또는 종목명을 적어 주세요 — 리포트 본문에서 코드를 찾지 못했습니다")
        f["save"] = True

        def prog(label: str, i: int, n: int) -> None:
            self.progress.configure(maximum=max(1, n), value=i)
            self.status(f"자동 생성 — {label}")
            self.root.update_idletasks()

        r = self.ops.draft_from_reports(paths, f, progress=prog)
        self._drain_ops_log()
        self.read_db()
        self.selected = r.map.ticker
        self._select(r.map.ticker)
        self.a_map = r.map
        self.a_load()
        self._set(self.over_text, r.summary_text())
        self.book.select(0)
        self.status(f"A 맵 초안 {r.map.map_id} — 검증 오류 {len(r.errors)} · 개요 탭에 생성 메모, 「A 맵 채우기」 탭에서 확정")
        if f.get("open_html"):
            self.open_html("A")

    def a_refresh_price(self) -> None:
        """고른 A 맵의 현재가를 지금 기준으로 (네이버 실시간 → 리서치 DB 종가)."""
        m = self._need("A")
        self.status("현재가 조회 중…")
        self.root.update_idletasks()
        price, as_of, src = self.ops.refresh_price(m)
        self._drain_ops_log()
        self.a_load()
        self.read_db()
        self._select(m.ticker)
        self._set(self.over_text, self.ops.overview_text(m.ticker or ""))
        self.status(f"현재가 {price:,.0f} ({as_of}) — {src}")

    def a_scenario_candidates(self) -> None:
        """증권사 자체 후보를 Bear/Base/Bull 로 — 서로 다른 증권사의 가정을 섞지 않는다. 확률은 비워 둔다."""
        m = self._need("A")
        labels = self.ops.candidate_labels(m)
        if not labels:
            raise OpsError("시나리오 후보가 없습니다 — 리포트로 자동 생성한 맵이거나 목표가가 읽힌 리포트가 있어야 합니다")
        none = "(비움)"
        fields = [("h", "증권사 한 곳의 가정 묶음만 고릅니다 (리포트 제시 케이스 > 산식 재계산 > 목표가). 확률은 넣지 않습니다.", "heading", None, None),
                  ("Bear", "Bear", "combo", none, [none, *labels]), ("Base", "Base", "combo", none, [none, *labels]), ("Bull", "Bull", "combo", none, [none, *labels])]
        dlg = FormDialog(self.root, "시나리오 후보 넣기", fields, width=70)
        if dlg.result is None:
            return
        done = []
        for name in ("Bear", "Base", "Bull"):
            v = dlg.result.get(name) or none
            if v == none:
                continue
            idx = labels.index(v)
            done.append(self.ops.apply_scenario_candidate(m, idx, name, save=False))
        if done:
            self.store.save_map(m)
        self._drain_ops_log()
        self.a_load()
        self.read_db()
        self._select(m.ticker)
        self.status("시나리오: " + " · ".join(done) if done else "시나리오 변경 없음")

    def a_kill_candidates(self) -> None:
        """리포트의 조건부 리스크 후보를 Kill Rule 로 — 관측 지표·갱신 주기·위반 시 행동을 사람이 채워야 들어간다 (설계 v4 V6)."""
        m = self._need("A")
        labels = self.ops.kill_candidate_labels(m)
        if not labels:
            raise OpsError("Kill 후보가 없습니다 — 리포트에 조건부 리스크 Claim 이 없으면 A4.4.x 를 직접 적습니다")
        fields = [("h", "자동으로는 후보까지만 만듭니다. '수익성 개선 지연' 은 '논제 붕괴' 가 아니니, 관측 지표·갱신 주기·위반 시 행동을 채운 것만 Rule 이 됩니다.", "heading", None, None),
                  ("cand", "후보", "combo", labels[0], labels),
                  ("observable", "관측 지표 (어디서 무엇을 보나)", "entry", "", None),
                  ("cadence", "갱신 주기", "combo", "분기", ["분기", "월", "주", "공시 즉시"]),
                  ("action", "위반 시 행동", "combo", "review", ["exit", "trim", "review", "hedge"]),
                  ("cond", "조건식 (빈칸이면 후보 임계값으로; 예 driver[A1.1].current < 50)", "entry", "", None)]
        dlg = FormDialog(self.root, "Kill 후보 확정", fields, width=74)
        if dlg.result is None:
            return
        idx = labels.index(dlg.result.get("cand") or labels[0])
        msg = self.ops.apply_kill_candidate(m, idx, observable=dlg.result.get("observable") or "", update_cadence=dlg.result.get("cadence") or "",
                                            action=dlg.result.get("action") or "", condition=(dlg.result.get("cond") or "").strip() or None)
        self._drain_ops_log()
        self.a_load()
        self.read_db()
        self._select(m.ticker)
        self.status("Kill 확정: " + msg)

    def a_example_probs(self) -> None:
        m = self._need("A")
        if not messagebox.askyesno("예시 확률", "30 / 45 / 25 는 통계적 근거가 없는 예시값입니다 (미검증 가정으로 기록됩니다). 넣을까요?", parent=self.root):
            return
        msg = self.ops.apply_example_probs(m)
        self._drain_ops_log()
        self.a_load()
        self.read_db()
        self._select(m.ticker)
        self.status(msg)

    def a_issues(self) -> None:
        m = self._need("A")
        text = self.ops.issues_text(m)
        self.log("쟁점 표:")
        for ln in text.split("\n"):
            self.log("  " + ln)
        self._set(self.over_text, text)
        self.status("쟁점 표 — 개요 칸과 실행 로그에 표시")

    def a_review_queue(self) -> None:
        m = self._need("A")
        rows = self.ops.review_queue(m)
        if not rows:
            self.status("검토 대기 Claim 없음")
            messagebox.showinfo("검토 대기함", "검토 대기 Claim 이 없습니다", parent=self.root)
            return
        n_req = sum(1 for r in rows if r.get("review_required") and r.get("status") == "verified")
        fields = [("h", f"검토 대기 {len(rows)}건 (자동 통과했지만 확인 권고 ⚑ {n_req}건 포함) — 승인은 verified(맵에 쓸 수 있음), 반려는 제외."
                        " '근거 불충분' 이었던 것을 승인하려면 이유를 적어야 합니다", "heading", None, None)]
        for i, r in enumerate(rows[:12]):
            flag = ("⚑ " + r["review_reason"] + "\n   ") if r.get("review_required") else ""
            fields.append((f"c{i}", f"[{r['broker']}|{r.get('tag', '')}|{','.join(r['roles']) or '-'}] {r['proposition'][:70]}\n   {flag}{r['validation']}"
                           + (f"\n   p.{r['page']} {r['quote'][:80]!r}" if r["quote"] else ""), "combo", "보류", ["보류", "승인", "반려"]))
            if r.get("needs_note"):
                fields.append((f"n{i}", "   ↳ 승인 이유 (근거 불충분 판정을 뒤집는 근거)", "entry", "", None))
        dlg = FormDialog(self.root, "Claim 검토 대기함", fields, width=74)
        if dlg.result is None:
            return
        n = 0
        for i, r in enumerate(rows[:12]):
            v = dlg.result.get(f"c{i}")
            if v in ("승인", "반려"):
                note = (dlg.result.get(f"n{i}") or "").strip()
                if v == "승인" and r.get("needs_note") and not note:
                    self.log(f"  {r['claim_id']}: 승인 이유가 없어 보류 (근거 불충분 판정은 이유를 적어야 승인된다)")
                    continue
                self.ops.review_claim(m, r["claim_id"], "approve" if v == "승인" else "reject", note=note)
                n += 1
        self._drain_ops_log()
        self.status(f"검토 {n}건 처리 — 맵에 반영하려면 「리포트로 자동 생성…」 을 다시 (저장본 Claim 사용)")

    def a_source(self) -> None:
        m = self._need("A")
        dlg = FormDialog(self.root, "노드 출처", [("nid", "노드 id (예: A2.1.1, A4.1, A8.4.1)", "entry", "A2.1.1", None)])
        if dlg.result is None:
            return
        nid = (dlg.result.get("nid") or "").strip()
        if nid not in m:
            raise OpsError(f"노드 {nid} 가 없습니다")
        info = self.ops.source_of(m, nid)
        lines = [f"출처: {info['name']}"]
        if info.get("path"):
            lines.append(f"파일: {info['path']}" + (f"  p.{info['page']}" if info.get("page") else ""))
        if info.get("quote"):
            lines.append(f"인용: {info['quote'][:400]}")
        for ln in lines:
            self.log("  " + ln)
        if info.get("path") and messagebox.askyesno("출처", "\n".join(lines) + "\n\n리포트 파일을 열까요?", parent=self.root):
            try:
                import os

                os.startfile(info["path"])  # type: ignore[attr-defined]  # Windows
            except Exception as e:  # noqa: BLE001
                self.log(f"  파일 열기 실패: {e}")
        self.status(f"{nid} 출처 — 실행 로그 참고")

    def a_changes(self, paths: list[str] | None = None) -> None:
        """기존 A 맵 + 새 리포트 → 변경안 대화상자 → 승인한 것만 적용 (원본은 maps/versions 에 보존)."""
        from .generate.changes import changeset_table

        m = self._need("A")
        if paths is None:
            paths = list(filedialog.askopenfilenames(parent=self.root, title="새 리포트 파일",
                                                     filetypes=[("리포트", "*.pdf *.txt *.md *.html *.docx"), ("모든 파일", "*.*")]))
        if not paths:
            return
        self.status("변경안 계산 중…")
        self.root.update_idletasks()
        cs = self.ops.propose_changes(m, paths, {"fetch_price": False})
        self._drain_ops_log()
        for ln in changeset_table(cs).split("\n"):
            self.log("  " + ln)
        if not cs.changes:
            self.status("변경 후보 없음")
            messagebox.showinfo("변경안", cs.summary, parent=self.root)
            return
        fields = [("h", cs.summary, "heading", None, None)]
        for c in cs.changes[:14]:
            fields.append((c.change_id, f"{c.change_id} {c.node_id} [{c.kind.value}] {c.note[:80]}" + (" (사용자 수정)" if c.user_touched else ""),
                           "combo", "보류", ["보류", "승인", "반려"]))
        dlg = FormDialog(self.root, "변경안 — 승인한 것만 적용", fields, width=70)
        if dlg.result is None:
            return
        decisions = {cid: ("approve" if v == "승인" else "reject") for cid, v in dlg.result.items() if v in ("승인", "반려")}
        done = self.ops.apply_changes(m, cs, decisions)
        self._drain_ops_log()
        self.a_load()
        self.read_db()
        self._select(m.ticker)
        self.status(f"변경안 적용 {len(done)}건 → v{m.meta.get('version', 1)}")

    def llm_check(self) -> None:
        """ANTHROPIC_API_KEY 가 실제로 동작하는지 — 인증·모델·작은 호출 1회. 키 값은 어디에도 보이지 않는다."""
        from .generate import llm_check

        self.status("LLM 연결 확인 중…")
        self.root.update_idletasks()
        r = llm_check()
        for ln in r.lines():
            self.log("  " + ln)
        self.status("LLM 연결 OK" if r.ok else "LLM 연결 실패 — 실행 로그 참고")
        (messagebox.showinfo if r.ok else messagebox.showwarning)("LLM 연결 확인", "\n".join(r.lines()), parent=self.root)

    def a_load(self) -> None:
        m = self.a_map
        if m is None:
            self.a_title.set(f"{self.selected or ''} — A 맵 없음. 「A 맵 생성」을 누르면 질문이 들어 있는 빈 맵이 생깁니다.")
            for v in self.a_vars.values():
                if isinstance(v, tk.StringVar):
                    v.set("")
            for d in self.a_drivers:
                for k, v in d.items():
                    if k != "id":
                        v.set("" if isinstance(v, tk.StringVar) else True)
            for sv in self.a_scen.values():
                for v in sv.values():
                    v.set("")
            for rv in self.a_risks:
                for v in rv.values():
                    v.set("")
            self.kill_driver_box.configure(values=[])
            return
        f = self.ops.a_form(m)
        self.a_title.set(f"{m.name} · {m.map_id} · 판단 {m.decision.value if m.decision else '작성중'} · 프로파일 {m.profile.value if m.profile else '-'}")
        for key in ("thesis", "horizon", "confidence", "price", "price_source", "implied", "mine", "why", "band", "revision",
                    "kill_condition", "kill_note", "early_warning", "w_confirmed", "w_reason", "entry_lo", "entry_hi",
                    "tranches", "stop_dd", "tp_price", "tp_size", "decision"):
            self.a_vars[key].set(f.get(key, ""))
        sug = f.get("w_suggested")
        self.a_vars["w_suggested"].set(f"{float(sug) * 100:.2f}%" if sug else "-")
        for i, d in enumerate(self.a_drivers):
            src = f["drivers"][i] if i < len(f["drivers"]) else {}
            for k, v in d.items():
                if k == "id":
                    v.set(src.get("id", f"A1.{i + 1}"))
                elif k == "higher_is_better":
                    v.set(bool(src.get("higher_is_better", True)))
                else:
                    v.set(src.get(k, ""))
        for nm, sv in self.a_scen.items():
            for k, v in sv.items():
                v.set(f["scenarios"][nm].get(k, ""))
        for i, rv in enumerate(self.a_risks):
            for k, v in rv.items():
                v.set(f["risks"][i].get(k, ""))
        labels = [f"{d['id']} {d['label']}" for d in f["drivers"] if d["label"]]
        self.kill_driver_box.configure(values=labels)
        self.a_vars["kill_driver"].set(labels[0] if labels else "")
        if not self.a_vars["kill_op"].get():
            self.a_vars["kill_op"].set("<")

    def _a_form_values(self) -> dict[str, Any]:
        f: dict[str, Any] = {k: v.get() for k, v in self.a_vars.items() if isinstance(v, tk.StringVar)}
        f["drivers"] = [{k: v.get() for k, v in d.items()} for d in self.a_drivers]
        f["scenarios"] = {nm: {k: v.get() for k, v in sv.items()} for nm, sv in self.a_scen.items()}
        f["risks"] = [{k: v.get() for k, v in rv.items()} for rv in self.a_risks]
        return f

    def a_save(self) -> list[str]:
        m = self._need("A")
        issues = self.ops.apply_a_form(m, self._a_form_values())
        self._drain_ops_log()
        errs = [i for i in issues if i.startswith("[ERR")]
        self.a_load()
        self.read_db()
        self._select(m.ticker)
        self._set(self.over_text, self.ops.overview_text(m.ticker or ""))
        self.status(f"A 맵 저장 — 검증 오류 {len(errs)}, 경고 {len(issues) - len(errs)}" + (" · 완료(판단 가능)" if not errs else ""))
        if errs:
            self._set(self.over_text, self.ops.overview_text(m.ticker or "") + "\n\n검증:\n" + "\n".join("  " + i for i in issues))
            self.book.select(0)
        return issues

    def a_eval(self) -> None:
        self.a_save()
        m = self._need("A")
        self._set(self.over_text, self.ops.overview_text(m.ticker or "") + "\n\n[평가]\n" + self.ops.eval_a_text(m))
        self.a_load()
        self.book.select(0)
        self.status("평가했습니다 — 개요 탭")

    def a_build_kill(self) -> None:
        m = self._need("A")
        pick = self.a_vars["kill_driver"].get()
        if not pick:
            raise OpsError("드라이버를 고르세요 (라벨이 있는 드라이버만 목록에 옵니다 — 먼저 저장)")
        did = pick.split(" ", 1)[0]
        cond = self.ops.build_condition(m, did, self.a_vars["kill_op"].get() or "<", self.a_vars["kill_threshold"].get())
        self.a_vars["kill_condition"].set(cond)
        self.status(f"조건식: {cond}")

    def a_snapshot(self) -> None:
        m = self._need("A")
        issues = self.a_save()
        errs = [i for i in issues if i.startswith("[ERR")]
        if errs and not messagebox.askyesno("스냅숏", "A 맵이 완료 상태가 아닙니다:\n" + "\n".join(errs[:6]) +
                                            "\n\n그래도 B 맵을 만들까요?", parent=self.root):
            return
        f = self.ops.a_form(m)
        dlg = FormDialog(self.root, f"매수 체결 → B 맵 — {m.name}", [
            ("entry_date", "체결일", "entry", self.ops.now.isoformat(), None),
            ("entry_price", "체결가 (원)", "entry", f["price"], None),
            ("quantity", "수량 (주)", "entry", "", None),
            ("w_target", "목표 비중 %", "entry", f["w_confirmed"], None),
            ("eps_fwd", "매수 시점 12M fwd EPS (주가 분해용, 선택)", "entry", "", None),
        ], note="A0·A1·A3·A4.4 가 B1 에 스냅숏으로 고정됩니다 (불변). 비중 밴드 상한 규칙이 B7 에 자동으로 생깁니다.")
        if dlg.result is None:
            return
        b = self.ops.snapshot(m, dlg.result)
        self._drain_ops_log()
        self.b_map = b
        self.read_db()
        self._select(b.ticker)
        self.on_select()
        self.book.select(2)
        self.status(f"B 맵 생성 {b.map_id} — 시장 상태를 넣고 「평가」")

    # ---------- B ----------

    def b_load(self) -> None:
        b = self.b_map
        for iid in self.drv_tree.get_children():
            self.drv_tree.delete(iid)
        if b is None:
            self.b_title.set(f"{self.selected or ''} — B 맵 없음. A 맵에서 「매수 체결 → B 맵」, 맵 없이 보유 중이면 「논제 복원 위자드」.")
            for v in self.b_vars.values():
                v.set("" if isinstance(v, tk.StringVar) else False)
            self._set(self.eval_text, "")
            return
        light = b.value("B0")
        self.b_title.set(f"{b.name} · {b.map_id} · {LIGHT_ICON.get(light, '')} {light or '미평가'} · 논제 {b.value('B2.3') or '미판정'}"
                         f" · 복원 {b.value('B1.5')} · 규칙 {len(b.rules())}개" + (" · 종료" if not b.is_open else ""))
        mk = b.market
        self.b_vars["as_of"].set(self.ops.now.isoformat())
        self.b_vars["price"].set("" if mk.price is None else f"{mk.price:g}")
        self.b_vars["w_now"].set("" if mk.w_now is None else f"{mk.w_now * 100:g}")
        self.b_vars["eps_fwd"].set("" if mk.eps_fwd is None else f"{mk.eps_fwd:g}")
        self.b_vars["band"].set("" if mk.band_percentile is None else f"{mk.band_percentile:g}")
        ta = mk.total_assets or self.store.get_setting("total_assets")
        self.b_vars["total_assets"].set("" if ta is None else f"{ta:g}")
        self.b_vars["adv"].set("" if mk.adv_shares is None else f"{mk.adv_shares:g}")
        self.b_vars["cash_yield"].set("0.03" if mk.cash_yield is None else f"{mk.cash_yield:g}")
        for n in b.drivers():
            d = n.driver
            assert d is not None
            self.drv_tree.insert("", "end", iid=n.id, values=(
                n.id, n.label, _fmt(d.at_entry), _fmt(d.current.value), _fmt(d.consensus.value), _fmt(d.my_estimate.value),
                d.direction.value if d.direction else "-", d.next_check.isoformat() if d.next_check else "-"))
        self.b_vars["state"].set(b.value("B2.3") or "")
        self.b_vars["note"].set("")
        self.b_vars["buy_again"].set(bool(b.value("B0.1")))
        self.b_vars["hold_reason"].set(b.value("B0.2") or "")
        self.b_vars["action"].set(b.value("B6") or b.meta.get("matrix_default") or "")
        self.b_vars["reason"].set("")
        self.b_vars["size"].set("")
        for v in self.b_bias.values():
            v.set(False)
        self._set(self.eval_text, "「평가」를 누르면 상태등·매트릭스 기본 대응·주가 분해가 여기에 나옵니다."
                  + (f"\n현재 기록: {light} / 매트릭스 {b.meta.get('matrix_default', '-')} [{b.meta.get('matrix_rule', '')}]" if light else ""))

    def b_market_apply(self) -> None:
        b = self._need("B")
        self.ops.b_market(b, {k: self.b_vars[k].get() for k in ("as_of", "price", "w_now", "eps_fwd", "band", "total_assets", "adv", "cash_yield")})
        self._drain_ops_log()
        self.b_load()
        self.status("시장 상태 갱신 — 「평가」로 상태등을 다시 계산하세요")

    def _picked_driver(self) -> str:
        sel = self.drv_tree.selection()
        if not sel:
            raise OpsError("드라이버 표에서 한 줄을 고르세요")
        return sel[0]

    def b_driver_edit(self) -> None:
        b = self._need("B")
        did = self._picked_driver()
        n = b[did]
        d = n.driver
        assert d is not None
        dlg = FormDialog(self.root, f"드라이버 값 갱신 — {n.label}", [
            ("value", "새 현재값", "entry", _fmt(d.current.value), None),
            ("as_of", "기준일", "entry", self.ops.now.isoformat(), None),
            ("source", "출처", "entry", d.current.source.name if d.current.source else "분기보고서", None),
            ("my_estimate", "내 추정 (바꿀 때만)", "entry", _fmt(d.my_estimate.value), None),
            ("next_check", "다음 확인일", "entry", d.next_check.isoformat() if d.next_check else "", None),
        ], note=f"매수시 {_fmt(d.at_entry)} → 현재 {_fmt(d.current.value)} / 컨센 {_fmt(d.consensus.value)} · 이전 값은 history 에 남고 방향(강화/유지/약화)이 다시 계산됩니다.")
        if dlg.result is None:
            return
        dirn = self.ops.b_driver(b, did, dlg.result)
        self._drain_ops_log()
        self.b_load()
        self.status(f"{n.label} 갱신 — 방향 {dirn}")

    def b_event_add(self) -> None:
        b = self._need("B")
        fields: list[FieldSpec] = [
            ("date", "날짜", "entry", self.ops.now.isoformat(), None),
            ("fact", "사실 (해석 말고 사실만, 한 문장)", "text", "", None),
            ("direction", "방향 (논제 기준)", "combo", "중립", EVENT_DIRECTIONS),
            ("priced_in", "주가 반영", "combo", "아니오", PRICED_IN),
            ("materiality", "중요도", "combo", "M", MATERIALITY),
            ("source", "출처", "entry", "공시", None),
            ("url", "URL", "entry", "", None),
            ("_h", "영향받는 드라이버", "heading", None, None),
        ]
        for n in b.drivers():
            fields.append((f"aff:{n.id}", f"{n.id} {n.label}", "check", False, None))
        dlg = FormDialog(self.root, f"사실 로그 추가 — {b.name}", fields,
                         note="affects 가 비어 있고 중요도 L 이면 로그만 남고 알림은 가지 않습니다.")
        if dlg.result is None:
            return
        r = dlg.result
        r["affects"] = [k.split(":", 1)[1] for k, v in r.items() if k.startswith("aff:") and v]
        self.ops.b_event(b, r)
        self._drain_ops_log()
        self.b_load()
        self.show_markdown("B")
        self.status("사실 로그 추가")

    def b_thesis_save(self) -> None:
        b = self._need("B")
        self.ops.b_thesis(b, {"state": self.b_vars["state"].get(), "note": self.b_vars["note"].get(),
                              "buy_again": bool(self.b_vars["buy_again"].get()), "hold_reason": self.b_vars["hold_reason"].get()})
        self._drain_ops_log()
        self.b_load()
        self.read_db()
        self._select(b.ticker)
        self.status(f"논제 상태 {b.value('B2.3')} 저장")

    def b_evaluate(self) -> None:
        b = self._need("B")
        res = self.ops.b_evaluate(b)
        self._drain_ops_log()
        self._set(self.eval_text, res.summary())
        self.b_vars["action"].set(res.matrix.action.value if res.matrix.action.value in ACTIONS else "")
        self.read_db()
        self._select(b.ticker)
        self.b_title.set(f"{b.name} · {b.map_id} · {LIGHT_ICON.get(res.light.light.value, '')} {res.light.light.value} · 논제 {b.value('B2.3') or '미판정'}")
        self._set(self.over_text, self.ops.overview_text(b.ticker or ""))
        self.book.select(2)
        self.status(f"평가: {res.light.light.value} · 매트릭스 {res.matrix.action.value}")

    def b_decide(self) -> None:
        b = self._need("B")
        f = {"action": self.b_vars["action"].get(), "reason": self.b_vars["reason"].get(), "size": self.b_vars["size"].get()}
        f.update({k: v.get() for k, v in self.b_bias.items()})
        msg = self.ops.b_decide(b, f)
        self._drain_ops_log()
        self.b_load()
        self.read_db()
        self._select(b.ticker)
        self._set(self.over_text, self.ops.overview_text(b.ticker or ""))
        self.status(f"결정 기록: {msg}")

    def b_close(self) -> None:
        b = self._need("B")
        dlg = FormDialog(self.root, f"종료 & 복기 — {b.name}", [
            ("thesis_correct", "논제가 맞았나", "combo", "p", ("y", "n", "p")),
            ("wrong_assumption", "틀렸다면 어느 가정이", "text", "", None),
            ("biases", "반복된 편향 (쉼표)", "entry", "", None),
            ("lesson", "다음에 다르게 할 것", "text", "", None),
        ], note="y 맞았다 / n 틀렸다 / p 부분. 복기는 B8.5 와 C 맵 복기 통합(C6)에 남습니다.")
        if dlg.result is None:
            return
        self.ops.b_close(b, dlg.result)
        self._drain_ops_log()
        self.read_db()
        self.status(f"{b.ticker} 종료 — 복기 기록")

    def b_reconstruct(self) -> None:
        pre = self.rows.get(self.selected or "", {})
        fields: list[FieldSpec] = [
            ("ticker", "종목코드", "entry", pre.get("ticker", ""), None),
            ("name", "종목명", "entry", pre.get("name", ""), None),
            ("invest_type", "투자 유형", "combo", pre.get("invest_type") or "growth", INVEST_TYPES),
            ("entry_date", "매수일 (대략)", "entry", "", None),
            ("entry_price", "매입 단가 (세금·복기용, 판단 입력 아님)", "entry", "", None),
            ("quantity", "수량", "entry", "", None),
            ("w_target", "목표 비중 %", "entry", "5", None),
            ("price", "현재가", "entry", "", None),
            ("w_now", "현재 비중 %", "entry", "", None),
            ("thesis", "원래 논제 (정말 기억이 없으면 비워 두세요 → '논제 없는 보유')", "text", "", None),
            ("_h", "핵심 드라이버 (최대 3개)", "heading", None, None),
        ]
        for i in range(1, 4):
            fields += [(f"d{i}_label", f"드라이버 {i} 이름", "entry", "", None),
                       (f"d{i}_current", "  현재값 / 내 추정 / 매수 당시 (쉼표)", "entry", "", None)]
        fields.append(("kill_condition", "무효화 조건식 (예 driver[B2.1.1].current < 3)", "entry", "", None))
        dlg = FormDialog(self.root, "논제 복원 위자드 — 맵 없이 보유 중인 종목", fields, width=52,
                         note="복원 품질이 '불가'면 상태등은 최소 🟡 가 되고 매트릭스는 '논제 복원 → 재판정' 을 돌려줍니다. 그 자체가 신호입니다.")
        if dlg.result is None:
            return
        r = dlg.result
        drivers = []
        for i in range(1, 4):
            if r.get(f"d{i}_label", "").strip():
                parts = [p.strip() for p in str(r.get(f"d{i}_current", "")).split(",")] + ["", "", ""]
                drivers.append({"label": r[f"d{i}_label"], "current": parts[0], "my_estimate": parts[1], "at_entry": parts[2]})
        r["drivers"] = drivers
        b = self.ops.reconstruct(r)
        self._drain_ops_log()
        self.read_db()
        self.selected = b.ticker
        self.refresh_table()
        self._select(b.ticker)
        self.book.select(2)
        self.status(f"B 맵 생성 {b.map_id} (복원 {b.value('B1.5')}) — 「평가」")

    # ---------- C / 렌더 / 도구 ----------

    def port_rollup(self) -> None:
        c = self.ops.rollup({k: v.get() for k, v in self.p_vars.items()})
        self._drain_ops_log()
        self._set(self.port_text, self.ops.portfolio_text(c))
        self.read_db()
        self.book.select(4)
        self.status("C 롤업 계산")

    def show_markdown(self, which: str) -> None:
        m = self.a_map if which == "A" else self.b_map
        if m is None:
            self._set(self.map_text, f"{which} 맵이 없습니다.")
            return
        self._set(self.map_text, self.ops.markdown_of(m))

    def open_html(self, which: str | None = None, *, summary: bool = False) -> None:
        if which is None:
            which = "B" if self.b_map else "A"
        if which == "C":
            m: Any = self.store.portfolio()
            if m is None:
                raise OpsError("C 맵이 없습니다 — 먼저 롤업 계산")
        else:
            m = self._need(which)
        p = self.ops.render(m, summary=summary)
        self._drain_ops_log()
        self._open_path(p)
        self.status(f"HTML → {p}")

    def render_all(self) -> None:
        rows = self.store.list_maps()
        self.progress.configure(maximum=max(1, len(rows)), value=0)
        for i, r in enumerate(rows, start=1):
            self.ops.render(self.store.load_map(r.map_id))
            self.progress.configure(value=i)
            self.root.update_idletasks()
        self._drain_ops_log()
        self.status(f"{len(rows)}개 렌더링 → {self.store.out_dir}")
        self.open_out_dir(only_if_browser=True)

    def validate_all(self) -> None:
        lines = self.ops.validate_all()
        self._set(self.over_text, "모든 맵 검증\n\n" + "\n".join(lines))
        self.book.select(0)
        self.status("검증 결과 — 개요 탭")

    def load_demo(self) -> None:
        self.ops.load_demo()
        self._drain_ops_log()
        self.read_db()
        self._select("000000")
        self.status("데모 적재 — 가온로보틱스 (가상 종목)")

    def reindex(self) -> None:
        n = self.store.reindex()
        self.log(f"인덱스 재구성 {n}개")
        self.read_db()

    def close(self) -> None:
        try:
            self.store.close()
        finally:
            self.root.destroy()


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float) and v.is_integer():
        return f"{int(v):,}"
    if isinstance(v, (int, float)):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return str(v)


# ============================================================================ entry
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="kstock 마인드맵 콘솔")
    parser.add_argument("--db", type=Path, default=None, help="SQLite 파일. 기본 data/db/kstock.sqlite")
    parser.add_argument("--maps", type=Path, default=None, help="맵 JSON 폴더 (기본 <db 상위>/maps)")
    parser.add_argument("--out", type=Path, default=None, help="렌더링 출력 폴더 (기본 <db 상위>/out)")
    parser.add_argument("--no-browser", action="store_true", help="HTML 을 만들기만 하고 브라우저를 열지 않음")
    return parser


def default_db() -> Path:
    return ROOT / DEFAULT_DB if not DEFAULT_DB.is_absolute() else DEFAULT_DB


def main(argv: list[str] | None = None) -> int:
    force_utf8()
    load_dotenv()  # 프로젝트 루트 .env — ANTHROPIC_API_KEY 등. 셸 환경변수가 우선
    args = build_parser().parse_args(argv)
    root = tk.Tk()
    app = Console(root, db=args.db or default_db(), maps=args.maps, out=args.out, open_browser=not args.no_browser)
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
