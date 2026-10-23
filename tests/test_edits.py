"""HTML 노드 편집 — 편집 연산 적용(generate/edits), 로컬 편집 서버(render/server), ops·CLI 연결."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from helpers import build_new_map
from kstock.generate.edits import (
    EditError,
    apply_edit_ops,
    apply_edits,
    edit_hash,
    edit_schemas,
    load_edit_file,
    next_child_id,
    preview_edits,
)
from kstock.models import load_map_file
from kstock.ops import Ops, OpsError
from kstock.render import build_tree, to_html
from kstock.render.server import EditServer, FileTarget, save_edits
from kstock.store import Store

TODAY = date(2026, 10, 23)


def _post(url: str, token: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "X-Token": token})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


class TestEditOps(unittest.TestCase):
    def setUp(self):
        self.m = build_new_map()

    def test_next_child_id(self):
        self.assertEqual(next_child_id(self.m, "A4"), "A4.6")
        self.assertEqual(next_child_id(self.m, "A0"), "A9")        # 루트 아래 = 새 가지
        self.assertEqual(next_child_id(self.m, "A0.*"), "A0.5")    # 기본 정보 묶음 = 보조 필드
        with self.assertRaises(EditError):
            next_child_id(self.m, "Z9")

    def test_set_records_history_and_user_tag(self):
        before = len(self.m["A1.1"].history)
        res = apply_edit_ops(self.m, [{"op": "set", "id": "A1.1", "fields": {"label": "영업이익률 (수정)",
                                      "driver": {**self.m["A1.1"].driver.model_dump(mode="json"), "my_estimate": {"value": 14.0}}}}], today=TODAY)
        n = self.m["A1.1"]
        self.assertEqual(res.applied, ["set A1.1 (driver)"])
        self.assertEqual(n.label, "영업이익률 (수정)")
        self.assertEqual(n.driver.my_estimate.value, 14.0)
        self.assertEqual(len(n.history), before + 1)
        self.assertIn("사용자", n.history[-1].source.name)
        self.assertIn("user", n.tags)
        self.assertEqual(n.as_of, TODAY)
        # 라벨만 바꾸면 history 는 그대로, user 태그는 붙는다
        res = apply_edit_ops(self.m, [{"op": "set", "id": "A4.1", "fields": {"label": "리스크 1 (수정)"}}], today=TODAY)
        self.assertEqual(res.applied, ["set A4.1"])
        self.assertIn("user", self.m["A4.1"].tags)

    def test_set_drops_draft_tags_and_rejects_bad_values(self):
        self.m["A4.1"].tags = ["draft", "pending", "warn"]
        apply_edit_ops(self.m, [{"op": "set", "id": "A4.1", "fields": {"value": "새 값"}}], today=TODAY)
        self.assertEqual(self.m["A4.1"].tags, ["user"])
        with self.assertRaises(EditError) as cm:
            apply_edit_ops(self.m, [{"op": "set", "id": "A1.1", "fields": {"weight": 5}}], today=TODAY)
        self.assertIn("weight", str(cm.exception))
        with self.assertRaises(EditError):
            apply_edit_ops(self.m, [{"op": "set", "id": "A1.1", "fields": {"id": "X"}}], today=TODAY)
        with self.assertRaises(EditError):
            apply_edit_ops(self.m, [{"op": "set", "id": "없음", "fields": {"label": "x"}}], today=TODAY)
        with self.assertRaises(EditError):
            apply_edit_ops(self.m, [{"op": "move", "id": "A1.1"}], today=TODAY)

    def test_add_then_edit_temp_id_then_delete(self):
        ops = [{"op": "add", "parent": "A4", "id": "new:1", "fields": {"label": "하락 리스크 4", "value": "환율", "node_type": "estimate"}},
               {"op": "set", "id": "new:1", "fields": {"question": "확률은?"}},
               {"op": "add", "parent": "A0.*", "fields": {"label": "메모", "value": "x"}}]
        res = apply_edit_ops(self.m, ops, today=TODAY)
        self.assertEqual(res.id_map, {"new:1": "A4.6"})
        self.assertEqual(self.m["A4.6"].question, "확률은?")
        self.assertEqual(self.m["A4.6"].parent, "A4")
        self.assertIn("user", self.m["A4.6"].tags)
        self.assertIsNotNone(self.m["A4.6"].source)
        self.assertEqual(self.m["A0.5"].parent, "A0")
        self.m["A3.1"].links = ["A4.6"]
        res = apply_edit_ops(self.m, [{"op": "delete", "id": "A4.6"}], today=TODAY)
        self.assertNotIn("A4.6", self.m)
        self.assertEqual(self.m["A3.1"].links, [])
        self.assertTrue(res.warnings)
        with self.assertRaises(EditError):
            apply_edit_ops(self.m, [{"op": "delete", "id": "A0"}], today=TODAY)
        with self.assertRaises(EditError):
            apply_edit_ops(self.m, [{"op": "add", "parent": "A4", "fields": {"label": "  "}}], today=TODAY)

    def test_delete_subtree(self):
        n_before = len(self.m.nodes)
        kids = 1 + sum(1 for _ in self.m.descendants("A4"))
        res = apply_edit_ops(self.m, [{"op": "delete", "id": "A4"}], today=TODAY)
        self.assertEqual(len(self.m.nodes), n_before - kids)
        self.assertIn(f"(+{kids - 1})", res.applied[0])

    def test_sub_model_none_cleaning(self):
        ev = {"date": "2026-11-01", "fact": "3Q 실적", "affects": [], "direction": None, "priced_in": None, "materiality": None,
              "source": {"name": "", "url": "", "retrieved_at": None}}
        trial, res = preview_edits(self.m, [{"op": "add", "parent": "A5", "id": "new:1", "fields": {"label": "3Q", "event": ev, "source": None,
                                                                                                    "confidence": None, "weight": None}}], today=TODAY)
        n = trial[res.id_map["new:1"]]
        self.assertEqual((n.event.direction.value, n.event.priced_in.value, n.event.materiality.value), ("중립", "아니오", "M"))
        self.assertIsNone(n.event.source)
        with self.assertRaises(EditError) as cm:
            preview_edits(self.m, [{"op": "add", "parent": "A5", "fields": {"label": "x", "event": {"date": None, "fact": None}}}], today=TODAY)
        self.assertIn("Field required", str(cm.exception))

    def test_preview_reports_new_errors_only(self):
        # 초안에 원래 있는 오류(없음) 와 비교해 **늘어나는** 오류만
        _, res = preview_edits(self.m, [{"op": "delete", "id": "A1.1"}], today=TODAY)
        self.assertTrue(any("드라이버" in e for e in res.new_errors))
        _, res = preview_edits(self.m, [{"op": "set", "id": "A4.1", "fields": {"label": "x"}}], today=TODAY)
        self.assertEqual(res.new_errors, [])
        # 원본은 그대로
        self.assertIn("A1.1", self.m)

    def test_apply_edits_versions_log_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            vdir = Path(tmp) / "versions"
            payload = {"map_id": self.m.map_id, "base_hash": edit_hash(self.m), "edits": [{"op": "set", "id": "A4.1", "fields": {"label": "리스크 1 (수정)"}}]}
            res = apply_edits(self.m, payload, today=TODAY, versions_dir=vdir)
            self.assertEqual(res.version, 2)
            self.assertEqual(self.m.meta["version"], 2)
            self.assertTrue((vdir / f"{self.m.map_id.replace(':', '_')}.v1.json").is_file())
            self.assertIn("HTML 편집 1건", self.m["A7.2"].value[-1]["text"])
            self.assertEqual(self.m.meta["edit_log"][-1]["ops"], ["set A4.1"])
            # 같은 base_hash 로 다시 → 맵이 바뀌었으니 거절, check_hash=False 면 통과
            with self.assertRaises(EditError):
                apply_edits(self.m, payload, today=TODAY)
            apply_edits(self.m, payload, today=TODAY, check_hash=False)
            with self.assertRaises(EditError):
                apply_edits(self.m, {"map_id": "B:x", "edits": payload["edits"]}, today=TODAY)
            with self.assertRaises(EditError):
                apply_edits(self.m, {"map_id": self.m.map_id, "edits": []}, today=TODAY)
            # 검증 오류가 늘면 거절되고 원본 불변
            with self.assertRaises(EditError):
                apply_edits(self.m, {"map_id": self.m.map_id, "edits": [{"op": "delete", "id": "A1.1"}]}, today=TODAY, check_hash=False)
            self.assertIn("A1.1", self.m)

    def test_saved_map_reloads(self):
        # history 의 이전 값이 None 이면 저장(exclude_none) 뒤 다시 읽지 못한다 — 자리표시 값으로 막는다
        self.m["A4.1"].value = None
        apply_edit_ops(self.m, [{"op": "set", "id": "A4.1", "fields": {"value": "새 값"}},
                               {"op": "add", "parent": "A4", "fields": {"label": "새 노드"}}], today=TODAY)
        with tempfile.TemporaryDirectory() as tmp:
            p = self.m.save(Path(tmp) / "m.json")
            m2 = load_map_file(p)
            self.assertEqual(m2["A4.1"].history[-1].value, "(비어 있음)")
            self.assertIn("A4.6", m2)

    def test_schemas_and_html(self):
        s = edit_schemas()
        keys = {r["key"] for r in s["subs"]["driver"]}
        self.assertIn("my_estimate.value", keys)
        self.assertTrue(any(r["adv"] for r in s["subs"]["driver"]))
        self.assertEqual(s["subs"]["rule"][0]["options"], ["fact", "price", "weight", "time", "event"])
        self.assertIn("confirmed", s["enums"]["status"])
        tree = build_tree(self.m)
        self.assertIn("edit", tree)
        aux = next(c for c in tree["children"] if c["id"] == "A0.*")
        self.assertTrue(aux.get("locked"))
        self.assertIn("node_type", aux["children"][0]["edit"])
        html = to_html(self.m)
        self.assertIn('"server": false', html)
        self.assertIn(edit_hash(self.m), html)
        self.assertIn('id="editor"', html)
        self.assertIn('"edit": null', to_html(self.m, edit=False))


class TestEditServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "A.json"
        build_new_map().save(self.path)
        self.srv = EditServer(FileTarget(self.path), today=TODAY)
        self.srv.start()
        self.base = f"http://{self.srv.host}:{self.srv.port}"

    def tearDown(self):
        self.srv.shutdown()
        self.tmp.cleanup()

    def test_roundtrip(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.base + "/", timeout=5)
        self.assertEqual(cm.exception.code, 403)
        with urllib.request.urlopen(self.srv.url, timeout=5) as r:
            html = r.read().decode("utf-8")
        self.assertIn('"server": true', html)
        self.assertIn(self.srv.token, html)
        health = json.loads(urllib.request.urlopen(self.base + "/health", timeout=5).read())
        self.assertTrue(health["ok"])
        # preview: 구조 훼손은 new_errors, 저장 안 됨
        r = _post(self.base + "/preview", self.srv.token, {"edits": [{"op": "delete", "id": "A1.1"}]})
        self.assertTrue(r["ok"])
        self.assertTrue(r["new_errors"])
        self.assertIn("A1.1", load_map_file(self.path))
        r = _post(self.base + "/preview", self.srv.token, {"edits": [{"op": "set", "id": "A1.1", "fields": {"weight": 7}}]})
        self.assertFalse(r["ok"])
        # save
        m = load_map_file(self.path)
        r = _post(self.base + "/save", self.srv.token, {"map_id": m.map_id, "base_hash": edit_hash(m),
                                                         "edits": [{"op": "add", "parent": "A4", "id": "new:1", "fields": {"label": "새 리스크", "value": "x"}},
                                                                   {"op": "set", "id": "new:1", "fields": {"question": "q"}}]})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["version"], 2)
        self.assertEqual(r["id_map"], {"new:1": "A4.6"})
        m2 = load_map_file(self.path)
        self.assertEqual(m2["A4.6"].question, "q")
        self.assertTrue((self.path.parent / "versions" / f"{m.map_id.replace(':', '_')}.v1.json").is_file())
        self.assertTrue(self.path.with_suffix(".html").is_file())
        # 옛 base_hash 로 다시 저장 → 거절
        r = _post(self.base + "/save", self.srv.token, {"map_id": m.map_id, "base_hash": edit_hash(m), "edits": [{"op": "set", "id": "A4.6", "fields": {"label": "y"}}]})
        self.assertFalse(r["ok"])
        self.assertIn("다르다", r["error"])
        # 틀린 토큰
        req = urllib.request.Request(self.base + "/preview", data=b"{}", method="POST", headers={"Content-Type": "application/json", "X-Token": "nope"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 403)


class TestEditOpsStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "db" / "kstock.sqlite")
        self.m = build_new_map()
        self.store.save_map(self.m)
        self.ops = Ops(self.store, today=TODAY)

    def tearDown(self):
        self.ops.stop_edit_servers()
        self.store.close()
        self.tmp.cleanup()

    def test_apply_edit_file(self):
        f = Path(self.tmp.name) / "A.edits.json"
        f.write_text(json.dumps({"map_id": self.m.map_id, "base_hash": edit_hash(self.m),
                                 "edits": [{"op": "set", "id": "A4.1", "fields": {"label": "리스크 1 (수정)"}}]}, ensure_ascii=False), encoding="utf-8")
        out = self.ops.apply_edit_file(f)
        self.assertEqual(out["version"], 2)
        self.assertEqual(self.store.load_map(self.m.map_id)["A4.1"].label, "리스크 1 (수정)")
        self.assertTrue(Path(out["html"]).is_file())
        with self.assertRaises(OpsError):
            self.ops.apply_edit_file(f)                       # 해시 불일치
        self.ops.apply_edit_file(f, force=True)
        bad = Path(self.tmp.name) / "bad.json"
        bad.write_text("[]", encoding="utf-8")
        with self.assertRaises(OpsError):
            self.ops.apply_edit_file(bad)
        with self.assertRaises(EditError):
            load_edit_file(bad)

    def test_edit_server_via_ops(self):
        url = self.ops.edit_server(self.m, open_browser=False)
        self.assertIs(self.ops.edit_server(self.m, open_browser=False) and self.ops.edit_servers[self.m.map_id], self.ops.edit_servers[self.m.map_id])
        srv = self.ops.edit_servers[self.m.map_id]
        with urllib.request.urlopen(url, timeout=5) as r:
            self.assertIn('"server": true', r.read().decode("utf-8"))
        base = url.split("/?")[0]
        r = _post(base + "/save", srv.token, {"map_id": self.m.map_id, "base_hash": edit_hash(self.m),
                                               "edits": [{"op": "set", "id": "A4.1", "fields": {"label": "리스크 1 (서버)"}}]})
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.store.load_map(self.m.map_id)["A4.1"].label, "리스크 1 (서버)")   # 다른 스레드의 연결로 저장됐다
        self.assertEqual(self.ops.edit_saved, [self.m.map_id])
        self.assertTrue(any("편집 서버" in ln for ln in self.ops.log_lines))
        self.assertEqual(self.ops.stop_edit_servers(), 1)

    def test_save_edits_helper_and_cli(self):
        from kstock.cli import main

        p = Path(self.tmp.name) / "file_A.json"
        self.m.save(p)
        f = Path(self.tmp.name) / "f.edits.json"
        f.write_text(json.dumps({"map_id": self.m.map_id, "base_hash": edit_hash(self.m),
                                 "edits": [{"op": "add", "parent": "A5", "fields": {"label": "촉매 (사용자)", "value": "x"}}]}), encoding="utf-8")
        self.assertEqual(main(["apply-edits", str(f), "--map", str(p)]), 0)
        self.assertIn("A5.3", load_map_file(p))
        self.assertEqual(main(["apply-edits", str(f), "--map", str(p)]), 2)          # 해시 불일치
        self.assertEqual(main(["apply-edits", str(f), "--map", str(p), "--force"]), 0)
        out = save_edits(FileTarget(p), json.loads(f.read_text(encoding="utf-8")), today=TODAY, check_hash=False)
        self.assertEqual(out["version"], 4)


if __name__ == "__main__":
    unittest.main()
