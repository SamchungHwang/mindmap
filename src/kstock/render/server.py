"""로컬 편집 서버 — 마인드맵 HTML 의 「저장」 이 맵 JSON 에 바로 들어가게 한다.

    mindmap edit <맵 id | 맵.json>          → 127.0.0.1 의 빈 포트에 띄우고 브라우저를 연다
    콘솔 「고른 맵 HTML 편집」                → 같은 서버를 백그라운드 스레드로

경로 (모두 같은 토큰 — GET 은 `?t=`, POST 는 `X-Token` 헤더)
    GET  /            편집 UI 가 켜진 HTML (맵을 매번 새로 읽는다)
    GET  /health      {"ok": true, "map_id": …}
    POST /preview     {"edits": […]} → 복사본에 적용한 트리 + 늘어나는 검증 오류 (저장 안 함)
    POST /save        {"map_id", "base_hash", "edits"} → generate.edits.apply_edits → 재계산 → 저장 → HTML 다시 씀
    POST /quit        서버 종료

표준 라이브러리만 쓴다(http.server). 외부에서 접근할 수 없게 127.0.0.1 에만 묶고, 토큰 없는 요청은 403.
"""

from __future__ import annotations

import json
import secrets
import threading
import webbrowser
from collections.abc import Callable
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..generate.edits import EditError, apply_edits, finalize_after_edit, preview_edits
from ..models import MindMap, load_map_file
from .native import build_tree, edit_options, to_html, write_html


class FileTarget:
    """맵 JSON 파일 하나 — 저장소 없이 `mindmap edit maps\\A_x.json` 으로 쓸 때."""

    def __init__(self, path: str | Path, *, html_path: str | Path | None = None):
        self.path = Path(path)
        self.html_path = Path(html_path) if html_path else self.path.with_suffix(".html")
        self.versions_dir = self.path.parent / "versions"

    def load(self) -> MindMap:
        return load_map_file(self.path)

    def save(self, m: MindMap) -> Path:
        return m.save(self.path)

    def render(self, m: MindMap) -> Path:
        return write_html(m, self.html_path)


class StoreTarget:
    """저장소(Store) 의 맵 — 콘솔·CLI 가 같은 인덱스를 본다."""

    def __init__(self, store: Any, map_id: str, *, log: Callable[[str], None] | None = None,
                 on_saved: Callable[[str], None] | None = None):
        self.store, self.map_id, self._log, self._on_saved = store, map_id, log, on_saved
        self.versions_dir = store.versions_dir

    def load(self) -> MindMap:
        return self.store.load_map(self.map_id)

    def save(self, m: MindMap) -> Path:
        p = self.store.save_map(m)
        if self._log:
            self._log(f"{m.map_id} HTML 편집 저장 → v{m.meta.get('version')}")
        if self._on_saved:
            self._on_saved(m.map_id)
        return p

    def render(self, m: MindMap) -> Path:
        return write_html(m, self.store.out_path(m.map_id.replace(":", "_") + ".html"))


def save_edits(target: Any, payload: dict[str, Any], *, today: date, check_hash: bool = True) -> dict[str, Any]:
    """편집 적용의 한 단위: 읽기 → apply_edits(검증·버전) → 재계산 → 저장 → HTML. 서버의 /save 와 CLI `apply-edits` 가 같이 쓴다."""
    m = target.load()
    res = apply_edits(m, payload, today=today, versions_dir=getattr(target, "versions_dir", None), check_hash=check_hash)
    notes = finalize_after_edit(m, today=today)
    path = target.save(m)
    html_path = target.render(m)
    return {"ok": True, "version": res.version, "applied": res.applied, "warnings": [*res.warnings, *notes], "id_map": res.id_map,
            "path": str(path), "html": str(html_path), "map_id": m.map_id}


class EditServer:
    def __init__(self, target: Any, *, today: date | None = None, host: str = "127.0.0.1", port: int = 0, layout: str = "balanced",
                 log: Callable[[str], None] | None = None):
        self.target, self.today, self.layout = target, today or date.today(), layout
        self.token = secrets.token_urlsafe(18)
        self.log = log or (lambda s: None)
        self.saves = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 — 조용히
                server.log("http " + (fmt % args))

            # ---- helpers
            def _json(self, code: int, obj: dict[str, Any]) -> None:
                body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _html(self, text: str) -> None:
                body = text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _authed(self) -> bool:
                q = parse_qs(urlsplit(self.path).query)
                tok = self.headers.get("X-Token") or (q.get("t") or [""])[0]
                return secrets.compare_digest(tok, server.token)

            def _body(self) -> dict[str, Any]:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    data = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError as e:
                    raise EditError(f"요청 JSON 오류: {e}") from e
                if not isinstance(data, dict):
                    raise EditError("요청은 JSON 객체여야 한다")
                return data

            # ---- routes
            def do_GET(self) -> None:  # noqa: N802
                path = urlsplit(self.path).path
                if path == "/health":
                    self._json(200, {"ok": True, "map_id": getattr(server.target, "map_id", None) or str(getattr(server.target, "path", ""))})
                    return
                if not self._authed():
                    self._json(403, {"ok": False, "error": "토큰이 없거나 틀리다 — 콘솔/CLI 가 연 주소로 열 것"})
                    return
                if path in ("/", "/index.html"):
                    try:
                        m = server.target.load()
                        self._html(to_html(m, layout=server.layout, edit=edit_options(m, server=True, token=server.token, today=server.today)))
                    except Exception as e:  # noqa: BLE001
                        server.log(f"오류 GET /: {e!r}")
                        self._json(500, {"ok": False, "error": f"맵을 읽지 못했다 — {type(e).__name__}: {e}"})
                    return
                self._json(404, {"ok": False, "error": "없는 경로"})

            def do_POST(self) -> None:  # noqa: N802
                path = urlsplit(self.path).path
                if not self._authed():
                    self._json(403, {"ok": False, "error": "토큰이 없거나 틀리다"})
                    return
                try:
                    if path == "/preview":
                        data = self._body()
                        m = server.target.load()
                        trial, res = preview_edits(m, data.get("edits") or [], today=server.today)
                        self._json(200, {"ok": True, "tree": build_tree(trial), "new_errors": res.new_errors, "warnings": res.warnings,
                                         "id_map": res.id_map, "applied": res.applied})
                    elif path == "/save":
                        data = self._body()
                        out = save_edits(server.target, data, today=server.today)
                        server.saves += 1
                        server.log(f"저장 v{out['version']}: {'; '.join(out['applied'])[:200]}")
                        self._json(200, out)
                    elif path == "/quit":
                        self._json(200, {"ok": True})
                        threading.Thread(target=server.shutdown, daemon=True).start()
                    else:
                        self._json(404, {"ok": False, "error": "없는 경로"})
                except EditError as e:
                    self._json(200, {"ok": False, "error": str(e)})
                except Exception as e:  # noqa: BLE001 — 페이지에 이유를 보여 준다
                    server.log(f"오류 {path}: {e!r}")
                    self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        self.host, self.port = self.httpd.server_address[0], self.httpd.server_address[1]
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/?t={self.token}"

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def start(self) -> threading.Thread:
        """백그라운드 스레드로 띄운다 (콘솔용)."""
        self._thread = threading.Thread(target=self.serve_forever, name=f"mindmap-edit-{self.port}", daemon=True)
        self._thread.start()
        return self._thread

    def shutdown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def open_browser(self) -> bool:
        try:
            return webbrowser.open(self.url)
        except Exception:  # noqa: BLE001
            return False


__all__ = ["EditServer", "FileTarget", "StoreTarget", "save_edits"]
