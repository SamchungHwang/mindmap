"""밸류에이션 (설계 v6) — A3.2 평가 기준(사람) → A3.3 평가 모델(코드) → A4.7 확률가중 투자 매력도(코드) → A3.4 모델 신뢰도(코드).

    PER 가격      = EPS(기준: 컨센 | 내 추정 | 정상화 | 입력) × 적용 PER          (기본: 컨센 × 목표가 중앙값/EPS — 둘 다 "기본값" 표시)
    정상화 가격    = 추정 연도 3개(2026E~2028E) EPS 평균 × 적용 PER                 (사용자 결정 ③ — 1곳 셀은 n 표시)
    SOTP 가격     = (Σ 사업부 OP × (1 − t) × 멀티플 + 순현금) / N                  (멀티플은 사용자 입력만 — 사용자 결정 ④; 검증용)
    역산          = 현재가 / PER → 내재 EPS ;  (내재 EPS 가 요구하는 OP − 컨센 OP) / 가격 드라이버 사업부 매출 → 내재 변수 경로 (%)
    공통 꼬리     t = 1 − NP/OP ;  N = 사용자 > 리포트 발행주식수 중앙값(header) > NP/EPS 유도 ;  PER = 사용자/기준 > 목표가 중앙값/EPS

민감도(1변수) · 시나리오(다변수) · 평가 모델이 **같은 꼬리 함수** `common_tail` 를 쓴다. 입력마다 출처(table · segment · header · criteria · user · default ·
derived · missing) 를 적고, 빠지면 status=미계산 + missing. LLM 은 어디에도 숫자를 만들지 않는다. 금액은 억원, EPS·주가는 원.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..models import MapKind, MindMap, NewMap, SensitivityInput, SotpPart, ValuationModel

EPS_BASES = ("consensus", "my_estimate", "normalized", "user")
EPS_BASIS_LABEL = {"consensus": "컨센", "my_estimate": "내 추정", "normalized": "정상화", "user": "입력"}
DIVIDEND_MODES = ("separate", "include", "exclude")
VALUATION_NODES = ("A3.2", "A3.3", "A3.4", "A4", "A4.1", "A4.7")   # 평가 기준·모델·신뢰도 + 시나리오 묶음·한눈에·매력도 (옛 맵도 열면 생긴다)
RESULT_EPS_NODE = "A3.1.3"
NORMALIZE_YEARS = 3                      # 정상화 EPS = 첫 추정 연도부터 3개 연도 평균 (사용자 결정 ③)
# 사업부 포함 관계 — SOTP 에 부모와 자식을 같이 넣지 않는다 (설계 v6 §3.2). 역산의 가격 드라이버 매출 합에서도 자식을 뺀다
SEGMENT_CONTAINS: dict[str, list[str]] = {
    "DS": ["메모리", "파운드리", "DRAM", "NAND", "HBM", "LSI"],
    "메모리": ["DRAM", "NAND", "HBM"],
    "DRAM": ["HBM"],
    "DX": ["MX", "VD", "네트워크", "생활가전", "DA"],
}
SNAP_METRICS = ("revenue", "op", "np", "eps", "dps", "bps", "net_debt", "net_cash", "fcf", "capex", "ocf", "per", "pbr", "roe")
# 유효세율 가드 (2026-10-31 3차 검토): 1 − NP/OP 는 영업외손익이 섞인 「비율」 이지 세율이 아니다 — 10-31 NAVER 지도에서 2027E NP ≈ OP
# (지분법·금융수익) 라 t = 0.4% 가 나와 SOTP 세후 OP 를 부풀렸다. 띠(10~35%) 밖이면 버리고 기본값.
# 한국 법인세 최고 구간 24% + 지방소득세 ≈ 26% 이지만 공제·이연을 감안해 22% 를 기본으로 둔다
TAX_BAND = (0.10, 0.35)
TAX_DEFAULT = 0.22
# 목표가 ÷ EPS 로 역산한 PER 의 상식 범위 — 밖이면 EPS(단위·정규화) 가 틀린 것이라 가격을 계산하지 않는다 (2026-10-31 삼성전자 0.0156배)
PER_SANE = (1.0, 300.0)


# --------------------------------------------------------------------------- 노드
def ensure_valuation_nodes(m: MindMap) -> list[str]:
    """A3.2~A3.4 이 없는 옛 스켈레톤에 노드를 만든다 (A 맵만)."""
    if not isinstance(m, NewMap) or "A3" not in m:
        return []
    from ..templates.questions import A_SPEC
    from ..templates.skeleton import _node_from_spec

    made: list[str] = []
    for nid in VALUATION_NODES:
        if nid not in m.nodes and nid in A_SPEC and nid.rsplit(".", 1)[0] in m:
            m.add(_node_from_spec(MapKind.NEW, nid, A_SPEC[nid]))
            made.append(nid)
    return made


# --------------------------------------------------------------------------- 평가 기준 (A3.2)
@dataclass
class Criteria:
    """A3.2 의 값(dict). 없는 키는 기본값 — 모델이 '기본값' 으로 표시한다."""

    eps_basis: str = "consensus"
    eps_year: str | None = None
    eps_value: float | None = None           # eps_basis=user 일 때
    per: float | None = None
    shares: float | None = None
    net_cash: float | None = None            # 억원
    net_cash_year: str | None = None
    dividend_in_return: str = "separate"
    dps: float | None = None                 # 원 (사용자가 직접 줄 때)
    sotp_multiples: dict[str, float] = field(default_factory=dict)
    sotp_stakes: dict[str, float] = field(default_factory=dict)   # 자회사 지배지분 비율 (0~1) — 없으면 1.0 + 경고
    earnings_basis: str = "지배"            # 설계 v6 §11.1: 컨센 EPS 의 순이익 기준 (지금은 지배만 지원 — 전체는 측정 지배 비율로 환산)
    share_count_basis: str = "보통주"       # 설계 v6 §11.1: 주식 수 기준 (지금은 보통주만 지원 — 우선주 포함·자기주식 제외 머리값은 걸러낸다)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_map(cls, m: MindMap) -> Criteria:
        v = m["A3.2"].value if "A3.2" in m and isinstance(m["A3.2"].value, dict) else {}
        c = cls(raw=dict(v))

        def num(k: str) -> float | None:
            x = v.get(k)
            try:
                return float(x) if x not in (None, "") else None
            except (TypeError, ValueError):
                return None

        b = str(v.get("eps_basis") or "consensus").strip()
        c.eps_basis = b if b in EPS_BASES else "consensus"
        c.eps_year = str(v["eps_year"]).strip() if v.get("eps_year") else None
        c.eps_value = num("eps") if num("eps") is not None else num("eps_value")
        c.per, c.shares, c.net_cash, c.dps = num("per"), num("shares"), num("net_cash"), num("dps")
        c.net_cash_year = str(v["net_cash_year"]).strip() if v.get("net_cash_year") else None
        d = str(v.get("dividend_in_return") or "separate").strip()
        c.dividend_in_return = d if d in DIVIDEND_MODES else "separate"
        c.earnings_basis = str(v.get("earnings_basis") or "지배").strip() or "지배"
        c.share_count_basis = str(v.get("share_count_basis") or "보통주").strip() or "보통주"
        mult = v.get("sotp_multiples")
        if isinstance(mult, dict):
            for k, x in mult.items():
                try:
                    if x not in (None, ""):
                        c.sotp_multiples[str(k)] = float(x)
                except (TypeError, ValueError):
                    continue
        st = v.get("sotp_stakes")
        if isinstance(st, dict):
            for k, x in st.items():
                try:
                    if x not in (None, ""):
                        c.sotp_stakes[str(k)] = float(x)
                except (TypeError, ValueError):
                    continue
        return c


def _inp(name: str, value: float | None, unit: str | None, source: str, period: str | None = None, note: str | None = None) -> SensitivityInput:
    return SensitivityInput(name=name, value=value, unit=unit, source=source if value is not None else "missing", period=period, note=note)


def _mean(cell: dict[str, Any] | None) -> float | None:
    if not cell:
        return None
    v = cell.get("mean")
    return float(v) if v is not None else None


def _median_of(cell: dict[str, Any] | None) -> float | None:
    if not cell:
        return None
    if cell.get("median") is not None:
        return float(cell["median"])
    xs = sorted(float(x) for x in (cell.get("by_broker") or {}).values() if x is not None)
    if not xs:
        return _mean(cell)
    return xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2


def year_cell(fin: dict[str, Any], year: str | None, metric: str) -> dict[str, Any] | None:
    """meta.draft.financials 에서 (연도, 지표) 셀 — years 표가 있으면 거기서, 없으면 기준 연도 지표에서."""
    if not year:
        return None
    years = fin.get("years") or {}
    c = (years.get(year) or {}).get(metric)
    if c:
        return c
    if year == fin.get("year") and isinstance(fin.get(metric), dict):
        return fin[metric]
    return None


# --------------------------------------------------------------------------- 공통 꼬리
@dataclass
class Tail:
    year: str | None = None
    op: float | None = None
    np: float | None = None
    eps: float | None = None
    eps_n: int = 0
    tp_median: float | None = None
    t: float | None = None
    shares: float | None = None             # EPS 환산용 (NP_E/EPS_E 유도 또는 사용자)
    shares_source: str = "missing"
    shares_out: float | None = None         # 공시 발행주식수 (SOTP 주당 환산)
    shares_out_source: str = "missing"
    per: float | None = None
    per_source: str = "missing"
    basis_status: str = "unchecked"         # 설계 v6 §11.1: comparable | mixed | not_comparable | unchecked(옛 맵)
    basis_notes: list[str] = field(default_factory=list)
    inputs: list[SensitivityInput] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.t is not None and self.shares is not None and self.per is not None


def common_tail(m: MindMap, user: dict[str, Any] | None = None, *, year: str | None = None, per_override: float | None = None) -> Tail:
    """OP → NP → EPS → 주가 공통 입력. 우선순위: 드라이버·시나리오의 사용자 입력 > A3.2 평가 기준 > 리포트 머리(발행주식수) > 표 유도 > 기본값."""
    user = {k: v for k, v in (user or {}).items() if v is not None}
    crit = Criteria.from_map(m)
    fin = (m.meta.get("draft") or {}).get("financials") or {}
    year = year or crit.eps_year or fin.get("year")
    tl = Tail(year=year, tp_median=fin.get("tp_median"))
    np_c, op_c, eps_c = year_cell(fin, year, "np"), year_cell(fin, year, "op"), year_cell(fin, year, "eps")
    tl.np, tl.op, tl.eps = _mean(np_c), _mean(op_c), _mean(eps_c)
    tl.eps_n = int((eps_c or {}).get("n") or 0)
    # 설계 v6 §11.1: 회계 기준 검사 결과 (financial_snapshot 이 남긴 것). 옛 맵(없음) 은 unchecked — 예전 규칙 그대로
    bc = fin.get("basis") or {}
    if bc:
        tl.basis_status = str(bc.get("status") or "unchecked")
        tl.basis_notes = list(bc.get("notes") or [])
        if (eps_c or {}).get("basis") == "std":
            tl.basis_notes.append(f"EPS 정규화: 지배 순이익 ÷ 공시 보통주 ({tl.eps_n}곳" + (f", 비율 환산 {len((eps_c or {}).get('approx') or {})}곳" if (eps_c or {}).get("approx") else "") + ")")
    # 유효세율 t
    tax = user.get("tax_rate")
    src_t = "user" if tax is not None else "derived"
    note_t = None
    if tax is None and tl.np and tl.op and tl.op > 0:
        raw = 1.0 - tl.np / tl.op
        cells = f"표 {(np_c or {}).get('n', 0)}·{(op_c or {}).get('n', 0)}곳"
        if TAX_BAND[0] <= raw <= TAX_BAND[1]:
            tax, note_t = raw, f"1 − NP/OP ({cells})"
        else:
            # 2026-10-31 3차 검토: NP/OP 역산은 영업외손익(지분법·금융·일회성) 이 섞여 세율이 아니다 — 띠 밖이면 버리고 기본값
            tax, src_t = TAX_DEFAULT, "default"
            note_t = (f"NP/OP 역산 {raw:.0%} 는 세율 띠 {TAX_BAND[0]:.0%}~{TAX_BAND[1]:.0%} 밖 ({cells}) — 영업외손익 포함이라 버림, "
                      f"기본 {TAX_DEFAULT:.0%} 적용. 세율은 A3.2 또는 시나리오 tax_rate 로 넣을 것")
    tl.t = float(tax) if tax is not None else None
    tl.inputs.append(_inp("유효세율 t", tl.t, "", src_t, year, note_t))
    if tl.t is None:
        tl.missing.append("유효세율 (표의 순이익·영업이익)")
    # 주식 수 — 두 개를 구분한다 (설계 v6 §2.2). ① EPS 환산용 N = NP_E/EPS_E (컨센 EPS 와 기준을 맞춘다: 가정 없는 Base = 컨센 EPS × PER 이 되도록)
    # ② 공시 발행주식수 N_out (리포트 머리 중앙값, 사용자 결정 ⑦) = SOTP 주당 환산·주당 환원. 사용자(드라이버 입력·A3.2) 가 shares 를 주면 둘 다 그 값
    hdr = (fin.get("shares_out") or {})
    std = fin.get("shares_std") or {}
    shares_eps: float | None = None
    note = None
    if user.get("shares") is not None:
        shares_eps, tl.shares_source = float(user["shares"]), "user"
    elif crit.shares is not None:
        shares_eps, tl.shares_source = crit.shares, "criteria"
        note = "A3.2 평가 기준"
    elif std.get("value") and (eps_c or {}).get("basis") == "std":
        # 설계 v6 §11.1: EPS 가 표준 주식 수로 정규화됐으면 환산 N 도 같은 값 — 유도 N 과의 이원화를 없앤다 (EPS_std × N_std = 지배 NP)
        shares_eps, tl.shares_source = float(std["value"]), "header"
        note = f"표준 주식 수 — {std.get('note') or '공시 보통주'}"
    elif tl.np and tl.eps and tl.eps > 0:
        shares_eps, tl.shares_source = tl.np * 1e8 / tl.eps, "derived"
        note = "NP_E / EPS_E — 컨센 EPS 기준에 맞춘 환산용"
        if hdr.get("median"):
            diff = shares_eps / float(hdr["median"]) - 1.0
            note += f" (공시 {float(hdr['median']) / 1e9:.3f}B 와 {diff:+.0%})"
    elif hdr.get("median"):
        shares_eps, tl.shares_source = float(hdr["median"]), "header"
        note = "리포트 발행주식수 중앙값 (표에 NP·EPS 가 없어 대신)"
    tl.shares = shares_eps
    tl.inputs.append(_inp("주식 수 N", shares_eps, "주", tl.shares_source, year, note))
    if shares_eps is None:
        tl.missing.append("주식 수 (표의 순이익·EPS 또는 리포트 머리 발행주식수)")
    if tl.shares_source in ("user", "criteria"):
        tl.shares_out, tl.shares_out_source = shares_eps, tl.shares_source
    elif hdr.get("median"):
        tl.shares_out, tl.shares_out_source = float(hdr["median"]), "header"
        tl.inputs.append(_inp("발행주식수 (공시)", tl.shares_out, "주", "header", None, f"리포트 머리 중앙값 ({len(hdr.get('by_broker') or {})}곳) — SOTP 주당 환산"))
    else:
        tl.shares_out, tl.shares_out_source = shares_eps, tl.shares_source
    # 적용 PER — 시나리오 지정 > 드라이버 사용자 입력 > 평가 기준 > 목표가 중앙값/EPS 기본값 (사용자 결정 ②)
    per: float | None = None
    pnote = None
    if per_override is not None:
        per, tl.per_source = float(per_override), "user"
    elif user.get("per_target") is not None:
        per, tl.per_source = float(user["per_target"]), "user"
    elif crit.per is not None:
        per, tl.per_source = crit.per, "criteria"
        pnote = "A3.2 평가 기준"
    elif tl.tp_median and tl.eps and tl.eps > 0:
        per, tl.per_source = tl.tp_median / tl.eps, "default"
        pnote = f"기본값 — 목표가 중앙값 {tl.tp_median:,.0f} / EPS_E {tl.eps:,.0f} ({fin.get('tp_n', 0)}곳)"
        if not (PER_SANE[0] <= per <= PER_SANE[1]):
            # 2026-10-31 삼성전자: EPS 30,722,739원(단위 오류) → PER 0.0156배로 목표가를 「재현」 했다. 역산 PER 가 상식 밖이면 EPS 가 틀린 것 — 계산을 멈춘다
            tl.missing.append(f"목표 PER — 목표가 {tl.tp_median:,.0f} / EPS_E {tl.eps:,.0f} = {per:.3g}배는 {PER_SANE[0]:g}~{PER_SANE[1]:g}배 밖 (EPS 단위·정규화 확인 전엔 계산 안 함)")
            per, tl.per_source, pnote = None, "missing", None
    tl.per = per
    tl.inputs.append(_inp("목표 PER", per, "배", tl.per_source, year, pnote))
    if per is None and not any(x.startswith("목표 PER") for x in tl.missing):
        tl.missing.append("목표 PER (A3.2 per 또는 목표가·EPS)")
    return tl


# --------------------------------------------------------------------------- EPS 기준
def my_estimate_eps(m: MindMap) -> tuple[float | None, str | None]:
    """A3.1.3 EPS 결과 지표의 내 추정 (값, 기간)."""
    n = m.get(RESULT_EPS_NODE)
    if n is None or n.driver is None or n.driver.my_estimate.value is None:
        return None, None
    return float(n.driver.my_estimate.value), n.driver.period


def normalized_eps(fin: dict[str, Any]) -> tuple[float | None, list[str], dict[str, int]]:
    """첫 추정 연도부터 NORMALIZE_YEARS 개 연도의 EPS 평균 (있는 연도만, 2개 이상). 연도별 n 도 돌려준다 (1곳 셀 표시용)."""
    years = fin.get("years") or {}
    est = sorted(y for y in years if y.endswith("E") and (years[y].get("eps") or {}).get("mean") is not None)
    pick = est[:NORMALIZE_YEARS]   # 첫 추정 연도부터 (2026E·2027E·2028E) — 고점 연도 하나로 가격을 매기지 않기 위해
    if len(pick) < 2:
        return None, pick, {y: int((years[y].get("eps") or {}).get("n") or 0) for y in pick}
    vals = [float(years[y]["eps"]["mean"]) for y in pick]
    return sum(vals) / len(vals), pick, {y: int((years[y].get("eps") or {}).get("n") or 0) for y in pick}


def eps_for_basis(m: MindMap, basis: str, tail: Tail, crit: Criteria) -> tuple[float | None, str, str, str | None]:
    """(EPS, 출처, 라벨, 경고). 내 추정이 없으면 컨센으로 내려오며 경고."""
    fin = (m.meta.get("draft") or {}).get("financials") or {}
    if basis == "user":
        if crit.eps_value is not None:
            return crit.eps_value, "criteria", f"입력 EPS {crit.eps_value:,.0f}", None
        return tail.eps, "table", f"컨센 EPS {tail.eps:,.0f}" if tail.eps else "컨센 EPS 없음", "입력 EPS 가 없어 컨센으로"
    if basis == "my_estimate":
        v, period = my_estimate_eps(m)
        if v is not None:
            warn = None if not period or not tail.year or period == tail.year else f"내 추정 기간 {period} ≠ 모델 연도 {tail.year}"
            return v, "user", f"내 추정 EPS {v:,.0f}", warn
        return tail.eps, "table", f"컨센 EPS {tail.eps:,.0f}" if tail.eps else "컨센 EPS 없음", "A3.1.3 내 추정이 비어 있어 컨센으로"
    if basis == "normalized":
        v, yrs, ns = normalized_eps(fin)
        if v is not None:
            one = [y for y, n in ns.items() if n <= 1]
            return v, "derived", f"정상화 EPS {v:,.0f} ({yrs[0][:4]}E~{yrs[-1][:4]}E 평균)", (f"{', '.join(one)} 는 증권사 1곳" if one else None)
        return tail.eps, "table", f"컨센 EPS {tail.eps:,.0f}" if tail.eps else "컨센 EPS 없음", "정상화에 쓸 추정 연도가 2개 미만 — 컨센으로"
    return tail.eps, "table", (f"컨센 EPS {tail.eps:,.0f} ({tail.year} {tail.eps_n}곳)" if tail.eps else "컨센 EPS 없음"), None


# --------------------------------------------------------------------------- 사업부 도우미
def segment_values(m: MindMap, year: str | None, metric: str) -> dict[str, tuple[float, int]]:
    segs = (m.meta.get("draft") or {}).get("segments") or {}
    out: dict[str, tuple[float, int]] = {}
    for seg, mets in segs.items():
        c = (mets.get(metric) or {}).get(year or "")
        if c and c.get("mean") is not None:
            out[seg] = (float(c["mean"]), int(c.get("n") or 0))
    return out


def drop_contained(segments: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """부모가 같이 있으면 자식을 뺀다. (남은 목록, 뺀 (자식, 부모) 쌍)."""
    keep: list[str] = []
    dropped: list[tuple[str, str]] = []
    for s in segments:
        parent = next((p for p, kids in SEGMENT_CONTAINS.items() if s in kids and p in segments), None)
        if parent and parent != s:
            dropped.append((s, parent))
        else:
            keep.append(s)
    return keep, dropped


def price_driver_segments(m: MindMap) -> list[str]:
    """가격·물량 템플릿 원인 드라이버의 사업부 (역산의 분모)."""
    info = (m.meta.get("draft") or {}).get("drivers") or {}
    segs: list[str] = []
    for n in getattr(m, "drivers", lambda: [])():
        d = n.driver
        if d is None or d.role != "cause":
            continue
        meta = info.get(n.id) or {}
        tpl = meta.get("template") or ("price_volume" if (meta.get("variable") in ("asp", "volume")) else None)
        if tpl == "price_volume" and meta.get("segment") and meta["segment"] not in segs:
            segs.append(meta["segment"])
    return segs


# --------------------------------------------------------------------------- A3.3 평가 모델
PER_SOURCE = {"default": "broker_target_implied", "user": "user", "criteria": "user", "missing": "missing"}
PER_SOURCE_LABEL = {"broker_target_implied": "목표가 유도", "user": "사용자", "historical_normalized": "과거 밴드", "peer": "피어", "missing": "없음",
                    "broker_per_row": "증권사 PER 행 중앙값"}


def implied_for_per(m: MindMap, tail: Tail, p0: float, per: float) -> dict[str, Any]:
    """PER 하나에 대한 역산 한 줄 — 다른 변수 고정. 돌려주는 dict: implied_eps · vs_consensus_pct · op_required · path_pct · path_label."""
    out: dict[str, Any] = {"per": per, "implied_eps": p0 / per, "vs_consensus_pct": None, "op_required": None, "path_pct": None, "path_label": None}
    if tail.eps:
        out["vs_consensus_pct"] = (out["implied_eps"] / tail.eps - 1.0) * 100.0
    keep, _ = drop_contained(price_driver_segments(m))
    rev = segment_values(m, tail.year, "revenue")
    base_rev = sum(rev[s][0] for s in keep if s in rev)
    if base_rev > 0 and tail.shares and tail.t is not None and tail.op is not None:
        op_req = out["implied_eps"] * tail.shares / 1e8 / (1.0 - tail.t)
        out["op_required"] = op_req
        out["path_pct"] = (op_req - tail.op) / base_rev * 100.0
        out["path_label"] = "+".join(s for s in keep if s in rev) + " ASP (k=1.0)"
    return out


def implied_table(m: MindMap, tail: Tail, p0: float, crit: Criteria | None = None, extra: list[float] | None = None) -> list[dict[str, Any]]:
    """여러 PER 의 역산 표 (설계 v6 §11.2 ③) — 목표가 유도 PER · 증권사 PER 행 중앙값(각 리포트 가격 기준) · 사용자 PER · 지정 목록.
    같은 가격을 설명하는 (PER, EPS, 경로) 조합이 여럿이라는 것을 보이기 위한 표 — 어느 줄도 '시장의 기대' 가 아니다."""
    fin = (m.meta.get("draft") or {}).get("financials") or {}
    rows: list[dict[str, Any]] = []
    seen: set[float] = set()

    def add(per: float | None, source: str, label: str) -> None:
        if not per or per <= 0 or round(per, 3) in seen:
            return
        seen.add(round(per, 3))
        r = implied_for_per(m, tail, p0, per)
        r.update({"source": source, "label": label})
        rows.append(r)

    if tail.per_source == "default" and tail.tp_median and tail.eps:
        add(tail.per, "broker_target_implied", f"목표가 유도 (중앙값 {tail.tp_median:,.0f} ÷ EPS {tail.eps:,.0f})")
    elif tail.per:
        add(tail.per, "user", "사용자 (A3.2)")
    if tail.tp_median and tail.eps and tail.per_source != "default":
        add(tail.tp_median / tail.eps, "broker_target_implied", f"목표가 유도 (중앙값 {tail.tp_median:,.0f} ÷ EPS {tail.eps:,.0f})")
    per_cell = year_cell(fin, tail.year, "per") if tail.year else None
    if isinstance(per_cell, dict) and per_cell.get("median"):
        add(float(per_cell["median"]), "broker_per_row", f"증권사 PER 행 중앙값 ({int(per_cell.get('n') or 0)}곳, 각 리포트 가격 기준)")
    if tail.eps:
        add(p0 / tail.eps, "consensus_eps", "실적 컨센 그대로일 때의 PER (현재가 ÷ 컨센 EPS)")
    for x in extra or []:
        add(float(x), "user", "지정")
    # 양 끝 사이가 1.5배 넘게 벌어지면 기하 중간점을 하나 끼운다 — "같은 가격을 설명하는 조합이 연속으로 있다" 를 보이기 위해
    pers = sorted(r["per"] for r in rows)
    if len(pers) >= 2 and pers[-1] / pers[0] > 1.5:
        mid = (pers[0] * pers[-1]) ** 0.5
        add(round(mid, 2), "midpoint", f"중간 ({pers[0]:.2f} 과 {pers[-1]:.2f} 의 기하 평균)")
    rows.sort(key=lambda r: -r["per"])
    return rows


def implied_line(vm: ValuationModel, p0: float | None) -> str:
    """역산 한 줄 — 조건부 문구 (설계 v6 §11.2). '내재' 라는 말을 쓰지 않는다."""
    if vm.implied_eps is None:
        return ""
    src = PER_SOURCE_LABEL.get(vm.per_source, vm.per_source)
    cond = f"PER {vm.per:.2f}({src})·다른 변수 고정 가정" if vm.per else "다른 변수 고정 가정"
    s = f"역산({cond}): 현재가" + (f" {p0:,.0f}" if p0 else "") + f" 를 설명하는 EPS {vm.implied_eps:,.0f}"
    if vm.implied_eps_vs_consensus_pct is not None:
        s += f" (컨센 {vm.implied_eps_vs_consensus_pct:+.0f}%)"
    if vm.implied_path_pct is not None:
        s += f" · {vm.implied_path_label} 변화 {vm.implied_path_pct:+.0f}%"
    others: list[dict[str, Any]] = []
    seen_path: set[int] = {int(round(vm.implied_path_pct))} if vm.implied_path_pct is not None else set()
    for r in vm.implied_table:
        if r.get("path_pct") is None or abs(r["per"] - (vm.per or 0)) < 1e-6 or int(round(r["path_pct"])) in seen_path:
            continue
        seen_path.add(int(round(r["path_pct"])))
        others.append(r)
    if others:
        s += " · 다른 PER 면: " + " / ".join(f"{r['per']:.2f}→{r['path_pct']:+.0f}%" for r in others[:3])
    if vm.implied_status == "assumption_dependent":
        s += " [가정 의존 — 독립 PER 를 넣으면 바뀜]"
    return s


def compute_valuation(m: MindMap, *, today: date | None = None) -> ValuationModel:
    """A3.2 기준으로 PER 가격·정상화·SOTP·역산을 계산해 돌려준다 (맵에는 쓰지 않는다 — `write_valuation` 이 쓴다)."""
    today = today or date.today()
    crit = Criteria.from_map(m)
    tail = common_tail(m)
    fin = (m.meta.get("draft") or {}).get("financials") or {}
    p0 = getattr(m, "current_price", None)
    vm = ValuationModel(eps_basis=crit.eps_basis, eps_year=tail.year, computed_at=today, per=tail.per,
                        per_basis="user" if tail.per_source in ("user", "criteria") else ("default" if tail.per is not None else "missing"),
                        basis_status=tail.basis_status)
    inputs = list(tail.inputs)
    missing = list(tail.missing)
    warnings: list[str] = []
    vm.eps_consensus = tail.eps
    vm.eps_my_estimate, _ = my_estimate_eps(m)
    vm.eps_normalized, vm.normalized_years, n_by_year = normalized_eps(fin)
    eps, src, label, warn = eps_for_basis(m, crit.eps_basis, tail, crit)
    vm.eps = eps
    inputs.append(_inp("기준 EPS", eps, "원", src, tail.year, label))
    if warn:
        warnings.append(warn)
    if eps is None:
        missing.append("기준 EPS (표의 EPS 또는 A3.2 eps)")
    if eps is not None and tail.per is not None:
        vm.price_per = eps * tail.per
    if vm.eps_normalized is not None and tail.per is not None:
        vm.price_normalized = vm.eps_normalized * tail.per
        one = [y for y, n in n_by_year.items() if n <= 1]
        if one:
            warnings.append(f"정상화 EPS 의 {', '.join(one)} 는 증권사 1곳")
    if vm.eps_my_estimate is not None and tail.per is not None:
        vm.price_my_estimate = vm.eps_my_estimate * tail.per
    # ---- SOTP (검증) — 멀티플은 사용자 입력만
    seg_op = segment_values(m, tail.year, "op")
    if crit.sotp_multiples:
        names = [s for s in crit.sotp_multiples]
        keep, dropped = drop_contained(names)
        if dropped:
            warnings.append("SOTP 사업부 겹침 — " + ", ".join(f"{c} ⊂ {p}" for c, p in dropped) + ": 자식은 뺐다")
        total_value = 0.0
        ok = tail.t is not None
        for seg in keep:
            mult = crit.sotp_multiples[seg]
            op_n = seg_op.get(seg)
            part = SotpPart(segment=seg, multiple=mult, source="user")
            if op_n is None:
                part.source = "missing"
                missing.append(f"SOTP {seg} 영업이익({tail.year}) — 사업부 표")
                ok = False
            else:
                part.op, part.n = op_n
                if tail.t is not None:
                    stake = crit.sotp_stakes.get(seg, 1.0)
                    part.np = part.op * (1.0 - tail.t)
                    part.value = part.np * mult * stake
                    total_value += part.value
                    if seg in ("SDC", "Harman", "하만") and seg not in crit.sotp_stakes:
                        warnings.append(f"SOTP {seg}: 지배지분 비율(sotp_stakes) 없음 — 100% 로 계산")
            vm.sotp.append(part)
        if tail.op is not None and keep:
            covered = sum(seg_op[s][0] for s in keep if s in seg_op)
            vm.sotp_other_op = tail.op - covered
        # 순현금 — 사용자 > 최근 실적 연도 순차입금×(−1) > 순현금 행
        nc_year = crit.net_cash_year or fin.get("latest_actual_year")
        if crit.net_cash is not None:
            vm.net_cash, vm.net_cash_year = crit.net_cash, crit.net_cash_year or "입력"
            inputs.append(_inp("순현금", vm.net_cash, "억원", "criteria", vm.net_cash_year, "A3.2 평가 기준"))
        else:
            nd = year_cell(fin, nc_year, "net_debt")
            ncs = year_cell(fin, nc_year, "net_cash")
            if nd and _mean(nd) is not None:
                vm.net_cash, vm.net_cash_year = -float(_mean(nd) or 0.0), nc_year
                inputs.append(_inp("순현금", vm.net_cash, "억원", "table", nc_year, f"순차입금 × −1 (표 {nd.get('n', 0)}곳, 최근 실적 연도)"))
            elif ncs and _mean(ncs) is not None:
                vm.net_cash, vm.net_cash_year = float(_mean(ncs) or 0.0), nc_year
                inputs.append(_inp("순현금", vm.net_cash, "억원", "table", nc_year, f"순현금 행 (표 {ncs.get('n', 0)}곳)"))
            else:
                inputs.append(_inp("순현금", None, "억원", "missing", nc_year))
                missing.append("순현금 (표의 순차입금 행 또는 A3.2 net_cash)")
                ok = False
        if ok and tail.shares_out:
            vm.shares = tail.shares_out
            vm.price_sotp = (total_value + (vm.net_cash or 0.0)) * 1e8 / tail.shares_out
            if vm.price_per:
                vm.gap_pct = (vm.price_sotp / vm.price_per - 1.0) * 100.0
                if abs(vm.gap_pct) > 15:
                    warnings.append(f"SOTP 와 PER 가격 차이 {vm.gap_pct:+.0f}% — 멀티플 또는 기준 EPS 를 다시 볼 것")
    else:
        warnings.append("SOTP 미계산 — A3.2 sotp_multiples {사업부: 배} 를 넣으면 검증한다 (사용자 입력만)")
    # ---- 역산 (설계 v6 §11.2 / v7 T2) — "PER 와 다른 변수를 고정하면 현재가를 설명하는 EPS·가격 드라이버 변화" — 수학적 해이지 시장의 기대가 아니다.
    # 적용 PER 가 목표가 유도값(순환) 이면 assumption_dependent, 사용자·독립 PER 면 anchored. 여러 PER 에 대한 표를 같이 둔다.
    vm.per_source = PER_SOURCE.get(tail.per_source, tail.per_source)
    if p0 and tail.per:
        vm.implied_status = "anchored" if vm.per_source == "user" else "assumption_dependent"
        row = implied_for_per(m, tail, p0, tail.per)
        vm.implied_eps, vm.implied_eps_vs_consensus_pct = row["implied_eps"], row["vs_consensus_pct"]
        vm.implied_path_pct, vm.implied_path_label = row["path_pct"], row["path_label"]
        vm.implied_table = implied_table(m, tail, p0, crit)
    # 설계 v6 §11.1: 회계 기준 호환성 — not_comparable 이면 값은 남기되 '확정' 으로 표시하지 않는다 (valuation_line · 검증 · 신뢰도가 읽는다)
    if tail.basis_status == "not_comparable":
        warnings.insert(0, "EPS 기준 불일치 — 정규화된 증권사 0곳: 가격은 참고값 (" + "; ".join(tail.basis_notes)[:160] + ")")
    elif tail.basis_status == "mixed":
        warnings.append("EPS 정규화 1곳뿐 — 컨센이 아니라 한 증권사 값 (" + "; ".join(tail.basis_notes)[:120] + ")")
    vm.inputs, vm.missing, vm.warnings = inputs, missing, warnings
    vm.status = "계산됨" if vm.price_per is not None else "미계산"
    vm.method = "price_per = EPS(basis) × PER ; price_normalized = mean(EPS 2026E~2028E) × PER ; price_sotp = (Σ seg OP×(1−t)×multiple×stake + net_cash)/N ; implied_eps = P0/PER"
    return vm


def basis_summary(fin: dict[str, Any]) -> str:
    """회계 기준 검사 결과 한 줄 (설계 v6 §11.1) — meta.draft.financials.basis 에서."""
    bc = fin.get("basis") or {}
    if not bc:
        return "검사 없음 (옛 맵 — 증권사 EPS 평균, NP/EPS 유도 주식 수)"
    st = bc.get("status")
    label = {"comparable": "비교 가능", "mixed": "정규화 1곳뿐 (참고)", "not_comparable": "기준 불일치 — 가격은 참고값", "unavailable": "발행주식수 없음 — 미정규화"}.get(st, st)
    e = bc.get("eps") or {}
    parts = [f"{label}"]
    if bc.get("shares_std"):
        parts.append(f"표준 주식 수 {bc['shares_std'] / 1e3:,.0f}천주 ({bc.get('shares_note') or ''})")
    if e.get("n"):
        parts.append(f"정규화 EPS {e['mean']:,.0f} ({e['n']}곳" + (f", 비율 환산 {len(e.get('approx') or {})}" if e.get("approx") else "") + ")")
    if e.get("excluded"):
        parts.append("제외 " + ", ".join(f"{k}({v})" for k, v in e["excluded"].items()))
    return " · ".join(parts)


def basis_table(fin: dict[str, Any]) -> str:
    """회계 기준 표 (설계 v6 §11.1 / v7 T1) — 증권사별 순이익 기준 · 발행주식수 · 주식 수 기준 · 증권사 EPS · 정규화 EPS · 비고. 콘솔·CLI 용 여러 줄."""
    bc = fin.get("basis") or {}
    if not bc:
        return "회계 기준 검사 없음 — 옛 맵 (리포트로 다시 만들면 생긴다)"
    e = bc.get("eps") or {}
    year = bc.get("year") or fin.get("year") or ""
    rows = [f"회계 기준 ({year}) — " + basis_summary(fin)]
    rows.append(f"  {'증권사':<10} {'순이익':<5} {'발행주식수(천주)':>14} {'주식 수 기준':<12} {'EPS(표)':>9} {'EPS 정규화':>10}  비고")
    per = bc.get("per_broker") or {}
    for key, b in per.items():
        rep = (e.get("reported") or {}).get(key)
        std = (e.get("by_broker") or {}).get(key)
        note = (e.get("approx") or {}).get(key) or (e.get("excluded") or {}).get(key) or (b.get("note") or "")
        sb = b.get("share_basis") or "불명"
        if b.get("share_basis_source") == "inferred":
            sb += "?"
        sh = f"{b['shares_header'] / 1e3:,.0f}" if b.get("shares_header") else "—"
        rows.append(f"  {key[:10]:<10} {(b.get('np_basis') or '—'):<5} {sh:>14} {sb:<12} {(f'{rep:,.0f}' if rep else '—'):>9} {(f'{std:,.0f}' if std else '제외'):>10}  {note}")
    if e.get("n"):
        rep_vals = list((e.get("reported") or {}).values())
        rows.append(f"  → 모델이 쓰는 컨센 EPS {e['mean']:,.0f} ({e['n']}곳)" + (f" · 표의 단순 평균 {sum(rep_vals) / len(rep_vals):,.0f}" if rep_vals else "")
                    + f" · 표준 주식 수 {bc['shares_std'] / 1e3:,.0f}천주 ({bc.get('shares_note') or ''})")
    for n in bc.get("notes") or []:
        rows.append("  · " + n)
    rows.append("  ('?' = 다른 증권사와 비교해 추정한 기준 · '전체×비율' = 전체 순이익에 같은 리포트 묶음에서 측정한 지배 비율을 곱한 값)")
    return "\n".join(rows)


def valuation_line(vm: ValuationModel | None, p0: float | None = None) -> str:
    """A3.3 한 줄 (값 칸)."""
    if vm is None:
        return "평가 모델 없음"
    if vm.status != "계산됨":
        return "평가 모델 미계산 — 필요: " + "; ".join(vm.missing)[:200]
    per_tag = "기본값" if vm.per_basis == "default" else "기준"
    head = "⚠ 기준 불일치 — 참고값: " if vm.basis_status == "not_comparable" else ""
    parts = [f"{head}PER {vm.price_per:,.0f}원 ({EPS_BASIS_LABEL.get(vm.eps_basis, vm.eps_basis)} EPS {vm.eps:,.0f} × {vm.per:.2f} {per_tag})"]
    if p0 and vm.price_per:
        parts[0] += f" {vm.price_per / p0 - 1:+.0%}"
    if vm.price_normalized is not None and vm.eps_basis != "normalized":
        parts.append(f"정상화 {vm.price_normalized:,.0f}원 ({vm.normalized_years[0][:4] if vm.normalized_years else ''}E~{vm.normalized_years[-1][:4] if vm.normalized_years else ''}E EPS {vm.eps_normalized:,.0f})")
    if vm.price_my_estimate is not None and vm.eps_basis != "my_estimate":
        parts.append(f"내 추정 {vm.price_my_estimate:,.0f}원")
    if vm.price_sotp is not None:
        parts.append(f"SOTP {vm.price_sotp:,.0f}원" + (f" (PER 대비 {vm.gap_pct:+.0f}%)" if vm.gap_pct is not None else ""))
    else:
        parts.append("SOTP 미계산 (멀티플 입력)")
    if vm.implied_eps is not None:
        parts.append(implied_line(vm, p0))
    if vm.per_basis == "default" and vm.price_per is not None and vm.eps_basis == "consensus":
        parts.append("Base = 목표가 중앙값 재현 (순환 — 내 PER 입력 전 참고)")
    return " · ".join(parts)


def valuation_table(vm: ValuationModel | None, p0: float | None = None) -> str:
    """CLI 용 여러 줄."""
    if vm is None:
        return "평가 모델 없음"
    rows = [valuation_line(vm, p0)]
    for i in vm.inputs:
        v = "없음" if i.value is None else (f"{i.value:,.4g}" if abs(i.value) < 1e6 else f"{i.value:,.0f}")
        rows.append(f"  {i.name:<10} {v:>16} {i.unit or '':<4} {i.source:<9} {i.period or '':<6} {i.note or ''}")
    for p in vm.sotp:
        rows.append(f"  SOTP {p.segment:<10} OP {('—' if p.op is None else f'{p.op / 1e4:,.1f}조'):>10} × (1−t) × {p.multiple if p.multiple is not None else '—'} = "
                    f"{('—' if p.value is None else f'{p.value / 1e4:,.1f}조'):>10} ({p.source}, {p.n}곳)")
    if vm.sotp and vm.sotp_other_op is not None:
        rows.append(f"  SOTP 기타(전사 OP − 사업부 합) {vm.sotp_other_op / 1e4:+,.1f}조 — 가치에 넣지 않음")
    if vm.sotp and vm.net_cash is not None:
        rows.append(f"  SOTP 순현금 {vm.net_cash / 1e4:,.1f}조 ({vm.net_cash_year})")
    rows.extend(implied_table_lines(vm, p0))
    for w in vm.warnings:
        rows.append(f"  ⚠ {w}")
    for x in vm.missing:
        rows.append(f"  미계산 — 필요: {x}")
    rows.append(f"  식: {vm.method}")
    return "\n".join(rows)


def implied_table_lines(vm: ValuationModel, p0: float | None = None) -> list[str]:
    """역산 표 (설계 v6 §11.2 ③) — PER 마다 현재가를 설명하는 EPS·컨센 대비·가격 드라이버 변화. 모든 줄이 '다른 변수 고정' 의 수학적 해다."""
    if not vm.implied_table:
        return []
    rows = [f"  역산 표 — 현재가{(' ' + format(p0, ',.0f')) if p0 else ''} 를 설명하는 (PER, EPS, 가격 드라이버) 조합 · 다른 변수 고정 · "
            + ("가정 의존 (PER 에 독립 근거 없음)" if vm.implied_status == "assumption_dependent" else "사용자 PER 기준")]
    rows.append(f"    {'적용 PER':>8} {'EPS':>9} {'vs 컨센':>8} {'필요 OP(조)':>11} {'드라이버 변화':>10}  PER 출처")
    for r in vm.implied_table:
        op = f"{r['op_required'] / 1e4:,.0f}" if r.get("op_required") is not None else "-"
        path = f"{r['path_pct']:+.0f}%" if r.get("path_pct") is not None else "-"
        vs = f"{r['vs_consensus_pct']:+.0f}%" if r.get("vs_consensus_pct") is not None else "-"
        rows.append(f"    {r['per']:>8.2f} {r['implied_eps']:>9,.0f} {vs:>8} {op:>11} {path:>10}  {r.get('label') or r.get('source') or ''}")
    lab = next((r.get("path_label") for r in vm.implied_table if r.get("path_label")), None)
    if lab:
        rows.append(f"    (드라이버 변화 = {lab} 를 컨센 대비 %로 — 전가율 k=1.0, 그 밖은 컨센 고정. 시장이 그렇게 본다는 뜻이 아니라 그 조합이 현재가를 설명한다는 뜻)")
    return rows


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:12]


def write_valuation(m: MindMap, *, today: date | None = None) -> ValuationModel:
    """A3.3 값·meta.draft.valuation 을 갱신하고 valuation_log 에 (입력 해시가 바뀐 때만) 한 줄 남긴다."""
    today = today or date.today()
    ensure_valuation_nodes(m)
    vm = compute_valuation(m, today=today)
    draft = m.meta.setdefault("draft", {})
    draft["valuation"] = vm.model_dump(mode="json")
    if "A3.3" in m:
        n = m["A3.3"]
        n.value = valuation_line(vm, getattr(m, "current_price", None))
        n.as_of = today
    key = _hash({"inputs": [(i.name, i.value, i.source) for i in vm.inputs], "basis": vm.eps_basis, "mult": sorted((p.segment, p.multiple) for p in vm.sotp)})
    log = draft.setdefault("valuation_log", [])
    if not log or log[-1].get("hash") != key:
        log.append({"date": today.isoformat(), "hash": key, "price_per": vm.price_per, "price_sotp": vm.price_sotp, "price_normalized": vm.price_normalized,
                    "eps_basis": vm.eps_basis, "per": vm.per, "status": vm.status})
        del log[:-30]
    return vm


__all__ = ["basis_summary", "basis_table", "implied_for_per", "implied_table", "implied_line", "implied_table_lines", "PER_SOURCE_LABEL", "Criteria", "Tail", "common_tail", "compute_valuation", "write_valuation", "valuation_line", "valuation_table", "ensure_valuation_nodes",
           "eps_for_basis", "normalized_eps", "my_estimate_eps", "segment_values", "drop_contained", "price_driver_segments", "year_cell",
           "EPS_BASES", "EPS_BASIS_LABEL", "DIVIDEND_MODES", "VALUATION_NODES", "SEGMENT_CONTAINS", "SNAP_METRICS"]
