"""촉매 & 타임라인 (A6.1) — 시점 읽기 · 실적 발표일 추정 · 세 출처의 이벤트를 한 가지로 합치기.

2026-10-30 보강. 그전에는 A6.1 이 「분기 말 + 8일」 식 달력 규칙 둘로만 채워졌고(출처 「통상 일정
기준 추정」), 같은 지도의 A9.4 에 이미 뽑혀 있던 `catalyst` Claim(「2028년까지 200MW」 「2027
상반기 55MW」) 은 날짜 정규식에 안 걸려 버려졌다. NAVER 는 4분기 실적을 2월 초에 내는데 지도는
1월 8일이라고 적었고, 그 날짜가 A8.1 체크포인트까지 복사됐다.

이 모듈이 하는 일 셋.

    parse_when          문장에서 시점을 읽는다 — 날짜뿐 아니라 **기간**(상반기·분기·연내) 도 읽는다
    earnings_calendar   다음 실적 발표일 — 그 회사의 과거 공시일(영업(잠정)실적) 이 있으면 그것으로,
                        없으면 통상 규칙으로. 어느 쪽인지 certainty 에 적는다
    merge_events        규칙 · DART 공시 · Claim 세 출처의 이벤트를 A6.1 에 합친다 — 공시가 추정을 이기고,
                        같은 사건은 한 줄이고, 날짜순으로 번호를 다시 매긴다

출처가 곧 등급이다 (`EventCertainty`). 공시확정 > 공시상 예정 > 과거 주기 추정 > 회사 목표 > 회사 계획(리포트 인용) > 리포트 전망 > 통상 일정 추정.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, replace
from datetime import date, timedelta

from ..models import Event, EventCertainty, EventStatus, MapKind, Node, NodeStatus, Source

#: 통상 규칙 — 잠정실적은 분기 말 +8일 (삼성전자식 잠정 공시), 확정은 법정 시한 (분기·반기보고서 45일 · 사업보고서 90일).
#: 회사별 과거 공시일이 있으면 이 값은 쓰지 않는다.
PROV_OFFSET_DEFAULT = 8
FINAL_OFFSET_Q = 45
FINAL_OFFSET_Q4 = 90
#: 과거 공시일로 추정하려면 적어도 이만큼은 있어야 한다 (한 번은 우연일 수 있다)
MIN_HISTORY = 2
#: 공시(IR 안내) 날짜가 추정 발표일과 이만큼 안에 있으면 같은 사건 — 추정을 공시로 바꾼다
SAME_EVENT_DAYS = 25
#: A6.1 에 두는 최대 건수 — 그 이상은 일정표지 판단 항목이 아니다
MAX_EVENTS = 16    # A6.1 에 두는 사건의 상한 — 넘치면 중요도·확실성·날짜순으로 남기고 생략한 것은 meta.draft.a6_1_dropped 에 적는다 (2026-10-31: 10 → 16, 두산에너빌리티는 사업이 많다)

_RANK = {EventCertainty.공시확정: 0, EventCertainty.공시상예정: 1, EventCertainty.과거주기: 2, EventCertainty.회사목표: 3,
         EventCertainty.회사계획인용: 4, EventCertainty.리포트전망: 5, EventCertainty.통상추정: 6, EventCertainty.미정: 7}


def certainty_rank(c: EventCertainty | str) -> int:
    return _RANK.get(EventCertainty(c), 9)


# ---------------------------------------------------------------------------- 시점 읽기
@dataclass(frozen=True)
class When:
    """문장에서 읽은 시점.

    date    기준일 — 날짜면 그 날, 기간이면 기간의 **끝** (정렬·체크포인트용)
    period  사람이 읽을 기간 표기 ("2027H1" · "2027Q1" · "2027-03" · "2028" · "~2027-04"). 날짜면 None
    start   기간의 시작 (상대 기간 「향후 6개월」 은 기준 날짜부터). 날짜면 None
    span    문장 안에서 시점 표현이 있던 자리 (라벨을 짧게 자를 때 쓴다)
    """

    date: date
    period: str | None = None
    start: date | None = None
    span: tuple[int, int] | None = None
    # 2026-10-31 두산에너빌리티: 한 문장에 사건 시점과 납기·운전 시점이 함께 있다 (「2030년 슬롯 … 본계약 전환은 2027년」)
    delivery: When | None = None          # 납기·공급·운전 시점 — 사건 시점이 아니다
    role: str = "event"                   # 이 시점의 역할 — event · delivery · unknown

    @property
    def is_period(self) -> bool:
        return self.period is not None


def _safe_date(y: int, mth: int, d: int) -> date | None:
    try:
        return date(y, mth, d)
    except ValueError:
        return None


def quarter_end(y: int, q: int) -> date:
    m = q * 3
    return date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)


def half_end(y: int, h: int) -> date:
    return date(y, 6, 30) if h == 1 else date(y, 12, 31)


def add_months(d: date, n: int) -> date:
    """n 개월 뒤 같은 날 (없으면 그 달 말일)."""
    y, m = d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1
    last = (date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def _year2(yy: str) -> int:
    return 2000 + int(yy) if len(yy) == 2 else int(yy)


_DATE_FULL = re.compile(r"(20\d{2})\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})\s*일?")
_DATE_MD = re.compile(r"(?<![\d.])(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_HALF = re.compile(r"(20\d{2})\s*년?\s*(상반기|하반기|1H|2H)|([12])H\s?(\d{2})(?!\d)")
_QUARTER = re.compile(r"(20\d{2})\s*년?\s*([1-4])\s*분기|([1-4])Q\s?(\d{2})(?!\d)|(20\d{2})\s*년?\s*([1-4])Q(?![\d])")
#: 연월 — 「2027년 1월」 「2027.1」 「'27년 1월」 「27년 1월 말」 (두 자리 연도는 「년」 이 꼭 붙어야 한다) · ISO 「2027-01」 (Claim horizon)
#: 2026-10-31 삼성전자: 「27년 1월 이사회」 를 못 읽어 horizon 「2027-01」 → 연도만 읽혀 2027-12-31 이 됐다
_YEAR_MONTH = re.compile(r"(?:(20\d{2})\s*년?|'?(?<!\d)(\d{2})\s*년)\s*(\d{1,2})\s*월(?!\s*\d{1,2}\s*일)|(?<!\d)(20\d{2})-(0[1-9]|1[0-2])(?![\d-])")
_YEAR_ONLY = re.compile(r"(20\d{2})\s*(?:년|E)?\s*(?:까지|내|중|연중|연내|말|초|경|부터)?")
_THIS_YEAR = re.compile(r"연내|올해\s*(?:안|중|말)|금년\s*(?:중|말|내)|연말(?!\s*배당)")
_NEXT_YEAR = re.compile(r"내년")
#: 상대 기간 — 「향후 6개월」 「6개월 내」 「2년 안에」 「3분기 안」. 기준은 **문장을 쓴 날**(리포트 날짜) 이지 오늘이 아니다
_RELATIVE = re.compile(r"(?:향후|앞으로|이후|내)?\s*(\d{1,2})\s*(개월|달|년|분기|주)\s*(?:간|내|이내|안에|안|뒤|후|에\s*걸쳐)?")
_RELATIVE_WORDS = ("향후", "앞으로", "내", "이내", "안에", "안", "간", "뒤", "후", "걸쳐")


def _relative(text: str, anchor: date) -> When | None:
    for m in _RELATIVE.finditer(text):
        n, unit = int(m[1]), m[2]
        before = text[max(0, m.start() - 4): m.start()]
        after = text[m.end(): m.end() + 4]
        # 「6개월」 만 덜렁 있으면 기간이 아니라 단위일 수 있다 (「6개월 평균」) — 앞뒤에 상대 기간 말이 있어야 한다
        if not any(w in (before + m[0] + after) for w in _RELATIVE_WORDS):
            continue
        if n <= 0 or (unit == "년" and n > 10) or (unit in ("개월", "달") and n > 36):
            continue
        if unit in ("개월", "달"):
            end = add_months(anchor, n)
        elif unit == "년":
            end = add_months(anchor, 12 * n)
        elif unit == "분기":
            end = add_months(anchor, 3 * n)
        else:
            end = anchor + timedelta(weeks=n)
        return When(end, f"~{end.isoformat()[:7]}", anchor, (m.start(), m.end()))
    return None


_MONTH_ONLY = re.compile(r"(?<![\d.\-/월년])(\d{1,2})\s*월(?!\s*\d{1,2}\s*일)(?!\s*말\s*\d)")
_ANNUAL_TARGET = re.compile(r"연간\s*(?:신규\s*)?(?:수주|매출|영업이익)?\s*목표|올해\s*(?:수주\s*)?목표|연말")
#: 시점의 역할 — 뒤에 이런 말이 붙으면 그 시점은 **납기·운전**이다 (「2030년 슬롯」 「2030년 출하 물량」 「2035년 운전」 「2028년 하반기 출하 목표」)
_DELIVERY_WORDS = ("출하", "납기", "납품", "인도", "공급분", "공급 물량", "운전", "가동", "상업운전", "준공", "완공", "슬롯", "예약 물량", "예약물량",
                   "예약 접수", "건설 목표", "건설을 목표")
#: 앞뒤에 이런 말이 있으면 **사건** 시점이다 (「본계약 전환은 2027년」 「2028년 이전 발주」 「연내 체결」)
_EVENT_WORDS = ("계약", "발주", "결정", "체결", "전환", "착수", "FID", "투자결정", "승인", "인허가", "발표", "출시", "수주", "입찰", "선정",
                "신청", "허가", "협의", "론칭", "양산", "완료", "공시", "결의", "증설", "체결", "목표 달성", "달성")
_SPEC_RANK = {"date": 0, "half": 1, "quarter": 2, "ym": 3, "relative": 4, "month": 5, "year": 6, "range": 6, "this_year": 6, "next_year": 6}
_ADJACENT = re.compile(r"[\s~∼–\-·/,]*(?:또는|혹은|및)?[\s]*(?:초|말|중)?[\s]*")
_YEAR_RANGE = re.compile(r"(20\d{2})\s*[~∼–\-]\s*(20\d{2})\s*년")


def _time_tokens(text: str, today: date, anchor: date) -> list[tuple[str, When]]:
    """문장의 시점 표현 전부 — (종류, When). 겹치는 자리는 더 구체적인 것(날짜 > 반기 > 분기 > 연월 > 상대 > 월 > 연도) 만 남긴다."""
    out: list[tuple[str, When]] = []

    def taken(span: tuple[int, int]) -> bool:
        return any(not (span[1] <= w.span[0] or span[0] >= w.span[1]) for _, w in out if w.span)

    for m in _DATE_FULL.finditer(text):
        d = _safe_date(int(m[1]), int(m[2]), int(m[3]))
        if d and not taken(m.span()):
            out.append(("date", When(d, span=m.span())))
    for m in _DATE_MD.finditer(text):
        mth, day = int(m[1]), int(m[2])
        if 1 <= mth <= 12 and not taken(m.span()):
            y = today.year if mth >= today.month else today.year + 1
            d = _safe_date(y, mth, day)
            if d:
                out.append(("date", When(d, span=m.span())))
    for m in _HALF.finditer(text):
        if taken(m.span()):
            continue
        if m[1]:
            y, h = int(m[1]), 1 if m[2] in ("상반기", "1H") else 2
        else:
            y, h = _year2(m[4]), int(m[3])
        out.append(("half", When(half_end(y, h), f"{y}H{h}", date(y, 1, 1) if h == 1 else date(y, 7, 1), m.span())))
    for m in _QUARTER.finditer(text):
        if taken(m.span()):
            continue
        if m[1]:
            y, q = int(m[1]), int(m[2])
        elif m[3]:
            y, q = _year2(m[4]), int(m[3])
        else:
            y, q = int(m[5]), int(m[6])
        qend = quarter_end(y, q)
        if "실적" in text or "발표" in text:
            out.append(("quarter", When(qend + timedelta(days=PROV_OFFSET_DEFAULT), span=m.span())))
        else:
            out.append(("quarter", When(qend, f"{y}Q{q}", date(y, (q - 1) * 3 + 1, 1), m.span())))
    for m in _YEAR_MONTH.finditer(text):
        if taken(m.span()):
            continue
        if m[4]:
            y, mth = int(m[4]), int(m[5])
        else:
            y, mth = (int(m[1]) if m[1] else _year2(m[2])), int(m[3])
        if 1 <= mth <= 12:
            mend = date(y + (mth // 12), (mth % 12) + 1, 1) - timedelta(days=1)
            out.append(("ym", When(mend, f"{y}-{mth:02d}", date(y, mth, 1), m.span())))
    rel = _relative(text, anchor)
    if rel is not None and rel.span and not taken(rel.span):
        out.append(("relative", rel))
    for m in _MONTH_ONLY.finditer(text):
        if taken(m.span()):
            continue
        mth = int(m[1])
        if 1 <= mth <= 12:
            y = today.year            # 달만 있으면 올해 — 지난 달이면 지난 사실(체결했다) 이다
            mend = date(y + (mth // 12), (mth % 12) + 1, 1) - timedelta(days=1)
            out.append(("month", When(mend, f"{y}-{mth:02d}", date(y, mth, 1), m.span())))
    for m in _YEAR_RANGE.finditer(text):
        if taken(m.span()):
            continue
        y1, y2 = int(m[1]), int(m[2])
        if y1 <= y2 <= y1 + 6:   # 「2027~2030년 중 FID」 — 이를 수 있는 가장 이른 해가 기준, 표기는 범위
            out.append(("range", When(date(y1, 12, 31), f"{y1}~{y2}", date(y1, 1, 1), m.span())))
    for m in _YEAR_ONLY.finditer(text):
        if taken(m.span()):
            continue
        y = int(m[1])
        out.append(("year", When(date(y, 12, 31), str(y), date(y, 1, 1), m.span())))
    for m in _THIS_YEAR.finditer(text):
        if not taken(m.span()):
            out.append(("this_year", When(date(today.year, 12, 31), str(today.year), today, m.span())))
    for m in _ANNUAL_TARGET.finditer(text):
        if not taken(m.span()):
            out.append(("this_year", When(date(today.year, 12, 31), str(today.year), today, m.span())))
    for m in _NEXT_YEAR.finditer(text):
        if not taken(m.span()):
            out.append(("next_year", When(date(today.year + 1, 12, 31), str(today.year + 1), date(today.year + 1, 1, 1), m.span())))
    out.sort(key=lambda kw: kw[1].span[0] if kw[1].span else 0)
    return out


def _role_of(text: str, w: When, others: list[When]) -> str:
    """시점 하나의 역할 — 바로 뒤(다음 시점 전까지, 12자) 에 납기·운전 말이 붙으면 delivery, 앞뒤(16자) 에 사건 말이 있으면 event, 아니면 unknown."""
    if not w.span:
        return "unknown"
    st, en = w.span
    nxt = min([o.span[0] for o in others if o.span and o.span[0] > st] + [len(text)])
    after = text[en:min(en + 12, nxt)]
    before = text[max(0, st - 16):st]
    if any(d in after for d in _DELIVERY_WORDS):
        return "delivery"
    if any(e in after for e in _EVENT_WORDS) or any(e in before for e in _EVENT_WORDS):
        return "event"
    return "unknown"


def parse_when(text: str, today: date, *, anchor: date | None = None) -> When | None:
    """문장에서 **사건의** 시점 하나를 읽는다. 정확한 날짜 > 반기 > 분기 > 연월 > 상대 기간 > 월 > 연도 > 연내·내년 순.

    기간은 `period` 로 돌려준다 — "2027H1" · "2027Q1" · "2027-03" · "2028" · 상대 기간 "~2027-04". 기준일은 그 기간의 끝이다.
    분기에 「실적」 이 붙어 있으면 (「4Q26 실적」) 기간이 아니라 **발표일 추정** 으로 본다 — 분기 말 + 통상 오프셋.
    상대 기간(「향후 6개월」) 은 `anchor`(리포트 날짜, 없으면 오늘) 부터 센다 — 2026-10-28 리포트의 6개월은 2027-04 까지다.

    2026-10-31 두산에너빌리티: 시점마다 **역할**을 본다 — 「2030년 슬롯 예약 물량의 본계약 전환은 2027년」 의 사건 시점은 2027 이고
    2030 은 납기(`delivery`) 다. 「2035년 운전을 목표로 2028년 이전 발주」 의 사건은 2028. 납기만 있으면(「2028년 하반기 출하 목표」)
    첫 출하 자체가 사건이라 그것을 쓴다.
    """
    if not text:
        return None
    tokens = _time_tokens(text, today, anchor or today)
    if not tokens:
        return None
    whens = [w for _, w in tokens]
    roles = {id(w): _role_of(text, w, whens) for w in whens}
    # 붙어 있는 시점(「10월 30일 3Q26 확정실적」 · 「연말~내년 초 발주」) 은 한 구절 — 역할을 못 정한 쪽이 이웃의 역할을 받는다
    for i, w in enumerate(whens):
        if roles[id(w)] != "unknown" or not w.span:
            continue
        for j in (i + 1, i - 1):
            if 0 <= j < len(whens) and whens[j].span and roles[id(whens[j])] != "unknown":
                a, b = (w, whens[j]) if j > i else (whens[j], w)
                gap = text[a.span[1]:b.span[0]]
                if _ADJACENT.fullmatch(gap):
                    roles[id(w)] = roles[id(whens[j])]
                    break
    events = [(k, w) for k, w in tokens if roles[id(w)] == "event"] or [(k, w) for k, w in tokens if roles[id(w)] != "delivery"] or tokens
    kind, chosen = sorted(events, key=lambda kw: (_SPEC_RANK[kw[0]], kw[1].span[0] if kw[1].span else 0))[0]
    delivery = next((w for _, w in tokens if roles[id(w)] == "delivery" and w is not chosen), None)
    if delivery is not None:
        delivery = replace(delivery, role="delivery")
    return replace(chosen, role=roles[id(chosen)], delivery=delivery)      # unknown 은 그대로 — 「질문에 사건 시점이 있나」 를 가릴 때 쓴다


def time_clause(text: str, w: When | None, *, limit: int = 60) -> str:
    """시점 표현이 들어 있는 **절** 하나 — 긴 전망 문장을 노드 제목으로 쓰지 않는다. 절이 없으면 앞부분."""
    t = " ".join((text or "").split())
    if not t:
        return ""
    if w is not None and w.span is not None:
        pos = w.span[0]
        # 쉼표·가운뎃점·접속으로 절을 자른다
        parts, acc, idx = [], 0, None
        for seg in re.split(r"(?<=[,，;·])\s*|\s(?:및|그리고|하며|하고|이며)\s", t):
            if not seg:
                continue
            st = t.find(seg, acc)
            parts.append((st, seg))
            acc = st + len(seg)
            if st <= pos < st + len(seg) + 1:
                idx = len(parts) - 1
        if idx is not None:
            seg = parts[idx][1].strip(" ,，;·")
            return seg if len(seg) <= limit else seg[: limit - 1].rstrip() + "…"
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def date_in_sentence(text: str, today: date) -> date | None:
    """옛 이름 호환 — 기준일만."""
    w = parse_when(text, today)
    return w.date if w else None


# ---------------------------------------------------------------------------- 실적 발표일
@dataclass(frozen=True)
class EarningsDate:
    date: date
    label: str
    kind: str                 # 잠정 | 확정
    certainty: EventCertainty
    basis: str                # 출처 한 줄 — Source.name 에 들어간다


def _reported_quarter(d: date) -> date:
    """공시일 d 가 보고하는 분기의 끝 — d 직전의 분기 말. (2월 6일 공시 → 전년 12월 31일)"""
    q_end = quarter_end(d.year, (d.month - 1) // 3 + 1)
    if q_end >= d:
        # 아직 이번 분기 안이면 직전 분기
        prev = (d.month - 1) // 3   # 0..3
        return quarter_end(d.year, prev) if prev else quarter_end(d.year - 1, 4)
    return q_end


def _q_label(q_end: date) -> str:
    return f"{(q_end.month - 1) // 3 + 1}Q{str(q_end.year)[2:]}"


def provisional_offset(history: list[date] | None) -> tuple[int, int]:
    """과거 공시일(영업(잠정)실적) 에서 분기 말 → 공시일 오프셋의 중앙값과 쓴 건수. 모자라면 통상값."""
    if not history:
        return PROV_OFFSET_DEFAULT, 0
    offs = []
    for d in sorted(set(history))[-8:]:
        off = (d - _reported_quarter(d)).days
        if 0 < off <= 100:
            offs.append(off)
    if len(offs) < MIN_HISTORY:
        return PROV_OFFSET_DEFAULT, 0
    return int(round(statistics.median(offs))), len(offs)


def business_day(d: date) -> date:
    """주말이면 다음 월요일 — 법정 시한은 말일이 공휴일이면 다음 영업일로 넘어가고, 회사도 주말엔 발표하지 않는다.
    공휴일 달력은 이 저장소에 없어 평일 기준이다 (2026-11-14 토요일 사고, 2026-10-31)."""
    return d + timedelta(days=(7 - d.weekday()) % 7) if d.weekday() >= 5 else d


def earnings_calendar(today: date, history: list[date] | None = None, *, n: int = 4) -> list[EarningsDate]:
    """오늘 이후의 실적 일정 — 직전 분기부터 본다 (10월 20일이면 3분기 발표가 아직 남아 있다).

    잠정: 회사별 과거 공시일 중앙값 오프셋 (있으면) / 통상 +8일.
    확정: 법정 시한 — 분기·반기보고서 45일, 사업보고서(4분기) 90일. 회사별 자료가 없으니 늘 통상 추정이다.
    """
    off, used = provisional_offset(history)
    cert = EventCertainty.과거주기 if used else EventCertainty.통상추정
    basis = f"과거 {used}분기 공시일 기준 추정 (분기 말 +{off}일)" if used else "통상 일정 기준 추정"
    out: list[EarningsDate] = []
    # 직전 분기 말부터
    cur_q = (today.month - 1) // 3 + 1
    y, q = (today.year, cur_q - 1) if cur_q > 1 else (today.year - 1, 4)
    for _ in range(4):
        end = quarter_end(y, q)
        lab = _q_label(end)
        prov = business_day(end + timedelta(days=off))
        final = business_day(end + timedelta(days=FINAL_OFFSET_Q4 if q == 4 else FINAL_OFFSET_Q))
        if prov > today:
            out.append(EarningsDate(prov, f"{lab} 잠정실적 발표 ({'과거 주기 추정' if used else '예정일 추정'})", "잠정", cert, basis))
        if final > today:
            what = "사업보고서" if q == 4 else "확정실적·분기보고서"
            out.append(EarningsDate(final, f"{lab} {what} (법정 시한)", "확정", EventCertainty.통상추정, "법정 제출 시한 기준 추정 (주말이면 다음 영업일)"))
        q += 1
        if q > 4:
            q, y = 1, y + 1
        if len(out) >= n:
            break
    out.sort(key=lambda e: e.date)
    return out[:n]


def next_earnings(today: date, history: list[date] | None = None) -> list[tuple[date, str, str]]:
    """옛 이름 호환 — (날짜, 라벨, 종류) 세 짝."""
    return [(e.date, e.label, e.kind) for e in earnings_calendar(today, history)]


# ---------------------------------------------------------------------------- 합치기
def _norm(s: str) -> str:
    return re.sub(r"[\s\[\]()·,.]+", "", s or "").lower()


def _key_words(fact: str) -> set[str]:
    return {w for w in re.split(r"[\s\[\]()·,./]+", fact or "") if len(w) >= 2}


_EARNINGS_WORDS = ("실적", "IR", "기업설명회", "컨퍼런스콜", "잠정")
_EARNINGS_RE = re.compile(r"실적\s*발표|실적발표|잠정\s*실적|확정\s*실적|잠정실적|확정실적|[1-4]Q\d{2}\s*실적|기업설명회|컨퍼런스\s*콜|컨퍼런스콜|IR\b|어닝")


def event_subject(ev: Event) -> str | None:
    """사건의 주체 — 사업(program) 이 있으면 그것, 아니면 본문 이벤트 ID 의 드라이버 조각 (`text:{driver}:{topic}:{period}` · `llm:…`).

    주체가 둘 다 있고 다르면 (X-energy vs 폴란드 AP1000, SMR vs 해상풍력) 기간·주제가 같아도 다른 사건이다.
    """
    if ev.program:
        return ev.program
    parts = (ev.event_id or "").split(":")
    if len(parts) == 4 and parts[0] in ("text", "llm"):
        return parts[1]
    return None


def _same_period_event(a: Event, b: Event) -> bool:
    """기간 사건(period 가 있는 것) 끼리 — 끝 날짜(12-31) 가 같은 것은 우연이므로 주체·주제·기간 겹침으로만 본다.

    - 주체가 둘 다 있고 다르면 다른 사건 (2026-10-31 두산에너빌리티: 「연말」 수주 사건 7개가 낱말 「수주·목표·계약」 로 하나가 되던 것)
    - 주제가 다르거나 기간이 안 겹치면 다른 사건
    - 같은 사업(program) 의 같은 주제 → 하나 (본문 문장·Q&A·Claim 이 같은 사건을 세 번 말한다)
    - 마일스톤(project) 끼리는 다른 사건 (55MW 2027H1 vs 100MW 2027)
    - 같은 본문 드라이버(두 리포트의 「SMR 4Q 수주 목표」) → 하나
    - 전망 하나 + 회사 계획 하나 (주체가 어느 쪽도 다르지 않을 때) → 하나 — 「27년 1월 주주환원 결정」 ↔ 「1Q27 주주환원계획 기대」 (2026-10-31 삼성전자)
    - 회사 계획 둘 · 전망 둘 은 주체가 같다고 확인되지 않으면 합치지 않는다
    """
    from ..units import periods_overlap

    sa, sb = event_subject(a), event_subject(b)
    if sa and sb and sa != sb:
        return False
    ta, tb = event_topic(a), event_topic(b)
    if ta != tb or not periods_overlap(a.period, b.period):
        return False
    if a.program and a.program == b.program:
        return True
    if a.project or b.project:
        return False
    if sa and sa == sb:
        return True
    certs = {a.certainty, b.certainty}
    company = {EventCertainty.회사계획인용, EventCertainty.회사목표}
    if EventCertainty.리포트전망 in certs and certs & company and ta != "plan":
        return True
    # 주체를 모르는 쪽(Claim · 옛 본문 ID) 이 있으면 — 일반 낱말(수주·목표·계약·시점…) 을 뺀 고유 낱말이 겹쳐야 하나다
    return bool((_key_words(a.fact) - _GENERIC_WORDS) & (_key_words(b.fact) - _GENERIC_WORDS))


#: 사건을 가르지 못하는 낱말 — 기간 사건끼리 「같은 사건」 을 정할 때 뺀다 (「수주 목표」 는 두산에너빌리티 사건 7개에 다 들어 있다)
_GENERIC_WORDS = frozenset("""
회사 계획 발표 전망 리포트 추정 목표 인용 기업 증권사
수주 계약 발주 체결 공시 확정 결정 정책 규모 구체적 이후 이사회 시점 여부 예정 가능성 달성 진행 상황 관련 추진 검토 협의
연내 연말 연초 내년 올해 상반기 하반기 분기 연간 중 약 및 등 이외에 대한 위한 따라 통해 대비
기자재 부품 후속 신규 추가 실현 확보 예상 기대 가능 불확실 지연 중심 주요 대형 국내 해외
""".split())


def same_event(a: Event, b: Event) -> bool:
    """같은 사건인가 — 영속 ID 가 같으면 같고, 실적류는 분기가 같으면, 기간 사건은 주체·주제·기간이 맞으면,
    그 밖엔 날짜가 3일 안이고 낱말이 겹치면 같다."""
    if a.event_id and b.event_id and a.event_id == b.event_id:
        return True
    gap = abs((a.date - b.date).days)
    ea = any(w in a.fact for w in _EARNINGS_WORDS)
    eb = any(w in b.fact for w in _EARNINGS_WORDS)
    if ea and eb and gap <= SAME_EVENT_DAYS:
        # 같은 분기를 가리키나 — 라벨에 nQyy 가 있으면 그것으로
        qa, qb = re.search(r"[1-4]Q\d{2}", a.fact), re.search(r"[1-4]Q\d{2}", b.fact)
        if qa and qb and qa[0] != qb[0]:
            return False
        # 잠정 vs 확정(보고서) 은 다른 사건
        if ("보고서" in a.fact) != ("보고서" in b.fact):
            return False
        return True
    if a.period and b.period and not ea and not eb:
        return _same_period_event(a, b)
    if gap > 3:
        return False
    wa, wb = _key_words(a.fact), _key_words(b.fact)
    return bool(wa & wb) or _norm(a.fact)[:12] == _norm(b.fact)[:12]


def merge_events(existing: list[Event], incoming: list[Event]) -> tuple[list[Event], list[str]]:
    """들어온 이벤트를 기존에 합친다. 같은 사건이면 **확실성이 높은 쪽**이 남고, 낮은 쪽의 affects·사전 확인은 넘겨받는다."""
    out = list(existing)
    notes: list[str] = []
    for ev in incoming:
        hit = next((i for i, cur in enumerate(out) if same_event(cur, ev)), None)
        if hit is None:
            out.append(ev)
            continue
        cur = out[hit]
        if cur.is_confirmed() and ev.is_confirmed() and ev.date > cur.date:
            ev = ev.model_copy(update={"status": EventStatus.delayed})   # 공시된 일정이 정정으로 미뤄졌다 — 사람이 보게 적는다
        # 밀려난 쪽의 관련 전망(evidence) 은 남는 쪽에 붙는다 — 리포트 전망 이벤트가 회사 계획에 합쳐질 때 문장이 사라지지 않게
        def _with_evidence(keep: Event, gone: Event) -> Event:
            extra = [x for x in gone.evidence if x not in keep.evidence]
            if not extra and gone.certainty == EventCertainty.리포트전망 and gone.fact and gone.fact not in " ".join(keep.evidence):
                extra = [f"(·) {gone.fact}"]
            return keep.model_copy(update={"evidence": [*keep.evidence, *extra]}) if extra else keep

        if certainty_rank(ev.certainty) < certainty_rank(cur.certainty) or (ev.is_confirmed() and cur.is_confirmed() and ev.date != cur.date):
            keep = ev.model_copy(update={"affects": sorted(set(ev.affects) | set(cur.affects)),
                                         "precheck": ev.precheck or cur.precheck, "bull": ev.bull or cur.bull, "bear": ev.bear or cur.bear,
                                         "rule": ev.rule or cur.rule,
                                         # 사건의 정체는 그대로다 — 추정이 공시로 바뀌어도 ID(와 노드 번호) 는 남는다
                                         "event_id": cur.event_id if (ev.event_id is None or (ev.event_id or "").startswith(("claim:", "llm:"))) and cur.event_id else ev.event_id})
            out[hit] = _with_evidence(keep, cur)
            notes.append(f"{cur.when_label()} {cur.fact[:24]} → {ev.when_label()} {ev.fact[:24]} ({ev.certainty.value} 가 {cur.certainty.value} 를 대신)")
        else:
            out[hit] = _with_evidence(cur.model_copy(update={"affects": sorted(set(cur.affects) | set(ev.affects))}), ev)
    return out, notes


def _node_index(nid: str) -> int:
    try:
        return int(nid.rsplit(".", 1)[1])
    except (IndexError, ValueError):
        return 0


def set_catalysts(m, events: list[Event], *, today: date, keep_tags: bool = True) -> list[str]:  # noqa: ANN001
    """A6.1 의 자식을 이 목록으로 다시 쓴다. **노드 번호는 한 번 주면 안 바꾼다** (2026-10-31).

    번호는 `event_id` 로 잇는다 — 같은 event_id 가 있던 노드의 번호를 그대로 받고, event_id 가 없거나 처음이면 같은 사건
    (`same_event`) 이던 노드의 번호를, 그것도 없으면 다음 빈 번호를 받는다. 날짜순으로 다시 매기지 않는다 — 다시 매기면
    `edits.json` 의 `set A6.1.3` 이 다른 사건을 가리킨다. 보이는 순서는 `MindMap.children()` 이 날짜로 정렬한다.
    사람이 고친 노드(`user`) 는 건드리지 않는다.
    """
    from .rules import mark_draft

    old = list(m.children("A6.1"))
    user_nodes = [n for n in old if "user" in n.tags]
    prev = [(n.id, n.event) for n in old if n not in user_nodes and n.event is not None]
    by_event_id = {ev.event_id: nid for nid, ev in prev if ev.event_id}
    for n in old:
        if n not in user_nodes:
            del m.nodes[n.id]
    taken = {n.id for n in user_nodes}
    dropped: list[Event] = []
    if len(events) > MAX_EVENTS:
        # 넘치면 **중요한 것**을 남긴다 — 중요도 H · 확실성 · 가까운 날짜 순 (전엔 날짜순 앞 10개라 먼 날의 H 가 잘렸다 · 2026-10-31 두산에너빌리티)
        keep = sorted(events, key=lambda e: (e.materiality.value != "H", certainty_rank(e.certainty), e.date))[:MAX_EVENTS]
        dropped = [e for e in events if e not in keep]
        events = [e for e in events if e in keep]
    if isinstance(m.meta, dict):
        # 생략한 사건은 사람이 보게 남긴다 — 소리 없이 사라지면 「왜 중형 가스터빈이 없나」 를 알 길이 없다
        m.meta.setdefault("draft", {})["a6_1_dropped"] = [f"{e.when_label()} {e.fact[:50]} ({e.certainty.value} · {e.materiality.value})" for e in dropped]
    events = sorted(events, key=lambda e: (e.date, certainty_rank(e.certainty)))
    ids: list[str] = []
    for ev in events:
        nid = by_event_id.get(ev.event_id or "")
        if nid is None or nid in taken:
            nid = next((pid for pid, pev in prev if pid not in taken and same_event(pev, ev)), None)
        if nid is None or nid in taken:
            i = max([_node_index(x) for x in taken] + [_node_index(x) for x, _ in prev] + [0]) + 1
            nid = f"A6.1.{i}"
            while nid in taken or nid in m.nodes:
                i += 1
                nid = f"A6.1.{i}"
        taken.add(nid)
        status = NodeStatus.confirmed if ev.is_confirmed() else NodeStatus.estimated if ev.certainty != EventCertainty.미정 else NodeStatus.unverified
        node = Node(id=nid, map=MapKind.NEW, parent="A6.1", label=ev.fact[:30], node_type="event", value_type="text", as_of=today,
                    status=status, event=ev, source=ev.source, links=[a for a in ev.affects if a in m.nodes], next_check=ev.checkpoint())
        mark_draft(node)
        if ev.certainty != EventCertainty.미정:
            node.tags = sorted(set(node.tags) | {ev.certainty.value.replace(" ", "")})
        m.add(node)
        ids.append(nid)
    return ids


# ---------------------------------------------------------------------------- 종류 · 연결 · 조건 (2026-10-31)
def event_kind(e: Event) -> str:
    """이벤트의 종류 — 연결 규칙과 기본 조건의 열쇠. earnings · report · rights · agm · target · forecast · other."""
    f = e.fact or ""
    if any(w in f for w in ("납입", "유상증자", "상장 예정", "상장예정", "발행가", "청약", "권리락", "배정 기준")):
        return "rights"
    if "주주총회" in f:
        return "agm"
    eid = e.event_id or ""
    if eid.startswith("report:") or "분기보고서" in f or "사업보고서" in f or ("보고서" in f and "실적" in f):
        return "report"
    if eid.startswith("earnings:") or _EARNINGS_RE.search(f):
        return "earnings"           # 「실적」 낱말 하나로는 안 된다 — 「완결된 계약으로 실적에 반영」 은 실적 발표가 아니다 (2026-10-31)
    if e.certainty in (EventCertainty.회사목표, EventCertainty.회사계획인용):
        return "target"
    if e.certainty == EventCertainty.리포트전망:
        return "forecast"
    return "other"


def event_period(e: Event) -> str | None:
    """이벤트의 회계 기간 — `period` 가 있으면 그것, 실적·보고서 이벤트는 사실/ID 의 「3Q26」 에서 (날짜가 박힌 사건이라 period 칸은 비어 있다)."""
    if e.period:
        return e.period
    mq = re.search(r"([1-4])Q(\d{2})", f"{e.event_id or ''} {e.fact}")
    return f"20{mq[2]}Q{mq[1]}" if mq else None


# ---------------------------------------------------------------------------- 주제별 검증 템플릿 (2026-10-31 삼성전자 검토)
#: 회사 계획·마일스톤의 주제 — 사실·증거 문장의 낱말로 정한다. 주제가 다르면 검증 항목·판단 규칙도 다르다
#: (삼성전자: 주주환원 정책 발표와 LTA 비중 목표에 AI 인프라용 「유상 계약·가동률」 규칙이 복사돼 있었다)
TOPIC_WORDS: list[tuple[str, tuple[str, ...]]] = [
    ("payout", ("주주환원", "배당", "자사주", "소각", "밸류업", "환원 정책", "환원정책")),
    ("lta", ("LTA", "장기공급계약", "장기 공급 계약", "계약 비중", "장기 계약", "장기계약")),
    ("hbm", ("HBM", "퀄", "양산 승인", "샘플")),
    ("foundry", ("파운드리", "수율", "테일러", "선단공정", "2nm", "3nm")),
    ("ai_dc", ("MW", "데이터센터", "GPU", "AI 팩토리", "AI팩토리", "GPUaaS", "클라우드", "네오클라우드", "AI DC")),
    # 2026-10-31 두산에너빌리티: 수주·계약(회사의 사건) 과 대외 프로젝트 진행(FID·인허가 — 회사 수주가 아니다) 을 가른다
    ("orders", ("수주", "계약", "발주", "본계약", "EPC", "슬롯", "체결", "입찰", "낙찰", "공급 범위")),
    ("project_progress", ("FID", "최종투자결정", "인허가", "COL", "통합허가", "건설허가", "부지 확정", "부지 선정", "개발사 선정", "행정명령", "금융지원", "PPA", "실사")),
    ("capacity", ("증설", "CAPA", "Capa", "캐파", "용량", "가동", "준공", "착공", "공장")),
    ("launch", ("출시", "론칭", "상용화", "서비스 개시", "출하", "인도", "납품 개시", "허가", "승인", "인증")),
]

TOPIC_LABEL = {"payout": "주주환원 정책", "lta": "장기공급계약(LTA)", "hbm": "HBM 양산·인증", "foundry": "파운드리", "ai_dc": "AI 인프라",
               "orders": "수주·계약", "project_progress": "대외 프로젝트 진행", "capacity": "증설·가동", "launch": "출시·출하·승인", "plan": "회사 계획"}

#: 주제별 — 사전 확인 · 상방 · 하방 · 판단 규칙. 사람이 종목에 맞게 고치는 초안이다
TOPIC_TEMPLATES: dict[str, dict[str, list[str] | str]] = {
    "payout": {
        "precheck": ["배당 총액·DPS", "자사주 매입·소각 규모와 기간", "FCF 대비 환원율", "정책의 지속성(연간 공식 정책인가 일회성인가)"],
        "bull": ["FCF 대비 환원율 상향 + 다년 정책으로 명문화"], "bear": ["일회성 특별배당에 그침 · 자사주 소각 없음 · 환원율 하향"],
        "rule": "환원 규모 발표만으로 가치를 올리지 않는다 — FCF 대비 환원율·지속성(다년 정책)·소각 여부가 확인되면 주당 환원 가정과 A3.2 를 갱신한다. "
                "일회성에 그치면 환원 가정을 원래대로 되돌린다",
    },
    "lta": {
        "precheck": ["계약 CAPA 비중(%)", "가격 조건(고정·연동)", "계약 기간", "고객 다변화(상위 고객 집중도)"],
        "bull": ["LTA 비중 목표 달성 + 가격 하한 조건 확인"], "bear": ["비중 미달 · 가격 연동으로 하락 노출 · 특정 고객 집중"],
        "rule": "계약 체결 소식만으로 매출을 올리지 않는다 — 계약 CAPA 비중·가격 조건·기간이 확인되면 가격·물량 드라이버의 하방 띠를 좁힌다. "
                "비중이 목표에 못 미치거나 가격이 전부 시장 연동이면 하방 가정을 유지한다",
    },
    "hbm": {
        "precheck": ["고객 인증(퀄) 통과", "출하량·점유율", "ASP·마진", "수율", "패키징 CAPA"],
        "bull": ["주요 고객 인증 통과 + 출하 개시 + 점유율 상승"], "bear": ["인증 지연 · 수율 미달 · 경쟁사 점유율 유지"],
        "rule": "인증·샘플 소식만으로 점유율을 올리지 않는다 — 출하량·ASP·수율이 확인되면 HBM 드라이버 현재값과 시나리오 확률을 갱신한다. "
                "인증이 다시 미뤄지면 성장 가정을 하향 검토한다",
    },
    "foundry": {
        "precheck": ["선단공정 가동률", "수율", "대형 고객 양산 전환", "사업부 영업손익"],
        "bull": ["대형 고객 양산 개시 + 적자 축소"], "bear": ["가동률 정체 · 수율 미달 · 적자 확대"],
        "rule": "고객 확보 소식만으로 흑자 전환을 가정하지 않는다 — 가동률·수율·사업부 손익이 확인되면 파운드리 드라이버와 SOTP 멀티플을 갱신한다",
    },
    "ai_dc": {
        "precheck": ["실제 가동·공급 개시", "유상 계약 용량(MW)·계약 단가", "GPU 가동률", "EBITDA·FCF 기여"],
        "bull": ["목표 달성 + 유상 고객·매출 확인"], "bear": ["지연 · 고객 미확보 · 고정비 부담 확대"],
        "rule": "구축·달성만으로 수익성이 입증됐다고 보지 않는다 — 유상 계약, 가동률, 수익구조와 현금흐름이 확인되면 사업가치와 시나리오를 재평가한다. "
                "지연되거나 고정 지급의무 부담이 커지면 성장 가정을 하향 검토한다",
    },
    "orders": {
        "precheck": ["계약 체결 공시(상대·금액·품목)", "수주 인식 시점과 수주잔고 반영", "선행조건(승인·설계 확정·금융) 충족 여부", "계약 단계: 협의 → 조건 합의 → 체결 대기 → 체결"],
        "bull": ["연내 체결 + 수주잔고 반영 + 공급 범위 확대"], "bear": ["체결 지연 · 규모 축소 · 조건 변경 · 경쟁사 선정"],
        "rule": "협의 물량을 확정 수주로 세지 않는다 — 계약 체결 공시와 수주잔고 반영이 확인되면 수주·매출 드라이버(A1) 와 A3 를 갱신하고, "
                "지연되면 그 분기의 수주 가정을 미룬다 (목표 대비 달성률로 판단)",
    },
    "project_progress": {
        "precheck": ["인허가·FID·부지·개발사 등 선행 단계의 실제 결정", "회사의 공급 범위·계약 여부 (프로젝트 진행 ≠ 회사 수주)", "금융·정부 지원 조건"],
        "bull": ["선행 단계 통과 + 회사 공급계약(발주) 체결"], "bear": ["FID·인허가 지연 · 사업 축소 · 공급사 변경"],
        "rule": "대외 프로젝트 진행은 회사의 수주가 아니다 — 회사의 공급계약·발주가 공시로 확인될 때만 수주로 승격한다. "
                "선행조건 미충족이 이어지면 관련 수주 가정의 시점을 미룬다",
    },
    "capacity": {
        "precheck": ["준공·가동 개시", "가동률", "증설분의 수주·고객", "단위 원가 변화"],
        "bull": ["가동 개시 + 가동률 상승 + 수주 확인"], "bear": ["준공 지연 · 가동률 저조 · 고정비 부담"],
        "rule": "증설 완료만으로 매출을 올리지 않는다 — 가동률·수주가 확인되면 물량 드라이버를 갱신하고, 지연되면 그 분기의 매출 가정을 미룬다",
    },
    "launch": {
        "precheck": ["출시·첫 출하·승인 실제 일자", "초기 판매·수주·가입 지표", "가격·마진"],
        "bull": ["일정대로 출시·출하 + 초기 지표 호조"], "bear": ["지연 · 초기 지표 부진"],
        "rule": "출시·출하·승인 자체가 아니라 초기 판매·수주 지표가 확인되면 드라이버를 갱신한다",
    },
    "plan": {
        "precheck": ["계획의 실행 지표(회사가 적은 수치)", "실행 일정 준수"],
        "bull": ["계획 달성 + 실적 지표로 확인"], "bear": ["지연 · 축소 · 철회"],
        "rule": "계획 발표만으로 가치가 입증됐다고 보지 않는다 — 실행 지표가 실적·공시로 확인되면 관련 드라이버와 시나리오를 재평가하고, 지연되면 가정을 미룬다",
    },
}


#: 사업명(고유명사) 사전 — 드라이버(smr · nuclear_orders) 보다 잘다. SMR 을 한 사건으로 묶으면 이미 계약한 사업과 기다리는 사업이 섞인다
PROGRAMS: list[tuple[str, str]] = [
    ("X-energy", r"X-energy|엑스에너지|다우케미컬"),
    ("NuScale", r"NuScale|뉴스케일"),
    ("TerraPower", r"TerraPower|테라파워"),
    ("i-SMR", r"i-SMR"),
    ("BWRX-300", r"BWRX"),
    ("Rolls-Royce SMR", r"Rolls-Royce|롤스로이스"),
    ("루마니아 SMR", r"루마니아"),
    ("폴란드 AP1000", r"폴란드|Lubiatowo"),
    ("Fermi America", r"Fermi"),
    ("V.C. Summer", r"V\.?\s*C\.?\s*Summer|VC\s*서머"),
    ("미국 AP1000", r"미국\s*AP1000|AP1000\s*Fleet|대미\s*투자|대미투자|DOE·DOC|DOE\s*프로젝트|AP1000\s*장주기"),
    ("불가리아 Kozloduy", r"Kozloduy|불가리아"),
    ("체코 원전", r"체코|두코바니"),
    ("중형 가스터빈", r"중형\s*가스터빈"),
    ("가스터빈 슬롯", r"슬롯|예약\s*물량|예약계약|예약\s*계약"),
    ("해상풍력", r"해상풍력|신재생"),
    ("복합화력 EPC", r"복합화력|복합\s*EPC"),
]
_PROGRAM_RE = [(name, re.compile(pat)) for name, pat in PROGRAMS]


def event_program(text: str) -> str | None:
    """문장의 사업명 — 사전의 첫 일치. 없으면 None (드라이버 라벨이 대신한다)."""
    for name, rx in _PROGRAM_RE:
        if rx.search(text or ""):
            return name
    return None


#: 계약 진행 단계 — 설명회·리포트가 적은 말을 다섯 단계로. 순서가 곧 진척이다 (뒤가 더 나아간 것)
STAGES: list[tuple[str, str]] = [
    ("불확실", r"불확실|미정|여부는\s*불확실|지연\s*가능"),
    ("협의 중", r"협의\s*중|협의중|협상\s*(?:진행|중)|추진\s*중|검토\s*중|협의\s*진행|논의\s*중"),
    ("조건 합의", r"조건.{0,8}합의|합의.{0,6}완료|협의.{0,6}(?:완료|마무리)|상당\s*부분\s*완료|대부분\s*완료"),
    ("체결 대기", r"체결\s*대기|계약\s*대기|서명\s*대기"),
    ("체결", r"체결했|체결\s*완료|계약\s*(?:을\s*)?완료|계약을\s*체결|체결됐|수주했|수주\s*완료|제작에\s*착수"),
]
_STAGE_RE = [(name, re.compile(pat)) for name, pat in STAGES]


def find_stage(text: str) -> str | None:
    """문장이 말하는 계약 단계 — 여러 단계 말이 있으면 가장 나아간 것 (「합의 대부분 완료 … 체결 대기 중」 → 체결 대기). 「불확실」 은 다른 단계가 없을 때만."""
    found = [name for name, rx in _STAGE_RE if rx.search(text or "")]
    if not found:
        return None
    real = [f for f in found if f != "불확실"]
    return real[-1] if real else "불확실"


def _topic_of_text(text: str) -> str | None:
    low = (text or "").lower()
    for topic, words in TOPIC_WORDS:
        if any(w.lower() in low for w in words):
            return topic
    return None


def event_topic(e: Event) -> str:
    """회사 계획·마일스톤의 주제 — **사실(제목) 먼저**, 없으면 사전 확인, 그다음 증거 문장. 못 정하면 「plan」(일반).
    증거는 다른 사업 말(데이터센터향 가스터빈) 을 끌어와 주제를 바꿔 버리기 쉬워 마지막에 본다 (2026-10-31 두산에너빌리티)."""
    return _topic_of_text(e.fact) or _topic_of_text(" ".join(e.precheck)) or _topic_of_text(" ".join(e.evidence)) or "plan"


def topic_template(topic: str) -> dict[str, list[str] | str]:
    return TOPIC_TEMPLATES.get(topic) or TOPIC_TEMPLATES["plan"]


#: 종류별 판단 규칙 초안 (2026-10-31) — 회사 계획·마일스톤에도 「언제 판단을 고치나」 가 있어야 한다. target 은 주제별 템플릿으로 (default_rule)
DEFAULT_RULES: dict[str, str] = {"target": str(TOPIC_TEMPLATES["plan"]["rule"])}
_ALL_TEMPLATE_RULES = frozenset(str(t["rule"]) for t in TOPIC_TEMPLATES.values())


def default_rule(e: Event) -> str | None:
    """이벤트의 판단 규칙 초안 — 회사 계획(target) 은 주제별, 그 밖은 종류별."""
    kind = event_kind(e)
    if kind == "target":
        return str(topic_template(event_topic(e))["rule"])
    return DEFAULT_RULES.get(kind)


def rule_is_template(rule: str | None) -> bool:
    """코드가 채운 템플릿 규칙인가 (사람이 쓴 게 아니다) — 주제가 틀린 템플릿은 바꿔 끼운다."""
    return bool(rule) and rule in _ALL_TEMPLATE_RULES


#: 종류별 상방·하방 조건 **초안** — 사람이 종목에 맞게 고친다. 리포트 전망은 비워 둔다 (문장 자체가 증거다)
DEFAULT_CONDITIONS: dict[str, tuple[list[str], list[str]]] = {
    "earnings": (["컨센서스 상회 + 다음 분기 가이던스 상향", "핵심 드라이버 지표(성장률·마진) 개선"],
                 ["컨센서스 하회", "비용 급증 · 가이던스 하향"]),
    "rights:납입": (["납입 완료 + 전략 협업 진전 발표"], ["납입 지연 · 조건 변경 · 희석 부각"]),
    "rights:상장": (["의무보유·보호예수 확인 → 수급 불확실성 해소"], ["유통 가능 물량 오해로 수급 악화"]),
    "rights": (["일정대로 진행"], ["일정 변경 · 조건 변경"]),
    "agm": (["주주환원 확대 · 정관 개선"], ["환원 축소 · 안건 부결"]),
}


def default_conditions(e: Event) -> tuple[list[str], list[str]]:
    kind = event_kind(e)
    if kind == "rights":
        if "납입" in e.fact:
            return DEFAULT_CONDITIONS["rights:납입"]
        if "상장" in e.fact:
            return DEFAULT_CONDITIONS["rights:상장"]
    if kind == "target":
        t = topic_template(event_topic(e))
        return list(t["bull"]), list(t["bear"])
    bull, bear = DEFAULT_CONDITIONS.get(kind, ([], []))
    return list(bull), list(bear)


def default_precheck(e: Event) -> list[str]:
    """회사 계획(target) 의 사전 확인 초안 — 주제별. 그 밖은 비움 (공시 이벤트는 dart._SPEC 이 준다)."""
    if event_kind(e) == "target":
        return list(topic_template(event_topic(e))["precheck"])
    return []


def link_affects(m, e: Event, *, sector: str | None = None) -> list[str]:  # noqa: ANN001
    """이벤트가 바꾸는 노드 — 종류별 규칙표 + 문장의 드라이버 낱말.

    실적       A1 원인 드라이버 전부 + A3.1 결과 지표 (발표가 전부를 갱신한다)
    보고서     A3.1
    유상증자   A3.2 평가 기준(주식 수) · A3.3 평가 모델 · A5.4 Kill
    주주총회   A9.1.3 지배구조·환원
    그 밖에    문장에서 섹터 사전의 드라이버 낱말이 읽히면 그 A1 노드 (AI팩토리·GPU → ai_dc → A1.5)
    """
    from .cause import driver_node_for
    from .kpi_catalog import normalize_driver

    kind = event_kind(e)
    ids: set[str] = set(e.affects)
    cause = [n.id for n in m.drivers() if n.driver is not None and n.driver.role != "result"]
    have = lambda *xs: {x for x in xs if x in m.nodes}  # noqa: E731
    if kind == "earnings":
        ids |= set(cause) | have("A3.1")
    elif kind == "report":
        ids |= have("A3.1")
    elif kind == "rights":
        ids |= have("A3.2", "A3.3", "A5.4")
    elif kind == "agm":
        ids |= have("A9.1.3")
    elif kind == "target" and e.materiality.value == "H":
        ids |= have("A3.3", "A4.1")   # 회사 계획이 이뤄지면 평가 모델·시나리오가 바뀐다
    if kind in ("target", "forecast") and event_topic(e) == "payout":
        ids |= have("A9.1.3")         # 주주환원 정책은 지배구조·환원 노드에도 (주총과 같은 자리)
    sector = sector or (m.meta.get("draft") or {}).get("sector")
    # 제목만이 아니라 증거·사전 확인 문장까지 훑는다 — 제목은 시점이 든 절만이라 드라이버 낱말이 빠지기 쉽다 (「기존 목표인 2027년 상반기 55MW」)
    for text in [e.fact, *e.evidence, *e.precheck, *e.bull, *e.bear]:
        did = normalize_driver(text, sector=sector) or normalize_driver(text.replace(" ", ""), sector=sector)   # 「AI 팩토리」 ↔ 「AI팩토리」
        node = driver_node_for(m, did) if did else None
        if node:
            ids.add(node)
            break
    if e.project:
        node = driver_node_for(m, e.project)
        if node:
            ids.add(node)
    return sorted(ids)


def link_catalysts(m, *, sector: str | None = None, only_empty: bool = True) -> list[str]:  # noqa: ANN001
    """A6.1 전부에 affects 와 기본 상방·하방 조건을 채운다. 사람이 고친 노드(user) 와 이미 채워진 칸은 건드리지 않는다.
    원인 드라이버(A1) 가 채워진 **뒤**에 불러야 한다 — 초안에서는 fill_cause_drivers 다음."""
    notes: list[str] = []
    for n in m.children("A6.1"):
        e = n.event
        if e is None or "user" in n.tags:
            continue
        upd: dict = {}
        if not e.affects or not only_empty:
            aff = link_affects(m, e, sector=sector)
            if aff != e.affects:
                upd["affects"] = aff
        if not e.bull and not e.bear:
            bull, bear = default_conditions(e)
            if bull or bear:
                upd["bull"], upd["bear"] = bull, bear
        if not e.precheck:
            pre = default_precheck(e)
            if pre:
                upd["precheck"] = pre
        want_rule = default_rule(e)
        if want_rule and (not e.rule or (rule_is_template(e.rule) and e.rule != want_rule)):
            # 비어 있으면 채우고, 코드가 채운 템플릿인데 주제가 다르면(주주환원 이벤트에 AI 인프라 규칙) 바꿔 끼운다 — 사람이 쓴 규칙은 안 건드린다
            upd["rule"] = want_rule
        if upd:
            n.event = e.model_copy(update=upd)
            n.links = [a for a in n.event.affects if a in m.nodes]
            notes.append(f"{n.id} {e.fact[:18]}: " + " · ".join(k for k in upd))
    return notes


_RESULT_WORDS = ("실적", "영업이익", "매출", "컨센", "가이던스", "이익", "마진", "OP ", "OPM")


def attach_evidence(m, *, when: When | None, driver_node: str | None, text: str, direction: str, today: date) -> str | None:  # noqa: ANN001
    """증권사 전망 문장을 **이미 있는 이벤트의 관련 전망(`evidence`)** 으로 붙인다. bull/bear 에는 넣지 않는다 — 그 칸은
    사건의 성패를 판정하는 조건이지 전망이 아니다 (2026-10-31 검토: 실적 이벤트 bull 에 AI 전망이 섞였다).

    붙이는 규칙 — 같은 사건을 가리켜야 한다.
      실적·보고서 이벤트   기간이 맞고(±45일) 문장이 실적을 말할 때만. 드라이버 일치는 세지 않는다 (실적은 A1 전부에 걸려 무엇이든 맞아 버린다)
      그 밖의 이벤트       같은 period 또는 ±45일, 아니면 같은 드라이버
    """
    from ..units import periods_overlap

    if when is None and driver_node is None:
        return None
    best: tuple[int, Node] | None = None
    for n in m.children("A6.1"):
        e = n.event
        if e is None:
            continue
        kind = event_kind(e)
        score = 0
        period_hit = False
        ep = event_period(e)
        if when is not None:
            if ep and when.period and (ep == when.period or periods_overlap(ep, when.period)):
                score, period_hit = 3, True       # 같은 기간이거나 한쪽이 다른 쪽에 든다 (2027-01 ⊂ 2027Q1)
            elif ep and when.period:
                period_hit = False                # 둘 다 기간이 있는데 다르다 — 날짜가 가까워도 다른 사건 (4Q26 전망 ≠ 3Q26 보고서)
            elif abs((e.date - when.date).days) <= 45:
                score, period_hit = 2, True
        if kind in ("earnings", "report"):
            if not (period_hit and any(w in text for w in _RESULT_WORDS)):
                continue
        elif driver_node and driver_node in e.affects:
            score += 2
        if score >= 2 and (best is None or score > best[0]):
            best = (score, n)
    if best is None:
        return None
    n = best[1]
    e = n.event
    mark = {"positive": "(+)", "negative": "(−)"}.get(direction, "(·)")
    line = f"{mark} " + " ".join(text.split())[:140]
    cur = list(e.evidence)
    if line in cur:
        return n.id
    cur.append(line)
    n.event = e.model_copy(update={"evidence": cur})
    n.as_of = today
    return n.id


def renumber_fresh(m) -> bool:  # noqa: ANN001
    """**새 초안에서만** A6.1 번호를 날짜순으로 다시 매긴다 — 사람이 고친 노드(user) 가 하나라도 있으면 손대지 않는다 (번호 고정 원칙).
    첫 생성본의 번호가 3 → 1 → 2 → 4 로 보일 이유는 없다 (2026-10-31 검토)."""
    kids = m.children("A6.1")
    if not kids or any("user" in n.tags for n in kids) or any(n.event is None for n in kids):
        return False
    ordered = sorted(kids, key=lambda n: (n.event.date, certainty_rank(n.event.certainty), _node_index(n.id)))
    if [n.id for n in ordered] == [f"A6.1.{i}" for i in range(1, len(ordered) + 1)]:
        return False
    for n in kids:
        del m.nodes[n.id]
    for i, n in enumerate(ordered, start=1):
        n.id = f"A6.1.{i}"
        m.nodes[n.id] = n
    return True


def event_matters(e: Event) -> bool:
    """A8.1 에 들어갈 사건인가 — 판단을 고쳐야 하는 것만. 중요도 H, 또는 바꾸는 노드가 있고 출처가 공시·과거 주기·회사 목표인 것.
    리포트 전망·통상 추정은 바꾸는 노드가 있어도 체크포인트가 아니다 (일정표가 되지 않게)."""
    if e.status.value == "cancelled":
        return False
    if e.materiality.value == "H":
        return True
    return bool(e.affects) and e.certainty in (EventCertainty.공시확정, EventCertainty.공시상예정, EventCertainty.과거주기, EventCertainty.회사목표,
                                              EventCertainty.회사계획인용)


def checkpoint_row(e: Event) -> dict[str, str]:
    """A8.1 한 줄의 재료 — **자르지 않는다**. 사람이 적은 규칙·검증 문장은 끝까지 읽혀야 한다 (2026-10-31 3차 검토: `[:28]`·`[:50]` 로 「… 유상 계약, 가」 처럼 끊겼다).
    길이는 화면(HTML 표의 줄바꿈) 이 감당한다."""
    return {
        "date": e.checkpoint().isoformat(),
        "fact": e.fact,
        "check": " · ".join(e.precheck) or "—",
        "action": e.rule or ("A1 드라이버·A3 갱신" if event_kind(e) == "earnings" else "—"),
        "affects": ", ".join(e.affects),
    }


def checkpoint_rows(m, *, today: date | None = None) -> list[dict[str, str]]:  # noqa: ANN001
    """A8.1 에 들어갈 사건들 (판단에 걸리는 것만 · 끝난 과거 사건 제외) — 원문 그대로."""
    rows = []
    for n in m.children("A6.1"):
        e = n.event
        if e is None or not event_matters(e):
            continue
        if today is not None and e.checkpoint() < today and e.status.value == "completed":
            continue
        rows.append(checkpoint_row(e))
    return rows


def checkpoint_lines(m, *, today: date | None = None) -> list[str]:  # noqa: ANN001
    """A8.1 노드 본문 — 날짜 · 사건 · 검증할 것 · 후속 조치, 한 줄에. 날짜 목록(값) 은 그대로 두고 이것은 말이다. 원문을 자르지 않는다."""
    return [f"{r['date']} {r['fact']} — 검증: {r['check']} → 후속: {r['action']}" + (f" [{r['affects']}]" if r["affects"] else "")
            for r in checkpoint_rows(m, today=today)]


def merge_into_map(m, incoming: list[Event], *, today: date) -> list[str]:  # noqa: ANN001
    """A6.1 에 이미 있는 (자동) 이벤트와 합쳐 다시 쓴다. 사람이 고친 노드(`user`) 는 그대로 둔다."""
    kids = [n for n in m.children("A6.1") if n.event is not None]
    existing = [n.event for n in kids if "user" not in n.tags]
    user_events = [n.event for n in kids if "user" in n.tags]
    # 사람이 고친 노드와 같은 사건은 들어오지 않는다 — 사람의 판이 이긴다 (같은 사건이 두 줄로 생기지 않게)
    incoming = [ev for ev in incoming if not any(same_event(u, ev) for u in user_events)]
    merged, notes = merge_events(existing, incoming)
    set_catalysts(m, merged, today=today)
    return notes


def rule_event(e: EarningsDate, *, today: date, affects: list[str]) -> Event:
    q = re.search(r"[1-4]Q\d{2}", e.label)
    eid = f"{'earnings' if e.kind == '잠정' else 'report'}:{q[0] if q else e.date.isoformat()}"
    return Event(date=e.date, fact=e.label, affects=affects, direction="미정", priced_in="미판단", event_id=eid,
                 materiality="H" if e.kind == "잠정" else "M", certainty=e.certainty,
                 precheck=["컨센서스 대비 매출·영업이익", "다음 분기 가이던스"] if e.kind == "잠정" else [],
                 rule="발표 뒤 A1 드라이버 현재값·A3 EPS 를 갱신하고 판단 변경 근거를 A8.2 에 적는다" if e.kind == "잠정" else None,
                 source=Source(name=e.basis, retrieved_at=today))


def event_from_text(text: str, *, today: date, certainty: EventCertainty, source: Source | None, materiality: str = "M",
                    affects: list[str] | None = None) -> Event | None:
    """문장 하나 → 이벤트. 시점을 못 읽거나 이미 지난 것이면 None."""
    w = parse_when(text, today)
    if w is None or w.date <= today:
        return None
    return Event(date=w.date, period=w.period, fact=" ".join(text.split())[:140], affects=affects or [], direction="미정",
                 priced_in="미판단", materiality=materiality, certainty=certainty, source=source,
                 event_id=f"text:{w.date.isoformat()}:{_norm(text)[:16]}")


__all__ = ["When", "parse_when", "date_in_sentence", "EarningsDate", "earnings_calendar", "next_earnings", "provisional_offset",
           "merge_events", "merge_into_map", "same_event", "set_catalysts", "business_day", "renumber_fresh", "checkpoint_lines", "checkpoint_rows", "checkpoint_row", "event_matters", "DEFAULT_RULES", "event_kind", "default_conditions", "default_rule", "default_precheck", "event_topic", "event_period", "topic_template", "rule_is_template", "TOPIC_TEMPLATES", "TOPIC_LABEL", "PROGRAMS", "event_program", "STAGES", "find_stage", "link_affects", "link_catalysts", "attach_evidence", "time_clause", "add_months", "rule_event", "event_from_text", "certainty_rank", "quarter_end", "half_end",
           "MAX_EVENTS", "SAME_EVENT_DAYS"]
