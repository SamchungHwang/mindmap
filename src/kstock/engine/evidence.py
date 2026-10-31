"""증거 계획 (설계 v7 §3.4 — T4·T7): 관측값 데이터 계약 · 같은 자 규칙 · 신호등 · 띠/경계 제안 · 증거 일정.

- 관측값(`Observation`) 은 여덟 칸이 다 있어야 저장된다. 리포트의 전망 수치는 관측값이 아니다 — verification=forecast 는 신호등에 쓰지 않는다.
- 같은 자 규칙: 관측값의 자(지표·값의 종류·기준) 가 필요 경로의 자와 같을 때만 비교한다. 다르면 저장은 되지만 비교에서 빠지고 "자가 다름" 으로 보인다.
- 신호등(코드): 🟢 비교 시점의 띠 안 · 🟡 띠 밖이지만 경계 안 / 추세가 경로에서 멀어짐 / 관측값 오래됨 · 🔴 Bear 경계를 넘음 · ⚪ 증거 없음.
  드라이버 신호등은 합산하지 않는다 — 역할별로 센다.
- 띠·경계 제안(코드): Base→Bear 가격 차이의 절반에 해당하는 변수 변화를 Bear 경계로(사용자 결정), 띠는 그 절반. 제안 표시가 붙고 사람이 바꾼다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from ..models import Light, MindMap, NewMap, Node, Observation, PathPoint, RequiredPath

LIGHT_ICON = {Light.GREEN: "🟢", Light.YELLOW: "🟡", Light.RED: "🔴", None: "⚪"}
ROLE_LABEL = {"required": "필수", "core": "핵심", "secondary": "보조", None: "역할 없음"}
VERIFICATION_LABEL = {"confirmed": "확인됨", "reported": "보도/추정", "forecast": "전망"}
_Q = re.compile(r"^(\d{2}|\d{4})Q([1-4])$")
_M = re.compile(r"^(\d{4})-(\d{2})$")
_Y = re.compile(r"^(\d{4})(?:E|A)?$")


# --------------------------------------------------------------------------- 기간 (월 · 분기 · 연) 의 순서와 끝 날짜
def period_key(at: str) -> tuple[int, int] | None:
    """'26Q4' · '2026Q4' · '4Q26' · '2026-10' · '2027' → 정렬 키 (연, 월). 모르면 None."""
    s = (at or "").strip().upper()
    m = _Q.match(s)
    if m:
        y = int(m[1]) + (2000 if len(m[1]) == 2 else 0)
        return y, int(m[2]) * 3
    m = re.match(r"^([1-4])Q(\d{2}|\d{4})[EPFA]?$", s)
    if m:
        y = int(m[2]) + (2000 if len(m[2]) == 2 else 0)
        return y, int(m[1]) * 3
    m = _M.match(s)
    if m:
        return int(m[1]), int(m[2])
    m = _Y.match(s)
    if m:
        return int(m[1]), 12
    return None


def period_end(at: str) -> date | None:
    k = period_key(at)
    if k is None:
        return None
    y, mth = k
    nxt = date(y + (1 if mth == 12 else 0), 1 if mth == 12 else mth + 1, 1)
    return nxt - timedelta(days=1)


def cadence_days(period: str) -> int:
    """관측 주기 — 월 31 · 분기 92 · 연 366 (주기의 2배를 넘으면 오래된 관측값)."""
    s = (period or "").strip().upper()
    if _M.match(s):
        return 31
    if _Q.match(s) or re.match(r"^[1-4]Q", s):
        return 92
    return 366


def normalize_period(at: str) -> str:
    """'4Q26E' · '2026Q4' → '26Q4' 로 통일 (분기만). 그 밖은 그대로."""
    k = period_key(at)
    s = (at or "").strip().upper()
    if k and (re.match(r"^[1-4]Q", s) or _Q.match(s)):
        return f"{k[0] % 100:02d}Q{k[1] // 3}"
    return (at or "").strip()


# --------------------------------------------------------------------------- 관측값 입력
def observe(m: MindMap, node_id: str, obs: Observation) -> list[str]:
    """드라이버에 관측값을 더한다. 돌려주는 것은 경고(자가 다름 · 전망값 · 중복 교체). 여덟 칸은 모델이 강제한다."""
    n = m[node_id]
    if n.driver is None:
        raise ValueError(f"{node_id} 는 드라이버가 아니다")
    d = n.driver
    warns: list[str] = []
    obs.period = normalize_period(obs.period)
    obs.compare_at = normalize_period(obs.compare_at)
    if d.required is not None and not d.required.ruler.same(obs.ruler):
        warns.append(f"자가 다름 — 관측 {obs.ruler.label()} vs 필요 경로 {d.required.ruler.label()}: 저장은 하되 신호등 비교에서 뺀다")
    if obs.verification == "forecast":
        warns.append("전망값(forecast) 은 관측값이 아니다 — 저장은 하되 신호등에 쓰지 않는다")
    # 같은 (지표·기간·자) 의 관측값이 있으면 교체
    for i, o in enumerate(d.observations):
        if o.metric_id == obs.metric_id and o.period == obs.period and o.value_type == obs.value_type and o.basis == obs.basis:
            d.observations[i] = obs
            warns.append(f"같은 기간({obs.period}) 관측값을 교체")
            break
    else:
        d.observations.append(obs)
    d.observations.sort(key=lambda o: (period_key(o.period) or (0, 0), o.as_of))
    return warns


def observation_from_dict(raw: dict[str, Any]) -> Observation:
    """CLI·수집기·에이전트 JSON → Observation (빠진 칸은 pydantic 오류로 드러난다)."""
    d = dict(raw)
    if isinstance(d.get("as_of"), str):
        d["as_of"] = date.fromisoformat(d["as_of"][:10])
    v = d.get("value")
    if isinstance(v, str):
        try:
            d["value"] = float(v.replace(",", ""))
        except ValueError:
            pass
    return Observation.model_validate(d)


def next_point(path: RequiredPath | None, today: date) -> PathPoint | None:
    """오늘 이후에 끝나는 첫 점 (비교·경계 제안의 기준). 없으면 마지막 점."""
    if path is None or not path.points:
        return None
    for p in path.points:
        end = period_end(p.at)
        if end is None or end >= today:
            return p
    return path.points[-1]


# --------------------------------------------------------------------------- 같은 자 · 신호등
def comparable(obs: Observation, path: RequiredPath | None) -> tuple[bool, str]:
    if path is None:
        return False, "필요 경로 없음"
    if obs.verification != "confirmed":
        return False, f"검증 등급 {VERIFICATION_LABEL.get(obs.verification, obs.verification)} — 확인됨만 신호등에 쓴다"
    if not path.ruler.same(obs.ruler):
        return False, "자가 다름"
    if path.point(obs.compare_at) is None:
        return False, f"필요 경로에 {obs.compare_at} 점이 없다"
    return True, ""


@dataclass
class LightResult:
    light: Light | None = None
    reason: str = ""
    trend: str | None = None               # on_path · drifting · off_path
    observation: Observation | None = None
    required: PathPoint | None = None
    deviation: float | None = None         # 관측 − 필요 (자의 단위)
    band: float | None = None
    stale: bool = False
    notes: list[str] = field(default_factory=list)


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None and not isinstance(v, str) else (float(v.replace(",", "")) if isinstance(v, str) and re.match(r"^-?[\d,]+(\.\d+)?$", v.strip()) else None)
    except (TypeError, ValueError):
        return None


def light_for(n: Node, *, today: date | None = None) -> LightResult:
    """드라이버 하나의 신호등 (v7 §3.4 규칙). 확인된 관측값 · 같은 자 · 비교 시점의 필요값과 띠가 있을 때만 켜진다."""
    today = today or date.today()
    d = n.driver
    r = LightResult()
    if d is None:
        r.reason = "드라이버 아님"
        return r
    path = d.required
    usable = [(o, comparable(o, path)) for o in d.observations]
    good = [o for o, (ok, _) in usable if ok]
    if not good:
        why = sorted({why for _, (ok, why) in usable if not ok}) or (["관측값 없음"] if not d.observations else [])
        r.reason = "증거 없음 — " + ("; ".join(why) if why else "관측값 없음")
        return r
    obs = max(good, key=lambda o: (period_key(o.period) or (0, 0), o.as_of))
    pt = path.point(obs.compare_at) if path is not None else None
    r.observation, r.required = obs, pt
    age = (today - obs.as_of).days
    r.stale = age > 2 * cadence_days(obs.period)
    band = pt.band if (pt is not None and pt.band is not None) else d.band
    r.band = band
    hib = d.higher_is_better
    # ---- 상태형 지표: 단계 도달 여부와 기한
    if obs.value_type == "state" or isinstance(obs.value, str) or (pt is not None and isinstance(pt.value, str)):
        want = str(pt.value) if pt is not None else ""
        have = str(obs.value)
        deadline = period_end(obs.compare_at)
        if want and (have == want or (want in have)):
            r.light, r.reason = Light.GREEN, f"단계 도달 ({have})"
        elif isinstance(d.bound_bear, str) and d.bound_bear and d.bound_bear in have:
            r.light, r.reason = Light.RED, f"Bear 경계 상태 ({have})"
        elif deadline and today > deadline:
            r.light, r.reason = Light.RED, f"기한 {obs.compare_at} 지남 — 아직 '{have}' (필요 '{want}')"
        else:
            r.light, r.reason = Light.YELLOW, f"단계 미도달 가능 — '{have}' (필요 '{want}', 기한 {obs.compare_at})"
        if r.stale:
            r.notes.append(f"관측값 {age}일 전 — 오래됨")
        return r
    # ---- 숫자형
    v, req = _num(obs.value), (_num(pt.value) if pt is not None else None)
    if v is None or req is None:
        r.light, r.reason = None, "관측값 또는 필요값이 숫자가 아니다"
        return r
    dev = v - req
    r.deviation = dev
    bear_side = (dev < 0) if hib else (dev > 0)
    bb = _num(d.bound_bear)
    crossed = bb is not None and ((v < bb) if hib else (v > bb))
    in_band = band is not None and abs(dev) <= band
    # 추세: 최근 두 관측이 연속으로 Bear 쪽으로 경로에서 멀어짐
    prev = [o for o in good if (period_key(o.period) or (0, 0)) < (period_key(obs.period) or (0, 0))]
    r.trend = "on_path"
    if prev and path is not None:
        p1 = max(prev, key=lambda o: (period_key(o.period) or (0, 0), o.as_of))
        pp = path.point(p1.compare_at)
        v1, r1 = _num(p1.value), (_num(pp.value) if pp is not None else None)
        if v1 is not None and r1 is not None:
            d1 = v1 - r1
            worse = (dev < d1) if hib else (dev > d1)
            if worse and bear_side:
                r.trend = "drifting"
    if crossed:
        r.light, r.reason = Light.RED, f"Bear 경계 {bb:,.4g} 를 넘음 (관측 {v:,.4g} · 필요 {req:,.4g})"
    elif band is None:
        r.light, r.reason = Light.YELLOW, f"허용 띠 미입력 — 관측 {v:,.4g} · 필요 {req:,.4g} (차이 {dev:+,.4g})"
    elif in_band and r.trend == "drifting":
        r.light, r.reason = Light.YELLOW, f"띠 안이지만 경로에서 멀어지는 중 (차이 {dev:+,.4g}, 띠 ±{band:,.4g})"
    elif in_band and r.stale:
        r.light, r.reason = Light.YELLOW, f"띠 안 · 관측값 {age}일 전 — 오래됨"
    elif in_band:
        r.light, r.reason = Light.GREEN, f"띠 안 (관측 {v:,.4g} · 필요 {req:,.4g} · 차이 {dev:+,.4g} · 띠 ±{band:,.4g})"
    else:
        r.light, r.reason = Light.YELLOW, f"띠 밖 · 경계 안 (관측 {v:,.4g} · 필요 {req:,.4g} · 차이 {dev:+,.4g} · 띠 ±{band:,.4g})"
    if r.trend == "off_path":
        pass
    if r.light != Light.RED and not bear_side and dev != 0 and band is not None and abs(dev) > band:
        r.notes.append("Bull 쪽 이탈")
    return r


# --------------------------------------------------------------------------- 띠 · 경계 제안
def propose_bounds(m: NewMap, node_id: str) -> dict[str, Any]:
    """Bear 경계 제안 (사용자 결정): 이 변수 하나로 Base→Bear 가격 차이의 절반을 만드는 변화. 띠 = 그 절반.
    Base·Bear 가격은 시나리오(없으면 평가 모델 PER 가격 × 0.8 을 Bear 로 쓰고 그렇게 표시). 민감도(가격 Δ/변수 Δ%) 가 있어야 한다."""
    n = m[node_id]
    d = n.driver
    out: dict[str, Any] = {"node_id": node_id, "status": "미계산", "note": ""}
    if d is None or d.sensitivity is None or d.sensitivity.price_delta is None:
        out["note"] = "민감도(가격 Δ) 가 없다 — 사업부 표·입력을 먼저"
        return out
    sens = d.sensitivity
    if sens.template in ("payout", "orders"):
        out["note"] = "가격에 닿지 않는 변수(환원·수주) — 경계는 사람이 정한다"
        return out
    if d.kind == "qualitative":
        out["note"] = "정성 드라이버 — 경계는 상태·문장으로 사람이 정한다 (예: '2분기 연속 원가율 상승')"
        return out
    delta = sens.input_delta_pct or 10.0
    slope = sens.price_delta / delta            # 원 / 변수 1%
    if abs(slope) < 1e-9:
        out["note"] = "민감도 기울기 0"
        return out
    sc = m.scenarios()
    base = sc.get("Base").target_price if sc.get("Base") else None
    bear = sc.get("Bear").target_price if sc.get("Bear") else None
    vm = (m.meta.get("draft") or {}).get("valuation") or {}
    if base is None:
        base = vm.get("price_per")
    how = "Base·Bear 시나리오 가격"
    if bear is None and base:
        bear, how = base * 0.8, "Bear 시나리오 없음 — Base × 0.8 을 임시 Bear 로"
    if not base or not bear:
        out["note"] = "Base/Bear 가격이 없다 — 시나리오 또는 평가 모델 필요"
        return out
    gap = base - bear
    x = -(gap / 2.0) / slope                   # 변수 변화 % (컨센 대비) 로 가격이 절반 내려가는 점
    lo, hi = {"price_volume": (-80.0, 200.0), "cost_ratio": (-30.0, 30.0), "direct_op": (-500.0, 500.0)}.get(sens.template, (-500.0, 500.0))
    if x < lo or x > hi:
        out["status"], out["note"] = "범위 밖", f"이 변수 {x:+.0f}% 로는 Base→Bear 차이의 절반을 만들 수 없다 (민감도 기울기 {slope:,.0f}원/% 작음) — 경계는 사람이 정한다"
        return out
    out.update({"status": "제안", "bound_bear_pct": x, "band_pct": abs(x) / 2.0, "gap": gap, "base": base, "bear": bear, "how": how,
                "note": f"{sens.template} 민감도 기울기 {slope:,.0f}원/% · Base {base:,.0f} → Bear {bear:,.0f} ({how}) 차이의 절반 {gap / 2:,.0f}원 ↔ 변수 {x:+.1f}%"})
    # 필요 경로가 있으면 다음 비교 시점(오늘 이후 첫 점) 의 값 × (1 + x%) 를 경계값으로
    nxt = next_point(d.required, date.today())
    if nxt is not None:
        pv = _num(nxt.value)
        if pv is not None:
            out["bound_bear_value"] = pv * (1.0 + x / 100.0)
            out["band_value"] = abs(pv * x / 100.0) / 2.0
            out["at"] = nxt.at
    return out


def apply_proposed_bounds(m: NewMap, node_id: str) -> dict[str, Any]:
    """제안된 띠·경계를 드라이버에 넣는다 (bounds_source=proposed; 사용자가 준 값은 덮어쓰지 않는다)."""
    d = m[node_id].driver
    pr = propose_bounds(m, node_id)
    if d is None or pr.get("status") != "제안":
        return pr
    if d.bounds_source == "user" and (d.bound_bear is not None or d.band is not None):
        pr["note"] += " · 사용자 값이 있어 덮어쓰지 않음"
        return pr
    if "bound_bear_value" in pr:
        d.bound_bear = round(pr["bound_bear_value"], 4)
        d.band = round(pr["band_value"], 4)
    else:
        unit = "%p" if (d.sensitivity is not None and d.sensitivity.template == "cost_ratio") else "%"
        d.bound_bear = f"컨센 대비 {pr['bound_bear_pct']:+.1f}{unit}"
        d.band = None
    d.bounds_source = "proposed"
    return pr


# --------------------------------------------------------------------------- 증거 계획 · 일정
@dataclass
class EvidenceRow:
    node_id: str
    label: str
    role: str | None
    ruler: str
    indicator: str
    observation: str
    required: str
    band: str
    bounds: str
    next_check: str
    light: Light | None
    reason: str
    trend: str | None


def evidence_plan(m: NewMap, *, today: date | None = None, write: bool = True) -> list[EvidenceRow]:
    """드라이버마다 한 줄 (v7 §3.4 표). write=True 면 드라이버의 light·trend 와 A5.5 값을 갱신한다."""
    today = today or date.today()
    rows: list[EvidenceRow] = []
    for n in m.drivers():
        d = n.driver
        if d is None or d.role == "result":
            continue
        lr = light_for(n, today=today)
        if write:
            d.light, d.light_reason, d.trend = lr.light, lr.reason, lr.trend
        obs = lr.observation or (d.observations[-1] if d.observations else None)
        obs_txt = (f"{obs.value} ({obs.as_of}, {VERIFICATION_LABEL.get(obs.verification, obs.verification)})" if obs else "—")
        req_txt = "—"
        if lr.required is not None:
            req_txt = f"{lr.required.at} {lr.required.value}" + (" (보간)" if lr.required.source == "interpolated" else "")
        elif d.required is not None and d.required.points:
            p = next_point(d.required, today) or d.required.points[0]
            req_txt = f"{p.at} {p.value}" + (" (보간)" if p.source == "interpolated" else "")
        band = lr.band if lr.band is not None else d.band
        rows.append(EvidenceRow(
            node_id=n.id, label=n.label.split(" (")[0], role=d.thesis_role,
            ruler=d.required.ruler.label() if d.required is not None else "(경로 없음)",
            indicator=(d.check_indicator or " · ".join(d.proxies))[:60],
            observation=obs_txt, required=req_txt, band=(f"±{band:,.4g}" if band is not None else "—") + (" (제안)" if d.bounds_source == "proposed" else ""),
            bounds=f"Bear {d.bound_bear if d.bound_bear is not None else '—'} / Bull {d.bound_bull if d.bound_bull is not None else '—'}",
            next_check=d.next_check.isoformat() if d.next_check else "—", light=lr.light, reason=lr.reason, trend=lr.trend))
    if write and "A5.5" in m:
        m["A5.5"].value = evidence_plan_text(rows)
        m["A5.5"].as_of = today
        draft = m.meta.setdefault("draft", {})
        draft["evidence_plan"] = [r.__dict__ | {"light": r.light.value if r.light else None} for r in rows]
        draft["lights"] = light_counts(rows)
    return rows


def light_counts(rows: list[EvidenceRow]) -> dict[str, dict[str, int]]:
    """역할별 신호등 수 — 합산하지 않는다."""
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        role = r.role or "none"
        key = r.light.value if r.light else "NONE"
        out.setdefault(role, {})[key] = out.setdefault(role, {}).get(key, 0) + 1
    return out


def light_counts_text(counts: dict[str, dict[str, int]]) -> str:
    order = ["required", "core", "secondary", "none"]
    parts = []
    for role in order:
        c = counts.get(role)
        if not c:
            continue
        inner = " ".join(f"{LIGHT_ICON.get(Light(k) if k != 'NONE' else None)}{v}" for k, v in sorted(c.items()))
        parts.append(f"{ROLE_LABEL.get(role if role != 'none' else None)} {inner}")
    return " · ".join(parts) if parts else "드라이버 없음"


def no_evidence(rows: list[EvidenceRow]) -> bool:
    """'증거 없는 판단' — ⚪ 가 드라이버 과반 (사용자 결정)."""
    cause = [r for r in rows]
    return bool(cause) and sum(1 for r in cause if r.light is None) * 2 > len(cause)


def evidence_plan_text(rows: list[EvidenceRow]) -> str:
    if not rows:
        return "증거 계획 없음 (원인 드라이버 없음)"
    lines = [f"{'드라이버':<14} {'역할':<4} {'신호':<2} {'관측 지표 (출처·주기)':<30} {'관측 (날짜·등급)':<26} {'필요값':<18} {'띠':<10} {'경계':<22} {'다음':<10} 사유"]
    for r in rows:
        lines.append(f"{r.label[:13]:<14} {ROLE_LABEL.get(r.role, '—')[:4]:<4} {LIGHT_ICON.get(r.light)}  {r.indicator[:29]:<30} {r.observation[:25]:<26} {r.required[:17]:<18} "
                     f"{r.band[:9]:<10} {r.bounds[:21]:<22} {r.next_check:<10} {r.reason[:70]}")
    return "\n".join(lines)


def _matters(e) -> bool:  # noqa: ANN001
    from ..generate.catalysts import event_matters

    return event_matters(e)


def evidence_schedule(m: NewMap, *, today: date | None = None) -> list[dict[str, Any]]:
    """증거 일정 (A8.1): 날짜마다 무엇을 · 어디서 · 어느 비교 시점의 필요값과. 드라이버의 다음 확인일 + 필요 경로의 점 + 촉매."""
    today = today or date.today()
    out: list[dict[str, Any]] = []
    for n in m.drivers():
        d = n.driver
        if d is None or d.role == "result":
            continue
        if d.next_check:
            pt = next_point(d.required, today)
            out.append({"date": d.next_check.isoformat(), "node": n.id, "what": n.label.split(" (")[0], "where": (d.check_indicator or "")[:60],
                        "compare_at": pt.at if pt else None, "required": (f"{pt.value}" if pt else None), "kind": "driver"})
        if d.required is not None:
            for p in d.required.points:
                end = period_end(p.at)
                if end and end >= today:
                    out.append({"date": end.isoformat(), "node": n.id, "what": n.label.split(" (")[0] + f" {p.at} 필요값", "where": d.required.ruler.label(),
                                "compare_at": p.at, "required": f"{p.value}" + (" (보간)" if p.source == "interpolated" else ""), "kind": "path"})
    for n in m.children("A6.1"):
        e = n.event
        if e is None or e.status.value == "cancelled":
            continue
        # 원문 그대로 — 자르지 않는다 (2026-10-31 3차 검토). 터미널 출력(evidence_schedule_text) 만 폭을 맞춰 자른다
        out.append({"date": e.checkpoint().isoformat(), "node": n.id, "what": (e.period + " " if e.period else "") + e.fact,
                    "where": f"촉매 · {e.certainty.value}", "compare_at": None, "required": " / ".join(e.precheck) or None, "kind": "event",
                    "check": " · ".join(e.precheck), "action": e.rule or "", "affects": list(e.affects),
                    "matters": _matters(e)})
    out.sort(key=lambda x: x["date"])
    draft = m.meta.setdefault("draft", {})
    draft["evidence_schedule"] = out
    return out


def evidence_schedule_text(items: list[dict[str, Any]], limit: int = 20) -> str:
    if not items:
        return "증거 일정 없음"
    return "\n".join(f"  {x['date']}  {x['what'][:28]:<28} {(x.get('where') or '')[:30]:<30} " + (f"vs {x['compare_at']} {x['required']}" if x.get("compare_at") else "") for x in items[:limit])


def propose_roles(m: NewMap, sector: str | None) -> list[str]:
    """역할 기본값 제안 (사용자 결정 — 권고값): 섹터 사전의 첫 원인 KPI = 핵심, 정성 드라이버 = 필수 후보, 나머지 보조. 사람이 준 역할은 건드리지 않는다."""
    from ..generate.kpi_catalog import cause_kpis

    first = next((k.driver_id for k in cause_kpis(sector)), None)
    notes: list[str] = []
    for n in m.drivers():
        d = n.driver
        if d is None or d.role == "result" or d.thesis_role is not None:
            continue
        did = (d.metric or "").split(":")[0]
        if first and did == first:
            d.thesis_role = "core"
        elif d.kind == "qualitative":
            d.thesis_role = "required"
        else:
            d.thesis_role = "secondary"
        d.thesis_role_source = "proposed"
        notes.append(f"{n.id} 역할 제안 {ROLE_LABEL[d.thesis_role]}")
    return notes


__all__ = ["observe", "observation_from_dict", "comparable", "next_point", "light_for", "LightResult", "propose_bounds", "apply_proposed_bounds",
           "evidence_plan", "evidence_plan_text", "evidence_schedule", "evidence_schedule_text", "EvidenceRow", "light_counts", "light_counts_text",
           "no_evidence", "propose_roles", "period_key", "period_end", "normalize_period", "cadence_days", "LIGHT_ICON", "ROLE_LABEL"]
