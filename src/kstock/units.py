"""금액 단위 · 회계 기간 — 한 곳에서 (2026-10-31 네 번째 검토).

실측: A1.1 의 「컨센 7.76조원 (하나증권 77,599조원)」 — 증권사별 값은 억원(원천) 인데 노드 단위(조원) 를 그대로 붙였다.
수치는 (값, 단위) 쌍으로만 다루고, 글로 적을 땐 여기 `fmt_amount` 하나를 쓴다. 억원 값이 1조를 넘으면 조원으로 보인다.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

#: 금액 단위 → 억원 배수
UNIT_TO_EOK: dict[str, float] = {"억원": 1.0, "십억원": 10.0, "조원": 10000.0, "백만원": 0.01, "bn": 10.0, "억": 1.0, "조": 10000.0}
EOK_PER_JO = 10000.0


def to_eok(value: float, unit: str | None) -> tuple[float, str]:
    """금액 단위를 억원으로. 금액 단위가 아니면 (값, 단위) 그대로."""
    u = (unit or "").strip()
    if u in UNIT_TO_EOK:
        return value * UNIT_TO_EOK[u], "억원"
    return value, u


def convert(value: float, from_unit: str | None, to_unit: str | None) -> float:
    """금액 단위 사이 변환. 둘 중 하나가 금액 단위가 아니면 값 그대로 (단위가 같다고 본다)."""
    f, t = (from_unit or "").strip(), (to_unit or "").strip()
    if f == t or f not in UNIT_TO_EOK or t not in UNIT_TO_EOK:
        return value
    return value * UNIT_TO_EOK[f] / UNIT_TO_EOK[t]


def _num(v: float, digits: int) -> str:
    s = f"{v:,.{digits}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def fmt_amount(value: float | None, unit: str | None, *, jo_from_eok: float = EOK_PER_JO) -> str:
    """값 + 단위를 사람이 읽는 글로. 억원은 1조 이상이면 조원으로 (77,599억원 → 7.76조원), 조원은 소수 둘째 자리, % 는 그대로."""
    if value is None:
        return "—"
    u = (unit or "").strip()
    if u in ("억원", "억"):
        if abs(value) >= jo_from_eok:
            return f"{_num(value / EOK_PER_JO, 2)}조원"
        return f"{_num(value, 0 if abs(value) >= 100 else 1)}억원"
    if u in ("십억원",):
        return fmt_amount(value * 10.0, "억원")
    if u in ("조원", "조"):
        if 0 < abs(value) < 0.1:
            return fmt_amount(value * EOK_PER_JO, "억원")     # −0.03조원 → −300억원 (방향을 잃지 않게)
        return f"{_num(value, 2)}조원"
    if u == "%":
        return f"{_num(value, 1)}%"
    if u in ("%p",):
        return f"{_num(value, 1)}%p"
    if u in ("원",):
        return f"{value:,.0f}원"
    if u in ("주",):
        return f"{value:,.0f}주"
    if isinstance(value, float) and not value.is_integer():
        return f"{_num(value, 2)}{u}"
    return f"{value:,.0f}{u}" if abs(value) >= 1000 else f"{_num(value, 2)}{u}"


# ---------------------------------------------------------------------------- 회계 기간
_Q = re.compile(r"^([1-4])Q(\d{2})$")
_H = re.compile(r"^([12])H(\d{2})$")
_Y = re.compile(r"^(20\d{2})$")
REPORT_LAG_DAYS = 45    # 분기 끝 뒤 실적이 나오기까지 — 그 전까지는 「전망」 이 맞다


def period_end(col: str | None) -> date | None:
    """기간 열(2025 · 2026E · 3Q26 · 1H27) 의 끝 날. 못 읽으면 None."""
    c = (col or "").strip().rstrip("EPAF")
    m = _Y.match(c)
    if m:
        return date(int(m.group(1)), 12, 31)
    m = _Q.match(c)
    if m:
        q, yy = int(m.group(1)), 2000 + int(m.group(2))
        mth = q * 3
        return date(yy, mth, 31 if mth in (3, 12) else 30)
    m = _H.match(c)
    if m:
        h, yy = int(m.group(1)), 2000 + int(m.group(2))
        return date(yy, 6, 30) if h == 1 else date(yy, 12, 31)
    return None


_YM = re.compile(r"^(20\d{2})-(0[1-9]|1[0-2])$")
_QP = re.compile(r"^(20\d{2})Q([1-4])$")
_HP = re.compile(r"^(20\d{2})H([12])$")


def period_range(p: str | None) -> tuple[date, date] | None:
    """기간 표기 → (시작, 끝). 이벤트 period(2027Q1 · 2027H1 · 2027-01 · 2028) 와 표 열(3Q26 · 1H27 · 2026E) 둘 다 읽는다."""
    c = (p or "").strip()
    if not c:
        return None
    m = _YM.match(c)
    if m:
        y, mth = int(m.group(1)), int(m.group(2))
        end = date(y + (mth // 12), (mth % 12) + 1, 1) - timedelta(days=1)
        return date(y, mth, 1), end
    m = _QP.match(c)
    if m:
        y, q = int(m.group(1)), int(m.group(2))
        return date(y, (q - 1) * 3 + 1, 1), period_end(f"{q}Q{str(y)[2:]}")
    m = _HP.match(c)
    if m:
        y, h = int(m.group(1)), int(m.group(2))
        return (date(y, 1, 1), date(y, 6, 30)) if h == 1 else (date(y, 7, 1), date(y, 12, 31))
    end = period_end(c)
    if end is None:
        return None
    cc = c.rstrip("EPAF")
    if _Y.match(cc):
        return date(end.year, 1, 1), end
    m = _Q.match(cc)
    if m:
        q = int(m.group(1))
        return date(end.year, (q - 1) * 3 + 1, 1), end
    m = _H.match(cc)
    if m:
        return (date(end.year, 1, 1) if m.group(1) == "1" else date(end.year, 7, 1)), end
    return None


def periods_overlap(a: str | None, b: str | None) -> bool:
    """두 기간이 겹치나 — 「2027-01」 은 「2027Q1」 에 들고, 「2027Q1」 은 「2027」 에 든다. 둘 중 하나를 못 읽으면 False."""
    ra, rb = period_range(a), period_range(b)
    if ra is None or rb is None:
        return False
    return ra[0] <= rb[1] and rb[0] <= ra[1]


def is_past_period(col: str | None, today: date | None) -> bool:
    """이미 **실적이 나왔을** 기간인가 — 연도 열은 지난 해, 분기·반기 열은 끝난 뒤 45일이 지난 것.
    지난 기간의 수치는 「전망(컨센)」 이 아니다 — 2026-10-31 NAVER A1.4 가 2025 비용을 컨센으로 들었다."""
    if today is None:
        return False
    end = period_end(col)
    if end is None:
        return False
    c = (col or "").strip().rstrip("EPAF")
    if _Y.match(c):
        return end.year < today.year
    return end + timedelta(days=REPORT_LAG_DAYS) < today


__all__ = ["UNIT_TO_EOK", "EOK_PER_JO", "to_eok", "convert", "fmt_amount", "period_end", "period_range", "periods_overlap", "is_past_period",
           "REPORT_LAG_DAYS"]
