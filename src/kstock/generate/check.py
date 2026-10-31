"""교차 검증 (자동 생성 계획 §3c) — 자동 초안이 스스로 모순되지 않게 고치고, 사람이 봐야 할 곳에 깃발을 세운다.

틀렸다는 뜻이 아니라 **사람이 봐야 한다**는 뜻의 `warn` 태그를 노드에 단다. 판단(A7.5)은 항상 WATCH 로 되돌린다.
"""

from __future__ import annotations

from datetime import date

from ..models import NewMap, NodeStatus
from ..reports.digest import ReportDigest
from .rules import DRAFT, mark_draft

WARN_TAG = "warn"


def _warn(m: NewMap, nid: str, msg: str, out: list[str]) -> None:
    if nid in m:
        n = m[nid]
        if WARN_TAG not in n.tags:
            n.tags = sorted(set(n.tags) | {WARN_TAG})
    out.append(f"{nid}: {msg}")


def cross_check(m: NewMap, d: ReportDigest | None, *, today: date) -> list[str]:
    flags: list[str] = []
    p0 = m.current_price
    sc = m.scenarios()
    # 확률 합
    if len(sc) == 3 and all(x.probability is not None for x in sc.values()):
        s = sum(x.probability or 0.0 for x in sc.values())
        if abs(s - 1.0) > 0.01:
            if 0.9 <= s <= 1.1:
                for x in sc.values():
                    x.probability = round(x.probability / s, 3)
                tot = sum(x.probability for x in sc.values())
                sc["Base"].probability = round(sc["Base"].probability + (1.0 - tot), 3)
                flags.append(f"A4: 확률 합 {s:.2f} → 1.0 정규화")
            else:
                _warn(m, "A4", f"확률 합 {s:.2f} — 사람이 다시 쓴다", flags)
        # 순서
        b, ba, bu = sc["Bear"].target_price, sc["Base"].target_price, sc["Bull"].target_price
        if not (b < ba < bu):
            _warn(m, "A4", f"목표가 순서 위반 Bear {b:,.0f} / Base {ba:,.0f} / Bull {bu:,.0f}", flags)
        if p0 is not None:
            if b > p0:
                _warn(m, "A4.2", f"Bear 목표가 {b:,.0f} 가 현재가 {p0:,.0f} 위 — 하락 시나리오가 없다", flags)
            if bu < p0:
                _warn(m, "A4.4", f"Bull 목표가 {bu:,.0f} 가 현재가 {p0:,.0f} 아래 — 상승 여지가 없다", flags)
        # 컨센서스 범위
        if d is not None and d.n_tp:
            lo, hi = d.tp_min * 0.7, d.tp_max * 1.2
            if not (lo <= ba <= hi):
                _warn(m, "A4.3", f"Base 목표가 {ba:,.0f} 가 컨센서스 범위 [{lo:,.0f}, {hi:,.0f}] 밖", flags)
    # 드라이버: current 는 출처 필수, my_estimate 는 '초안' 표기
    for n in m.drivers():
        dr = n.driver
        assert dr is not None
        if dr.current.value is not None and dr.current.source is None:
            _warn(m, n.id, "driver.current 에 출처가 없다", flags)
        if dr.my_estimate.value is not None and "초안" not in (dr.my_estimate.rationale or ""):
            dr.my_estimate.rationale = ((dr.my_estimate.rationale or "") + f" ({DRAFT})").strip()
        if dr.consensus.value is not None and dr.current.value is not None and dr.current.value != 0:
            ratio = dr.consensus.value / dr.current.value
            if ratio > 5 or ratio < 0.2:
                _warn(m, n.id, f"컨센서스/현재 비율 {ratio:.1f} — 단위가 다르거나 분기/연간이 섞였을 수 있다", flags)
    # 컨센서스 편향 메모
    if d is not None and d.reports:
        dist = d.opinion_dist
        rated = sum(v for k, v in dist.items() if k in ("BUY", "HOLD", "SELL"))
        if rated >= 2 and dist.get("BUY", 0) == rated:
            # A2.1 값은 사람 몫(내재 가정) — 편향 메모는 질문 쪽에 붙이고 깃발로 알린다 (설계 v3 §2.4)
            q = m["A2.1"].question or ""
            if "매수 일색" not in q and "매도·중립 의견 0건" not in q:
                m["A2.1"].question = (q + " | 컨센서스 편향: 매도·중립 의견 0건 (매수 일색) — 리포트만으로는 반대 논리를 볼 수 없다").strip(" |")
            flags.append("A2.1: 컨센서스 편향 — 리포트 의견이 매수 일색")
    # 판단은 항상 WATCH
    if m.value("A7.5") != "WATCH":
        m["A7.5"].set("WATCH", as_of=today, status=NodeStatus.unverified)
        flags.append("A7.5: 자동 초안의 판단은 WATCH 로 고정 (BUY 는 사람만)")
    mark_draft(m["A7.5"])
    return flags


__all__ = ["cross_check", "WARN_TAG"]
