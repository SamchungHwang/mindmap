"""저장소 — 관심종목과 맵 인덱스는 SQLite, 맵 본문은 JSON 파일.

    data/
      db/kstock.sqlite      watchlist · maps(인덱스) · settings
      maps/A_000000_2026-10-19.json   맵 본문 (MindMap.save)
      out/                  렌더링 결과 (HTML)

맵 본문의 진실은 JSON 파일이고, SQLite 의 `maps` 는 목록·검색용 인덱스다 (`reindex()` 로 재구성 가능).
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .models import HoldingMap, InvestType, MapKind, MindMap, NewMap, Portfolio, load_map

DEFAULT_DB = Path("data/db/kstock.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
  ticker      TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  market      TEXT NOT NULL DEFAULT 'KR',
  sector      TEXT,               -- 섹터 맵 코드 예: KR-ROBOT
  invest_type TEXT,               -- growth/value/turnaround/momentum/event
  note        TEXT,
  status      TEXT NOT NULL DEFAULT 'active',   -- active | archived
  added_at    TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS maps (
  map_id      TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,      -- NEW | HOLD | PORT | MACRO | SECTOR
  ticker      TEXT,
  name        TEXT,
  path        TEXT NOT NULL,
  status      TEXT,               -- A: 판단(BUY/WATCH/PASS/작성중) · B: 상태등 · C: -
  closed      INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_maps_ticker ON maps(ticker, kind);
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT
);
"""


@dataclass
class WatchItem:
    ticker: str
    name: str
    market: str = "KR"
    sector: str | None = None
    invest_type: str | None = None
    note: str | None = None
    status: str = "active"
    added_at: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MapRow:
    map_id: str
    kind: str
    ticker: str | None
    name: str | None
    path: str
    status: str | None
    closed: bool
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _safe_filename(map_id: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", map_id) + ".json"


def map_status(m: MindMap) -> str | None:
    if isinstance(m, NewMap):
        return m.decision.value if m.decision else "작성중"
    if isinstance(m, HoldingMap):
        return m.value("B0") or "미평가"
    return None


class Store:
    def __init__(self, db_path: str | Path = DEFAULT_DB, maps_dir: str | Path | None = None,
                 out_dir: str | Path | None = None):
        self.db_path = Path(db_path)
        base = self.db_path.parent.parent if self.db_path.parent.name == "db" else self.db_path.parent
        self.maps_dir = Path(maps_dir) if maps_dir else base / "maps"
        self.out_dir = Path(out_dir) if out_dir else base / "out"
        self.claims_dir = base / "claims"          # 설계 v3: ClaimSet 저장 (리포트별)
        self.issues_dir = base / "issues"          # IssueSet 저장 (종목별)
        self.changes_dir = base / "changes"        # ChangeSet 저장 (변경안)
        self.versions_dir = self.maps_dir / "versions"   # 승인 전 원본 보존
        self.archive_dir = self.maps_dir / "_archive"    # 「새로 만들기」 가 치운 기존 초안 보관 (v4 부록)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.maps_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            return row["value"]

    def set_setting(self, key: str, value: Any) -> None:
        self.conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, json.dumps(value, ensure_ascii=False)))
        self.conn.commit()

    # ------------------------------------------------------------------ watchlist
    def add_watch(self, ticker: str, name: str, *, market: str = "KR", sector: str | None = None,
                  invest_type: str | InvestType | None = None, note: str | None = None) -> WatchItem:
        ticker = ticker.strip()
        if not ticker or not name.strip():
            raise ValueError("ticker 와 name 은 비울 수 없다")
        it = InvestType(invest_type).value if invest_type else None
        now = _now()
        self.conn.execute(
            """INSERT INTO watchlist(ticker, name, market, sector, invest_type, note, status, added_at, updated_at)
               VALUES(?,?,?,?,?,?, 'active', ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET name=excluded.name, market=excluded.market,
                 sector=COALESCE(excluded.sector, watchlist.sector),
                 invest_type=COALESCE(excluded.invest_type, watchlist.invest_type),
                 note=COALESCE(excluded.note, watchlist.note), status='active', updated_at=excluded.updated_at""",
            (ticker, name.strip(), market, sector, it, note, now, now))
        self.conn.commit()
        return self.get_watch(ticker)  # type: ignore[return-value]

    def get_watch(self, ticker: str) -> WatchItem | None:
        row = self.conn.execute("SELECT * FROM watchlist WHERE ticker=?", (ticker,)).fetchone()
        return WatchItem(**dict(row)) if row else None

    def list_watch(self, *, include_archived: bool = False, sector: str | None = None) -> list[WatchItem]:
        sql, args = "SELECT * FROM watchlist", []
        conds = []
        if not include_archived:
            conds.append("status='active'")
        if sector:
            conds.append("sector=?")
            args.append(sector)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY added_at, ticker"
        return [WatchItem(**dict(r)) for r in self.conn.execute(sql, args)]

    def update_watch(self, ticker: str, **fields: Any) -> WatchItem | None:
        allowed = {"name", "market", "sector", "invest_type", "note", "status"}
        upd = {k: v for k, v in fields.items() if k in allowed}
        if not upd:
            return self.get_watch(ticker)
        if "invest_type" in upd and upd["invest_type"]:
            upd["invest_type"] = InvestType(upd["invest_type"]).value
        sets = ", ".join(f"{k}=?" for k in upd) + ", updated_at=?"
        self.conn.execute(f"UPDATE watchlist SET {sets} WHERE ticker=?", (*upd.values(), _now(), ticker))
        self.conn.commit()
        return self.get_watch(ticker)

    def archive_watch(self, ticker: str) -> bool:
        cur = self.conn.execute("UPDATE watchlist SET status='archived', updated_at=? WHERE ticker=?", (_now(), ticker))
        self.conn.commit()
        return cur.rowcount > 0

    def delete_watch(self, ticker: str) -> bool:
        cur = self.conn.execute("DELETE FROM watchlist WHERE ticker=?", (ticker,))
        self.conn.commit()
        return cur.rowcount > 0

    def import_watch_csv(self, path: str | Path) -> int:
        """CSV 열: ticker,name[,market][,sector][,invest_type][,note]. 머리글 필수."""
        n = 0
        with Path(path).open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if not row.get("ticker") or not row.get("name"):
                    continue
                self.add_watch(row["ticker"], row["name"], market=row.get("market") or "KR",
                               sector=row.get("sector") or None, invest_type=row.get("invest_type") or None,
                               note=row.get("note") or None)
                n += 1
        return n

    def export_watch_csv(self, path: str | Path) -> int:
        items = self.list_watch(include_archived=True)
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(WatchItem.__dataclass_fields__))
            w.writeheader()
            for it in items:
                w.writerow(it.as_dict())
        return len(items)

    # ------------------------------------------------------------------ maps
    def map_path(self, m: MindMap) -> Path:
        return self.maps_dir / _safe_filename(m.map_id)

    def save_map(self, m: MindMap) -> Path:
        """JSON 저장 + 인덱스 갱신."""
        p = self.map_path(m)
        m.save(p)
        closed = int(isinstance(m, HoldingMap) and m.closed_at is not None)
        self.conn.execute(
            """INSERT INTO maps(map_id, kind, ticker, name, path, status, closed, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(map_id) DO UPDATE SET name=excluded.name, path=excluded.path, status=excluded.status,
                 closed=excluded.closed, updated_at=excluded.updated_at""",
            (m.map_id, m.kind.value, m.ticker, m.name, str(p), map_status(m), closed,
             m.created_at.isoformat(), m.updated_at.isoformat()))
        self.conn.commit()
        return p

    def load_map(self, map_id: str) -> MindMap:
        row = self.conn.execute("SELECT path FROM maps WHERE map_id=?", (map_id,)).fetchone()
        if row is None:
            raise KeyError(f"map not found: {map_id}")
        return load_map(json.loads(Path(row["path"]).read_text(encoding="utf-8")))

    def list_maps(self, kind: MapKind | str | None = None, *, ticker: str | None = None,
                  include_closed: bool = False) -> list[MapRow]:
        sql, args, conds = "SELECT * FROM maps", [], []
        if kind:
            conds.append("kind=?")
            args.append(MapKind(kind).value)
        if ticker:
            conds.append("ticker=?")
            args.append(ticker)
        if not include_closed:
            conds.append("closed=0")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY kind, ticker, created_at"
        return [MapRow(map_id=r["map_id"], kind=r["kind"], ticker=r["ticker"], name=r["name"], path=r["path"],
                       status=r["status"], closed=bool(r["closed"]), created_at=r["created_at"],
                       updated_at=r["updated_at"]) for r in self.conn.execute(sql, args)]

    def latest_map(self, ticker: str, kind: MapKind | str) -> MindMap | None:
        rows = self.list_maps(kind, ticker=ticker)
        return self.load_map(rows[-1].map_id) if rows else None

    def archive_map(self, map_id: str) -> Path | None:
        """인덱스에서 빼고 파일은 maps/_archive/<map_id>_<시각>.json 으로 옮긴다 (지우지 않는다). 없으면 None."""
        import shutil
        from datetime import datetime

        row = self.conn.execute("SELECT path FROM maps WHERE map_id=?", (map_id,)).fetchone()
        if row is None:
            return None
        src = Path(row["path"])
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        dst = self.archive_dir / f"{_safe_filename(map_id).removesuffix('.json')}_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
        if src.is_file():
            shutil.move(str(src), str(dst))
        self.conn.execute("DELETE FROM maps WHERE map_id=?", (map_id,))
        self.conn.commit()
        return dst if src.is_file() or dst.is_file() else None

    def delete_map(self, map_id: str, *, remove_file: bool = False) -> bool:
        row = self.conn.execute("SELECT path FROM maps WHERE map_id=?", (map_id,)).fetchone()
        if row is None:
            return False
        self.conn.execute("DELETE FROM maps WHERE map_id=?", (map_id,))
        self.conn.commit()
        if remove_file:
            Path(row["path"]).unlink(missing_ok=True)
        return True

    def sync_index(self) -> int:
        """maps_dir 에 있는데 인덱스에 없는 JSON 을 등록한다 (다른 도구가 넣어 둔 맵을 줍는다). 등록 수 반환."""
        known = {Path(r["path"]).name for r in self.conn.execute("SELECT path FROM maps")}
        n = 0
        for p in sorted(self.maps_dir.glob("*.json")):
            if p.name in known:
                continue
            try:
                m = load_map(json.loads(p.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001 — 깨진 파일은 건너뛴다
                continue
            closed = int(isinstance(m, HoldingMap) and m.closed_at is not None)
            self.conn.execute(
                """INSERT INTO maps(map_id, kind, ticker, name, path, status, closed, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(map_id) DO UPDATE SET path=excluded.path, status=excluded.status, updated_at=excluded.updated_at""",
                (m.map_id, m.kind.value, m.ticker, m.name, str(p), map_status(m), closed,
                 m.created_at.isoformat(), m.updated_at.isoformat()))
            n += 1
        if n:
            self.conn.commit()
        return n

    def reindex(self) -> int:
        """maps_dir 의 JSON 을 모두 읽어 인덱스를 재구성한다."""
        self.conn.execute("DELETE FROM maps")
        n = 0
        for p in sorted(self.maps_dir.glob("*.json")):
            try:
                m = load_map(json.loads(p.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001 — 깨진 파일은 건너뛴다
                continue
            closed = int(isinstance(m, HoldingMap) and m.closed_at is not None)
            self.conn.execute(
                "INSERT INTO maps(map_id, kind, ticker, name, path, status, closed, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (m.map_id, m.kind.value, m.ticker, m.name, str(p), map_status(m), closed,
                 m.created_at.isoformat(), m.updated_at.isoformat()))
            n += 1
        self.conn.commit()
        return n

    def holdings(self, *, include_closed: bool = False) -> list[HoldingMap]:
        out: list[HoldingMap] = []
        for r in self.list_maps(MapKind.HOLD, include_closed=include_closed):
            m = self.load_map(r.map_id)
            if isinstance(m, HoldingMap):
                out.append(m)
        return out

    def portfolio(self) -> Portfolio | None:
        rows = self.list_maps(MapKind.PORT)
        if not rows:
            return None
        m = self.load_map(rows[-1].map_id)
        return m if isinstance(m, Portfolio) else None

    # ------------------------------------------------------------------ misc
    def out_path(self, name: str) -> Path:
        return self.out_dir / name

    def summary(self) -> dict[str, Any]:
        wl = self.list_watch()
        rows = self.list_maps()
        by_kind: dict[str, int] = {}
        for r in rows:
            by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
        lights = {"GREEN": 0, "YELLOW": 0, "RED": 0}
        for r in rows:
            if r.kind == "HOLD" and r.status in lights:
                lights[r.status] += 1
        return {"db": str(self.db_path), "maps_dir": str(self.maps_dir), "watchlist": len(wl),
                "maps": by_kind, "lights": lights, "today": date.today().isoformat()}


def iter_watch_tickers(items: Iterable[WatchItem]) -> list[str]:
    return [i.ticker for i in items]


__all__ = ["Store", "WatchItem", "MapRow", "DEFAULT_DB", "map_status"]
