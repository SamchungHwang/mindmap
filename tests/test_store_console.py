import tempfile
import unittest
from datetime import date
from pathlib import Path

from helpers import D0, D1, build_all, build_new_map  # noqa: F401  (sys.path 설정)
from kstock.console_text import Console
from kstock.models import HoldingMap, NewMap, Portfolio
from kstock.store import Store


class ScriptedIO:
    """스크립트 입력 + 출력 수집. 입력이 바닥나면 StopIteration → Console 은 종료로 처리."""

    def __init__(self, inputs):
        self._it = iter(inputs)
        self.out: list[str] = []
        self.prompts: list[str] = []

    def input(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return next(self._it)

    def print(self, *parts, **_):
        self.out.append(" ".join(str(p) for p in parts))

    def text(self) -> str:
        return "\n".join(self.out)


def run_console(store: Store, inputs, today=D0) -> ScriptedIO:
    io = ScriptedIO(inputs)
    Console(store, input_fn=io.input, print_fn=io.print, open_browser=False, today=today).run()
    return io


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "db" / "kstock.sqlite")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_watchlist_crud_and_csv(self):
        s = self.store
        s.add_watch("000660", "SK하이닉스", sector="KR-SEMI", invest_type="growth", note="HBM")
        s.add_watch("005930", "삼성전자")
        self.assertEqual([w.ticker for w in s.list_watch()], ["000660", "005930"])
        s.update_watch("005930", invest_type="value", sector="KR-SEMI")
        self.assertEqual(s.get_watch("005930").invest_type, "value")
        self.assertEqual(len(s.list_watch(sector="KR-SEMI")), 2)
        s.archive_watch("005930")
        self.assertEqual(len(s.list_watch()), 1)
        self.assertEqual(len(s.list_watch(include_archived=True)), 2)
        # 재추가하면 다시 active, 기존 섹터는 유지
        s.add_watch("005930", "삼성전자")
        self.assertEqual(s.get_watch("005930").status, "active")
        self.assertEqual(s.get_watch("005930").sector, "KR-SEMI")
        p = Path(self.tmp.name) / "wl.csv"
        self.assertEqual(s.export_watch_csv(p), 2)
        s.delete_watch("000660")
        self.assertEqual(s.import_watch_csv(p), 2)
        self.assertIsNotNone(s.get_watch("000660"))
        with self.assertRaises(ValueError):
            s.add_watch("", "x")

    def test_sync_index_picks_up_foreign_files(self):
        a, b, c, _ = build_all()
        a.save(self.store.maps_dir / "A_foreign.json")      # 인덱스를 거치지 않고 파일만
        self.assertEqual(self.store.list_maps(), [])
        self.assertEqual(self.store.sync_index(), 1)
        self.assertEqual(self.store.list_maps()[0].map_id, a.map_id)
        self.assertEqual(self.store.sync_index(), 0)        # 두 번째는 할 일 없음

    def test_map_index_roundtrip_and_reindex(self):
        a, b, c, _ = build_all()
        for m in (a, b, c):
            self.store.save_map(m)
        rows = self.store.list_maps()
        self.assertEqual(sorted(r.kind for r in rows), ["HOLD", "NEW", "PORT"])
        self.assertEqual(next(r for r in rows if r.kind == "NEW").status, "BUY")
        self.assertEqual(next(r for r in rows if r.kind == "HOLD").status, "YELLOW")
        self.assertIsInstance(self.store.latest_map("000000", "HOLD"), HoldingMap)
        self.assertEqual(len(self.store.holdings()), 1)
        self.assertEqual(self.store.reindex(), 3)
        self.store.set_setting("cash", 1.5e7)
        self.assertEqual(self.store.get_setting("cash"), 1.5e7)
        self.assertTrue(self.store.delete_map(a.map_id, remove_file=True))
        self.assertEqual(len(self.store.list_maps("NEW")), 0)


class TestConsole(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "db" / "kstock.sqlite")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_add_watch_via_menu(self):
        io = run_console(self.store, [
            "1", "2",                       # 관심종목 → 추가
            "000660",                       # 코드만 (목록에 없음 → 종목명을 묻는다)
            "SK하이닉스", "", "KR-SEMI", "1", "HBM 증설",
            "n",                            # 바로 A 맵? 아니오
            "1",                            # 목록 보기
            "0", "0",                       # 뒤로, 종료
        ])
        w = self.store.get_watch("000660")
        self.assertIsNotNone(w)
        self.assertEqual((w.sector, w.invest_type, w.note), ("KR-SEMI", "growth", "HBM 증설"))
        self.assertIn("SK하이닉스", io.text())
        self.assertIn("종료.", io.text())

    def test_full_flow_a_to_c(self):
        self.store.add_watch("000660", "SK하이닉스", sector="KR-SEMI", invest_type="growth")
        script = [
            # ---- A 맵 생성 + 전체 채우기
            "2", "2", "000660", "3", "y",
            # A0
            "SK하이닉스는 HBM 점유율 덕분에 1Y 내 260,000원. 시장은 2027 증설분을 반영하지 않음", "2", "2", "200000", "",
            # A1 드라이버 ×3: 라벨, 단위, 현재값, 출처, 컨센, 내 추정, 근거, 확인지표, 다음확인일, 클수록 좋은가
            "HBM 점유율", "%", "50", "", "52", "60", "증설 선점", "분기 IR", "2026-11-14", "y",
            "영업이익률", "%", "40", "", "42", "48", "믹스 개선", "분기보고서", "", "y",
            "CAPEX(조원)", "조원", "20", "", "", "24", "증설", "IR", "", "y",
            # A2
            "HBM 점유율 50% 유지, PER 8배", "증설 완료 시 점유율 60%, 마진 48%", "1",
            # A4 Bear/Base/Bull: 확률, 목표가, 가정, 기간, 방법
            "0.25", "170000", "HBM 가격 하락", "", "",
            "0.5", "260000", "점유율 60%, PER 9배", "", "PER 9배 × 2027E EPS",
            "0.25", "320000", "경쟁사 지연", "", "",
            "50", "1",                       # 밴드 백분위, 리비전 up
            # A5 리스크 1, 2(건너뜀), 3(건너뜀)
            "HBM 가격 경쟁", "0.3", "-25", "", "",
            # 무효화 조건: 드라이버 1 번, <, 40 ; 설명 ; 조기 경보
            "1", "1", "40", "점유율 40% 아래면 논제 폐기", "월별 수출 데이터",
            # A7: 확정 비중(기본값), 사유, 진입 하단/상단, 분할, 손절 dd, 익절 가격(없음), 판단 BUY
            "", "신규", "180000", "210000", "", "", "", "1",
            "0",                              # A 메뉴 → 뒤로
            # ---- 스냅숏
            "2", "5", "1", "", "", "50", "", "",
            "0",
            # ---- B: 시장 갱신 → (B 메뉴에 머문 채) 평가(결정 안 함)
            "3", "2", "1", "", "210000", "6", "", "60", "100000000", "", "",
            "6", "1", "n",
            "0",
            # ---- C 롤업
            "4", "1", "20000000", "", "",
            "0", "0",
        ]
        io = run_console(self.store, script)
        text = io.text()
        a_rows = self.store.list_maps("NEW")
        b_rows = self.store.list_maps("HOLD")
        self.assertEqual(len(a_rows), 1, text)
        self.assertEqual(len(b_rows), 1, text)
        a = self.store.load_map(a_rows[0].map_id)
        b = self.store.load_map(b_rows[0].map_id)
        self.assertIsInstance(a, NewMap)
        self.assertIsInstance(b, HoldingMap)
        # A: 드라이버 3개, 시나리오, kill, BUY 유지 (RR = 60000/30000 = 2.0 ≥ rr_min 2.0)
        self.assertEqual(len(a.drivers()), 3)
        self.assertEqual(a["A1.1"].label, "HBM 점유율")
        self.assertAlmostEqual(a.scenarios()["Base"].target_price, 260000)
        self.assertEqual(a["A5.4.1"].rule.condition, "driver[A1.1].current < 40")
        self.assertEqual(a.decision.value, "BUY")
        self.assertIn("손익비 2.00", text)
        # B: 스냅숏 + 시장 상태 + 평가
        self.assertEqual(b.position.quantity, 50)
        self.assertEqual(b.market.price, 210000)
        self.assertAlmostEqual(b.market.w_now, 0.06)
        self.assertIn(b.value("B0"), ("GREEN", "YELLOW", "RED"))
        self.assertEqual(b["B1.6"].links, ["S.KR-SEMI", "M0"])
        # C
        c = self.store.portfolio()
        self.assertIsInstance(c, Portfolio)
        self.assertEqual(c.value("C0")["n_holdings"], 1)
        self.assertEqual(self.store.get_setting("cash"), 20000000)
        self.assertIn("종료.", text)

    def test_tools_demo_and_render(self):
        io = run_console(self.store, ["6", "2", "0", "5", "2", "1", "0", "0"])
        self.assertEqual(len(self.store.list_maps()), 3)
        htmls = list(self.store.out_dir.glob("*.html"))
        self.assertEqual(len(htmls), 1)
        self.assertIn("layoutBalanced", htmls[0].read_text(encoding="utf-8"))
        self.assertIn("데모 적재", io.text())

    def test_draft_from_reports_menu(self):
        fixtures = sorted((Path(__file__).resolve().parent / "fixtures" / "reports").glob("*.txt"))
        io = run_console(self.store, [
            "2", "8",                                   # A 맵 → 리포트로 자동 생성
            ";".join(str(p) for p in fixtures),         # 파일 경로들
            "", "", "1",                                # 종목코드·종목명 기본값, 투자유형 growth
            "n",                                        # LLM 안 씀
            "n",                                        # HTML 안 열기
            "0", "0",
        ], today=date(2026, 10, 20))
        text = io.text()
        self.assertIn("A 맵 초안", text)
        self.assertIn("검증: 오류 0", text)
        rows = self.store.list_maps("NEW")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].ticker, rows[0].status), ("005930", "WATCH"))
        self.assertIsNotNone(self.store.get_watch("005930"))

    def test_quit_anywhere(self):
        io = run_console(self.store, ["1", "2", "q", "0"])
        self.assertIn("종료.", io.text())
        self.assertEqual(self.store.list_watch(), [])


if __name__ == "__main__":
    unittest.main()
