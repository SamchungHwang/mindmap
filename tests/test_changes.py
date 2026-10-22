"""변경안 (설계 v3 P5): 기존 맵 + 새 리포트 → ChangeSet → 승인분만 적용 · 원본 버전 보존 · 사용자 수정 노드 불변 · A7.2 로그."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from helpers import D0  # noqa: F401  (sys.path 설정)
from kstock.generate.changes import apply_changes, changeset_table, load_changeset, map_hash, propose_changes
from kstock.models import ChangeDecision, ChangeKind
from kstock.ops import Ops, OpsError
from kstock.store import Store
from kstock.symbols import Symbol, SymbolBook

FIX = Path(__file__).resolve().parent / "fixtures" / "reports"
TXTS = sorted(FIX.glob("*.txt"))
TODAY = date(2026, 10, 20)

FOURTH = """2026년 10월 19일 | 기업분석
삼성전자 (005930)
HBM4 가격 협상 타결 — 2027년 영업이익 상향
투자의견 BUY (유지)
목표주가(12M) 600,000원 (상향)
현재주가(10/17) 265,000원
52주 최고/최저(원) 362,500/89,000
(십억원) 2024A 2025A 2026E 2027E 2028E
매출액 300,871 333,606 720,000 1,000,000 1,200,000
영업이익 32,726 43,601 380,000 700,000 800,000
순이익 33,621 44,261 320,000 560,000 640,000
EPS(원) 4,950 6,564 48,000 84,000 96,000
HBM4 공급 계약 단가가 전년 대비 두 배로 확정되어 2027년 영업이익 추정치를 700조원으로 상향한다. 목표주가는 600,000원으로 상향한다.
리스크 요인
미국 관세 정책 변경 시 세트 수출 마진이 훼손될 수 있다.
2027년 1월 HBM4 공급 계약 단가 확정이 예정되어 있다.
가상증권 리서치센터 fake@virtual.co.kr
"""


class TestChangeSet(unittest.TestCase):
    def test_propose_apply_and_versions(self):
        with tempfile.TemporaryDirectory() as t:
            store = Store(Path(t) / "db" / "k.sqlite")
            ops = Ops(store, today=TODAY, symbols=SymbolBook([Symbol("005930", "삼성전자", "KR", None, 1.0)]))
            # 기존 리포트 3건을 폴더에 복사 (새 리포트와 같은 폴더에서 찾는다)
            rep = Path(t) / "reports"
            rep.mkdir()
            olds = []
            for p in TXTS:
                q = rep / p.name
                q.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
                olds.append(q)
            r = ops.draft_from_reports(olds, {"fetch_price": False})
            m = r.map
            # 사용자가 A0 를 고쳤다 (draft 태그 제거) → 손댄 노드
            m["A0"].set("내 논제: HBM4 판가 2배 — 12개월 400,000", as_of=TODAY)
            m["A0"].tags = [tg for tg in m["A0"].tags if tg != "draft"]
            store.save_map(m)
            h0 = map_hash(m)
            new = rep / "virtual_005930_20261019.txt"
            new.write_text(FOURTH, encoding="utf-8")
            cs = ops.propose_changes(m, [new], {"fetch_price": False})
            self.assertEqual(map_hash(m), h0)                                   # 제안만, 맵은 그대로
            self.assertTrue(cs.changes, cs.summary)
            kinds = {c.kind for c in cs.changes}
            self.assertIn(ChangeKind.estimate, kinds)                           # 컨센서스 변화 (TP 600k 리포트가 평균을 올림)
            self.assertIn(ChangeKind.tp_revision, kinds)
            self.assertTrue(list(store.changes_dir.glob("*.json")))
            table = changeset_table(cs)
            self.assertIn("변경 후보", table)
            # 일부 승인, 일부 반려
            est = next(c for c in cs.changes if c.kind == ChangeKind.estimate)
            tp = next(c for c in cs.changes if c.kind == ChangeKind.tp_revision)
            others = [c.change_id for c in cs.changes if c.change_id not in (est.change_id, tp.change_id)]
            # v4 V7: 입력 집합 id · 함께 바뀌는 노드 · 부분 승인 경고
            self.assertTrue(cs.input_snapshot_id)
            self.assertIn("A3.6.4", est.affects)                               # A1 추정 변화 → 목표가 분포가 함께 바뀐다
            self.assertIn("↔ 함께", table)
            done = ops.apply_changes(m, cs, {est.change_id: "approve", tp.change_id: "approve", **{cid: "reject" for cid in others}})
            self.assertEqual(len(done), 2)
            self.assertEqual(m.meta["version"], 2)
            self.assertEqual(m.meta["draft"]["input_snapshot_id"], cs.input_snapshot_id)
            self.assertEqual(m["A0"].value, "내 논제: HBM4 판가 2배 — 12개월 400,000")       # 사용자 수정 보존
            self.assertEqual(m[est.node_id].driver.consensus.value, est.after["consensus"])
            self.assertTrue(any("변경안" in str(h.source) for h in m[est.node_id].history))
            self.assertTrue(isinstance(m["A7.2"].value, list) and m["A7.2"].value[-1]["version"] == 2)
            vers = list(store.versions_dir.glob("*.v1.json"))
            self.assertEqual(len(vers), 1)                                      # 원본 보존
            old = json.loads(vers[0].read_text(encoding="utf-8"))
            self.assertEqual(old["meta"].get("version", 1), 1)
            self.assertEqual(store.load_map(m.map_id).meta["version"], 2)
            saved = load_changeset(sorted(store.changes_dir.glob("*.json"))[-1])
            self.assertEqual(saved.result_version, 2)
            self.assertEqual(sum(1 for c in saved.changes if c.decision == ChangeDecision.rejected), len(others))
            # 맵이 그 뒤 바뀌면 같은 변경안은 다시 적용할 수 없다
            cs2 = ops.propose_changes(m, [new], {"fetch_price": False})
            m["A0"].set("또 고침", as_of=TODAY)
            for c in cs2.changes:
                c.decision = ChangeDecision.approved
            with self.assertRaises(OpsError):
                ops.apply_changes(m, cs2, {})
            store.close()

    def test_v4_partial_approval_warning_snapshot_mismatch_and_rollback(self):
        from kstock.generate.changes import ChangeError, partial_approval_warnings
        from kstock.generate.draft import draft_from_reports

        with tempfile.TemporaryDirectory() as t:
            rep = Path(t) / "reports"
            rep.mkdir()
            olds = []
            for p in TXTS:
                q = rep / p.name
                q.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
                olds.append(q)
            new = rep / "virtual_005930_20261019.txt"
            new.write_text(FOURTH, encoding="utf-8")
            base = draft_from_reports(olds, ticker="005930", name="삼성전자", today=TODAY, claims="rules", fetch_price=False).map
            fresh = draft_from_reports([*olds, new], ticker="005930", name="삼성전자", today=TODAY, claims="rules", fetch_price=False).map
            cs = propose_changes(base, fresh, new_report_ids=["hk_1"], today=TODAY, all_report_ids=["hk_1", "a", "b", "c"])
            est = next(c for c in cs.changes if c.kind == ChangeKind.estimate)
            dep = next((c for c in cs.changes if c.node_id in est.affects), None)
            self.assertIsNotNone(dep, [c.node_id for c in cs.changes])
            est.decision = ChangeDecision.approved
            dep.decision = ChangeDecision.rejected
            warns = partial_approval_warnings(cs)
            self.assertTrue(any(dep.node_id in w and "분석 시점" in w for w in warns))
            # 다른 입력으로 만든 변경안은 거절
            with self.assertRaises(ChangeError):
                apply_changes(base.model_copy(deep=True), cs, today=TODAY, expected_input_snapshot_id="deadbeef00")
            # 정상 적용 — 부분 승인 ⚑ 가 A7.2 로그에 남는다, 버전 파일은 .tmp 가 남지 않는다
            m = base.model_copy(deep=True)
            vdir = Path(t) / "versions"
            done = apply_changes(m, cs, today=TODAY, versions_dir=vdir, expected_input_snapshot_id=cs.input_snapshot_id)
            self.assertEqual(len(done), 1)
            self.assertIn("부분 승인", m["A7.2"].value[-1]["text"])
            self.assertEqual([p.suffix for p in vdir.iterdir()], [".json"])
            # 적용 결과가 검증 오류를 내면 원본은 그대로 (복사본에만 적용됐다가 버려진다)
            from kstock.models import Change, MapKind, Node

            bad = propose_changes(base, fresh, new_report_ids=["hk_1"], today=TODAY)
            est2 = next(c for c in bad.changes if c.kind == ChangeKind.estimate)
            est2.decision = ChangeDecision.approved
            orphan = Node(id="A9.9.1", map=MapKind.NEW, parent="A9.9", label="고아 노드", node_type="fact", value_type="text", value="x")
            bad.changes.append(Change(change_id="chX", node_id="A9.9.1", kind=ChangeKind.new_catalyst, before=None,
                                      after={"node": orphan.model_dump(mode="json")}, decision=ChangeDecision.approved))
            m2 = base.model_copy(deep=True)
            h = map_hash(m2)
            n_versions = len(list(vdir.iterdir()))
            with self.assertRaises(ChangeError):
                apply_changes(m2, bad, today=TODAY, versions_dir=vdir)
            self.assertEqual(map_hash(m2), h)                                  # 실패했으면 원본 불변
            self.assertEqual(m2.meta.get("version", 1), 1)
            self.assertEqual(len(list(vdir.iterdir())), n_versions)            # 버전 파일도 안 만든다

    def test_no_changes_when_same_reports(self):
        with tempfile.TemporaryDirectory() as t:
            store = Store(Path(t) / "db" / "k.sqlite")
            ops = Ops(store, today=TODAY, symbols=SymbolBook([Symbol("005930", "삼성전자", "KR", None, 1.0)]))
            r = ops.draft_from_reports(TXTS, {"fetch_price": False})
            cs = propose_changes(r.map, r.map, new_report_ids=[], today=TODAY)
            self.assertEqual(cs.changes, [])
            self.assertEqual(apply_changes(r.map, cs, today=TODAY), [])
            store.close()


if __name__ == "__main__":
    unittest.main()
