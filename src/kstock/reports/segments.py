"""사업부(세그먼트) 표 추출 (설계 v5 R4) — "사업부별 실적 전망" 표에서 사업부 × 지표(매출·영업이익·영업이익률) × 기간 을 읽는다.

전사 추정치 표(`extract.parse_estimate_tables`) 는 사업부 행을 일부러 섞지 않는다. 여기서는 반대로 사업부 행만 모아
`ReportFacts.segments[segment][metric][col]` 에 둔다 (금액은 억원, 이익률은 %). 민감도 모델(설계 v5 §3) 이 "DRAM 매출" 같은
입력을 여기서 찾는다.

표 양식 (삼성전자 3건 실측 — IBK · 유진 · DS 가 전부 같은 뼈대):
    [캡션: 사업부별 세부 전망 / 사업부별 실적 추이 및 전망 / 부문별 상세실적]
    (단위: 십억원)  1Q25 2Q25 … 3Q26E 4Q26E 2025 2026E 2027E          ← 기간 머리 줄 (단위는 머리·캡션·블록 줄 어디에나)
    매출액 79,141 74,566 …                                           ← 블록 표시 줄 (전사 합계; 지표 바뀜)
    반도체 25,126 …  /  메모리 …  /  DRAM …  /  NAND …  /  비메모리 …     ← 사업부 행
    영업이익 6,685 …  → 사업부 행 …  → 영업이익률 8.4% …  → 사업부 행 …
사업부 이름은 증권사마다 다르다 (반도체=DS, 메모리=Memory, 비메모리=Foundry/LSI=LSI/Foundry=SLSI, IM=무선=MX / Networks …) →
`SEGMENT_ALIASES` 로 정규화한다. 모르는 행(대형·중소형·NW/기타·기타) 은 버린다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PERIOD = re.compile(r"^(?:[1-4]Q\d{2}[EPFA]?|20\d{2}[EPFA]?|FY\d{2}[EPF]?|\d{2}[EPF])$")
NUM = re.compile(r"^[-−–]?\(?\d[\d,]*(?:\.\d+)?\)?%?$|^n/?a$|^-$", re.I)
UNIT_IN_TEXT = re.compile(r"\((?:단위\s*[:：]\s*)?(십억원|조원|억원|백만원|KRW\s*bn|bn)\)")
UNIT_TO_EOK = {"억원": 1.0, "십억원": 10.0, "조원": 10000.0, "백만원": 0.01, "KRW bn": 10.0, "bn": 10.0}

BLOCK_LABELS: list[tuple[str, str]] = [
    ("opm", r"영업이익률|OPM|OP\s*margin"),
    ("op", r"영업이익(?!률)|OP(?![a-zA-Z])"),
    ("revenue", r"매출액|영업수익|매출(?!\s*비중|\s*총|\s*원가)|Revenue|Sales"),
    ("share", r"매출\s*비중"),
]
SEGMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "DS": ("DS", "DS부문", "DS 부문", "반도체", "반도체(DS)", "Semiconductor", "DS(반도체)"),
    "메모리": ("메모리", "Memory", "메모리반도체", "메모리 반도체"),
    "DRAM": ("DRAM", "디램", "D램"),
    "NAND": ("NAND", "낸드", "NAND Flash"),
    "HBM": ("HBM",),
    "파운드리": ("비메모리", "Foundry/LSI", "LSI/Foundry", "SLSI", "S.LSI", "파운드리", "Foundry", "시스템LSI", "LSI", "파운드리/LSI", "Foundry&LSI", "비메모리(S.LSI/Foundry)"),
    "DX": ("DX", "DX부문", "DX 부문", "세트", "Set"),
    "MX": ("MX / Networks", "MX/네트워크", "MX/Networks", "MX", "IM", "무선", "MX/NW", "모바일"),
    "VD": ("VD / DA", "VD/CE", "VD", "CE", "VD/DA", "TV/가전"),
    "SDC": ("SDC", "Display", "디스플레이", "DP"),
    "Harman": ("Harman", "HAR", "하만"),
    # 설계 v7 T10 — NAVER (인터넷·플랫폼)
    "서치플랫폼": ("서치플랫폼", "서치 플랫폼", "Search Platform", "검색", "광고", "서치플랫폼(광고)", "Search"),
    "커머스": ("커머스", "Commerce", "쇼핑", "커머스(쇼핑)"),
    "핀테크": ("핀테크", "Fintech", "파이낸셜", "파이낸셜 플랫폼", "파이낸셜플랫폼", "네이버파이낸셜", "페이", "핀테크(페이)"),
    "광고": ("광고", "Ads", "Advertising"),
    "콘텐츠": ("콘텐츠", "Contents", "Content", "웹툰", "콘텐츠(웹툰)"),
    "클라우드": ("클라우드", "Cloud", "클라우드(AI)", "네이버클라우드", "Enterprise"),
    # 설계 v7 T10 — 두산에너빌리티 (에너지·원전·기계)
    "에너빌리티": ("에너빌리티", "두산에너빌리티(별도)", "에너빌리티(별도)", "별도", "Enerbility", "발전", "에너지"),
    "원자력": ("원자력", "원전", "Nuclear", "원자력(원전)", "원자력 기자재", "원자력기자재", "원전 기자재"),
    "가스터빈": ("가스터빈", "Gas Turbine", "GT"),
    "밥캣": ("밥캣", "두산밥캣", "Bobcat"),
    "산업차량": ("산업차량", "두산산업차량", "Industrial Vehicle"),
    "퓨얼셀": ("퓨얼셀", "두산퓨얼셀", "Fuel Cell", "연료전지"),
}
# 캡션이 '사업부별/부문별' 이고 행 이름이 짧은 한글·영문(숫자 없음) 이면 사전에 없어도 사업부로 받는다 (설계 v7 T10 — 종목마다 사전을 늘리지 않아도 되게)
GENERIC_CAPTION = re.compile(r"사업부|부문|segment|사업별|Division", re.I)
GENERIC_ROW = re.compile(r"^[가-힣A-Za-z][가-힣A-Za-z/&·\s]{0,11}$")
_ALIAS_INDEX = {a.replace(" ", "").lower(): seg for seg, aliases in SEGMENT_ALIASES.items() for a in aliases}
SKIP_ROWS = re.compile(r"^(QoQ|YoY|Q[o0]Q/Y\s*oY|증감|성장률|대형|중소형|가전/기타|NW/기타|기타|합계|계|EBITDA|EBIT|순이익|당기순이익|영업이익률|매출총이익|판관비|감가상각|세전|지배)\b", re.I)


@dataclass
class SegmentTable:
    unit: str | None = None
    cols: list[str] = field(default_factory=list)
    # segment → metric → col → value (금액은 억원, opm·share 는 %)
    data: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    caption: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def n_cells(self) -> int:
        return sum(len(cols) for mets in self.data.values() for cols in mets.values())

    def value(self, segment: str, metric: str, col: str) -> float | None:
        return self.data.get(segment, {}).get(metric, {}).get(col)

    def to_dict(self) -> dict[str, Any]:
        return {"unit": self.unit, "cols": list(self.cols), "caption": self.caption, "data": {s: {m: dict(c) for m, c in ms.items()} for s, ms in self.data.items()}}


def _num(tok: str) -> float | None:
    t = tok.replace("−", "-").replace("–", "-").replace(",", "").rstrip("%")
    if t.lower() in ("n/a", "na", "-", ""):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _split_row(line: str) -> tuple[str, list[str]]:
    """'DRAM 12,152 13,136 …' → ('DRAM', [숫자 토큰들]). 이름은 첫 숫자 토큰 앞까지 (공백 포함 가능: 'MX / Networks')."""
    toks = line.split()
    i = 0
    while i < len(toks) and not NUM.match(toks[i]):
        i += 1
    return " ".join(toks[:i]).strip(), toks[i:]


def _norm_segment(name: str) -> str | None:
    key = re.sub(r"\s+", "", name).lower()
    key = re.sub(r"\((십억원|조원|억원|%|백만원)\)", "", key)
    return _ALIAS_INDEX.get(key)


def _block_metric(name: str) -> str | None:
    base = UNIT_IN_TEXT.sub("", name).replace("(%)", "").strip()
    for metric, pat in BLOCK_LABELS:
        if re.fullmatch(rf"(?:{pat})\s*", base, re.I):
            return metric
    return None


def _is_header(toks: list[str]) -> bool:
    per = [t for t in toks if PERIOD.match(t)]
    return len(per) >= 4 and len(per) >= len(toks) - 2


def extract_segment_tables(text: str) -> list[SegmentTable]:
    """본문에서 사업부 표를 전부 찾는다 (보통 1~2개). 셀이 하나도 없는 표는 뺀다."""
    lines = [ln.strip() for ln in text.splitlines()]
    tables: list[SegmentTable] = []
    i = 0
    while i < len(lines):
        toks = lines[i].replace("(단위:", "(단위: ").split()
        header_toks = [t for t in toks if PERIOD.match(t)]
        if not (len(header_toks) >= 4 and _is_header([t for t in toks if not UNIT_IN_TEXT.match(t) and t not in ("(단위:", "단위:")])):
            i += 1
            continue
        tbl = SegmentTable(cols=header_toks)
        um = UNIT_IN_TEXT.search(" ".join(lines[max(0, i - 3):i + 1]))
        if um:
            tbl.unit = um.group(1).replace("KRW ", "KRW ")
        tbl.caption = next((lines[j] for j in range(i - 1, max(-1, i - 4), -1) if lines[j] and not UNIT_IN_TEXT.fullmatch(lines[j])), None)
        metric: str | None = None
        j = i + 1
        blank = 0
        while j < len(lines):
            ln = lines[j]
            if not ln:
                blank += 1
                if blank >= 2:
                    break
                j += 1
                continue
            blank = 0
            if ln.startswith(("자료", "주:", "주)", "출처")) or _is_header([t for t in ln.split() if not UNIT_IN_TEXT.match(t)]):
                break
            name, nums = _split_row(ln)
            if not nums or len(nums) < max(3, len(tbl.cols) - 2):
                j += 1
                if not nums and name and _block_metric(name) is None and not _norm_segment(name) and j - i > 2:
                    # 표가 끝나고 문장이 시작됐다
                    break
                continue
            bm = _block_metric(name)
            if bm is not None:
                metric = bm
                if tbl.unit is None:
                    um2 = UNIT_IN_TEXT.search(ln)
                    if um2:
                        tbl.unit = um2.group(1)
                j += 1
                continue
            if metric is None or SKIP_ROWS.match(name):
                j += 1
                continue
            seg = _norm_segment(name)
            if seg is None and tbl.caption and GENERIC_CAPTION.search(tbl.caption) and GENERIC_ROW.match(name.strip()) and not SKIP_ROWS.match(name):
                seg = name.strip()           # 사전 밖 사업부 — 이름 그대로 (설계 v7 T10)
                tbl.warnings.append(f"사전 밖 사업부 행 '{seg}' 을 이름 그대로 받음")
            if seg is None:
                j += 1
                continue
            cells = tbl.data.setdefault(seg, {}).setdefault(metric, {})
            if cells:            # 같은 사업부가 두 번 (IM 과 무선 → MX): 먼저 나온 행이 이긴다
                j += 1
                continue
            vals = nums[-len(tbl.cols):] if len(nums) >= len(tbl.cols) else nums
            cols = tbl.cols[-len(vals):]
            scale = 1.0
            if metric in ("revenue", "op"):
                scale = UNIT_TO_EOK.get(tbl.unit or "십억원", 10.0)
                if tbl.unit is None:
                    tbl.warnings.append("사업부 표의 단위를 찾지 못해 십억원으로 가정")
            for col, tok in zip(cols, vals, strict=False):
                v = _num(tok)
                if v is not None:
                    cells[col] = round(v * scale, 4) if metric in ("revenue", "op") else v
            j += 1
        if tbl.n_cells:
            tables.append(tbl)
        i = max(j, i + 1)
    return tables


def merge_segment_tables(tables: list[SegmentTable]) -> SegmentTable:
    """여러 표(요약·상세) 를 하나로 — 상세(셀 많은 것) 가 먼저, 겹치는 셀은 먼저 것이 이긴다."""
    out = SegmentTable()
    for t in sorted(tables, key=lambda t: -t.n_cells):
        out.unit = out.unit or t.unit
        out.caption = out.caption or t.caption
        for c in t.cols:
            if c not in out.cols:
                out.cols.append(c)
        for seg, mets in t.data.items():
            for met, cells in mets.items():
                dst = out.data.setdefault(seg, {}).setdefault(met, {})
                for col, v in cells.items():
                    dst.setdefault(col, v)
        out.warnings += t.warnings
    return out


__all__ = ["SegmentTable", "SEGMENT_ALIASES", "extract_segment_tables", "merge_segment_tables"]
