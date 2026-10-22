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
AMOUNT_METRICS = {"revenue", "op", "op_adj", "np", "ebitda"}


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

        def _factor(f: ReportFacts) -> float | None:
            fac = UNIT_TO_EOK.get(f.unit or "", None)
            if fac is None and f.estimates:
                return 10.0
            return fac

        for f in agg:
            factor = _factor(f)
            if UNIT_TO_EOK.get(f.unit or "", None) is None and f.estimates:
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
                   earnings_date=earnings_date, earnings_date_source=ed_src)

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
        rows.append("  (EPS 는 증권사별 주식 수 가정이 다를 수 있다 — 표에서는 확인 불가)")
        return "\n".join(rows)

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
