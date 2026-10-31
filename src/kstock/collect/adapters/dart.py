"""OpenDART 공시 어댑터 — 촉매 일정을 **공시 본문**에서 읽는다 (2026-10-30 A6 보강).

- 포털 https://opendart.fss.or.kr — API 키는 환경변수 `DART_API_KEY` (리서치 저장소의 .env 에도 같은 키가 있다)
- 쓰는 엔드포인트 (표준 라이브러리 urllib, 짧은 타임아웃)

      공시검색   GET /api/list.json?crtfc_key=…&corp_code=…&bgn_de=YYYYMMDD&end_de=…&page_no=…&page_count=100
      공시원문   GET /api/document.xml?crtfc_key=…&rcept_no=…        → zip (안에 XML 하나 이상)
      고유번호   GET /api/corpCode.xml                                 → zip (corpCode.xml)

- 목록은 리서치 DB `disclosures` 표가 1차 출처다 (이름·접수번호·날짜). DB 가 낡았거나 없을 때만 list.json 을 부른다.
- 본문은 접수번호마다 한 번만 받아 `cache_dir/<rcept_no>.txt` 에 둔다. 공시는 안 바뀐다 — 정정은 새 접수번호로 온다.
- 날짜를 읽는 공시는 셋뿐이다. 다른 공시는 본문을 받지 않는다 (「깃발 선 것만 본문」).

      기업설명회(IR)개최(안내공시)     일시 → 실적발표·IR
      유상증자결정 (주요사항보고서)    납입일 · 신주 상장예정일 · 발행가 확정(기준)일 · 청약일 · 권리락일 · 배정기준일
      주주총회소집결의 · 소집공고      일시 → 주주총회

  표 구조를 믿지 않는다 — 태그를 벗긴 평문에서 「라벨 … 날짜」 를 정규식으로 찾는다. 같은 종류의 날짜가
  정정 공시로 다시 오면 **나중 접수번호가 이긴다.**
"""

from __future__ import annotations

import io
import json
import os
import re
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from ...models import Event, EventCertainty, Source
from ..base import DisclosureSource, NotConfigured, RawDisclosure

BASE = "https://opendart.fss.or.kr/api"
DART_VIEW = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

#: 본문을 읽는 공시 — (이름에 들어 있는 말, 종류)
WATCH_KINDS: tuple[tuple[str, str], ...] = (
    ("기업설명회", "ir"),
    ("유상증자결정", "rights"),
    ("주주총회소집", "agm"),
)


def kind_of(report_nm: str) -> str | None:
    """공시 이름 → 읽을 종류. 자회사·종속회사 건은 그 회사 일정이 아니므로 None."""
    nm = report_nm or ""
    if any(k in nm for k in ("자회사", "종속회사")):
        return None
    for key, kind in WATCH_KINDS:
        if key in nm:
            return kind
    return None


def dart_cache_dir() -> Path:
    """공시 본문 캐시 — 이 저장소의 data/dart (환경변수 KSTOCK_DART_CACHE 로 바꿀 수 있다)."""
    v = os.environ.get("KSTOCK_DART_CACHE", "").strip()
    if v:
        return Path(v).expanduser()
    from ...env import ROOT

    return ROOT / "data" / "dart"


def dart_api_key(research_db: Path | None = None) -> str | None:
    """`DART_API_KEY` — 환경변수가 없으면 리서치 저장소의 .env (DB 의 세 단계 위) 에서 그 한 줄만 읽는다."""
    key = os.environ.get("DART_API_KEY", "").strip()
    if key:
        return key
    if research_db is None:
        return None
    for root in (research_db.parent.parent.parent, research_db.parent.parent, research_db.parent):
        env = root / ".env"
        if env.is_file():
            try:
                for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.strip().startswith("DART_API_KEY="):
                        v = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if v:
                            return v
            except OSError:
                continue
    return None


# ---------------------------------------------------------------------------- HTTP
def _http_get(url: str, *, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 — https 고정 주소
        return res.read()


def _mask(url: str) -> str:
    return re.sub(r"crtfc_key=[^&]+", "crtfc_key=***", url)


class DartError(RuntimeError):
    pass


class OpenDart:
    """OpenDART 호출기. `fetch` 를 바꿔 끼우면 시험에서 네트워크 없이 돈다."""

    def __init__(self, api_key: str | None = None, *, cache_dir: str | Path | None = None, timeout: float = 15.0,
                 fetch: Callable[[str], bytes] | None = None):
        self.api_key = (api_key or os.environ.get("DART_API_KEY") or "").strip()
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.timeout = timeout
        self._fetch = fetch or (lambda url: _http_get(url, timeout=self.timeout))
        self.calls = 0

    def _require(self) -> str:
        if not self.api_key:
            raise NotConfigured("DART_API_KEY 가 없습니다 (.env 참고)")
        return self.api_key

    def _url(self, path: str, **params: str) -> str:
        q = {"crtfc_key": self._require(), **params}
        return f"{BASE}/{path}?" + urllib.parse.urlencode(q)

    def _get(self, path: str, **params: str) -> bytes:
        url = self._url(path, **params)
        self.calls += 1
        try:
            return self._fetch(url)
        except Exception as e:  # noqa: BLE001
            raise DartError(f"{_mask(url)}: {type(e).__name__}: {str(e)[:120]}") from e

    # ---- 목록
    def list_json(self, corp_code: str, bgn: date, end: date, *, page_count: int = 100, max_pages: int = 5) -> list[dict]:
        out: list[dict] = []
        for page in range(1, max_pages + 1):
            raw = self._get("list.json", corp_code=corp_code, bgn_de=bgn.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"),
                            page_no=str(page), page_count=str(page_count))
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise DartError(f"list.json 응답이 JSON 이 아니다 ({e})") from e
            status = str(data.get("status", ""))
            if status == "013":   # 조회된 데이터가 없습니다
                break
            if status != "000":
                raise DartError(f"list.json status {status}: {data.get('message', '')}")
            rows = data.get("list") or []
            out += rows
            if page >= int(data.get("total_page") or 1) or not rows:
                break
        return out

    # ---- 본문
    def document_text(self, rcept_no: str) -> str:
        """공시 원문의 평문 — 태그를 벗기고 셀 사이에 공백을 둔다. 캐시가 있으면 네트워크를 안 탄다."""
        cache = (self.cache_dir / f"{rcept_no}.txt") if self.cache_dir else None
        if cache is not None and cache.is_file():
            return cache.read_text(encoding="utf-8")
        raw = self._get("document.xml", rcept_no=rcept_no)
        text = zip_to_text(raw)
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(text, encoding="utf-8")
        return text

    # ---- 고유번호 (DB 에 없을 때만)
    def corp_code(self, stock_code: str) -> str | None:
        raw = self._get("corpCode.xml")
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                xml = z.read(z.namelist()[0]).decode("utf-8", errors="ignore")
        except (zipfile.BadZipFile, IndexError) as e:
            raise DartError(f"corpCode.xml 이 zip 이 아니다 ({e})") from e
        m = re.search(r"<corp_code>(\d+)</corp_code>\s*<corp_name>[^<]*</corp_name>\s*<stock_code>\s*" + re.escape(stock_code) + r"\s*</stock_code>", xml)
        return m[1] if m else None


def zip_to_text(raw: bytes) -> str:
    """document.xml 응답(zip) → 평문. zip 이 아니면(오류 JSON 등) 그대로 디코드해 돌려준다 — 호출한 쪽이 status 를 본다."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            parts = [z.read(n).decode("utf-8", errors="ignore") for n in z.namelist() if n.lower().endswith((".xml", ".html", ".htm", ".txt"))]
    except zipfile.BadZipFile:
        parts = [raw.decode("utf-8", errors="ignore")]
    return "\n".join(strip_tags(p) for p in parts)


def strip_tags(xml: str) -> str:
    """태그를 벗기되 셀·줄 경계는 공백/줄바꿈으로 남긴다. 숫자·날짜가 붙어 버리면 못 읽는다."""
    s = re.sub(r"<!--.*?-->", " ", xml, flags=re.S)
    s = re.sub(r"</?(TR|P|BR|TABLE|TABLE-GROUP|TITLE|SECTION-\d|LIBRARY)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"</?(TD|TH|TE|TU|SPAN|COL)[^>]*>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    lines = [" ".join(ln.split()) for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln)


# ---------------------------------------------------------------------------- 본문에서 날짜 읽기
_DATE = r"(20\d{2})\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})\s*일?"
_TIME = r"(\d{1,2})\s*[:시]\s*(\d{2})?"


def _date_of(m: re.Match) -> date | None:
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


_ITEM_LINE = re.compile(r"^\s*(?:\d{1,2}(?:-\d)?\.|[가-힣]\.|※|\*|[-–•])\s*\S")   # 「10. 신주의 배당기산일」 「가. 개요」 「- 확정 발행가액…」 — 새 항목의 시작


def _is_item_line(line: str) -> bool:
    return bool(_ITEM_LINE.match(line))


def _find_after(text: str, label_pat: str, *, max_lines: int = 4) -> tuple[date, str] | None:
    """「라벨 → 날짜」 를 **줄 단위**로 읽는다 (2026-10-31 실측으로 고침).

    DART 원문은 표의 셀마다 한 줄이다. 라벨 줄 뒤에 날짜 줄이 이어지고, 정정공시의 「정정사항」 표에서는
    사유 줄 다음에 **정정 전 · 정정 후 날짜가 연달아** 온다. 그래서 라벨 뒤 연속 날짜 줄을 모아 둘 이상이면
    **마지막(정정 후)** 을 쓴다 — 첫 날짜를 집으면 정정 전(이미 지난 날짜) 을 읽어 사건이 사라진다.
    새 항목 줄(「10. …」) 이나 날짜 뒤의 날짜 아닌 줄에서 멈춘다 — 다음 항목(배당기산일) 의 날짜를 섞지 않는다.
    """
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        lm = re.search(label_pat, ln)
        if not lm:
            continue
        segs = [ln[lm.end():]] + lines[i + 1: i + 1 + max_lines]
        found: list[tuple[int, re.Match, str]] = []
        for j, sl in enumerate(segs):
            if j > 0 and (_is_item_line(sl) or (found and found[-1][0] == 0)):
                break   # 라벨 줄에 날짜가 있었으면 그 줄로 끝 (한 줄 한 행 꼴) — 다음 행의 날짜를 섞지 않는다
            hits = list(re.finditer(_DATE, sl))
            if hits:
                found += [(j, h, sl) for h in hits]
            elif found:
                break
        if not found:
            continue
        j, dm, sl = found[-1] if len(found) >= 2 else found[0]
        d = _date_of(dm)
        if d is None:
            continue
        tail = sl[dm.end(): dm.end() + 30] + " " + (segs[j + 1] if j + 1 < len(segs) else "")
        tm = re.search(_TIME, tail)
        when = f"{int(tm[1]):02d}:{tm[2] or '00'}" if tm and 0 <= int(tm[1]) < 24 else ""
        return d, when
    return None


@dataclass(frozen=True)
class Found:
    kind: str          # ir | 납입일 | 신주상장예정일 | 발행가확정일 | 청약일 | 권리락일 | 배정기준일 | agm
    date: date
    time: str = ""
    label: str = ""


IR_LABEL = r"(?:행사\s*)?일시|일\s*시|개최\s*일시|개최일"
RIGHTS_LABELS: tuple[tuple[str, str, str], ...] = (
    # (종류, 라벨 정규식, 사람이 읽을 말)
    ("납입일", r"납\s*입\s*일", "유상증자 납입일"),
    ("신주상장예정일", r"신주\s*(?:의\s*)?(?:상장\s*예정일|상장예정일|상장일)", "신주 상장 예정일"),
    ("발행가확정일", r"(?:발행가(?:액)?\s*확정\s*(?:예정)?일|기준주가\s*(?:산정\s*)?기준일|확정\s*발행가(?:액)?\s*산정\s*기준일)", "발행가 확정 기준일"),
    ("청약일", r"청\s*약\s*일", "청약일"),
    ("권리락일", r"권리락\s*일", "권리락일"),
    ("배정기준일", r"(?:신주\s*)?배정\s*기준일", "신주 배정 기준일"),
)
AGM_LABEL = r"주주총회\s*(?:일시|일자|개최\s*일시)|일\s*시|개최\s*일시"


IR_PURPOSE = re.compile(r"개최\s*목적|목\s*적|주요\s*설명회\s*내용|설명회\s*내용|제\s*목")


def ir_purpose(text: str) -> str:
    """IR 안내의 목적 한 줄 — 「3. 개최목적」 다음 줄(「2026년 3분기 실적발표」 / 「9월 컨퍼런스 참석」). 같은 줄에 있으면 그것."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m = IR_PURPOSE.search(ln)
        if not m:
            continue
        rest = ln[m.end():].strip(" :：")
        if len(rest) >= 3:
            return " ".join(rest.split())[:60]
        if i + 1 < len(lines) and lines[i + 1].strip() and not _is_item_line(lines[i + 1]):
            return " ".join(lines[i + 1].split())[:60]
    return ""


def parse_ir_notice(text: str) -> Found | None:
    hit = _find_after(text, IR_LABEL)
    return Found("ir", hit[0], hit[1], ir_purpose(text)) if hit else None


def parse_rights_offering(text: str) -> list[Found]:
    out: list[Found] = []
    seen: set[str] = set()
    for kind, pat, label in RIGHTS_LABELS:
        hit = _find_after(text, pat)
        if hit and kind not in seen:
            seen.add(kind)
            out.append(Found(kind, hit[0], hit[1], label))
    return out


def parse_agm(text: str) -> Found | None:
    hit = _find_after(text, AGM_LABEL)
    return Found("agm", hit[0], hit[1], "주주총회") if hit else None


def parse_document(kind: str, text: str) -> list[Found]:
    if kind == "ir":
        f = parse_ir_notice(text)
        return [f] if f else []
    if kind == "rights":
        return parse_rights_offering(text)
    if kind == "agm":
        f = parse_agm(text)
        return [f] if f else []
    return []


# ---------------------------------------------------------------------------- 이벤트 만들기
@dataclass(frozen=True)
class DartItem:
    rcept_no: str
    rcept_dt: date
    report_nm: str

    @property
    def url(self) -> str:
        return DART_VIEW.format(rcept_no=self.rcept_no)

    @property
    def corrected(self) -> bool:
        return "정정" in self.report_nm


#: 종류별로 반드시 읽혀야 하는 항목 — 없으면 메모에 남긴다
_EXPECTED: dict[str, tuple[str, ...]] = {"ir": ("ir",), "rights": ("납입일",), "agm": ("agm",)}

#: 공시 종류별 — 중요도 · 확실성 · 사전 확인 · 판단 규칙 (사람이 고칠 초안)
_SPEC: dict[str, dict] = {
    "ir": {"materiality": "H", "certainty": EventCertainty.공시확정, "fact": "실적발표·IR",
           "precheck": ["컨센서스 대비 매출·영업이익", "사업부별 성장률", "다음 분기·연간 가이던스"],
           "rule": "발표 뒤 A1 드라이버 현재값·A3 EPS 를 갱신하고, 판단 변경 근거를 A8.2 에 적는다"},
    "납입일": {"materiality": "H", "certainty": EventCertainty.공시확정, "fact": "유상증자 납입",
             "precheck": ["납입 완료 공시", "최종 신주 수·희석률", "자금 사용 목적"],
             "rule": "납입 완료만으로 판단을 바꾸지 않는다 — 발행가는 주가 하한이 아니다"},
    "신주상장예정일": {"materiality": "M", "certainty": EventCertainty.공시상예정, "fact": "신주 상장 예정",
                 "precheck": ["보호예수·의무보유 조건", "유통 가능 물량"], "rule": "상장일 즉시 매도 가능 물량으로 취급하지 않는다"},
    "발행가확정일": {"materiality": "M", "certainty": EventCertainty.공시확정, "fact": "발행가 확정 기준일",
                "precheck": ["확정 발행가", "최종 신주 수 → 희석률 재계산"], "rule": None},
    "청약일": {"materiality": "M", "certainty": EventCertainty.공시확정, "fact": "유상증자 청약일", "precheck": ["청약률"], "rule": None},
    "권리락일": {"materiality": "M", "certainty": EventCertainty.공시확정, "fact": "권리락일", "precheck": ["권리락 기준가"], "rule": None},
    "배정기준일": {"materiality": "L", "certainty": EventCertainty.공시확정, "fact": "신주 배정 기준일", "precheck": [], "rule": None},
    "agm": {"materiality": "M", "certainty": EventCertainty.공시확정, "fact": "주주총회",
            "precheck": ["안건 (정관·이사 선임·배당)", "주주환원 발언"], "rule": None},
}


def events_from_items(items: list[DartItem], text_of: Callable[[str], str], *, today: date, name: str | None = None,
                      affects_ir: list[str] | None = None) -> tuple[list[Event], list[str]]:
    """공시 목록 → 이벤트. 본문은 `text_of(rcept_no)` 로 받는다 (OpenDart.document_text 또는 시험용 사전).

    같은 종류의 날짜는 **나중 접수번호가 이긴다** (정정). 오늘 이전 날짜는 버린다 — 촉매가 아니라 사실 로그다.
    """
    notes: list[str] = []
    latest: dict[str, tuple[Found, DartItem]] = {}
    for it in sorted(items, key=lambda x: x.rcept_no):
        kind = kind_of(it.report_nm)
        if kind is None:
            continue
        try:
            text = text_of(it.rcept_no)
        except Exception as e:  # noqa: BLE001 — 한 건 실패가 전체를 막지 않는다
            notes.append(f"{it.rcept_no} {it.report_nm[:20]}: 본문 못 받음 ({type(e).__name__}: {str(e)[:60]})")
            continue
        found = parse_document(kind, text)
        if not found:
            notes.append(f"{it.rcept_no} {it.report_nm[:24]}: 본문에서 날짜를 못 읽었다 — 원문 확인")
            continue
        # 읽었는데 **핵심 항목이 빠졌으면** 그것도 적는다 — 「상장예정일만 읽고 납입일을 놓친」 2026-10-31 사고가 조용히 지나가지 않게
        for need in _EXPECTED.get(kind, ()):
            if not any(f.kind == need for f in found):
                notes.append(f"{it.rcept_no} {it.report_nm[:24]}: {need} 을 못 읽었다 (라벨 불일치 또는 원문에 없음) — 원문 확인")
        for f in found:
            key = f.kind if f.kind != "ir" else f"ir:{f.date.isoformat()}"
            if f.kind == "ir":
                # IR 안내는 분기마다 새 건 — 날짜마다 따로. 단 정정(같은 날짜 근처 재공시) 은 나중 것으로
                prev = next((k for k, (pf, _) in latest.items() if k.startswith("ir:") and abs((pf.date - f.date).days) <= 10), None)
                if prev:
                    del latest[prev]
            latest[key] = (f, it)
    events: list[Event] = []
    for f, it in sorted(latest.values(), key=lambda kv: kv[0].date):
        if f.date < today:
            continue
        spec = _SPEC.get(f.kind) or {"materiality": "M", "certainty": EventCertainty.공시확정, "fact": f.label, "precheck": [], "rule": None}
        fact, materiality, precheck, rule = spec["fact"], spec["materiality"], list(spec["precheck"]), spec["rule"]
        affects: list[str] = []
        if f.kind == "ir":
            q = _quarter_for_ir(f.date)
            earnings = ("실적" in (f.label or "")) or not f.label   # 목적을 못 읽었으면 실적 발표로 본다 (분기 첫 두 달이면 그럴 가능성이 크다)
            if earnings:
                fact = f"{q} 실적발표·IR" if q else "실적발표·IR"
                affects = list(affects_ir or [])
            else:
                fact, materiality, precheck, rule = f"IR·기업설명회 — {f.label[:40]}", "M", ["발표 자료의 가이던스·신규 사실"], None
        if f.time:
            fact += f" ({f.time})"
        src = Source(name=f"DART {it.report_nm} ({it.rcept_dt.isoformat()})", url=it.url, retrieved_at=today)
        if f.kind == "ir":
            eid = f"earnings:{_quarter_for_ir(f.date)}" if earnings and _quarter_for_ir(f.date) else f"ir:{f.date.isoformat()}"
        elif f.kind == "agm":
            eid = f"agm:{f.date.year}"
        else:
            eid = f"rights:{f.kind}"
        events.append(Event(date=f.date, fact=fact, affects=affects, direction="미정", priced_in="미판단", materiality=materiality,
                            certainty=spec["certainty"], precheck=precheck, rule=rule, source=src, event_id=eid))
    return events, notes


# ---------------------------------------------------------------------------- 파서 점검 (2026-10-31 3차 검토: 「파서 적중률을 종목별로 확인」)
@dataclass
class CheckRow:
    """공시 한 건의 파서 결과 — `mindmap dart-check` 가 종목별 적중률을 셀 때 쓴다."""

    item: DartItem
    kind: str                                   # ir | rights | agm
    status: str                                 # ok | partial | none | no_text
    found: list[Found] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)   # _EXPECTED 중 못 읽은 항목
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def line(self) -> str:
        it = self.item
        head = f"{it.rcept_dt.isoformat()} {it.rcept_no} {it.report_nm[:30]:<30} [{self.kind}]"
        if self.status == "no_text":
            return f"{head} 본문 못 받음 — {self.error}"
        if self.status == "none":
            return f"{head} 날짜를 못 읽었다 — 원문 확인 {it.url}"
        got = ", ".join(f"{f.kind} {f.date.isoformat()}" + (f" {f.time}" if f.time else "") for f in self.found)
        tail = f" · 못 읽음: {', '.join(self.missing)}" if self.missing else ""
        return f"{head} {'OK ' if self.ok else '부분'} {got}{tail}"


def check_items(items: list[DartItem], text_of: Callable[[str], str]) -> list[CheckRow]:
    """읽어야 하는 공시(IR·유상증자·주총) 전부를 파서에 넣고 건마다 결과를 돌려준다. 날짜가 지났어도 센다 — 적중률은 과거 공시로 재는 것이다."""
    rows: list[CheckRow] = []
    for it in sorted(items, key=lambda x: x.rcept_no):
        kind = kind_of(it.report_nm)
        if kind is None:
            continue
        try:
            text = text_of(it.rcept_no)
        except Exception as e:  # noqa: BLE001
            rows.append(CheckRow(it, kind, "no_text", error=f"{type(e).__name__}: {str(e)[:80]}"))
            continue
        found = parse_document(kind, text)
        if not found:
            rows.append(CheckRow(it, kind, "none"))
            continue
        missing = [need for need in _EXPECTED.get(kind, ()) if not any(f.kind == need for f in found)]
        rows.append(CheckRow(it, kind, "partial" if missing else "ok", found=found, missing=missing))
    return rows


def check_summary(rows: list[CheckRow]) -> dict[str, int]:
    """종류별·상태별 수 — {"watched": n, "ok": n, "partial": n, "none": n, "no_text": n}."""
    out = {"watched": len(rows), "ok": 0, "partial": 0, "none": 0, "no_text": 0}
    for r in rows:
        out[r.status] += 1
    return out


def _quarter_for_ir(d: date) -> str:
    """IR 일시로 보고 분기 — 2월 초 IR 은 전년 4분기. 분기 첫 두 달 안의 IR 만 직전 분기로 본다."""
    q = (d.month - 1) // 3 + 1
    in_q_month = (d.month - 1) % 3
    if in_q_month <= 1:
        pq, py = (q - 1, d.year) if q > 1 else (4, d.year - 1)
        return f"{pq}Q{str(py)[2:]}"
    return ""


def items_from_db_rows(rows) -> list[DartItem]:  # noqa: ANN001
    """ResearchDb.disclosures() 결과 → DartItem (접수번호 없는 줄은 본문을 못 받으니 뺀다)."""
    return [DartItem(r.rcept_no, r.rcept_dt, r.report_nm) for r in rows if getattr(r, "rcept_no", None)]


def items_from_list_json(rows: list[dict]) -> list[DartItem]:
    out: list[DartItem] = []
    for r in rows:
        try:
            d = date(int(r["rcept_dt"][:4]), int(r["rcept_dt"][4:6]), int(r["rcept_dt"][6:8]))
        except (KeyError, ValueError, TypeError):
            continue
        out.append(DartItem(str(r.get("rcept_no", "")), d, str(r.get("report_nm", ""))))
    return out


#: 「일어났다」 를 알리는 공시 — 본문 없이 이름만으로 완료를 적는다 (2026-10-31)
COMPLETION_KINDS: tuple[tuple[str, str, str], ...] = (
    ("증권발행실적보고서", "rights:납입일", "증권발행실적보고서 접수 — 납입·발행 완료"),
    ("정기주주총회결과", "agm", "주주총회 결과 공시"),
)


@dataclass
class DartEventsResult:
    events: list[Event] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    items_seen: int = 0
    source: str = ""     # db | api | db+api
    completions: list[dict] = field(default_factory=list)   # {event_id, date, note, url} — 이벤트를 completed 로 바꿀 근거


def completions_from_items(items: list[DartItem]) -> list[dict]:
    out: list[dict] = []
    for it in sorted(items, key=lambda x: x.rcept_no):
        nm = it.report_nm or ""
        if any(k in nm for k in ("자회사", "종속회사")):
            continue
        for key, eid, note in COMPLETION_KINDS:
            if key in nm:
                out.append({"event_id": eid if eid != "agm" else f"agm:{it.rcept_dt.year}", "date": it.rcept_dt, "note": f"{note} ({it.rcept_dt.isoformat()})", "url": it.url})
    return out


def dart_events(code: str, *, today: date, db=None, dart: OpenDart | None = None, lookback_days: int = 120,  # noqa: ANN001
                affects_ir: list[str] | None = None) -> DartEventsResult:
    """종목 하나의 **공시확정 촉매**. 목록은 리서치 DB → (낡았으면) OpenDART list.json, 본문은 OpenDART.

    DB 목록이 `today` 보다 7일 넘게 낡았으면 그 뒤 기간만 list.json 으로 보충한다. 키·네트워크가 없으면
    DB 에 있는 것만으로 간다 (본문을 못 받으니 이벤트는 없고, notes 에 까닭이 남는다).
    """
    res = DartEventsResult()
    since = today - timedelta(days=lookback_days)
    items: list[DartItem] = []
    db_until: date | None = None
    if db is not None:
        try:
            rows = db.disclosures(code, since=since)
            items += items_from_db_rows(rows)
            db_until = max((r.rcept_dt for r in rows), default=None)
            res.source = "db"
        except Exception as e:  # noqa: BLE001
            res.notes.append(f"리서치 DB 공시 목록 실패 ({type(e).__name__})")
    if dart is not None and dart.api_key:
        gap_from = (db_until + timedelta(days=1)) if db_until else since
        if db_until is None or (today - db_until).days > 7:
            corp = None
            try:
                corp = db.corp_code(code) if db is not None else None
            except Exception:  # noqa: BLE001
                corp = None
            try:
                corp = corp or dart.corp_code(code)
                if corp:
                    fresh = items_from_list_json(dart.list_json(corp, gap_from, today))
                    known = {it.rcept_no for it in items}
                    items += [it for it in fresh if it.rcept_no not in known]
                    res.source = (res.source + "+api").lstrip("+")
                else:
                    res.notes.append("DART 고유번호를 못 찾았다")
            except (DartError, NotConfigured) as e:
                res.notes.append(f"DART 목록 보충 실패 — {e}")
    res.items_seen = len(items)
    res.completions = completions_from_items(items)
    watched = [it for it in items if kind_of(it.report_nm)]
    if not watched:
        res.notes.append("기간 안에 IR 안내·유상증자·주총 공시가 없다" if items else "공시 목록이 비었다")
        return res
    if dart is None or not dart.api_key:
        res.notes.append(f"DART_API_KEY 없음 — 공시 {len(watched)}건의 본문을 못 받아 확정 일정을 못 만든다 "
                         "(.env 의 DART_API_KEY 또는 리서치 저장소 .env)")
        return res

    def text_of(rcept_no: str) -> str:
        return dart.document_text(rcept_no)

    res.events, notes = events_from_items(watched, text_of, today=today, affects_ir=affects_ir)
    res.notes += notes
    return res


# ---------------------------------------------------------------------------- 옛 인터페이스 (collect.base.DisclosureSource)
class DartDisclosureSource(DisclosureSource):
    def __init__(self, api_key: str | None = None, *, timeout: float = 10.0, db=None):  # noqa: ANN001
        self.client = OpenDart(api_key, timeout=timeout)
        self.db = db

    @property
    def api_key(self) -> str | None:
        return self.client.api_key or None

    def corp_code(self, ticker: str) -> str:
        c = None
        if self.db is not None:
            c = self.db.corp_code(ticker)
        c = c or self.client.corp_code(ticker)
        if not c:
            raise DartError(f"{ticker}: DART 고유번호를 못 찾았다")
        return c

    def list_disclosures(self, ticker: str, since: date) -> list[RawDisclosure]:
        rows = self.client.list_json(self.corp_code(ticker), since, date.today())
        return [RawDisclosure(ticker, it.rcept_dt, it.report_nm, url=it.url, source="DART") for it in items_from_list_json(rows)]


__all__ = ["DartDisclosureSource", "OpenDart", "DartError", "DartItem", "DartEventsResult", "dart_events", "events_from_items",
           "parse_ir_notice", "ir_purpose", "parse_rights_offering", "parse_agm", "parse_document", "kind_of", "strip_tags", "zip_to_text",
           "dart_api_key", "dart_cache_dir", "completions_from_items", "COMPLETION_KINDS", "items_from_list_json", "items_from_db_rows", "BASE",
           "CheckRow", "check_items", "check_summary"]
