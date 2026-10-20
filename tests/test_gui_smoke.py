"""GUI 콘솔 스모크 테스트 — tkinter 없이 돈다.

tkinter / tkinter.ttk / messagebox / filedialog 를 가짜 모듈로 바꿔 끼운 뒤 창을 만들고 모든 핸들러를
실제 Store·Ops 위에서 호출한다. 위젯 의미(배치·그리기)는 검사하지 못하지만, 화면 코드의 파이썬 오류
(이름 오류, 인자 수, 잘못된 키, 상태 전이)는 전부 여기서 걸린다. 진짜 창은 Windows 에서 띄워 본다.
"""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from helpers import D0, D1, build_all, build_new_map  # noqa: F401  (sys.path 설정)


# --------------------------------------------------------------------------- 가짜 tkinter
class _Var:
    def __init__(self, master=None, value=None, name=None):
        self._v = value if value is not None else ""

    def get(self):
        return self._v

    def set(self, v):
        self._v = v


class _BoolVar(_Var):
    def __init__(self, master=None, value=False, name=None):
        self._v = bool(value)

    def set(self, v):
        self._v = bool(v)


class _Widget:
    """모든 위젯. 메서드 호출을 받아들이고 자식·값을 흉내낸다."""

    def __init__(self, *args, **kw):
        self.kw = dict(kw)
        self.children: list[_Widget] = []
        self._items: dict[str, dict] = {}
        self._order: list[str] = []
        self._selection: tuple[str, ...] = ()
        self._text = ""
        self._tabs: list[_Widget] = []
        self._bindings: dict[str, object] = {}
        self._protocol = None
        if args and isinstance(args[0], _Widget):
            args[0].children.append(self)

    # 배치/설정
    def pack(self, *a, **k): return self
    def grid(self, *a, **k): return self
    def configure(self, *a, **k): self.kw.update(k); return self
    config = configure
    def cget(self, k): return self.kw.get(k)
    def rowconfigure(self, *a, **k): pass
    def columnconfigure(self, *a, **k): pass
    def bind(self, seq, fn=None, add=None): self._bindings[seq] = fn
    def bind_all(self, *a, **k): pass
    def unbind_all(self, *a, **k): pass
    def focus_set(self): pass
    def winfo_height(self): return 800
    def update_idletasks(self): pass
    def update(self): pass
    def after_idle(self, fn, *a): fn(*a)
    def destroy(self): pass
    def transient(self, *a): pass
    def resizable(self, *a): pass
    def grab_set(self): pass
    def wait_window(self): pass
    def protocol(self, name, fn): self._protocol = fn
    def title(self, *a): pass
    def geometry(self, *a): pass
    def minsize(self, *a): pass
    def clipboard_clear(self): pass
    def clipboard_append(self, *a): pass
    # Treeview
    def heading(self, *a, **k): pass
    def column(self, *a, **k): pass
    def tag_configure(self, *a, **k): pass
    def insert(self, parent="", index="end", iid=None, values=(), tags=(), **k):
        if iid is None:  # Text.insert 와 구분: Treeview 는 iid 를 준다
            self._text += str(index) if parent in ("1.0", "end") else ""
            return
        self._items[iid] = {"values": values, "tags": tags}
        self._order.append(iid)
        return iid
    def get_children(self, item=""): return tuple(self._order)
    def delete(self, *iids):
        if iids and iids[0] in ("1.0",):
            self._text = ""
            return
        for i in iids:
            self._items.pop(i, None)
            if i in self._order:
                self._order.remove(i)
    def selection(self): return self._selection
    def selection_set(self, *iids):
        for i in iids:
            if i not in self._items:
                raise RuntimeError(f"TclError: Item {i} not found")
        self._selection = tuple(iids)
        fn = self._bindings.get("<<TreeviewSelect>>")
        if fn:
            fn(None)
    def see(self, *a): pass
    def item(self, iid, **k): return self._items[iid]
    # Text
    def get(self, start=None, end=None): return self._text
    # Notebook
    def add(self, child, **k): self._tabs.append(child)
    def select(self, idx=None): pass
    def index(self, what): return len(self._tabs)
    # PanedWindow
    def sashpos(self, *a): pass
    # Canvas
    def create_window(self, *a, **k): return 1
    def itemconfigure(self, *a, **k): pass
    def bbox(self, *a): return (0, 0, 10, 10)
    def yview(self, *a): pass
    def yview_scroll(self, *a): pass
    def set(self, *a): pass
    # Style
    def theme_names(self): return ("vista", "clam")
    def theme_use(self, *a): pass
    # Menu
    def add_command(self, **k): pass
    def add_separator(self): pass
    def add_cascade(self, **k): pass


class _Text(_Widget):
    def insert(self, index, text, *tags):
        if index == "end":
            self._text += text
        else:
            self._text = text + self._text
    def delete(self, *a): self._text = ""
    def get(self, start=None, end=None): return self._text


class _Menu(_Widget):
    pass


def _fake_tk() -> tuple[types.ModuleType, types.ModuleType, types.ModuleType, types.ModuleType]:
    tk = types.ModuleType("tkinter")
    tk.Tk = _Widget
    tk.Toplevel = _Widget
    tk.Menu = _Menu
    tk.Text = _Text
    tk.Canvas = _Widget
    tk.Frame = _Widget
    tk.Misc = _Widget
    tk.Widget = _Widget
    tk.StringVar = _Var
    tk.BooleanVar = _BoolVar
    tk.Variable = _Var
    tk.TclError = RuntimeError
    ttk = types.ModuleType("tkinter.ttk")
    for name in ("Frame", "Label", "Button", "Entry", "Combobox", "Checkbutton", "LabelFrame", "Treeview", "Scrollbar",
                 "Notebook", "PanedWindow", "Progressbar", "Style"):
        setattr(ttk, name, _Widget)
    mb = types.ModuleType("tkinter.messagebox")
    mb.calls: list[tuple] = []
    mb.showwarning = lambda *a, **k: mb.calls.append(("warn", a))
    mb.showerror = lambda *a, **k: mb.calls.append(("error", a))
    mb.askyesno = lambda *a, **k: True
    mb.askokcancel = lambda *a, **k: True
    fd = types.ModuleType("tkinter.filedialog")
    fd.askopenfilename = lambda *a, **k: ""
    fd.askopenfilenames = lambda *a, **k: ()
    fd.asksaveasfilename = lambda *a, **k: ""
    tk.ttk, tk.messagebox, tk.filedialog = ttk, mb, fd
    return tk, ttk, mb, fd


class TestGuiSmoke(unittest.TestCase):
    def setUp(self):
        tk, ttk, mb, fd = _fake_tk()
        self.mb = mb
        self.patcher = mock.patch.dict(sys.modules, {"tkinter": tk, "tkinter.ttk": ttk, "tkinter.messagebox": mb,
                                                     "tkinter.filedialog": fd})
        self.patcher.start()
        sys.modules.pop("kstock.console", None)
        import kstock.console as console  # noqa: PLC0415

        self.console = console
        self.tmp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.app = console.Console(self.root, db=Path(self.tmp.name) / "db" / "k.sqlite", open_browser=False, debug=True)
        self.app.ops.today = D0

    def tearDown(self):
        self.app.store.close()
        self.tmp.cleanup()
        self.patcher.stop()
        sys.modules.pop("kstock.console", None)

    def test_build_and_empty_handlers(self):
        app = self.app
        self.assertEqual(app.status_var.get(), "읽었습니다")
        self.assertIn("관심종목 0", app.fresh_var.get())
        for fn in (app.a_save, app.a_eval, app.a_snapshot, app.b_evaluate, app.b_market_apply, app.b_decide, app.open_html):
            with self.assertRaises(self.console.OpsError):
                fn()
        app.validate_all()
        app.render_all()
        app.reindex()
        app.to_log()
        app.copy_log()
        app.clear_log()
        app.show_markdown("A")

    def test_watch_add_then_a_map_fill_snapshot_evaluate(self):
        app = self.app
        app.w_ticker.set("000660"); app.w_name.set("SK하이닉스"); app.w_sector.set("KR-SEMI"); app.w_type.set("growth"); app.w_note.set("HBM")
        app.watch_add_then_a()
        self.assertEqual(app.selected, "000660")
        self.assertIsNotNone(app.a_map)
        self.assertIn("000660", app.tree.get_children())
        # 폼 채우기
        v = app.a_vars
        v["thesis"].set("SK하이닉스는 HBM 덕분에 1Y 내 260,000원"); v["horizon"].set("1Y"); v["confidence"].set("M"); v["price"].set("200000")
        v["implied"].set("PER 8배"); v["mine"].set("점유율 60%"); v["why"].set("시간축"); v["band"].set("50"); v["revision"].set("up")
        for i, (label, cur, est, nc) in enumerate((("HBM 점유율", "50", "60", "2026-11-14"), ("영업이익률", "40", "48", ""), ("CAPEX", "20", "24", ""))):
            d = app.a_drivers[i]
            d["label"].set(label); d["current"].set(cur); d["my_estimate"].set(est); d["next_check"].set(nc); d["unit"].set("%")
        for nm, (p, tp) in {"Bear": ("0.25", "170000"), "Base": ("0.5", "260000"), "Bull": ("0.25", "320000")}.items():
            app.a_scen[nm]["probability"].set(p); app.a_scen[nm]["target_price"].set(tp); app.a_scen[nm]["horizon"].set("12")
        app.a_risks[0]["text"].set("가격 경쟁"); app.a_risks[0]["probability"].set("0.3"); app.a_risks[0]["impact_pct"].set("-25")
        v["w_confirmed"].set("5"); v["entry_lo"].set("180000"); v["entry_hi"].set("210000"); v["tranches"].set("3"); v["stop_dd"].set("25"); v["decision"].set("BUY")
        issues = app.a_save()
        self.assertFalse([i for i in issues if i.startswith("[ERR") and "Kill" not in i], issues)
        # 무효화 조건 빌더 (저장 후 라벨이 생겨 목록에 뜬다)
        self.assertTrue(app.a_vars["kill_driver"].get().startswith("A1.1"))
        v["kill_threshold"].set("40")
        app.a_build_kill()
        self.assertEqual(v["kill_condition"].get(), "driver[A1.1].current < 40")
        issues = app.a_save()
        self.assertFalse([i for i in issues if i.startswith("[ERR")], issues)
        app.a_eval()
        self.assertIn("손익비 2.00", app.over_text.get())
        # 스냅숏 — 대화상자를 흉내내어 결과를 넣는다
        self.console.FormDialog = _dialog_returning({"entry_date": "2026-10-19", "entry_price": "200000", "quantity": "50", "w_target": "5", "eps_fwd": ""})
        app.a_snapshot()
        self.assertIsNotNone(app.b_map)
        self.assertEqual(app.b_map.position.quantity, 50)
        # B 운영: 시장 → 드라이버 → 사실 로그 → 논제 → 평가 → 결정
        app.b_vars["price"].set("210000"); app.b_vars["w_now"].set("6"); app.b_vars["band"].set("60"); app.b_vars["total_assets"].set("100000000")
        app.b_market_apply()
        self.assertAlmostEqual(app.b_map.market.w_now, 0.06)
        app.drv_tree._selection = ("B2.1.1",)
        self.console.FormDialog = _dialog_returning({"value": "55", "as_of": "2026-12-01", "source": "IR", "my_estimate": "", "next_check": "2027-02-14"})
        app.b_driver_edit()
        self.assertEqual(app.b_map["B2.1.1"].driver.current.value, 55)
        self.console.FormDialog = _dialog_returning({"date": "2026-12-02", "fact": "HBM4 양산", "direction": "강화", "priced_in": "부분",
                                                     "materiality": "H", "source": "공시", "url": "", "aff:B2.1.1": True, "aff:B2.1.2": False})
        app.b_event_add()
        self.assertEqual(len(app.b_map.fact_log()), 1)
        app.b_vars["state"].set("유효"); app.b_vars["buy_again"].set(True)
        app.b_thesis_save()
        app.b_evaluate()
        self.assertIn(app.b_map.value("B0"), ("GREEN", "YELLOW", "RED"))
        self.assertIn("매트릭스 기본 대응", app.eval_text.get())
        app.b_bias["disposition"].set(True)
        app.b_decide()
        self.assertEqual(len(app.b_map.decision_log), 1)
        # C
        app.p_vars["cash"].set("20000000")
        app.port_rollup()
        self.assertIn("총자산", app.port_text.get())
        # 렌더링·마크다운
        app.show_markdown("B")
        self.assertIn("# ", app.map_text.get())
        app.open_html("B")
        self.assertTrue(list(app.store.out_dir.glob("*.html")))
        # 종료·복기
        self.console.FormDialog = _dialog_returning({"thesis_correct": "y", "wrong_assumption": "", "biases": "", "lesson": "좋았다"})
        closed_id = app.b_map.map_id
        app.b_close()
        self.assertIsNotNone(app.store.load_map(closed_id).closed_at)
        self.assertIsNone(app.b_map)  # 종료된 맵은 표의 B 칸에서 빠진다
        self.assertEqual(self.mb.calls, [])

    def test_reconstruct_and_demo(self):
        app = self.app
        self.console.FormDialog = _dialog_returning({
            "ticker": "111111", "name": "복원종목", "invest_type": "value", "entry_date": "2025-03-01", "entry_price": "10000",
            "quantity": "100", "w_target": "4", "price": "9000", "w_now": "3.5", "thesis": "", "d1_label": "", "d1_current": "",
            "d2_label": "", "d2_current": "", "d3_label": "", "d3_current": "", "kill_condition": ""})
        app.b_reconstruct()
        self.assertEqual(app.selected, "111111")
        self.assertTrue(app.b_map.no_thesis)
        app.b_evaluate()
        self.assertEqual(app.b_map.value("B0"), "YELLOW")
        app.load_demo()
        self.assertEqual(app.selected, "000000")
        self.assertIn("000000", app.tree.get_children())
        self.assertIn("RED", "YELLOW RED")  # 색 태그는 상태등 두 개만
        rows = app.ops.table_rows(filter_key="attention")
        self.assertEqual({r["ticker"] for r in rows}, {"000000", "111111"})


class TestGuiReportDraft(unittest.TestCase):
    """리포트 파일 여러 개 → 대화상자(종목 확인) → A 맵 초안 → 표·A 탭·개요에 반영."""

    setUp = TestGuiSmoke.setUp
    tearDown = TestGuiSmoke.tearDown

    def test_draft_from_reports(self):
        from kstock.symbols import Symbol, SymbolBook

        app = self.app
        app.ops.symbols = SymbolBook([Symbol("005930", "삼성전자", "KR", None, 5e14)], source="test")
        fixtures = sorted((Path(__file__).resolve().parent / "fixtures" / "reports").glob("*.txt"))
        # 파일 고르기 취소 → 아무 일 없음
        app.a_draft_reports()
        self.assertIsNone(app.a_map)
        # 대화상자 취소 → 아무 일 없음
        self.console.FormDialog = _dialog_returning(None)
        app.a_draft_reports([str(p) for p in fixtures])
        self.assertIsNone(app.a_map)
        self.assertEqual(app.store.list_maps(), [])
        # 확인 → 초안 저장, 관심종목 등록, A 탭 로드
        self.console.FormDialog = _dialog_returning({"ticker": "005930", "name": "삼성전자", "invest_type": "growth", "use_llm": False, "open_html": True})
        app.a_draft_reports([str(p) for p in fixtures])
        self.assertIsNotNone(app.a_map)
        self.assertEqual(app.selected, "005930")
        self.assertIn("005930", app.tree.get_children())
        self.assertEqual(app.a_map.decision.value, "WATCH")
        self.assertEqual(app.a_drivers[0]["label"].get(), "분기 영업이익 (4Q26)")
        self.assertEqual(app.a_vars["decision"].get(), "WATCH")
        self.assertIn("A 맵 초안", app.over_text.get())
        self.assertTrue(list(app.store.out_dir.glob("A_005930*.html")))  # open_html 이 마지막에 HTML 을 만든다
        self.assertIn("HTML", app.status_var.get())
        # 초안을 사람이 고쳐 저장하면 그대로 A 맵 흐름을 탄다
        app.a_vars["thesis"].set("HBM4 판가 2배로 분기 OP 100조 체제 유지 — 12개월 360,000")
        app.a_drivers[0]["my_estimate"].set("110")
        issues = app.a_save()
        self.assertFalse([i for i in issues if i.startswith("[ERR")], issues)
        self.assertEqual(app.a_map["A1.1"].driver.my_estimate.value, 110)
        # LLM 체크 + 키 없음 → 입력 오류로 안내 (debug 모드라 _guard 가 다시 던진다)
        self.console.FormDialog = _dialog_returning({"ticker": "005930", "name": "삼성전자", "invest_type": "growth", "use_llm": True, "open_html": False})
        with self.assertRaises(self.console.OpsError) as cm:
            app._guard(lambda: app.a_draft_reports([str(p) for p in fixtures]))
        self.assertIn("ANTHROPIC_API_KEY", str(cm.exception))


class TestGuiSymbols(unittest.TestCase):
    """한 칸 입력: 이름만 → 코드 채움, 코드만 → 이름 채움, 여러 후보 → 고르기 대화상자."""

    setUp = TestGuiSmoke.setUp
    tearDown = TestGuiSmoke.tearDown

    def test_lookup_and_one_field_add(self):
        from kstock.symbols import Symbol, SymbolBook

        app = self.app
        app.ops.symbols = SymbolBook([Symbol("005930", "삼성전자", "KR", None, 5e14), Symbol("005935", "삼성전자우", "KR", None, 4e13),
                                      Symbol("000660", "SK하이닉스", "KR", "KR-SEMI", 2e14)], source="test")
        # 이름만 + Enter(찾기)
        app.w_name.set("sk하이닉스")
        self.assertTrue(app.watch_lookup())
        self.assertEqual((app.w_ticker.get(), app.w_name.get(), app.w_sector.get()), ("000660", "SK하이닉스", "KR-SEMI"))
        # 코드만 + 추가
        for v in (app.w_ticker, app.w_name, app.w_sector):
            v.set("")
        app.w_ticker.set("5930")
        app.watch_add()
        self.assertEqual(app.store.get_watch("005930").name, "삼성전자")
        # 여러 후보 → 대화상자에서 고른다
        self.console.FormDialog = _dialog_returning({"pick": "005935 삼성전자우 (KR)"})
        app.w_ticker.set("")
        app.w_name.set("삼성")
        app.watch_add()
        self.assertEqual(app.store.get_watch("005935").name, "삼성전자우")
        # 못 찾는 이름 → 입력 오류
        app.w_ticker.set("")
        app.w_name.set("없는회사")
        with self.assertRaises(self.console.OpsError):
            app.watch_add()
        self.assertEqual(self.mb.calls, [])


def _dialog_returning(result):
    class _D:
        def __init__(self, *a, **k):
            self.result = result
    return _D


if __name__ == "__main__":
    unittest.main()
