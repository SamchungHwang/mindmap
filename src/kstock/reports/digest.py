"""여러 리포트의 사실을 한 종목으로 합친다 — ReportDigest.

리포트 한 건은 증권사 한 곳의 뷰다. 여러 건을 모으면 컨센서스가 된다: 목표가 분포(평균·중앙·최소·최대), 의견 분포,
직전 목표가 대비 리비전 방향, 연도별 추정치(평균). 문장은 여러 리포트에 거듭 나오는 것을 위로 올린다.
금액은 **억원** 으로 통일한다 (표 단위가 십억원이면 ×10).
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from ..models import Source
from .extract import KIND_WORDS, METRIC_LABEL, RATIO_METRICS, ReportFacts, Sentence

UNIT_TO_EOK = {"억원": 1.0, "십억원": 10.0, "조원": 10000.0, "백만원": 0.01, "천원": 0.00001, "bn": 10.0, "mn": 0.01}
AMOUNT_METRICS = {"revenue", "op", "op_adj", "np", "np_ctrl", "np_total", "ebitda", "net_debt", "net_cash", "fcf", "capex", "ocf"}   # 설계 v6 S1: 현금흐름·순차입금 행도 금액(억원)

# ---- 설계 v6 §11.1 (v7 W0): 회계 기준 — 순이익 기준(지배/전체) 과 주식 수 기준(보통주 / 보통주+우선주 / 자기주식 제외) 을 증권사별로 분류하고,
# EPS 를 "지배 순이익 ÷ 공시 보통주" 로 정규화한다. 기준이 다른 EPS 는 평균하지 않는다.
SHARE_BASIS_COMMON = "보통주"
SHARE_BASIS_WITH_PREF = "보통주+우선주"
SHARE_BASIS_EX_TREASURY = "자기주식 제외"
NP_BASIS_STD = "지배"


def unit_factor(f: ReportFacts) -> float | None:
    """표 금액 → 억원 배수. 단위를 못 읽었는데 추정치가 있으면 십억원(국내 리포트 기본) 으로 가정한다."""
    fac = UNIT_TO_EOK.get(f.unit or "", None)
    if fac is None and f.estimates:
        return 10.0
    return fac


@dataclass
class BrokerBasis:
    """증권사 한 곳의 회계 기준 — EPS 정규화의 재료."""

    broker: str
    np_basis: str | None = None              # 지배 | 전체 | None (표에 없음)
    shares_header: float | None = None       # 리포트 머리 발행주식수 (주)
    share_basis: str | None = None           # 보통주 | 보통주+우선주 | 자기주식 제외 | None (불명)
    share_basis_source: str | None = None    # label (머리 문구) | inferred (다른 증권사와 비교) | None
    note: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"broker": self.broker, "np_basis": self.np_basis, "shares_header": self.shares_header,
                "share_basis": self.share_basis, "share_basis_source": self.share_basis_source, "note": self.note}


@dataclass
class EpsStd:
    """한 연도의 정규화 EPS — EPS_i^std = NP_i(지배) × 1e8 ÷ shares_std. 비교 불가 증권사는 평균에서 뺀다."""

    year: str
    shares_std: float | None
    shares_source: str
    by_broker: dict[str, float] = field(default_factory=dict)        # 정규화 EPS (원)
    reported: dict[str, float] = field(default_factory=dict)         # 증권사가 적은 EPS (원)
    np_by_broker: dict[str, float] = field(default_factory=dict)     # 지배 순이익 (억원)
    excluded: dict[str, str] = field(default_factory=dict)           # 증권사 → 비교 불가 사유
    approx: dict[str, str] = field(default_factory=dict)             # 증권사 → 전체 × 측정 지배 비율로 맞춘 경우의 메모

    @property
    def n(self) -> int:
        return len(self.by_broker)

    @property
    def mean(self) -> float | None:
        return statistics.fmean(self.by_broker.values()) if self.by_broker else None

    @property
    def median(self) -> float | None:
        return statistics.median(self.by_broker.values()) if self.by_broker else None

    @property
    def status(self) -> str:
        """comparable(정규화 2곳 이상) · mixed(1곳) · not_comparable(0곳 — 기준이 안 맞아서) · unavailable(표준 주식 수가 없어 정규화 자체가 불가 — 예전 규칙)."""
        if self.shares_std is None:
            return "unavailable"
        return "comparable" if self.n >= 2 else ("mixed" if self.n == 1 else "not_comparable")

    def to_dict(self) -> dict[str, object]:
        return {"year": self.year, "status": self.status, "mean": self.mean, "median": self.median, "n": self.n,
                "by_broker": dict(self.by_broker), "reported": dict(self.reported), "np_by_broker": dict(self.np_by_broker),
                "excluded": dict(self.excluded), "approx": dict(self.approx), "shares_std": self.shares_std, "shares_source": self.shares_source}


@dataclass
class TargetPoint:
    date: date | None
    broker: str | None
    tp: float
    prev_tp: float | None
    opinion: str | None
    change: str | None
    path: Path

    @property
    def move(self) -> str | None:
        if self.prev_tp is None:
            return self.change
        if self.tp > self.prev_tp:
            return "상향"
        if self.tp < self.prev_tp:
            return "하향"
        return "유지"


@dataclass
class EstimateCell:
    values: list[float] = field(default_factory=list)
    by_broker: dict[str, float] = field(default_factory=dict)   # 증권사 → 값 (분산·이견 표시용)
    sources: dict[str, tuple[str, str, str]] = field(default_factory=dict)   # 증권사 → (리포트 파일, 날짜, "대표"|"보강")  (설계 v4 V4)

    @property
    def n_backfilled(self) -> int:
        return sum(1 for v in self.sources.values() if v[2] == "보강")

    @property
    def mean(self) -> float:
        return statistics.fmean(self.values)

    @property
    def n(self) -> int:
        return len(self.values)

    @property
    def median(self) -> float:
        return statistics.median(self.values)

    @property
    def spread(self) -> dict[str, float | int | dict[str, float]]:
        """min/max/median/mean/std/rel/abs/n/values — 증권사 간 이견의 크기."""
        xs = self.values
        med = statistics.median(xs)
        mn, mx = min(xs), max(xs)
        return {"min": mn, "max": mx, "median": med, "mean": statistics.fmean(xs),
                "std": statistics.pstdev(xs) if len(xs) > 1 else 0.0,
                "rel": (mx - mn) / abs(med) if med else 0.0, "abs": mx - mn, "n": len(xs), "values": dict(self.by_broker)}


MAX_AGE_DAYS = 45          # 기준일(가장 최근 리포트) 보다 이보다 오래된 리포트는 집계에서 뺀다 (참고로만). None 이면 안 뺀다
EARNINGS_LAG_DAYS = 8      # 분기 말 + 8일 ≈ 잠정실적 발표 (실제 발표일이 없을 때의 추정)


@dataclass
class ReportDigest:
    ticker: str | None
    name: str | None
    as_of: date
    reports: list[ReportFacts]
    targets: list[TargetPoint]
    estimates: dict[str, dict[str, EstimateCell]]      # metric → year label → cell (억원 / 원 / 배 / %)
    quarterly: dict[str, dict[str, EstimateCell]]
    unit_note: str
    sentences: dict[str, list[Sentence]]                # kind → 점수순
    warnings: list[str] = field(default_factory=list)
    excluded: dict[str, str] = field(default_factory=dict)        # path.name → 집계 제외 사유 (같은 증권사 이전 건 / 45일 초과)
    pre_earnings: dict[str, str] = field(default_factory=dict)    # path.name → "3Q26 실적 전" (최신 실적 발표 전에 쓴 리포트)
    earnings_date: date | None = None                             # 집계에 쓴 최신 실적 발표일 (실제 또는 추정)
    earnings_date_source: str = ""
    db_check: object | None = None                                # reports.dbcheck.DbCheck (DB 교차 확인 결과)
    # 설계 v5 R4: 사업부 셀 — (segment, metric, col) → 증권사별 값 (금액 억원). 열 라벨(…P/…E) 은 그대로 둔다
    segments: dict[tuple[str, str, str], EstimateCell] = field(default_factory=dict)

    # ------------------------------------------------------------------ 만들기
    @classmethod
    def build(cls, facts: list[ReportFacts], *, ticker: str | None = None, name: str | None = None,
              today: date | None = None, latest_per_broker: bool = True, max_age_days: int | None = MAX_AGE_DAYS,
              earnings_date: date | None = None) -> ReportDigest:
        """facts 전부를 보관하되 **집계(목표가·의견·추정치)** 에는 증권사별 최신 1건, 기준일 45일 이내만 쓴다 (설계 v3 §2.6).
        earnings_date: 최신 실적 발표일 (리서치 DB/DART 에서 알면 넘긴다; 없으면 분기 표에서 추정)."""
        warnings: list[str] = []
        for f in facts:
            warnings += f.warnings
        excluded: dict[str, str] = {}
        if latest_per_broker:
            by_broker: dict[str, ReportFacts] = {}
            for f in facts:
                if not f.broker:
                    continue
                cur = by_broker.get(f.broker)
                if cur is None or (f.date or date.min) > (cur.date or date.min):
                    by_broker[f.broker] = f
            for f in facts:
                if f.broker and by_broker[f.broker] is not f:
                    excluded[f.path.name] = f"같은 증권사({f.broker}) 의 더 새 리포트 {by_broker[f.broker].date} 가 있어 리비전 이력으로만"
        dates_all = [f.date for f in facts if f.date]
        if max_age_days and dates_all:
            cutoff = max(dates_all) - timedelta(days=max_age_days)
            for f in facts:
                if f.date and f.date < cutoff and f.path.name not in excluded:
                    excluded[f.path.name] = f"기준일 {max(dates_all)} 보다 {(max(dates_all) - f.date).days}일 오래됨 (>{max_age_days}일) — 참고로만"
        agg = [f for f in facts if f.path.name not in excluded]
        # 종목: 지정값 > 리포트 다수결
        codes = Counter(f.ticker for f in facts if f.ticker)
        if ticker is None and codes:
            ticker = codes.most_common(1)[0][0]
        if codes and len(codes) > 1:
            warnings.append("리포트의 종목코드가 하나가 아니다: " + ", ".join(f"{c}×{n}" for c, n in codes.most_common()))
        if ticker and codes and ticker not in codes:
            warnings.append(f"지정 종목 {ticker} 가 리포트 본문의 코드 {list(codes)} 와 다르다 — 종목을 확인하라")
        names = Counter(f.name for f in facts if f.name and (f.ticker == ticker or not f.ticker))
        if name is None and names:
            name = names.most_common(1)[0][0]
        dates = [f.date for f in facts if f.date]
        as_of = max(dates) if dates else (today or date.today())
        if dates and (max(dates) - min(dates)).days > 90:
            warnings.append(f"리포트 날짜가 {min(dates)} ~ {max(dates)} 로 {(max(dates) - min(dates)).days}일 벌어져 있다 — 오래된 목표가가 컨센서스에 섞인다")
        # 목표가 — 집계 대상만
        targets = [TargetPoint(f.date, f.broker, f.target_price, f.prev_target_price, f.opinion, f.tp_change, f.path)
                   for f in agg if f.target_price]
        # 회계 기준 (순이익 지배/전체 가 섞이면 다수 기준만 평균)
        np_basis = Counter(f.basis.get("np") for f in agg if f.basis.get("np"))
        np_major = np_basis.most_common(1)[0][0] if np_basis else None
        if len(np_basis) > 1:
            warnings.append("순이익 기준이 섞여 있다: " + ", ".join(f"{b} {n}" for b, n in np_basis.most_common())
                            + f" — {np_major} 기준만 평균한다")
        # 추정치 (단위 통일) — 집계 대상(증권사별 대표 문서) 이 먼저 채우고, 대표 문서에 없는 (지표, 기간) 셀은
        # 같은 증권사의 이전 리포트(45일 안) 로 보강한다 (설계 v4 V4: 셀 단위 최신 유효 추정치). 열 라벨(…A/…P/…E) 이 다르면 다른 셀이다.
        estimates: dict[str, dict[str, EstimateCell]] = defaultdict(lambda: defaultdict(EstimateCell))
        quarterly: dict[str, dict[str, EstimateCell]] = defaultdict(lambda: defaultdict(EstimateCell))
        unit_notes: Counter[str] = Counter()
        cutoff_bf = (max(dates_all) - timedelta(days=max_age_days)) if (max_age_days and dates_all) else None
        backfill_pool = sorted((f for f in facts if f.path.name in excluded and f.broker and (f.date or date.min) >= (cutoff_bf or date.min)
                                and "리비전 이력" in excluded[f.path.name]),
                               key=lambda f: f.date or date.min, reverse=True)
        n_backfill = 0

        _factor = unit_factor
        # 설계 v6 §11.1 B2: 단위를 못 읽어 십억원으로 가정한 리포트는 다른 증권사(단위를 읽은) 의 같은 셀과 비교해 ×1000 어긋나면 보정한다
        unit_fix: dict[str, float] = {}
        known = [f for f in agg if UNIT_TO_EOK.get(f.unit or "", None) is not None and f.estimates]
        for f in agg:
            if UNIT_TO_EOK.get(f.unit or "", None) is not None or not f.estimates or not known:
                continue
            ratios: list[float] = []
            for metric in ("revenue", "op"):
                for col, v in (f.estimates.get(metric) or {}).items():
                    peers = [g.estimates[metric][col] * (unit_factor(g) or 1.0) for g in known
                             if metric in g.estimates and col in g.estimates[metric] and g.estimates[metric][col]]
                    if peers and v:
                        ratios.append((v * 10.0) / statistics.median(peers))
            if ratios:
                r = statistics.median(ratios)
                if 300 <= r <= 3000:
                    unit_fix[f.path.name] = 0.001
                elif 1 / 3000 <= r <= 1 / 300:
                    unit_fix[f.path.name] = 1000.0

        for f in agg:
            factor = _factor(f)
            if f.path.name in unit_fix and factor:
                factor *= unit_fix[f.path.name]
            if UNIT_TO_EOK.get(f.unit or "", None) is None and f.estimates:
                if f.path.name in unit_fix:
                    unit = "억원" if unit_fix[f.path.name] < 1 else "조원"
                    warnings.append(f"{f.path.name}: 추정치 표 단위를 못 읽어 십억원으로 가정했으나 다른 증권사와 ×1000 어긋나 {unit} 으로 보정")
                    unit_notes[f"보정:{unit}"] += 1
                else:
                    warnings.append(f"{f.path.name}: 추정치 표 단위를 못 읽어 십억원으로 가정")   # 단위를 못 읽으면 십억원 (국내 리포트 기본)
                    unit_notes["가정:십억원"] += 1
            elif f.unit:
                unit_notes[f.unit] += 1
            for table, dst in ((f.estimates, estimates), (f.quarterly, quarterly)):
                for metric, cols in table.items():
                    if metric == "np" and np_major and f.basis.get("np") and f.basis.get("np") != np_major:
                        continue
                    for col, v in cols.items():
                        val = v * factor if (metric in AMOUNT_METRICS and factor) else v
                        cell = dst[metric][col]
                        cell.values.append(val)
                        if f.broker:
                            cell.by_broker[f.broker] = val
                            cell.sources[f.broker] = (f.path.name, str(f.date or ""), "대표")
        for f in backfill_pool:   # 최신 것부터 — 같은 증권사의 빈 셀만 채운다
            factor = _factor(f)
            if f.path.name in unit_fix and factor:
                factor *= unit_fix[f.path.name]
            for table, dst in ((f.estimates, estimates), (f.quarterly, quarterly)):
                for metric, cols in table.items():
                    if metric == "np" and np_major and f.basis.get("np") and f.basis.get("np") != np_major:
                        continue
                    for col, v in cols.items():
                        cell = dst[metric][col]
                        if f.broker in cell.by_broker:
                            continue
                        val = v * factor if (metric in AMOUNT_METRICS and factor) else v
                        cell.values.append(val)
                        cell.by_broker[f.broker] = val
                        cell.sources[f.broker] = (f.path.name, str(f.date or ""), "보강")
                        n_backfill += 1
        if n_backfill:
            warnings.append(f"추정치 셀 {n_backfill}개는 최신 리포트에 없어 같은 증권사 이전 리포트에서 보강 (표의 * 표시)")
        # 사업부 셀 (설계 v5 R4) — 대표 문서만. 값은 extract 가 이미 억원으로 맞췄다
        segments: dict[tuple[str, str, str], EstimateCell] = defaultdict(EstimateCell)
        for f in agg:
            for seg, mets in (f.segments or {}).items():
                for metric, cols in mets.items():
                    for col, v in cols.items():
                        cell = segments[(seg, metric, col)]
                        cell.values.append(v)
                        if f.broker:
                            cell.by_broker[f.broker] = v
                            cell.sources[f.broker] = (f.path.name, str(f.date or ""), "대표")
        # 실적 전/후 — 최신 실적 분기의 발표일(실제 또는 분기 말 +8일 추정) 보다 앞선 리포트
        pre: dict[str, str] = {}
        ed_src = ""
        if earnings_date is None:
            q = cls._latest_actual_quarter_label(facts)
            if q:
                qn, yy = int(q[0]), 2000 + int(q[2:4])
                qend = date(yy, qn * 3, 1)
                qend = date(yy + (1 if qn == 4 else 0), 1 if qn == 4 else qn * 3 + 1, 1) - timedelta(days=1)
                earnings_date = qend + timedelta(days=EARNINGS_LAG_DAYS)
                ed_src = f"{q} 분기 말 +{EARNINGS_LAG_DAYS}일 추정"
        else:
            ed_src = "실제 발표일"
        if earnings_date:
            for f in facts:
                if f.date and f.date < earnings_date:
                    pre[f.path.name] = f"{earnings_date} 실적 발표 전 리포트"
        # 문장 — 여러 리포트에 거듭 나오면 가산
        merged: dict[str, dict[str, Sentence]] = {k: {} for k in KIND_WORDS}
        for f in facts:
            for s in f.sentences:
                key = s.key()
                cur = merged[s.kind].get(key)
                if cur is None:
                    merged[s.kind][key] = Sentence(s.text, s.kind, s.score)
                else:
                    cur.score += s.score * 0.5 + 1.0
        sentences = {k: sorted(v.values(), key=lambda s: -s.score) for k, v in merged.items()}
        unit_note = ", ".join(f"{u}×{n}" for u, n in unit_notes.most_common()) or "미상"
        if excluded:
            warnings.append(f"집계 제외 {len(excluded)}건: " + "; ".join(f"{k} — {v}" for k, v in excluded.items()))
        return cls(ticker=ticker, name=name, as_of=as_of, reports=facts, targets=targets,
                   estimates={m: dict(c) for m, c in estimates.items()}, quarterly={m: dict(c) for m, c in quarterly.items()},
                   unit_note=unit_note, sentences=sentences, warnings=warnings, excluded=excluded, pre_earnings=pre,
                   earnings_date=earnings_date, earnings_date_source=ed_src, segments=dict(segments))

    @staticmethod
    def _latest_actual_quarter_label(facts: list[ReportFacts]) -> str | None:
        labs = {c for f in facts for cols in f.quarterly.values() for c in cols if c.endswith("A")}
        if not labs:
            return None
        return sorted(labs, key=lambda c: (c[2:4], c[0]))[-1]

    # ------------------------------------------------------------------ 집계 대상
    @property
    def aggregated(self) -> list[ReportFacts]:
        """집계(목표가·의견·추정치) 에 쓴 리포트 — 증권사별 최신 1건, 기준일 45일 이내."""
        return [f for f in self.reports if f.path.name not in self.excluded]

    def spread(self, metric: str, col: str) -> dict | None:
        c = self.cell(metric, col)
        return c.spread if c and c.n else None

    # ------------------------------------------------------------------ 컨센서스
    # ---- 설계 v6 S1: 발행주식수 (리포트 머리) — 증권사별 값과 중앙값. 우선주 포함 여부가 다를 수 있어 중앙값을 기본으로 쓴다
    @property
    def shares_by_broker(self) -> dict[str, float]:
        return {f.broker or f.path.name: f.shares_out for f in self.aggregated if f.shares_out}

    @property
    def shares_out(self) -> float | None:
        xs = sorted(self.shares_by_broker.values())
        if not xs:
            return None
        return xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2

    # ---- 설계 v6 §11.1 (v7 W0): 회계 기준 분류 · 표준 주식 수 · 정규화 EPS
    def broker_bases(self) -> dict[str, BrokerBasis]:
        """증권사별 순이익 기준(표 행 라벨) 과 주식 수 기준(머리 문구, 없으면 다른 증권사와 비교해 추정).
        추정 규칙: 가장 작은 값 대비 +6~30% 는 '보통주+우선주', 가장 큰 값 대비 −1.5~−10% 는 '자기주식 제외', 그 밖은 '보통주'. 30% 넘게 벌어지면 불명."""
        out: dict[str, BrokerBasis] = {}
        for f in self.aggregated:
            key = f.broker or f.path.name
            b = BrokerBasis(broker=key, np_basis=f.basis.get("np"), shares_header=f.shares_out)
            note = (f.shares_note or "")
            if f.shares_out:
                if "우선주" in note and "포함" in note:
                    b.share_basis, b.share_basis_source = SHARE_BASIS_WITH_PREF, "label"
                elif "보통주" in note:
                    b.share_basis, b.share_basis_source = SHARE_BASIS_COMMON, "label"
                elif "자기주식" in note or "자사주" in note:
                    b.share_basis, b.share_basis_source = SHARE_BASIS_EX_TREASURY, "label"
            out[key] = b
        vals = [b.shares_header for b in out.values() if b.shares_header]
        if vals:
            lo, hi = min(vals), max(vals)
            for b in out.values():
                if not b.shares_header or b.share_basis:
                    continue
                v = b.shares_header
                if hi / lo > 1.30:
                    b.note = f"증권사 간 발행주식수가 {hi / lo - 1:+.0%} 벌어져 기준을 추정할 수 없다"
                    continue
                r_lo, r_hi = v / lo, v / hi
                if r_lo >= 1.06:
                    b.share_basis, b.share_basis_source = SHARE_BASIS_WITH_PREF, "inferred"
                    b.note = f"최소값 대비 {r_lo - 1:+.1%} — 우선주 포함으로 추정"
                elif r_hi <= 0.985 and hi / lo < 1.06:
                    b.share_basis, b.share_basis_source = SHARE_BASIS_EX_TREASURY, "inferred"
                    b.note = f"최대값 대비 {r_hi - 1:+.1%} — 자기주식 제외로 추정"
                else:
                    b.share_basis, b.share_basis_source = SHARE_BASIS_COMMON, "inferred"
        return out

    def shares_std(self, user: float | None = None) -> tuple[float | None, str, str]:
        """표준 주식 수 = 공시 보통주 (사용자 결정 ⑦). 돌려주는 것: (값, 출처 user|header|header_median|missing, 메모)."""
        if user:
            return float(user), "user", "A3.8 평가 기준"
        bases = self.broker_bases()
        common = sorted(b.shares_header for b in bases.values() if b.shares_header and b.share_basis == SHARE_BASIS_COMMON)
        if common:
            med = common[len(common) // 2] if len(common) % 2 else (common[len(common) // 2 - 1] + common[len(common) // 2]) / 2
            srcs = Counter(b.share_basis_source for b in bases.values() if b.share_basis == SHARE_BASIS_COMMON)
            return med, "header", f"공시 보통주 {len(common)}곳 중앙값 ({', '.join(f'{k} {n}' for k, n in srcs.items())})"
        if self.shares_out:
            return self.shares_out, "header_median", "주식 수 기준 불명 — 리포트 머리 중앙값"
        return None, "missing", "발행주식수 없음"

    def control_ratio(self, year: str) -> tuple[float | None, int, str | None]:
        """지배 비율 r = 지배 순이익 ÷ 전체 순이익 — 같은 표에 두 행을 다 적은 증권사들의 중앙값 (측정값, 추정이 아니다).
        그 연도에 없으면 최근 실적 연도의 것. 돌려주는 것: (r, 곳 수, 어느 연도)."""
        def _at(yr: str) -> list[float]:
            out: list[float] = []
            for f in self.aggregated:
                c, t = (f.estimates.get("np_ctrl") or {}).get(yr), (f.estimates.get("np_total") or {}).get(yr)
                if c and t and t > 0 and 0.3 <= c / t <= 1.02:
                    out.append(min(c / t, 1.0))
            return out
        xs = _at(year)
        used = year
        if len(xs) < 1:
            la = self.latest_actual_year("np") or self.latest_actual_year("op")
            if la:
                xs, used = _at(la), la
        if not xs:
            return None, 0, None
        return statistics.median(xs), len(xs), used

    def eps_std(self, year: str, *, shares: float | None = None) -> EpsStd:
        """연도 하나의 정규화 EPS — EPS_i^std = NP_i(지배) × 1e8 ÷ shares_std.
        지배 순이익은 ① 표의 지배 행(np_ctrl) ② 순이익 행의 기준이 지배이면 그 행 ③ 전체만 있으면 측정된 지배 비율(control_ratio ≥ 0.9) 을 곱한 값(표시 'ratio')
        — ③ 도 없으면 비교 불가로 평균에서 뺀다."""
        sv, ssrc, _ = self.shares_std(shares)
        e = EpsStd(year=year, shares_std=sv, shares_source=ssrc)
        bases = self.broker_bases()
        eps_cell = (self.estimates.get("eps") or {}).get(year)
        r, r_n, r_year = self.control_ratio(year)
        for f in self.aggregated:
            key = f.broker or f.path.name
            if eps_cell is not None and key in eps_cell.by_broker:
                e.reported[key] = eps_cell.by_broker[key]
            fac = unit_factor(f) or 1.0
            ctrl = (f.estimates.get("np_ctrl") or {}).get(year)
            total = (f.estimates.get("np_total") or {}).get(year)
            npv = (f.estimates.get("np") or {}).get(year)
            nb = bases.get(key)
            if ctrl is not None and (total is None or (total > 0 and 0.3 <= ctrl / total <= 1.05)):
                np_ctrl, how = ctrl * fac, "exact"
            elif npv is not None and nb is not None and nb.np_basis == NP_BASIS_STD:
                np_ctrl, how = npv * fac, "exact"
            elif (total is not None or npv is not None) and r is not None and r >= 0.9:
                np_ctrl, how = (total if total is not None else npv) * fac * r, "ratio"
            elif total is None and npv is None:
                if key in e.reported:
                    e.excluded[key] = "순이익 셀 없음"
                continue
            else:
                e.excluded[key] = "전체 순이익만 (지배 비율 측정값 없음)" if r is None else f"전체 순이익만 (지배 비율 {r:.2f} < 0.9)"
                continue
            if not sv:
                e.excluded[key] = "표준 주식 수 없음"
                continue
            e.np_by_broker[key] = np_ctrl
            e.by_broker[key] = np_ctrl * 1e8 / sv
            if how == "ratio":
                e.approx[key] = f"전체 × 지배 비율 {r:.3f} ({r_n}곳 측정, {r_year})"
        return e

    def basis_check(self, year: str | None = None, *, shares: float | None = None) -> dict[str, object]:
        """회계 기준 호환성 검사 (설계 v6 §11.1) — compute_valuation·compute_sensitivity 가 들어가기 전에 본다."""
        year = year or self.next_estimate_year("eps") or self.next_estimate_year("np")
        bases = self.broker_bases()
        sv, ssrc, snote = self.shares_std(shares)
        e = self.eps_std(year, shares=shares) if year else None
        notes: list[str] = []
        np_mix = Counter(b.np_basis or "불명" for b in bases.values())
        if len(np_mix) > 1:
            notes.append("순이익 기준 혼합: " + ", ".join(f"{b.broker} {b.np_basis or '불명'}" for b in bases.values()))
        sh_mix = Counter(b.share_basis or "불명" for b in bases.values() if b.shares_header)
        if len(sh_mix) > 1:
            notes.append("주식 수 기준 혼합: " + ", ".join(f"{b.broker} {b.share_basis or '불명'}" for b in bases.values() if b.shares_header))
        if e is not None and e.excluded:
            notes.append("EPS 비교 불가: " + ", ".join(f"{k} ({v})" for k, v in e.excluded.items()))
        return {"year": year, "status": e.status if e is not None else ("unavailable" if sv is None else "not_comparable"), "shares_std": sv, "shares_source": ssrc, "shares_note": snote,
                "per_broker": {k: b.to_dict() for k, b in bases.items()}, "eps": e.to_dict() if e is not None else None, "notes": notes}

    @property
    def tp_values(self) -> list[float]:
        return [t.tp for t in self.targets]

    @property
    def n_tp(self) -> int:
        return len(self.targets)

    @property
    def tp_mean(self) -> float | None:
        return statistics.fmean(self.tp_values) if self.targets else None

    @property
    def tp_median(self) -> float | None:
        return statistics.median(self.tp_values) if self.targets else None

    @property
    def tp_min(self) -> float | None:
        return min(self.tp_values) if self.targets else None

    @property
    def tp_max(self) -> float | None:
        return max(self.tp_values) if self.targets else None

    def tp_percentile(self, q: float) -> float | None:
        """분포의 q 분위 (0~1, 선형 보간)."""
        xs = sorted(self.tp_values)
        if not xs:
            return None
        if len(xs) == 1:
            return xs[0]
        pos = q * (len(xs) - 1)
        lo, hi = int(pos), min(int(pos) + 1, len(xs) - 1)
        return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)

    @property
    def opinion_dist(self) -> Counter[str]:
        return Counter((f.opinion or "미상") for f in self.aggregated)

    @property
    def revision(self) -> str | None:
        """직전 목표가 대비 상향/하향 건수로 방향. 정보가 없으면 None."""
        moves = Counter(t.move for t in self.targets if t.move)
        ups, downs = moves.get("상향", 0), moves.get("하향", 0)
        if not moves:
            return None
        if ups > downs:
            return "up"
        if downs > ups:
            return "down"
        return "flat"

    @property
    def price(self) -> tuple[float, date | None, ReportFacts] | None:
        """가장 최근 리포트의 현재주가."""
        cands = [f for f in self.reports if f.current_price]
        if not cands:
            return None
        f = max(cands, key=lambda f: (f.price_date or f.date or date.min))
        return f.current_price, f.price_date or f.date, f

    def _plausible_52w(self, xs: list[float]) -> list[float]:
        """'287 ,000' 이 '287' 로 읽히는 식의 사고를 거른다: 현재가의 1/5~5배, 현재가가 없으면 후보 중앙값의 1/5~5배."""
        xs = [x for x in xs if x and x >= 100]
        if not xs:
            return []
        p = self.price[0] if self.price else None
        ref = p if p else sorted(xs)[len(xs) // 2]
        return [x for x in xs if ref / 5 <= x <= ref * 5]

    @property
    def high_52w(self) -> float | None:
        xs = self._plausible_52w([f.high_52w for f in self.reports if f.high_52w])
        return max(xs) if xs else None

    @property
    def low_52w(self) -> float | None:
        xs = self._plausible_52w([f.low_52w for f in self.reports if f.low_52w])
        return min(xs) if xs else None

    @property
    def brokers(self) -> list[str]:
        return sorted({f.broker for f in self.aggregated if f.broker})

    # ------------------------------------------------------------------ 추정치
    def years(self, metric: str | None = None) -> list[str]:
        ys: set[str] = set()
        for m, cols in self.estimates.items():
            if metric is None or m == metric:
                ys |= set(cols)
        return sorted(ys)

    def cell(self, metric: str, year: str) -> EstimateCell | None:
        return self.estimates.get(metric, {}).get(year)

    # ------------------------------------------------------------------ 사업부 (설계 v5 R4)
    def segment_cell(self, segment: str, metric: str, col: str) -> EstimateCell | None:
        """사업부 셀. col 이 '2027E' 처럼 접미가 있으면 그 셀만, '3Q26' 처럼 접미가 없으면 같은 기간의 …P/…E/…A 셀을 **합쳐서**
        돌려준다 (증권사마다 잠정(P)/추정(E) 표기가 달라도 같은 분기다 — sources 에 접미를 남긴다)."""
        c = self.segments.get((segment, metric, col))
        if c is not None or col[-1:] in ("E", "P", "A", "F"):
            return c
        merged = EstimateCell()
        for (seg, met, k), cell in self.segments.items():
            if seg == segment and met == metric and k.rstrip("EPAF") == col:
                for b, v in cell.by_broker.items():
                    if b in merged.by_broker:
                        continue
                    merged.by_broker[b] = v
                    merged.values.append(v)
                    src = cell.sources.get(b)
                    merged.sources[b] = (src[0], src[1], f"{src[2]} {k}") if src else ("", "", k)
        return merged if merged.values else None

    def segment_value(self, segment: str, metric: str, col: str) -> float | None:
        c = self.segment_cell(segment, metric, col)
        return c.mean if c else None

    def segment_names(self) -> list[str]:
        return sorted({k[0] for k in self.segments})

    def value(self, metric: str, year: str) -> float | None:
        c = self.cell(metric, year)
        return c.mean if c else None

    def latest_actual_year(self, metric: str) -> str | None:
        ys = [y for y in self.years(metric) if y.endswith("A")]
        return ys[-1] if ys else None

    def estimate_years(self, metric: str) -> list[str]:
        return [y for y in self.years(metric) if y.endswith("E")]

    def next_estimate_year(self, metric: str) -> str | None:
        """기준일 다음 해의 E, 없으면 첫 E. (10월 리포트의 '2026E' 는 거의 끝난 해 — 2027E 가 베팅 대상)"""
        es = self.estimate_years(metric)
        if not es:
            return None
        target = f"{self.as_of.year + 1}E"
        if target in es:
            return target
        later = [y for y in es if int(y[:4]) > self.as_of.year]
        return later[0] if later else es[-1]

    def latest_actual_quarter(self, metric: str) -> tuple[str, float] | None:
        cols = self.quarterly.get(metric, {})
        acts = sorted((c for c in cols if c.endswith("A")), key=lambda c: (c[3:5], c[0]))
        if not acts:
            return None
        c = acts[-1]
        return c, cols[c].mean

    def next_estimate_quarter(self, metric: str) -> tuple[str, EstimateCell] | None:
        cols = self.quarterly.get(metric, {})
        ests = sorted((c for c in cols if c.endswith("E")), key=lambda c: (c[3:5], c[0]))
        return (ests[0], cols[ests[0]]) if ests else None

    # ------------------------------------------------------------------ 금액 단위 (억원 ↔ 조원)
    @property
    def amount_unit(self) -> tuple[str, float]:
        """금액 표시 단위. 매출·영업이익이 10조원(100,000억) 을 넘는 회사는 조원으로 읽는 편이 낫다. (단위, 억원 나누는 수)"""
        big = 0.0
        for metric in ("revenue", "op"):
            for cell in self.estimates.get(metric, {}).values():
                big = max(big, abs(cell.mean))
            for cell in self.quarterly.get(metric, {}).values():
                big = max(big, abs(cell.mean))
        return ("조원", 10000.0) if big >= 100_000 else ("억원", 1.0)

    def amt(self, metric: str, year: str) -> float | None:
        """표시 단위로 환산한 금액 (amount 지표만). 그 외 지표는 그대로."""
        v = self.value(metric, year)
        if v is None:
            return None
        return v / self.amount_unit[1] if metric in AMOUNT_METRICS else v

    def fmt_amt(self, metric: str, year: str) -> str:
        v = self.amt(metric, year)
        if v is None:
            return "-"
        unit, scale = self.amount_unit
        if metric in AMOUNT_METRICS:
            return (f"{v:,.1f}" if scale > 1 else f"{v:,.0f}") + unit
        return f"{v:,.1f}" if metric in RATIO_METRICS else f"{v:,.0f}"

    def metric_label(self, metric: str) -> str:
        unit = self.amount_unit[0] if metric in AMOUNT_METRICS else "원" if metric in ("eps", "bps", "dps") else "배" if metric in ("per", "pbr", "ev_ebitda") else "%" if metric in RATIO_METRICS else ""
        return f"{METRIC_LABEL.get(metric, metric)}({unit})" if unit else METRIC_LABEL.get(metric, metric)

    # ------------------------------------------------------------------ 문장
    def top(self, kind: str, n: int = 5) -> list[str]:
        return [s.text for s in self.sentences.get(kind, [])[:n]]

    # ------------------------------------------------------------------ 출처·요약
    def sources(self) -> list[Source]:
        out: list[Source] = []
        for f in self.reports:
            nm = f"{f.broker or '증권사 미상'} {f.date.isoformat() if f.date else ''} 리포트".strip()
            if f.title:
                nm += f" — {f.title[:40]}"
            out.append(Source(name=nm, url=str(f.path), retrieved_at=f.date))
        return out

    def source_all(self) -> Source:
        n = len(self.reports)
        brokers = ", ".join(self.brokers[:6]) + (" 외" if len(self.brokers) > 6 else "")
        return Source(name=f"증권사 리포트 {n}건 ({brokers})", url=str(self.reports[0].path.parent) if self.reports else "",
                      retrieved_at=self.as_of)

    def consensus_line(self) -> str:
        if not self.targets:
            return "목표주가: 리포트에서 읽지 못함"
        dist = ", ".join(f"{k} {v}" for k, v in self.opinion_dist.most_common())
        rev = {"up": "상향 우세", "down": "하향 우세", "flat": "유지"}.get(self.revision or "", "직전값 없음")
        return (f"목표주가 {self.n_tp}건 평균 {self.tp_mean:,.0f} · 중앙 {self.tp_median:,.0f} · {self.tp_min:,.0f}~{self.tp_max:,.0f}"
                f" · 의견 {dist} · 리비전 {rev}")

    def estimates_table(self) -> str:
        years = self.years()
        if not years:
            return "(추정치 표 없음)"
        head = "지표".ljust(14) + "".join(y.rjust(12) for y in years)
        rows = [head]
        any_bf = False
        for metric in ("revenue", "op", "op_adj", "np", "eps", "per", "pbr", "roe", "opm", "ev_ebitda"):
            cols = self.estimates.get(metric)
            if not cols:
                continue
            cells = []
            unit, scale = self.amount_unit
            for y in years:
                c = cols.get(y)
                if c is None:
                    cells.append("-".rjust(12))
                    continue
                mark = "*" if c.n_backfilled else ""
                any_bf = any_bf or bool(mark)
                if metric in AMOUNT_METRICS:
                    txt = f"{c.mean / scale:,.1f}" if scale > 1 else f"{c.mean:,.0f}"
                else:
                    txt = f"{c.mean:,.1f}" if metric in RATIO_METRICS else f"{c.mean:,.0f}"
                cells.append((txt + mark).rjust(12))
            rows.append(self.metric_label(metric).ljust(14) + "".join(cells))
        if any_bf:
            rows.append("  * 최신 리포트에 없어 같은 증권사 이전 리포트(45일 안) 값으로 보강한 셀 포함")
        rows.extend(self.basis_lines())
        return "\n".join(rows)

    def basis_lines(self) -> list[str]:
        """표 아래 한두 줄 — EPS 기준 통일 결과 (설계 v6 §11.1). 표의 EPS 행은 증권사가 적은 값의 평균이고, 모델은 아래 정규화 값을 쓴다."""
        year = self.next_estimate_year("eps") or self.next_estimate_year("np")
        if not year:
            return []
        bc = self.basis_check(year)
        e = bc.get("eps") or {}
        st = bc.get("status")
        if st == "unavailable":
            return ["  (EPS 기준 통일 불가 — 리포트 머리에 발행주식수가 없다. 증권사 EPS 를 그대로 평균; A3.8 shares 를 주면 정규화)"]
        sv = bc.get("shares_std") or 0
        parts = []
        for b, v in (e.get("by_broker") or {}).items():
            tag = " (전체×비율)" if b in (e.get("approx") or {}) else ""
            parts.append(f"{b} {v:,.0f}{tag}")
        for b, why in (e.get("excluded") or {}).items():
            parts.append(f"{b} 제외({why})")
        head = f"  EPS 기준 통일 ({year}, 지배 순이익 ÷ 공시 보통주 {sv / 1e3:,.0f}천주): "
        line = head + " · ".join(parts)
        if e.get("n"):
            line += f" → {e['mean']:,.0f} ({e['n']}곳" + (f", 표의 평균 {statistics.fmean(e['reported'].values()):,.0f} 대신 모델이 쓰는 값" if e.get("reported") else "") + ")"
        else:
            line += " → 비교 가능 0곳 — 모델 가격은 참고값"
        out = [line]
        mix = [n for n in bc.get("notes") or [] if n.startswith("주식 수 기준 혼합")]
        if mix:
            out.append("  " + mix[0] + " — 보통주 값만 표준으로")
        return out

    def summary_lines(self) -> list[str]:
        lines = [f"[리포트 {len(self.reports)}건] {self.name or ''} {self.ticker or ''} · 기준일 {self.as_of}"]
        for f in sorted(self.reports, key=lambda f: f.date or date.min, reverse=True):
            tag = " · (집계 제외)" if f.path.name in self.excluded else ""
            tag += " · 실적 전" if f.path.name in self.pre_earnings else ""
            lines.append(f"  - {f.label}" + (f" · {f.title}" if f.title else "") + tag + f"  [{f.path.name}]")
        lines.append(self.consensus_line())
        p = self.price
        if p:
            lines.append(f"현재주가 {p[0]:,.0f} ({p[1]}) ← {p[2].broker or p[2].path.name}")
        lines.append(f"추정치 금액 단위 {self.amount_unit[0]} (표 원단위 {self.unit_note})")
        lines.append(self.estimates_table())
        for w in self.warnings:
            lines.append(f"  ! {w}")
        return lines

    def materials(self, max_chars: int = 9000) -> str:
        """LLM 에 넣는 자료 발췌 — 리포트 머리·컨센서스·추정치·문장."""
        parts = ["## 리포트", *(f"- {f.label} · {f.title or ''} ({f.path.name})" for f in self.reports),
                 "## 컨센서스", self.consensus_line(), f"## 추정치 (금액 {self.amount_unit[0]}, 리포트 {len(self.reports)}건 평균)", self.estimates_table()]
        for kind, title in (("thesis", "논거 문장"), ("risk", "리스크 문장"), ("catalyst", "촉매 문장"),
                            ("valuation", "밸류에이션 문장"), ("shareholder", "주주환원 문장"), ("business", "사업 문장")):
            top = self.top(kind, 8)
            if top:
                parts.append(f"## {title}")
                parts += [f"- {t}" for t in top]
        text = "\n".join(parts)
        return text[:max_chars]


def digest_label(d: ReportDigest) -> str:
    return re.sub(r"\s+", " ", f"{d.name or ''} {d.ticker or ''} 리포트 {len(d.reports)}건").strip()


__all__ = ["ReportDigest", "TargetPoint", "EstimateCell", "digest_label", "UNIT_TO_EOK", "AMOUNT_METRICS", "MAX_AGE_DAYS", "EARNINGS_LAG_DAYS"]
