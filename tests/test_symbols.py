import sqlite3
import tempfile
import unittest
from pathlib import Path

from helpers import D0, D1, build_all, build_new_map  # noqa: F401  (sys.path 설정)
from kstock.ops import AmbiguousSymbol, Ops, OpsError
from kstock.store import Store
from kstock.symbols import Symbol, SymbolBook, default_book, normalize_code, refresh_csv


def make_book() -> SymbolBook:
    """테스트마다 새로 — ops.add_watch 가 book 을 늘리므로 공유하면 서로 오염된다."""
    return SymbolBook([
        Symbol("005930", "삼성전자", "KR", None, 5e14),
        Symbol("005935", "삼성전자우", "KR", None, 4e13),
        Symbol("000660", "SK하이닉스", "KR", None, 2e14),
        Symbol("006400", "삼성SDI", "KR", None, 3e13),
        Symbol("000000", "가온로보틱스", "KR", "KR-ROBOT", 1e11),
    ], source="test")


class TestSymbolBook(unittest.TestCase):
    def setUp(self):
        global BOOK
        BOOK = make_book()

    def test_normalize_code(self):
        self.assertEqual(normalize_code("5930"), "005930")
        self.assertEqual(normalize_code("A005930"), "005930")
        self.assertEqual(normalize_code(" 000660 "), "000660")
        self.assertIsNone(normalize_code("삼성전자"))
        self.assertIsNone(normalize_code(""))

    def test_resolve_tiers(self):
        self.assertEqual(BOOK.resolve("5930").one.name, "삼성전자")
        self.assertEqual(BOOK.resolve("삼성전자").one.code, "005930")
        self.assertEqual(BOOK.resolve("삼성 전자").one.code, "005930")      # 공백 무시
        self.assertEqual(BOOK.resolve("sk하이닉스").one.code, "000660")     # 대소문자 무시
        self.assertEqual(BOOK.resolve("하이닉스").one.code, "000660")       # 포함
        r = BOOK.resolve("삼성")
        self.assertEqual(r.status, "many")
        self.assertEqual([c.name for c in r.candidates], ["삼성전자", "삼성전자우", "삼성SDI"])  # 시총 순
        self.assertEqual(BOOK.resolve("없는회사").status, "none")
        self.assertEqual(BOOK.resolve("999999").status, "none")

    def test_csv_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "symbols.csv"
            self.assertEqual(BOOK.to_csv(p), 5)
            b2 = SymbolBook.from_csv(p)
            self.assertEqual(len(b2), 5)
            self.assertEqual(b2.by_code("000000").sector, "KR-ROBOT")
            self.assertEqual(b2.by_code("005930").market_cap, 5e14)

    def test_research_db_replica(self):
        """리서치 DB 와 같은 모양(prices · reports)의 작은 복제본에서 읽는다."""
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "kstock.sqlite"
            c = sqlite3.connect(db)
            c.executescript("""
                CREATE TABLE prices (trade_date TEXT, stock_code TEXT, name TEXT, open REAL, high REAL, low REAL, close REAL,
                                     volume REAL, value REAL, market_cap REAL, shares REAL, market TEXT, sector TEXT);
                CREATE TABLE reports (report_id TEXT, section TEXT, stock_code TEXT, stock_name TEXT);
                INSERT INTO prices VALUES ('2026-09-20','005930','삼성전자',0,0,0,0,0,0,4e14,0,'KOSPI',NULL);
                INSERT INTO prices VALUES ('2026-09-21','005930','삼성전자',0,0,0,0,0,0,5e14,0,'KOSPI',NULL);
                INSERT INTO prices VALUES ('2026-09-21','035420','NAVER',0,0,0,0,0,0,3e13,0,'KOSPI',NULL);
                INSERT INTO prices VALUES ('2026-09-21','247540','에코프로비엠',0,0,0,0,0,0,2e13,0,'KOSDAQ','2차전지');
                INSERT INTO reports VALUES ('r1','company','049830','승일');
                INSERT INTO reports VALUES ('r2','company','005930','삼성전자');
            """)
            c.commit()
            c.close()
            b = SymbolBook.from_research_db(db)
            self.assertEqual(len(b), 4)                      # prices 3 + reports 에만 있는 승일 1
            self.assertEqual(b.by_code("005930").market_cap, 5e14)   # 최신 거래일 행
            self.assertEqual(b.by_code("247540").market, "KR")
            self.assertEqual(b.by_code("247540").sector, "2차전지")
            self.assertEqual(b.resolve("승일").one.code, "049830")
            self.assertEqual(b.resolve("naver").one.code, "035420")
            # default_book: 설정의 research_db 를 따라간다 + 관심종목 합치기
            store = Store(Path(d) / "mm" / "db" / "k.sqlite")
            store.set_setting("research_db", str(db))
            store.add_watch("111111", "수동종목")
            book = default_book(store, csv_path=Path(d) / "none.csv")
            self.assertEqual(len(book), 5)
            self.assertTrue(any(s.startswith("research:") for s in book.sources))
            p, n = refresh_csv(store, csv_path=Path(d) / "ref" / "symbols.csv")
            self.assertEqual(n, 4)
            self.assertTrue(p.exists())
            store.close()


class TestOneFieldWatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "db" / "k.sqlite")
        self.ops = Ops(self.store, today=D0, symbols=make_book())

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_name_only_and_code_only(self):
        it = self.ops.add_watch({"name": "SK하이닉스"})
        self.assertEqual((it.ticker, it.name), ("000660", "SK하이닉스"))
        it = self.ops.add_watch({"ticker": "5930", "invest_type": "value"})
        self.assertEqual((it.ticker, it.name, it.invest_type), ("005930", "삼성전자", "value"))
        it = self.ops.add_watch({"ticker": "가온로보틱스"})            # 코드 칸에 이름
        self.assertEqual((it.ticker, it.sector), ("000000", "KR-ROBOT"))  # 섹터도 목록에서

    def test_ambiguous_and_missing(self):
        with self.assertRaises(AmbiguousSymbol) as cm:
            self.ops.add_watch({"name": "삼성"})
        self.assertEqual(cm.exception.candidates[0].code, "005930")
        with self.assertRaises(OpsError):
            self.ops.add_watch({"ticker": "123456"})          # 목록에 없는 코드, 이름 없음
        with self.assertRaises(OpsError):
            self.ops.add_watch({"name": "없는회사"})
        with self.assertRaises(OpsError):
            self.ops.add_watch({})
        it = self.ops.add_watch({"ticker": "123456", "name": "수동입력"})   # 둘 다 주면 목록 없이도 등록
        self.assertEqual(it.name, "수동입력")
        self.assertEqual(self.ops.complete_symbol("123456", None)[1], "수동입력")  # 이제 관심종목에서 찾힌다


if __name__ == "__main__":
    unittest.main()
