"""리포트 → 마인드맵의 중간 산출물 (설계 v3 §2.1 데이터 계약).

    EvidenceSpan  리포트 텍스트의 한 구간 (페이지·오프셋). 같은 구간이 여러 Claim 을 받칠 수 있다.
    Claim         검증 가능한 명제 하나. 내용 유형(epistemic_type) 과 투자 역할(roles, 복수) 을 분리한다.
    Issue         같은 테마(driver) / 메커니즘(driver→target 경로) 에 걸린 Claim 들의 증권사 간 비교 — 상태는 복수.
    ChangeSet     기존 맵 + 새 리포트 → 변경 후보 목록. 자동으로 노드를 바꾸지 않는다 (사용자 승인).
    ClaimSet      리포트 한 건의 Claim 묶음 (저장 단위, data/claims/<report_id>.json).

LLM 은 Claim 후보와 Issue 요약만 만들고, 검증·묶기·맵 매핑은 코드가 한다. 모든 자동 노드는 claim_id 로 원문까지 되짚을 수 있다.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import StrEnum


# --------------------------------------------------------------------------- enums
class EpistemicType(StrEnum):
    fact = "fact"            # 리포트가 사실로 적은 것 (실적·계약·공시)
    estimate = "estimate"    # 전망·추정치
    opinion = "opinion"      # 애널리스트 판단 ("매수 유지가 타당")


class AssertedBy(StrEnum):
    """누구의 주장인가 (설계 v4 V1). 증권사 분석 / 기업 발표·가이던스 / 시장·컨센서스 / 정부·규제기관."""

    analyst = "analyst"
    company = "company"
    market = "market"
    authority = "authority"
    other = "other"


class Certainty(StrEnum):
    """확정 수준 (v4 V1). realized=이미 일어남(실적) · announced=공식 발표 · planned=계획·예정 · expected=전망·추정 · conditional=조건부."""

    realized = "realized"
    announced = "announced"
    planned = "planned"
    expected = "expected"
    conditional = "conditional"


CERTAINTY_TAG = {Certainty.realized: "실적", Certainty.announced: "기업 발표", Certainty.planned: "기업 계획",
                 Certainty.expected: "증권사 전망", Certainty.conditional: "조건부"}


class ClaimRole(StrEnum):
    thesis = "thesis"
    risk = "risk"
    catalyst = "catalyst"
    valuation = "valuation"
    shareholder = "shareholder"
    business = "business"
    revision_reason = "revision_reason"   # 목표주가·추정치 변경 사유
    scenario = "scenario"                 # 리포트가 제시한 Bull/Base/Bear


class ClaimDirection(StrEnum):
    positive = "positive"
    negative = "negative"
    neutral = "neutral"


class SupportVerdict(StrEnum):
    supported = "supported"
    contradicted = "contradicted"
    insufficient = "insufficient"
    unchecked = "unchecked"


class ClaimStatus(StrEnum):
    verified = "verified"          # 인용 존재 · 숫자 일치 · 의미 지지 모두 통과 (또는 사람이 승인)
    needs_review = "needs_review"  # 한 단계라도 미흡 — 검토 대기함
    rejected = "rejected"          # contradicted 또는 사람이 반려


class Coverage(StrEnum):
    agree = "agree"
    disagree = "disagree"
    partial = "partial"       # 기준 명제의 일부에만 동의 (예: 공급 증가엔 동의, 이익 효과엔 반대) — v4 V3
    mentioned = "mentioned"   # 언급했지만 방향이 중립/불명
    silent = "silent"         # 언급 없음 — 부정이 아니다
    unanalyzed = "unanalyzed"  # 그 리포트를 끝까지 읽지 못해(청크 한도) 침묵인지 모름 — v4 V3


class IssueLevel(StrEnum):
    theme = "theme"
    mechanism = "mechanism"


class IssueDisplay(StrEnum):
    common = "공통 견해"
    disagreement = "핵심 이견"
    independent = "독립 주장"
    unresolved = "미해결 쟁점"


class ChangeKind(StrEnum):
    estimate = "추정치 변화"
    new_claim = "새 주장"
    conflict = "기존 논제와 충돌"
    tp_revision = "TP 리비전"
    new_risk = "새 리스크"
    new_catalyst = "새 촉매"
    judgment_input = "확률/판단 영향"


class ChangeDecision(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


# --------------------------------------------------------------------------- 근거
class EvidenceSpan(BaseModel):
    """정규화된 리포트 텍스트(`ReportDoc.text`) 의 [start, end) 구간. page 는 1부터."""

    model_config = ConfigDict(extra="forbid")

    span_id: str
    report_id: str
    page: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str

    @classmethod
    def make(cls, report_id: str, page: int, start: int, end: int, text: str) -> EvidenceSpan:
        h = hashlib.sha1(f"{report_id}:{start}:{end}".encode()).hexdigest()[:10]
        return cls(span_id=f"sp_{h}", report_id=report_id, page=page, start=start, end=end, text=text)


class QuantRef(BaseModel):
    """주장이 기대는 숫자 — 표(`ReportFacts.estimates`) 나 인용문 안에 있는 값만."""

    model_config = ConfigDict(extra="forbid")

    metric: str                       # op | revenue | eps | per | opm | hbm_mix … (표 지표 id 또는 driver id)
    col: str | None = None            # 2027E | 3Q26E | null
    value: float
    unit: str | None = None           # 억원 | 십억원 | 조원 | 원 | % | 배
    source: str = "text"              # table | text


class Validation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote: str = "unchecked"          # ok | miss | unchecked
    numbers: str = "unchecked"        # ok | mismatch | none | unchecked
    support: SupportVerdict = SupportVerdict.unchecked
    note: str = ""


class Extractor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = ""                   # claude-sonnet-5-5 | rules | human
    prompt_version: str = ""
    chunk_id: str = ""


# --------------------------------------------------------------------------- 범위·단위 (v4 V4 보강: 전사 수치와 사업부 수치를 같은 셀에 놓지 않는다)
SEGMENT_WORDS = ("사업부", "부문", "DRAM", "NAND", "HBM", "메모리", "MX", "VD", "CE", "가전", "FOUNDRY", "파운드리", "LSI", "SDC", "DS ", "DS부문",
                 "DX", "네트워크", "디스플레이", "비메모리", "자회사", "별도", "SOLUTION", "하만", "HARMAN")
_AMOUNT_TO_EOK = {"억원": 1.0, "억": 1.0, "십억원": 10.0, "십억": 10.0, "조원": 10000.0, "조": 10000.0, "백만원": 0.01, "천원": 0.00001, "bn": 10.0, "mn": 0.01}


_SEGMENT_RE = re.compile("(?:" + "|".join(re.escape(w.strip()) for w in SEGMENT_WORDS) + r")[^.。\n]{0,14}?(?:영업이익|영업적자|적자|매출|실적|OPM|이익률|이익)", re.I)


def is_segment_text(text: str) -> bool:
    """사업부·제품 범위의 **수치** 문장인가 — 사업부·제품 이름 뒤 14자 안에 영업이익·적자·매출·실적이 따라올 때
    ('메모리 부문 영업이익 110.1조', 'MX/NW 는 2.0조원의 영업적자', 'DRAM 83.8조원'). 'HBM4 출하 확대가 이익을 끌어올린다' 같은
    전사 인과 문장은 아니다. 전사 표에 있는 숫자인지(corroborated) 가 더 정확하므로 facts 가 있으면 그것을 먼저 쓴다."""
    return bool(_SEGMENT_RE.search(text or ""))


def normalized_value(value: float, unit: str | None) -> tuple[float, str]:
    """비교용 정규화 — 금액은 억원으로 (조원·십억원 섞임 방지), 그 밖(%·원·배) 은 그대로. 돌려주는 것: (값, 단위 부류)."""
    u = (unit or "").strip()
    if u in _AMOUNT_TO_EOK:
        return value * _AMOUNT_TO_EOK[u], "억원"
    return value, u


# --------------------------------------------------------------------------- Claim
class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    report_id: str
    broker: str = ""
    report_date: date | None = None
    proposition: str                                      # 검증 가능한 명제 하나
    epistemic_type: EpistemicType = EpistemicType.estimate
    roles: list[ClaimRole] = Field(default_factory=list)
    driver_id: str | None = None                          # 정규화 사전 id (없으면 null + driver_label)
    driver_label: str | None = None                       # LLM 이 쓴 자유 라벨 (사전 후보)
    target_metric_id: str | None = None
    mechanism: str | None = None                          # "인증 완료 → 출하"
    direction: ClaimDirection = ClaimDirection.neutral
    condition: str | None = None                          # 조건부 주장의 조건
    horizon: str | None = None                            # 2027E | 4Q26 | 2026-10-30
    quant_refs: list[QuantRef] = Field(default_factory=list)
    span_ids: list[str] = Field(default_factory=list)
    validation: Validation = Field(default_factory=Validation)
    status: ClaimStatus = ClaimStatus.needs_review
    inferred: bool = False
    section: str | None = None                            # 투자포인트 | 실적 전망 | 밸류에이션 | 리스크 | 기타
    extractor: Extractor = Field(default_factory=Extractor)
    # --- v4 V1: 사실·전망·계획 구분 (기본값이 있어 c1/c2 저장본도 그대로 읽힌다)
    asserted_by: AssertedBy = AssertedBy.analyst
    certainty: Certainty = Certainty.expected
    as_of: date | None = None                             # 정보 기준일 (None 이면 리포트 날짜)
    # --- v4 V2: 기계 검증 vs 사람 승인
    verified_by: str = ""                                 # "" | machine | human
    review_required: bool = False                         # 자동 통과해도 사람이 봐야 하는 것 (목표가·A1 지표 수치, 증권사 간 수치 충돌, 조건부 리스크 …)
    review_reason: str = ""

    @field_validator("proposition")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 5:
            raise ValueError("proposition 이 너무 짧다")
        return v

    @property
    def usable(self) -> bool:
        """맵·Issue 에 쓸 수 있는가: verified 이고 리포트 밖 추론이 아닌 것."""
        return self.status == ClaimStatus.verified and not self.inferred

    @property
    def tag(self) -> str:
        """화면 접두어 — [실적] [기업 발표] [기업 계획] [증권사 전망] [조건부] (v4 V1)."""
        return CERTAINTY_TAG.get(self.certainty, "증권사 전망")

    @property
    def is_segment(self) -> bool:
        """사업부·제품 범위의 주장인가 — 수치 비교(증권사 간 분산·충돌) 에서 전사 수치와 섞지 않는다."""
        return is_segment_text(self.proposition) or is_segment_text(self.driver_label or "")

    @property
    def effective_as_of(self) -> date | None:
        return self.as_of or self.report_date

    def has_role(self, role: ClaimRole | str) -> bool:
        return ClaimRole(role) in self.roles


class ClaimSet(BaseModel):
    """리포트 한 건의 추출 결과 (저장 단위)."""

    model_config = ConfigDict(extra="forbid")

    report_id: str
    path: str = ""
    broker: str = ""
    report_date: date | None = None
    ticker: str | None = None
    quality: str = "llm"                                  # llm | rules | human
    spans: list[EvidenceSpan] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    analyzed_chars: int = 0                               # v4 V3: 청크로 실제 읽은 글자 수 (0 = 기록 없음 → 전부 읽은 것으로 본다)
    total_chars: int = 0

    @property
    def analyzed_ratio(self) -> float:
        if not self.total_chars:
            return 1.0
        return min(1.0, self.analyzed_chars / self.total_chars)

    def span(self, span_id: str) -> EvidenceSpan | None:
        return next((s for s in self.spans if s.span_id == span_id), None)

    def usable(self) -> list[Claim]:
        return [c for c in self.claims if c.usable]

    def review_queue(self, *, include_required: bool = True) -> list[Claim]:
        """검토 대기함: needs_review 전부 + (v4 V2) 자동 통과했지만 review_required 인 것 (verified 유지, ⚑)."""
        out = [c for c in self.claims if c.status == ClaimStatus.needs_review]
        if include_required:
            out += [c for c in self.claims if c.status == ClaimStatus.verified and c.review_required]
        return out


# --------------------------------------------------------------------------- Issue
class NumericSpread(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    col: str | None = None
    unit: str | None = None
    values: dict[str, float] = Field(default_factory=dict)   # broker → value
    min: float
    max: float
    median: float
    rel: float                        # (max − min) / |median|
    abs: float                        # max − min
    tp_impact: float | None = None    # 근사: 차이가 목표가에 미치는 비율 (가능할 때만)


class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str                                         # "<driver_id>" | "<driver_id>/<mechanism_slug>"
    level: IssueLevel
    driver_id: str | None = None
    driver_label: str = ""
    target_metric_id: str | None = None
    mechanism: str | None = None
    claim_ids: list[str] = Field(default_factory=list)
    coverage: dict[str, Coverage] = Field(default_factory=dict)       # broker → 상태 (컨센 대비 동의/반대/언급/침묵)
    directions: dict[str, str] = Field(default_factory=dict)          # broker → positive | negative | neutral (그 증권사의 방향)
    consensus_direction: str = "none"                                  # positive | negative | mixed | none
    disagreement_dimensions: list[str] = Field(default_factory=list)  # direction | magnitude | timing | condition
    numeric_spread: NumericSpread | None = None
    unresolved_questions: list[str] = Field(default_factory=list)
    summary: str = ""                                                  # LLM 한 줄 (claim_ids 가 근거)
    display: IssueDisplay | None = None                                # 화면용 — 코드가 규칙으로 계산
    children: list[str] = Field(default_factory=list)                 # theme → mechanism issue_ids
    # --- v4 V3: 무엇에 동의/반대하는지의 기준
    reference_proposition: str = ""                                    # 증권사별 판정은 이 명제 하나에 대해서만
    coverage_basis: str = "direction"                                  # direction(방향 통계) | llm(명제 판정)
    judgement_notes: dict[str, str] = Field(default_factory=dict)     # broker → 판정 이유 한 줄

    @property
    def n_brokers(self) -> int:
        return sum(1 for v in self.coverage.values() if v not in (Coverage.silent, Coverage.unanalyzed))


class IssueSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str | None = None
    report_ids: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)

    def get(self, issue_id: str) -> Issue | None:
        return next((i for i in self.issues if i.issue_id == issue_id), None)

    def by_display(self, display: IssueDisplay) -> list[Issue]:
        return [i for i in self.issues if i.display == display]


# --------------------------------------------------------------------------- ChangeSet
class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_id: str
    node_id: str
    kind: ChangeKind
    before: Any = None
    after: Any = None
    claim_ids: list[str] = Field(default_factory=list)
    user_touched: bool = False
    note: str = ""
    decision: ChangeDecision = ChangeDecision.pending
    affects: list[str] = Field(default_factory=list)     # v4 V7: 이 변경과 함께 바뀌어야 하는 노드 (A1.i → A3.5.4, A2.1.x …)


class ChangeSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_map_id: str
    base_version: int = 1
    base_snapshot_hash: str = ""
    new_report_ids: list[str] = Field(default_factory=list)
    changes: list[Change] = Field(default_factory=list)
    summary: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    decided_at: datetime | None = None
    result_version: int | None = None
    input_snapshot_id: str = ""                           # v4 V7: 입력 리포트 집합(report_id 정렬)+기준일의 sha1[:10] — 다른 입력의 변경안을 섞지 않게

    def pending(self) -> list[Change]:
        return [c for c in self.changes if c.decision == ChangeDecision.pending]

    def approved(self) -> list[Change]:
        return [c for c in self.changes if c.decision == ChangeDecision.approved]


def report_id_for(path: str, text: str = "") -> str:
    """리포트 id: 파일명에 한경 리포트 번호(4~7자리)가 있으면 'hk_<번호>', 없으면 내용 해시."""
    import re
    from pathlib import Path

    stem = Path(path).stem
    m = re.search(r"(?:hk|report|rpt)[_-]?(\d{4,7})(?!\d)", stem, re.I)
    if m:
        return f"hk_{m[1]}"
    # 마지막 4~7자리 숫자 묶음. 종목코드(0 으로 시작하는 6자리) 와 날짜(8자리) 는 아니다
    nums = [g for g in re.findall(r"(?<!\d)(\d{4,7})(?!\d)", stem) if not (len(g) == 6 and g.startswith("0"))]
    if nums:
        return f"hk_{nums[-1]}"
    h = hashlib.sha1((text or stem).encode("utf-8", "replace")).hexdigest()[:10]
    return f"f_{h}"


__all__ = [
    "EpistemicType", "AssertedBy", "Certainty", "CERTAINTY_TAG", "ClaimRole", "ClaimDirection", "SupportVerdict", "ClaimStatus",
    "Coverage", "IssueLevel", "IssueDisplay",
    "ChangeKind", "ChangeDecision", "EvidenceSpan", "QuantRef", "Validation", "Extractor", "Claim", "ClaimSet",
    "NumericSpread", "Issue", "IssueSet", "Change", "ChangeSet", "report_id_for", "SEGMENT_WORDS", "is_segment_text", "normalized_value",
]
