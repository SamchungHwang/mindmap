"""리포트 본문 → 구조화된 사실 (규칙 기반, LLM 없이).

증권사 리포트 표지는 회사마다 다르지만 적는 것은 같다: 종목(코드)·날짜·증권사, 투자의견, 목표주가(직전·변경),
현재주가, 상승여력, 그리고 연도별 추정치 표(매출액·영업이익·순이익·EPS·PER·PBR…). 본문에서는 논거·리스크·
촉매 문장을 뽑는다. PDF 텍스트는 2단 조판이 섞여 지저분하므로 모든 추출은 「후보를 모아 가장 그럴듯한 것」을
고르는 방식이다. 못 찾은 값은 None — 지어내지 않는다.

여기서 나온 숫자는 전부 `source=리포트` 인 사실이다. 판단은 하지 않는다 (§11).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .loader import ReportDoc

# ---------------------------------------------------------------------------- 증권사
BROKERS = [
    "KB증권", "NH투자증권", "미래에셋증권", "삼성증권", "키움증권", "한국투자증권", "신한투자증권", "하나증권", "메리츠증권",
    "대신증권", "유안타증권", "DB금융투자", "DB증권", "SK증권", "현대차증권", "IBK투자증권", "한화투자증권", "교보증권",
    "유진투자증권", "BNK투자증권", "LS증권", "iM증권", "다올투자증권", "흥국증권", "상상인증권", "DS투자증권", "케이프투자증권",
    "신영증권", "한양증권", "리딩투자증권", "부국증권", "토스증권", "카카오페이증권", "NH증권", "KB Securities", "Kiwoom",
    "Mirae Asset", "Hana Securities", "Shinhan Securities", "Daishin", "Yuanta", "Eugene", "Meritz",
]
BROKER_ALIASES = {"DB증권": "DB금융투자", "NH증권": "NH투자증권", "IBKS": "IBK투자증권", "iM증권리서치본부": "iM증권"}
EMAIL_DOMAINS = {
    "kbsec": "KB증권", "nhqv": "NH투자증권", "miraeasset": "미래에셋증권", "samsung": "삼성증권", "kiwoom": "키움증권",
    "truefriend": "한국투자증권", "koreainvestment": "한국투자증권", "shinhan": "신한투자증권", "hanafn": "하나증권",
    "meritz": "메리츠증권", "daishin": "대신증권", "yuantakorea": "유안타증권", "yuanta": "유안타증권", "db-fi": "DB금융투자",
    "sks.co.kr": "SK증권", "hmsec": "현대차증권", "ibks": "IBK투자증권", "hanwha": "한화투자증권", "iprovest": "교보증권",
    "eugenefn": "유진투자증권", "bnkfn": "BNK투자증권", "ls-sec": "LS증권", "imfnsec": "iM증권", "daolsec": "다올투자증권",
    "heungkuk": "흥국증권", "sangsangin": "상상인증권", "ds-sec": "DS투자증권", "capefn": "케이프투자증권", "shinyoung": "신영증권",
}

# ---------------------------------------------------------------------------- 정규식
_NUM = r"\d{1,3}(?:,\d{3})+|\d{3,9}"
_CHANGE = r"신규|유지|상향|하향|Maintain|Initiate|Upgrade|Downgrade|Initiation|M|U|D|N|신규편입"
_OPINION = (r"Strong\s*Buy|StrongBuy|Trading\s*Buy|Buy|BUY|매수|적극매수|Outperform|OUTPERFORM|Overweight|OVERWEIGHT|비중확대|"
            r"Hold|HOLD|보유|중립|Neutral|NEUTRAL|Marketperform|Market\s*Perform|Sell|SELL|매도|비중축소|Underperform|Reduce|"
            r"Not\s*Rated|NR|N/R")

RE_TICKER = re.compile(r"(?P<name>[가-힣A-Za-z0-9&·\.\-]{1,30})?\s*\(\s*A?(?P<code>\d{6})(?:\s*(?:KS|KQ|\.KS|\.KQ|코스피|코스닥))?\s*\)")
RE_TICKER2 = re.compile(r"(?:KOSPI|KOSDAQ|코스피|코스닥|종목코드|Code)\s*[:：]?\s*A?(?P<code>\d{6})\b|\bA?(?P<code2>\d{6})\s*(?:KS|KQ)\b")
RE_DATE4 = re.compile(r"(?<!\d)(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?(?!\d)")
RE_DATE_YM = re.compile(r"(?<!\d)(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*월?(?![\d.])")
RE_DATE2 = re.compile(r"(?<![\d.])'?(\d{2})\.\s*(\d{1,2})\.\s*(\d{1,2})(?![\d.])")
RE_FILENAME_DATE = re.compile(r"(20\d{2})(\d{2})(\d{2})")
RE_EMAIL = re.compile(r"[\w.\-]+@([\w\-]+(?:\.[\w\-]+)+)")
RE_ANALYST = re.compile(r"(?:Analyst|애널리스트|연구원|RA)\s*[:：]?\s*([가-힣]{2,4})(?!증권)")

RE_TP = re.compile(
    r"목표\s*주가(?:\s*\((?:12M|6M|12개월|6개월|원)\))?[를을은는도와과의]?\s*(?:\((?:" + _CHANGE + r")\))?\s*(?:" + _CHANGE
    + r")?\s*[:：]?\s*(?:\(원\))?\s*(?P<num>" + _NUM + r")\s*원?", re.I)
RE_TP_MAN = re.compile(r"목표\s*주가(?:\s*\([^)]{1,8}\))?[를을은는도와과의]?\s*(?:\((?:" + _CHANGE + r")\))?\s*[:：]?\s*(?P<man>\d+(?:\.\d+)?)\s*만\s*원")
RE_TP_EN = re.compile(r"(?:\bTP|Target\s*Price|목표가)\s*(?:\(KRW\)|\(원\))?\s*[:：]?\s*(?P<num>" + _NUM + r")\s*원?", re.I)
RE_TP_ARROW = re.compile(r"목표\s*주가[^\n]{0,25}?(?P<prev>" + _NUM + r")\s*원?\s*(?:→|->|에서|⇒)\s*(?P<new>" + _NUM + r")\s*원")
RE_TP_PREV = re.compile(r"(?:기존|직전|이전|종전|전)\s*(?:목표\s*주가|TP)?\s*[:：]?\s*(?:대비\s*)?(?P<num>" + _NUM + r")\s*원", re.I)
RE_TP_CHANGE = re.compile(r"목표\s*주가[^\n]{0,40}?(?P<chg>상향|하향|유지|신규|Maintain|Upgrade|Downgrade|Initiate)|"
                          r"(?P<chg2>상향|하향|유지|신규)[^\n]{0,12}?목표\s*주가", re.I)
RE_UPSIDE = re.compile(r"(?:상승\s*여력|Upside)\s*[:：]?\s*\(?(?P<pct>-?\d{1,3}(?:\.\d+)?)\s*%", re.I)
RE_PRICE = re.compile(
    r"(?:현재\s*(?:주가|가)|주가|전일\s*종가|종가|기준\s*주가|\bCP|Price)\s*(?:\((?P<d>[^()]{3,14})\))?\s*(?:\(원\)|\(KRW\))?\s*[:：]?\s*"
    r"(?P<num>" + _NUM + r")\s*원?(?!\s*(?:→|->))", re.I)
RE_PRICE_WITH_DATE_AFTER = re.compile(r"현재\s*주가\s*\(원\)\s*(?P<num>" + _NUM + r")\s+(?P<d>20\d{2}\.\s*\d{1,2}\.\s*\d{1,2})")
RE_OPINION = re.compile(r"투자\s*의견\s*[:：]?\s*(?P<op>" + _OPINION + r")\s*(?:\((?P<chg>" + _CHANGE + r")\))?", re.I)
RE_OPINION_LINE = re.compile(r"^(?P<op>" + _OPINION + r")\s*(?:\((?P<chg>" + _CHANGE + r")\))?\s*(?:$|현재|목표|\||I\b|·)", re.I | re.M)
RE_52W_HI = re.compile(r"52\s*주\s*(?:최고가|최고|고가|High)(?:\s*/\s*(?:최저가|최저|저가|Low))?[^\d\n]{0,14}(?P<hi>" + _NUM + r")(?:\s*원)?(?:\s*/\s*(?P<lo>" + _NUM + r"))?", re.I)
RE_52W_LO = re.compile(r"52\s*주\s*(?:최저가|최저|저가|Low)[^\d\n]{0,14}(?P<lo>" + _NUM + r")", re.I)
RE_TP_HISTORY = re.compile(r"^(?P<d>(?:20)?\d{2}[.\-/]\s?\d{1,2}[.\-/]\s?\d{1,2})\s+(?P<op>" + _OPINION + r")\s+(?P<tp>" + _NUM + r")", re.I | re.M)
RE_MKTCAP = re.compile(r"시가\s*총액\s*\(?(?P<unit>십억원|억원|조원|백만원)?\)?\s*[:：]?\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<unit2>십억원|억원|조원|백만원)?")

# ---------------------------------------------------------------------------- 추정치 표
YEAR_TOKEN = re.compile(r"(?<![\d.,])(?:FY)?(?P<y>20\d{2})\s?(?P<f>[AEFPaefp])?(?![\d,])")
QUARTER_TOKEN = re.compile(r"(?<![\dQ])(?P<q>[1-4])Q\s?(?P<y>\d{2})\s?(?P<f>[AEFPaefp])?(?![\d])")
UNIT_TOKEN = re.compile(r"(십억원|억원|조원|백만원|천원|bn|mn)")
NUM_TOKEN = re.compile(r"\(?-?\d[\d,]*(?:\.\d+)?\)?%?|적전|흑전|적지|흑지|N/?A|n/?a|(?<=\s)-(?=\s|$)")
ROW_LABELS: list[tuple[str, str]] = [
    ("revenue", r"매출액|매출(?!\s*총|\s*원가|\s*채권)|영업수익|순영업수익"),
    ("op", r"영업이익(?!률)"),
    ("np", r"지배주주\s*(?:지분\s*)?순이익|순이익\s*\(지배\)|당기순이익\s*\(지배(?:주주)?\)|당기순이익|순이익|지배순이익"),
    ("ebitda", r"EBITDA(?!\s*마진)"),
    ("opm", r"영업이익률|OPM|OP\s*margin"),
    ("eps", r"EPS(?!\s*(?:증감|성장|증가|growth))"),
    ("bps", r"BPS"),
    ("dps", r"DPS"),
    ("per", r"P/E|PER(?!\s*밴드)"),
    ("pbr", r"P/B|PBR(?!\s*밴드)"),
    ("roe", r"ROE"),
    ("ev_ebitda", r"EV/EBITDA|EV/\s*EBITDA"),
    ("div_yield", r"배당수익률"),
]
GROWTH_WORDS = re.compile(r"증가율|성장률|증감률|증감|YoY|QoQ|growth|\(%\s*YoY\)", re.I)
METRIC_LABEL = {"revenue": "매출액", "op": "영업이익", "np": "순이익", "ebitda": "EBITDA", "opm": "영업이익률", "eps": "EPS",
                "bps": "BPS", "dps": "DPS", "per": "PER", "pbr": "PBR", "roe": "ROE", "ev_ebitda": "EV/EBITDA",
                "div_yield": "배당수익률"}
RATIO_METRICS = {"opm", "per", "pbr", "roe", "ev_ebitda", "div_yield"}

# ---------------------------------------------------------------------------- 문장
DISCLAIMER = re.compile(r"당사는|본 자료|조사분석|Compliance|컴플라이언스|투자등급|투자의견 비율|비율공시|자료\s*[:：]|주\s*[:：]|"
                        r"리서치센터|리서치본부|저작권|법적 책임|증빙|사전 제공|이해관계|무단|배포|복제|www\.|@|괴리율|유효기간", re.I)
SENT_SPLIT = re.compile(r"(?<=[\.。!?])\s+(?=[A-Z가-힣①-⑩\d\(\[「“\"'])|(?<=다\.)|(?<=음\.)|(?<=함\.)|(?<=됨\.)|(?<=임\.)")
KIND_WORDS: dict[str, list[str]] = {
    "thesis": ["전망", "예상", "기대", "확대", "성장", "증가", "개선", "상향", "수혜", "점유율", "수주", "양산", "출하", "판가",
               "가격 인상", "회복", "턴어라운드", "레벨업", "최대", "호조", "본격화", "가속", "모멘텀", "재평가", "저평가"],
    "risk": ["리스크", "우려", "불확실", "둔화", "하락", "하향", "지연", "경쟁", "부담", "악화", "감소", "변동성", "규제", "관세",
             "환율", "적자", "충당금", "소송", "공급 과잉", "피크아웃", "역성장", "희석", "유상증자"],
    "catalyst": ["예정", "발표", "출시", "공시", "승인", "계약", "착공", "가동", "컨퍼런스", "실적 발표", "잠정실적", "확정실적",
                 "주주총회", "상장", "인증", "수주 발표", "학회", "입찰", "선정", "론칭", "양산 시작", "정책"],
    "valuation": ["PER", "PBR", "EV/EBITDA", "밸류에이션", "목표주가 산정", "Target", "할인", "프리미엄", "적용", "배", "SOTP", "DCF",
                  "역사적", "밴드", "멀티플", "P/E", "P/B"],
    "shareholder": ["배당", "자사주", "주주환원", "소각", "지배구조", "밸류업", "배당성향", "환원율"],
    "business": ["사업 구조", "매출 비중", "비즈니스", "사업부", "주력 제품", "매출 구성", "매출의", "영위", "핵심 고객", "제품 포트폴리오", "매출액의"],
}
TIME_WORDS = re.compile(r"\d{1,2}\s*월|[1-4]\s*분기|[1-4]Q\s*\d{2}|상반기|하반기|연내|연말|연초|20\d{2}\s*년|분기\s*말|내년|올해|이달|다음\s*달|[1-4]Q")


@dataclass
class Sentence:
    text: str
    kind: str
    score: float

    def key(self) -> str:
        return re.sub(r"[\s\d,\.%()]+", "", self.text)[:60]


@dataclass
class ReportFacts:
    """리포트 한 건에서 읽은 것. 못 읽은 것은 None."""

    path: Path
    broker: str | None = None
    analyst: str | None = None
    date: date | None = None
    ticker: str | None = None
    name: str | None = None
    title: str | None = None
    opinion: str | None = None          # BUY | HOLD | SELL | NR
    opinion_raw: str | None = None
    opinion_change: str | None = None   # 신규 | 유지 | 상향 | 하향
    target_price: float | None = None
    prev_target_price: float | None = None
    tp_change: str | None = None        # 신규 | 유지 | 상향 | 하향
    current_price: float | None = None
    price_date: date | None = None
    upside_pct: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    market_cap: float | None = None     # 원
    tp_history: list[tuple[date, str | None, float]] = field(default_factory=list)  # 투자의견 변동 내역 (최근순)
    estimates: dict[str, dict[str, float]] = field(default_factory=dict)   # metric → {"2026E": v}
    quarterly: dict[str, dict[str, float]] = field(default_factory=dict)   # metric → {"3Q26E": v}
    unit: str | None = None             # 추정치 표 금액 단위 (십억원 등)
    sentences: list[Sentence] = field(default_factory=list)
    chars: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        parts = [self.broker or "증권사 미상", self.date.isoformat() if self.date else "날짜 미상"]
        if self.target_price:
            parts.append(f"TP {self.target_price:,.0f}")
        if self.opinion:
            parts.append(self.opinion)
        return " · ".join(parts)

    def by_kind(self, kind: str, n: int = 5) -> list[str]:
        return [s.text for s in sorted((s for s in self.sentences if s.kind == kind), key=lambda s: -s.score)[:n]]

    def years(self) -> list[str]:
        ys: set[str] = set()
        for m in self.estimates.values():
            ys |= set(m)
        return sorted(ys)


# ---------------------------------------------------------------------------- 숫자·날짜 도우미
def _to_num(s: str) -> float | None:
    t = s.strip().replace(",", "")
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").rstrip("%")
    if t in ("", "-", "N/A", "n/a", "NA", "na") or re.fullmatch(r"적전|흑전|적지|흑지", t):
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _valid_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date(s: str, *, ref_year: int | None = None) -> date | None:
    """'2026.10.12' '2026-10-12' '2026년 10월 12일' '26.4.15' '10/17' '9.18' → date. 연도가 없으면 ref_year."""
    t = s.strip().strip("()").strip()
    m = RE_DATE4.search(t)
    if m:
        return _valid_date(int(m[1]), int(m[2]), int(m[3]))
    m = RE_DATE2.search(t)
    if m:
        return _valid_date(2000 + int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})\s*[./월]\s*(\d{1,2})\s*일?", t)
    if m and ref_year:
        return _valid_date(ref_year, int(m[1]), int(m[2]))
    return None


# ---------------------------------------------------------------------------- 개별 추출
def find_ticker(text: str) -> tuple[str | None, str | None]:
    """본문 앞부분에서 가장 자주 나오는 (종목명, 6자리 코드). 종목명은 힌트일 뿐 — 종목 목록으로 확정한다."""
    head = text[:6000]
    codes: Counter[str] = Counter()
    names: dict[str, Counter[str]] = {}
    for m in RE_TICKER.finditer(head):
        code = m["code"]
        codes[code] += 2
        nm = (m["name"] or "").strip(" -·.")
        if nm and not nm.isdigit() and len(nm) >= 2:
            names.setdefault(code, Counter())[nm] += 1
    for m in RE_TICKER2.finditer(head):
        code = m["code"] or m["code2"]
        if code:
            codes[code] += 1
    if not codes:
        return None, None
    code = codes.most_common(1)[0][0]
    name = names.get(code, Counter()).most_common(1)[0][0] if names.get(code) else None
    return name, code


def find_date(text: str, filename: str = "") -> date | None:
    head = text[:2500]
    cands: list[date] = []
    for m in RE_DATE4.finditer(head):
        d = _valid_date(int(m[1]), int(m[2]), int(m[3]))
        if d and 2000 <= d.year <= 2100:
            cands.append(d)
    if cands:
        return cands[0]
    for m in RE_DATE2.finditer(head):
        d = _valid_date(2000 + int(m[1]), int(m[2]), int(m[3]))
        if d:
            return d
    m = RE_FILENAME_DATE.search(filename)
    if m:
        d = _valid_date(int(m[1]), int(m[2]), int(m[3]))
        if d:
            return d
    m = RE_DATE_YM.search(head)
    if m:
        return _valid_date(int(m[1]), int(m[2]), 1)
    return None


def find_broker(text: str) -> str | None:
    head = text[:8000]
    score: Counter[str] = Counter()
    for b in BROKERS:
        n = head.count(b)
        if n:
            score[BROKER_ALIASES.get(b, b)] += n * (2 if b.endswith("증권") or b.endswith("투자") else 1)
    for m in RE_EMAIL.finditer(head):
        dom = m[1].lower()
        for key, b in EMAIL_DOMAINS.items():
            if key in dom:
                score[b] += 3
    if "IBKS" in head:
        score["IBK투자증권"] += 2
    return score.most_common(1)[0][0] if score else None


def normalize_opinion(raw: str | None) -> str | None:
    if not raw:
        return None
    t = re.sub(r"\s+", "", raw).lower()
    if t in ("notrated", "nr", "n/r", "n/a", "없음", "투자의견없음"):
        return "NR"
    if t.startswith(("strongbuy", "buy", "매수", "적극매수", "outperform", "overweight", "비중확대")):
        return "BUY"
    if t.startswith(("tradingbuy", "hold", "보유", "중립", "neutral", "marketperform")):
        return "HOLD"
    if t.startswith(("sell", "매도", "비중축소", "underperform", "reduce")):
        return "SELL"
    return None


def _norm_change(raw: str | None) -> str | None:
    if not raw:
        return None
    t = raw.strip().lower()
    return {"m": "유지", "maintain": "유지", "유지": "유지", "n": "신규", "initiate": "신규", "initiation": "신규", "신규": "신규",
            "신규편입": "신규", "u": "상향", "upgrade": "상향", "상향": "상향", "d": "하향", "downgrade": "하향", "하향": "하향"}.get(t)


def find_opinion(text: str) -> tuple[str | None, str | None, str | None]:
    """(정규화 의견, 원문, 변경). 표지(앞 4000자)의 '투자의견 ○○' 를 우선, 없으면 줄 첫머리의 BUY/매수."""
    head = text[:4000]
    for rx in (RE_OPINION, RE_OPINION_LINE):
        for m in rx.finditer(head):
            op = normalize_opinion(m["op"])
            if op:
                return op, m["op"].strip(), _norm_change(m["chg"])
    for m in RE_OPINION.finditer(text):
        op = normalize_opinion(m["op"])
        if op:
            return op, m["op"].strip(), _norm_change(m["chg"])
    return None, None, None


def _pick_price(cands: list[tuple[int, float]], *, head_chars: int, lo: float, hi: float) -> float | None:
    """(위치, 값) 후보들 → 표지 안의 최다 빈도값, 동률이면 앞의 것."""
    good = [(pos, v) for pos, v in cands if lo <= v <= hi]
    if not good:
        return None
    head = [(pos, v) for pos, v in good if pos < head_chars] or good
    freq = Counter(v for _, v in head)
    best = max(freq.values())
    for _pos, v in head:
        if freq[v] == best:
            return v
    return None


def find_target_price(text: str) -> tuple[float | None, float | None, str | None]:
    """(목표주가, 직전 목표주가, 변경). 표지의 '목표주가 ○○○원' 을 우선한다."""
    cands: list[tuple[int, float]] = []
    for m in RE_TP.finditer(text):
        v = _to_num(m["num"])
        if v is not None:
            cands.append((m.start(), v))
    for m in RE_TP_MAN.finditer(text):
        cands.append((m.start(), float(m["man"]) * 10000))
    if not cands:
        for m in RE_TP_EN.finditer(text):
            v = _to_num(m["num"])
            if v is not None:
                cands.append((m.start(), v))
    tp = _pick_price(cands, head_chars=4000, lo=100, hi=50_000_000)
    prev: float | None = None
    change: str | None = None
    m = RE_TP_ARROW.search(text[:8000])
    if m:
        p, n = _to_num(m["prev"]), _to_num(m["new"])
        if p and n and 100 <= p <= 50_000_000:
            prev = p
            if tp is None or (n and abs(n - tp) / max(tp, 1) > 0.5):
                tp = n
    if prev is None:
        for m in RE_TP_PREV.finditer(text[:8000]):
            v = _to_num(m["num"])
            if v and 100 <= v <= 50_000_000 and v != tp:
                prev = v
                break
    m = RE_TP_CHANGE.search(text[:6000])
    if m:
        change = _norm_change(m["chg"] or m["chg2"])
    if change is None and tp is not None and prev is not None:
        change = "상향" if tp > prev else "하향" if tp < prev else "유지"
    if prev is None and change == "유지" and tp is not None:
        prev = tp
    return tp, prev, change


def find_current_price(text: str, *, ref_year: int | None = None, target_price: float | None = None) -> tuple[float | None, date | None]:
    cands: list[tuple[int, float, str | None]] = []
    for m in RE_PRICE_WITH_DATE_AFTER.finditer(text[:6000]):
        v = _to_num(m["num"])
        if v is not None:
            cands.append((m.start(), v, m["d"]))
    for m in RE_PRICE.finditer(text[:12000]):
        v = _to_num(m["num"])
        if v is None:
            continue
        label = m.group(0)
        # '목표주가' 바로 뒤의 '주가' 에 걸리지 않도록: 앞 6글자에 '목표' 가 있으면 제외
        if "목표" in text[max(0, m.start() - 6):m.start()]:
            continue
        if label.strip().startswith("주가") and m["d"] is None:
            continue  # 날짜 없는 '주가 123,456' 은 차트 축일 가능성이 크다
        cands.append((m.start(), v, m["d"]))
    if not cands:
        return None, None
    price = _pick_price([(p, v) for p, v, _ in cands], head_chars=4000, lo=100, hi=50_000_000)
    if price is None:
        return None, None
    if target_price and price == target_price:
        others = [(p, v) for p, v, _ in cands if v != target_price]
        price = _pick_price(others, head_chars=4000, lo=100, hi=50_000_000) or price
    d_raw = next((d for p, v, d in cands if v == price and d), None)
    return price, parse_date(d_raw, ref_year=ref_year) if d_raw else None


def find_upside(text: str) -> float | None:
    m = RE_UPSIDE.search(text[:6000])
    return float(m["pct"]) if m else None


def find_52w(text: str) -> tuple[float | None, float | None]:
    head = text[:8000]
    hi: float | None = None
    lo: float | None = None
    m = RE_52W_HI.search(head)
    if m:
        hi = _to_num(m["hi"])
        lo = _to_num(m["lo"]) if m["lo"] else None
    if lo is None:
        m2 = RE_52W_LO.search(head)
        if m2:
            lo = _to_num(m2["lo"])
    if hi and lo and lo > hi:
        hi, lo = lo, hi
    return hi, lo


def find_tp_history(text: str, *, ref_year: int | None = None) -> list[tuple[date, str | None, float]]:
    """'투자의견 변동 내역' 표: (날짜, 의견, 목표주가) 최근순. 직전 목표주가와 리비전 방향의 근거."""
    out: list[tuple[date, str | None, float]] = []
    for m in RE_TP_HISTORY.finditer(text):
        d = parse_date(m["d"], ref_year=ref_year)
        tp = _to_num(m["tp"])
        if d and tp and 100 <= tp <= 50_000_000:
            out.append((d, normalize_opinion(m["op"]), tp))
    out.sort(key=lambda r: r[0], reverse=True)
    return out


def find_market_cap(text: str) -> float | None:
    m = RE_MKTCAP.search(text[:8000])
    if not m:
        return None
    v = _to_num(m["num"])
    if v is None:
        return None
    unit = m["unit"] or m["unit2"]
    return v * {"십억원": 1e9, "억원": 1e8, "조원": 1e12, "백만원": 1e6}.get(unit or "", 1.0)


def find_title(text: str) -> str | None:
    """표지 첫 12줄 가운데 날짜·증권사·코드·수치·문장이 아닌 첫 한국어 줄 — 제목일 확률이 가장 높다."""
    for ln in text.split("\n")[:12]:
        s = ln.strip()
        if len(s) < 6 or len(s) > 48 or RE_DATE4.search(s) or RE_DATE2.search(s) or re.search(r"\(\s*A?\d{6}", s):
            continue
        if re.search(r"[다음함됨임요]\s*\.\s*$|\.\s*$", s):
            continue  # 문장은 제목이 아니다
        if re.search(r"\d{2,3},\d{3}|투자의견|목표주가|현재주가|상승여력|시가총액|증권|Analyst|RA\s|@|Report|Brief|Comment|Company|Rating|\d+\s*%", s, re.I):
            continue
        if len(re.findall(r"[가-힣]", s)) < 4:
            continue
        return s
    return None


# ---------------------------------------------------------------------------- 추정치 표
def _year_label(y: int, flag: str | None, ref_year: int) -> str:
    f = (flag or "").upper()
    if f == "A":
        return f"{y}A"
    if f in ("E", "F", "P"):
        return f"{y}E"
    return f"{y}A" if y < ref_year else f"{y}E"


def _header_years(line: str, ref_year: int) -> list[str] | None:
    toks = [(m.start(), int(m["y"]), m["f"]) for m in YEAR_TOKEN.finditer(line)]
    toks = [(p, y, f) for p, y, f in toks if 2000 <= y <= 2100]
    if len(toks) < 3:
        return None
    # 표 머리: 연도가 줄의 대부분을 차지하고, 오름차순 연속
    words = [w for w in line.split() if w]
    if len(toks) / max(1, len(words)) < 0.35:
        return None
    years = [y for _, y, _ in toks]
    if any(b - a not in (0, 1) for a, b in zip(years, years[1:], strict=False)):
        # 두 표가 나란히 ("2024 2025 2026E 2027E 2024 2025 2026E 2027E") 면 앞 묶음만
        first: list[tuple[int, int, str | None]] = []
        for t in toks:
            if first and t[1] <= first[-1][1]:
                break
            first.append(t)
        if len(first) < 3:
            return None
        toks = first
    return [_year_label(y, f, ref_year) for _, y, f in toks]


def _header_quarters(line: str, ref_date: date) -> list[str] | None:
    toks = list(QUARTER_TOKEN.finditer(line))
    if len(toks) < 3:
        return None
    out: list[str] = []
    for m in toks:
        y = 2000 + int(m["y"])
        q = int(m["q"])
        f = (m["f"] or "").upper()
        if f in ("", "A", "P"):
            # P(잠정) 는 실적이다. 표기가 없으면 분기 말이 기준일 이전인가로 가른다
            quarter_end = date(y, q * 3, 1)
            f = "A" if (f in ("A", "P") or quarter_end < ref_date) else "E"
        elif f == "F":
            f = "E"
        lab = f"{m['q']}Q{str(y)[2:]}{f}"
        if out and lab in out:
            break
        out.append(lab)
    return out if len(out) >= 3 else None


def _row_numbers(rest: str, n: int) -> list[float | None] | None:
    toks = NUM_TOKEN.findall(rest)
    if len(toks) < max(2, n - 1):
        return None
    vals = [_to_num(t) for t in toks[:n]]
    while len(vals) < n:
        vals.append(None)
    return vals


def parse_estimate_tables(text: str, *, ref_date: date) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]], str | None]:
    """연도별 표 → estimates[metric][yearlabel], 분기 표 → quarterly, 금액 단위."""
    ref_year = ref_date.year
    lines = text.split("\n")
    estimates: dict[str, dict[str, float]] = {}
    quarterly: dict[str, dict[str, float]] = {}
    units: Counter[str] = Counter()
    i = 0
    while i < len(lines):
        line = lines[i]
        cols = _header_years(line, ref_year)
        target = estimates
        if cols is None:
            cols = _header_quarters(line, ref_date)
            target = quarterly
        if cols is None:
            i += 1
            continue
        ctx = " ".join(lines[max(0, i - 3):i + 1])
        um = UNIT_TOKEN.search(ctx)
        unit = um[1] if um else None
        got = 0
        j = i + 1
        while j < len(lines) and j < i + 70:
            ln = lines[j]
            if _header_years(ln, ref_year) or _header_quarters(ln, ref_date):
                break
            for metric, pat in ROW_LABELS:
                # 라벨 뒤 괄호는 단위 (원)(배)(%)(십억원) 만 — '(5)' 는 음수다
                m = re.match(r"^(?:" + pat + r")\s*(?:\((?:[^)\d]{1,12})\))?\s*(?:\(지배\))?\s*(?P<rest>.*)$", ln, re.I)
                if not m:
                    continue
                label_part = ln[:m.start("rest")]
                if GROWTH_WORDS.search(label_part):
                    break
                vals = _row_numbers(m["rest"], len(cols))
                if vals is None:
                    break
                dst = target.setdefault(metric, {})
                for col, v in zip(cols, vals, strict=False):
                    if v is not None and col not in dst:
                        dst[col] = v
                        got += 1
                if unit and target is estimates and metric in ("revenue", "op", "np", "ebitda"):
                    units[unit] += 1
                break
            j += 1
        i = j if got else i + 1
    unit = units.most_common(1)[0][0] if units else None
    return estimates, quarterly, unit


# ---------------------------------------------------------------------------- 문장
def paragraphs(text: str) -> list[str]:
    """줄바꿈으로 잘린 문장을 다시 붙인다. 문장 끝(다. 음. .)이 아닌 줄은 다음 줄과 이어진 것으로 본다."""
    out: list[str] = []
    buf = ""
    for ln in text.split("\n"):
        s = ln.strip()
        if not s:
            if buf:
                out.append(buf)
                buf = ""
            continue
        buf = (buf + " " + s) if buf else s
        if re.search(r"[\.。!?]\s*$|[다음함됨임요]\s*$", s):
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def _score(sent: str, kind: str, name: str | None) -> float:
    words = KIND_WORDS[kind]
    hits = sum(1 for w in words if w in sent)
    if hits == 0:
        return 0.0
    score = float(hits)
    if re.search(r"\d", sent):
        score += 1.0
    if name and name in sent:
        score += 1.0
    if kind == "catalyst" and not TIME_WORDS.search(sent):
        return 0.0
    if kind == "thesis" and re.search(r"전망|예상|판단|기대", sent):
        score += 0.5
    # 너무 길거나 짧은 문장은 감점
    n = len(sent)
    if n < 25 or n > 220:
        score -= 1.0
    return score


def extract_sentences(text: str, *, name: str | None = None, limit_per_kind: int = 8) -> list[Sentence]:
    seen: set[str] = set()
    cands: list[Sentence] = []
    for para in paragraphs(text):
        if DISCLAIMER.search(para):
            continue
        for raw in SENT_SPLIT.split(para):
            s = raw.strip(" ·•▶■◆-–—*①②③④⑤⑥⑦⑧⑨⑩")
            s = re.sub(r"\s+", " ", s)
            if len(s) < 15 or len(s) > 260 or len(re.findall(r"[가-힣]", s)) < 6:
                continue
            if DISCLAIMER.search(s) or re.search(r"\d{1,3},\d{3}\s+\d{1,3},\d{3}\s+\d", s):
                continue  # 표의 숫자 줄
            k = re.sub(r"[\s\d,\.%()]+", "", s)[:60]
            if k in seen:
                continue
            seen.add(k)
            for kind in KIND_WORDS:
                sc = _score(s, kind, name)
                if sc > 0:
                    cands.append(Sentence(s, kind, sc))
    out: list[Sentence] = []
    for kind in KIND_WORDS:
        ks = sorted((c for c in cands if c.kind == kind), key=lambda c: -c.score)[:limit_per_kind]
        out.extend(ks)
    return out


# ---------------------------------------------------------------------------- 진입점
def extract_facts(doc: ReportDoc, *, hint_ticker: str | None = None, today: date | None = None) -> ReportFacts:
    text = doc.text
    f = ReportFacts(path=doc.path, chars=len(text), warnings=list(doc.warnings))
    if doc.is_empty:
        f.warnings.append(f"{doc.name}: 읽을 글자가 없어 사실을 뽑지 못했다")
        return f
    f.name, f.ticker = find_ticker(text)
    if hint_ticker and f.ticker and f.ticker != hint_ticker:
        f.warnings.append(f"{doc.name}: 본문 종목코드 {f.ticker} ≠ 지정 {hint_ticker}")
    f.date = find_date(text, doc.name)
    ref_year = (f.date or today or date.today()).year
    f.broker = find_broker(text)
    m = RE_ANALYST.search(text[:4000])
    f.analyst = m[1] if m else None
    f.title = find_title(text)
    f.opinion, f.opinion_raw, f.opinion_change = find_opinion(text)
    f.target_price, f.prev_target_price, f.tp_change = find_target_price(text)
    # 투자의견 변동 내역 표가 있으면 직전 목표주가·변경을 거기서 확정한다 (표지 문구보다 믿을 만하다)
    f.tp_history = find_tp_history(text, ref_year=ref_year)
    if f.target_price is not None:
        earlier = [tp for d, _, tp in f.tp_history if (f.date is None or d < f.date) and tp != f.target_price]
        same_day = [tp for d, _, tp in f.tp_history if f.date is not None and d >= f.date]
        if f.prev_target_price is None and earlier:
            f.prev_target_price = earlier[0]
            if f.tp_change is None or f.tp_change == "유지" and f.prev_target_price != f.target_price:
                f.tp_change = "상향" if f.target_price > f.prev_target_price else "하향"
        elif f.prev_target_price is None and same_day and not earlier and len(f.tp_history) == 1:
            f.tp_change = f.tp_change or "신규"
    f.current_price, f.price_date = find_current_price(text, ref_year=ref_year, target_price=f.target_price)
    if f.price_date is None and f.current_price is not None:
        f.price_date = f.date
    f.upside_pct = find_upside(text)
    if f.upside_pct is None and f.target_price and f.current_price:
        f.upside_pct = round((f.target_price / f.current_price - 1) * 100, 1)
    f.high_52w, f.low_52w = find_52w(text)
    f.market_cap = find_market_cap(text)
    f.estimates, f.quarterly, f.unit = parse_estimate_tables(text, ref_date=f.date or today or date.today())
    f.sentences = extract_sentences(text, name=f.name)
    if f.target_price is None and f.opinion != "NR":
        f.warnings.append(f"{doc.name}: 목표주가를 찾지 못했다")
    if f.current_price is None:
        f.warnings.append(f"{doc.name}: 현재주가를 찾지 못했다")
    if not f.estimates:
        f.warnings.append(f"{doc.name}: 연도별 추정치 표를 찾지 못했다")
    return f


__all__ = ["ReportFacts", "Sentence", "extract_facts", "find_ticker", "find_date", "find_broker", "find_opinion",
           "find_target_price", "find_current_price", "find_upside", "find_52w", "find_tp_history", "parse_estimate_tables",
           "extract_sentences", "paragraphs", "normalize_opinion", "parse_date", "METRIC_LABEL", "RATIO_METRICS", "KIND_WORDS"]
