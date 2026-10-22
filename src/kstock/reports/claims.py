"""Claim 추출 (설계 v3 §2.2) — 리포트 텍스트 → 섹션 청킹 → LLM(JSON) → 인용 위치 찾기(EvidenceSpan) → 중복 제거 → ClaimSet.

LLM 이 없으면 `rules_claims()` 가 키워드 문장 분류로 저품질 Claim 을 만든다 (`quality="rules"`). 검증은 `validate_claims.py`.
저장: data/claims/<report_id>.json (ClaimSet JSON).
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..generate.kpi_catalog import driver_catalog_text, normalize_driver
from ..generate.prompts import CLAIM_PROMPT_VERSION, CLAIM_SYSTEM, claim_prompt
from ..models import (
    AssertedBy,
    Certainty,
    Claim,
    ClaimDirection,
    ClaimRole,
    ClaimSet,
    ClaimStatus,
    EpistemicType,
    EvidenceSpan,
    Extractor,
    QuantRef,
    Validation,
    report_id_for,
)
from .extract import ReportFacts, extract_sentences
from .loader import ReportDoc

CLAIMS_DIR = Path("data/claims")
MAX_CHUNK_CHARS = 18000          # ≈ 6k 토큰 (한국어)
MAX_CHUNKS = 6
# 소제목 줄: 짧고(≤ 30자), 가격·숫자 표기가 없어야 한다 ('목표주가 (유지) 460,000원' 은 표지 데이터지 소제목이 아니다)
SECTION_RE = re.compile(r"^(?P<h>(?:투자\s*포인트|Investment\s*Point|핵심\s*포인트|실적\s*(?:전망|리뷰|Preview|Review)|분기\s*실적|"
                        r"밸류에이션|Valuation|목표주가\s*(?:산정|근거)|리스크\s*(?:요인)?|Risk|위험\s*요인|결론|요약|Summary|사업\s*현황|시나리오|Bull|Bear)"
                        r"(?![^\n]*\d[\d,]*\s*원)[^\n\d]{0,30})$", re.I | re.M)
PRIORITY = ("투자포인트", "요약", "실적", "밸류에이션", "리스크", "시나리오", "기타")


@dataclass
class Chunk:
    chunk_id: str
    section: str
    start: int
    end: int
    text: str

    @property
    def priority(self) -> int:
        for i, p in enumerate(PRIORITY):
            if p in self.section:
                return i
        return len(PRIORITY)


# --------------------------------------------------------------------------- 페이지·섹션
def page_of(text: str, offset: int) -> int:
    """정규화된 텍스트의 offset 이 몇 페이지인가 (loader 가 페이지를 '\\f' 로 잇는다). 1부터."""
    return text.count("\f", 0, max(0, offset)) + 1


def _section_name(heading: str) -> str:
    h = heading.lower()
    if re.search(r"투자\s*포인트|investment|핵심\s*포인트|결론|요약|summary|투자의견", h):
        return "투자포인트" if "포인트" in h or "investment" in h else "요약"
    if re.search(r"실적|preview|review|분기", h):
        return "실적"
    if re.search(r"밸류|valuation|목표주가", h):
        return "밸류에이션"
    if re.search(r"리스크|risk|위험", h):
        return "리스크"
    if re.search(r"시나리오|bull|bear", h):
        return "시나리오"
    return "기타"


def chunk_report(doc: ReportDoc, *, max_chars: int = MAX_CHUNK_CHARS, max_chunks: int = MAX_CHUNKS) -> list[Chunk]:
    """소제목으로 자르고, 우선순위 섹션(투자포인트·실적·밸류·리스크) 부터 max_chunks 개. 표지(처음 2,500자) 는 항상 첫 청크에."""
    text = doc.text
    cuts = [m.start() for m in SECTION_RE.finditer(text)]
    bounds = sorted(set([0, *cuts, len(text)]))
    raw: list[tuple[str, int, int]] = []
    for a, b in zip(bounds, bounds[1:], strict=False):
        seg = text[a:b]
        if len(seg.strip()) < 80:
            continue
        head = seg.strip().split("\n", 1)[0]
        raw.append((_section_name(head) if a in cuts else ("투자포인트" if a == 0 else "기타"), a, b))
    # 길이 제한으로 나누기
    pieces: list[tuple[str, int, int]] = []
    for name, a, b in raw:
        while b - a > max_chars:
            cut = text.rfind("\n", a + int(max_chars * 0.7), a + max_chars)
            cut = cut if cut > a else a + max_chars
            pieces.append((name, a, cut))
            a = cut
        pieces.append((name, a, b))
    # 작은 조각은 앞 조각에 합치기 (같은 이름이 아니어도 길이가 짧으면)
    merged: list[tuple[str, int, int]] = []
    for name, a, b in pieces:
        if merged and (b - a) < 1200 and (b - merged[-1][1]) <= max_chars:
            pn, pa, _ = merged[-1]
            merged[-1] = (pn, pa, b)
        else:
            merged.append((name, a, b))
    chunks = [Chunk(f"ch{i + 1}", n, a, b, text[a:b]) for i, (n, a, b) in enumerate(merged)]
    chunks.sort(key=lambda c: (c.priority, c.start))
    return chunks[:max_chunks]


# --------------------------------------------------------------------------- 인용 위치
_WS = re.compile(r"\s+")


def locate_quote(text: str, quote: str, *, min_ratio: float = 0.86) -> tuple[int, int] | None:
    """quote 가 text 어디에 있나 (정규화된 공백 기준). 정확히 없으면 공백을 무시한 비교, 그래도 없으면 difflib 로 가장 비슷한 창(≥ min_ratio)."""
    q = _WS.sub(" ", quote).strip(" \"'“”‘’")
    if len(q) < 8:
        return None
    i = text.find(q)
    if i >= 0:
        return i, i + len(q)
    # 공백 무시 매칭: 양쪽에서 공백을 지운 문자열로 찾고 원문 위치로 되돌린다
    squash = []
    idx = []
    for k, ch in enumerate(text):
        if not ch.isspace():
            squash.append(ch)
            idx.append(k)
    s_text = "".join(squash)
    s_q = _WS.sub("", q)
    j = s_text.find(s_q)
    if j >= 0 and s_q:
        return idx[j], idx[j + len(s_q) - 1] + 1
    # 근사: 길이 비슷한 창을 훑는다 (앞 40자로 후보를 좁힘)
    probe = s_q[:40]
    best: tuple[float, int, int] | None = None
    for m in re.finditer(re.escape(probe[:12]), s_text):
        a = m.start()
        window = s_text[a:a + len(s_q) + 20]
        r = difflib.SequenceMatcher(None, s_q, window[:len(s_q)]).ratio()
        if r >= min_ratio and (best is None or r > best[0]):
            best = (r, idx[a], idx[min(a + len(s_q), len(idx)) - 1] + 1)
    return (best[1], best[2]) if best else None


# --------------------------------------------------------------------------- LLM 답 → Claim
def _date_or_none(v: Any) -> date | None:
    if not v or not isinstance(v, str):
        return None
    try:
        return date.fromisoformat(v.strip()[:10])
    except ValueError:
        return None


def _enum(v: Any, allowed: type, default: Any) -> Any:
    try:
        return allowed(str(v).strip().lower())
    except (ValueError, AttributeError):
        return default


def _roles(v: Any) -> list[ClaimRole]:
    out: list[ClaimRole] = []
    for r in (v or []):
        try:
            cr = ClaimRole(str(r).strip().lower())
        except ValueError:
            continue
        if cr not in out:
            out.append(cr)
    return out


def _quants(v: Any) -> list[QuantRef]:
    out: list[QuantRef] = []
    for q in (v or []):
        if not isinstance(q, dict):
            continue
        try:
            val = float(str(q.get("value", "")).replace(",", ""))
        except ValueError:
            continue
        out.append(QuantRef(metric=str(q.get("metric") or "?"), col=(str(q["col"]) if q.get("col") else None), value=val,
                            unit=(str(q["unit"]) if q.get("unit") else None), source="text"))
    return out


def claims_from_answer(answer: dict[str, Any], *, doc: ReportDoc, report_id: str, broker: str, report_date: date | None,
                       chunk: Chunk, sector: str | None, model: str, spans: list[EvidenceSpan], seq: list[int]) -> list[Claim]:
    out: list[Claim] = []
    for raw in answer.get("claims") or []:
        if not isinstance(raw, dict) or not raw.get("proposition"):
            continue
        # 인용은 quotes(1~3개, c2) 또는 quote(1개, c1). 찾은 것마다 span 하나 — 하나도 못 찾으면 quote=miss.
        qs_raw = raw.get("quotes")
        quotes = [str(q) for q in qs_raw if q] if isinstance(qs_raw, list) else []
        if not quotes and raw.get("quote"):
            quotes = [str(raw["quote"])]
        quotes = quotes[:3]
        span_ids: list[str] = []
        missing: list[str] = []
        for quote in quotes:
            loc = locate_quote(doc.text, quote)
            if not loc:
                missing.append(quote)
                continue
            sp = EvidenceSpan.make(report_id, page_of(doc.text, loc[0]), loc[0], loc[1], doc.text[loc[0]:loc[1]])
            if not any(s.span_id == sp.span_id for s in spans):
                spans.append(sp)
            if sp.span_id not in span_ids:
                span_ids.append(sp.span_id)
        loc = bool(span_ids)
        note = ""
        if not loc:
            note = f"인용을 원문에서 못 찾음: {(quotes[0] if quotes else '')[:60]!r}"
        elif missing:
            note = f"인용 {len(missing)}개는 원문에서 못 찾아 제외: {missing[0][:40]!r}"
        seq[0] += 1
        did = str(raw.get("driver_id") or "").strip().lower() or None
        if did is not None and did not in _driver_ids():
            did = normalize_driver(did, sector=sector)
        label = str(raw.get("driver_label") or "") or None
        if did is None and label:
            did = normalize_driver(label, sector=sector)
        try:
            c = Claim(
                claim_id=f"{report_id}:c{seq[0]}", report_id=report_id, broker=broker, report_date=report_date,
                proposition=str(raw["proposition"]), epistemic_type=_enum(raw.get("epistemic_type"), EpistemicType, EpistemicType.estimate),
                roles=_roles(raw.get("roles")), driver_id=did, driver_label=label,
                target_metric_id=(str(raw["target_metric_id"]) if raw.get("target_metric_id") else None),
                mechanism=(str(raw["mechanism"]) if raw.get("mechanism") else None),
                direction=_enum(raw.get("direction"), ClaimDirection, ClaimDirection.neutral),
                condition=(str(raw["condition"]) if raw.get("condition") else None),
                horizon=(str(raw["horizon"]) if raw.get("horizon") else None),
                quant_refs=_quants(raw.get("quant")), span_ids=span_ids,
                validation=Validation(quote="ok" if loc else "miss", note=note),
                status=ClaimStatus.needs_review, inferred=bool(raw.get("inferred")), section=chunk.section,
                extractor=Extractor(model=model, prompt_version=CLAIM_PROMPT_VERSION, chunk_id=chunk.chunk_id),
                asserted_by=_enum(raw.get("asserted_by"), AssertedBy, AssertedBy.analyst),
                certainty=_enum(raw.get("certainty"), Certainty, Certainty.expected),
                as_of=_date_or_none(raw.get("as_of")),
            )
            # 규칙: realized 는 fact, conditional 은 condition 필수 (없으면 expected 로 내린다)
            if c.certainty == Certainty.realized:
                c.epistemic_type = EpistemicType.fact
            if c.certainty == Certainty.conditional and not c.condition:
                c.certainty = Certainty.expected
        except ValueError:
            continue
        out.append(c)
    return out


def _driver_ids() -> set[str]:
    from ..generate.kpi_catalog import DRIVERS

    return {d.id for d in DRIVERS}


def dedupe(claims: list[Claim], *, threshold: float = 0.85) -> list[Claim]:
    """명제가 거의 같은 Claim 은 하나로 (청크 겹침·반복 서술). 인용이 있는 쪽, 숫자가 있는 쪽을 남긴다."""
    kept: list[Claim] = []
    for c in claims:
        dup = None
        for k in kept:
            if difflib.SequenceMatcher(None, c.proposition, k.proposition).ratio() >= threshold:
                dup = k
                break
        if dup is None:
            kept.append(c)
            continue
        score_c = (c.validation.quote == "ok", len(c.quant_refs), len(c.roles))
        score_k = (dup.validation.quote == "ok", len(dup.quant_refs), len(dup.roles))
        if score_c > score_k:
            kept[kept.index(dup)] = c
    return kept


# --------------------------------------------------------------------------- 추출 진입점
def facts_summary(f: ReportFacts) -> str:
    bits = []
    if f.opinion:
        bits.append(f"의견 {f.opinion}")
    if f.target_price:
        bits.append(f"목표주가 {f.target_price:,.0f}" + (f" (직전 {f.prev_target_price:,.0f})" if f.prev_target_price else ""))
    if f.current_price:
        bits.append(f"현재가 {f.current_price:,.0f}")
    for m in ("op", "eps"):
        cols = f.estimates.get(m)
        if cols:
            bits.append(m + " " + " ".join(f"{c}:{v:,.0f}" for c, v in sorted(cols.items())[-3:]) + (f" ({f.unit})" if m == "op" and f.unit else ""))
    return " · ".join(bits) or "-"


def extract_claims(doc: ReportDoc, facts: ReportFacts, filler, *, sector: str | None = None, name: str | None = None,  # noqa: ANN001
                   ticker: str | None = None, max_chunks: int = MAX_CHUNKS, model_name: str | None = None, progress=None) -> ClaimSet:  # noqa: ANN001
    """LLM 으로 Claim 후보를 뽑아 ClaimSet 을 만든다 (검증은 validate_claims.run_all). 청크마다 complete_json 1회."""
    from ..generate.llm_fill import complete_json

    report_id = report_id_for(str(doc.path), doc.text)
    cs = ClaimSet(report_id=report_id, path=str(doc.path), broker=facts.broker or "", report_date=facts.date, ticker=ticker or facts.ticker,
                  quality="llm")
    chunks = chunk_report(doc, max_chunks=max_chunks)
    model = model_name or getattr(filler, "model", None) or type(filler).__name__
    seq = [0]
    cands: list[Claim] = []
    catalog = driver_catalog_text(sector)
    for i, ch in enumerate(chunks, start=1):
        if progress:
            progress(f"{doc.path.name}: 청크 {i}/{len(chunks)} ({ch.section})", i, len(chunks))
        prompt = claim_prompt(ch.text, broker=facts.broker or "?", report_date=str(facts.date or "?"), name=name or facts.name or "?",
                              ticker=facts.ticker or "?", section=ch.section, catalog_text=catalog, facts_summary=facts_summary(facts))
        try:
            ans = complete_json(filler, CLAIM_SYSTEM, prompt, retries=1)
        except Exception as e:  # noqa: BLE001 — 한 청크 실패해도 나머지는 간다
            cs.notes.append(f"{ch.chunk_id} ({ch.section}) 실패: {type(e).__name__}: {str(e)[:120]}")
            continue
        cands += claims_from_answer(ans, doc=doc, report_id=report_id, broker=facts.broker or "", report_date=facts.date, chunk=ch,
                                    sector=sector, model=str(model), spans=cs.spans, seq=seq)
    cs.claims = dedupe(cands)
    # v4 V3: 얼마나 읽었나 — 청크 한도로 못 읽은 구간이 있으면 '침묵' 을 '안 읽음' 으로 구분할 수 있게
    cs.total_chars = len(doc.text)
    cs.analyzed_chars = min(cs.total_chars, sum(len(ch.text) for ch in chunks if not any(n.startswith(f"{ch.chunk_id} ") and "실패" in n for n in cs.notes)))
    cs.notes.append(f"청크 {len(chunks)}개 · 후보 {len(cands)} → 중복 제거 {len(cs.claims)} · 인용 확인 {sum(1 for c in cs.claims if c.validation.quote == 'ok')}"
                    + (f" · 읽은 비율 {cs.analyzed_ratio:.0%}" if cs.analyzed_ratio < 0.95 else ""))
    return cs


KIND_TO_ROLE = {"thesis": ClaimRole.thesis, "risk": ClaimRole.risk, "catalyst": ClaimRole.catalyst, "valuation": ClaimRole.valuation,
                "shareholder": ClaimRole.shareholder, "business": ClaimRole.business}


def rules_claims(doc: ReportDoc, facts: ReportFacts, *, sector: str | None = None, ticker: str | None = None, per_kind: int = 5) -> ClaimSet:
    """LLM 없이: 키워드 문장 분류 → 저품질 Claim (quality='rules', validation.support=unchecked, verified 로 두어 맵에 쓰되 표기)."""
    report_id = report_id_for(str(doc.path), doc.text)
    cs = ClaimSet(report_id=report_id, path=str(doc.path), broker=facts.broker or "", report_date=facts.date, ticker=ticker or facts.ticker,
                  quality="rules")
    sents = facts.sentences or extract_sentences(doc.text, name=facts.name)
    n = 0
    for kind, role in KIND_TO_ROLE.items():
        for s in [x for x in sents if x.kind == kind][:per_kind]:
            loc = locate_quote(doc.text, s.text)
            span_ids: list[str] = []
            if loc:
                sp = EvidenceSpan.make(report_id, page_of(doc.text, loc[0]), loc[0], loc[1], doc.text[loc[0]:loc[1]])
                cs.spans.append(sp)
                span_ids.append(sp.span_id)
            n += 1
            cs.claims.append(Claim(
                claim_id=f"{report_id}:r{n}", report_id=report_id, broker=facts.broker or "", report_date=facts.date,
                proposition=s.text[:200], epistemic_type=EpistemicType.opinion, roles=[role],
                direction=ClaimDirection.negative if kind == "risk" else ClaimDirection.positive if kind in ("thesis", "catalyst") else ClaimDirection.neutral,
                span_ids=span_ids, validation=Validation(quote="ok" if loc else "miss", numbers="none"),
                status=ClaimStatus.verified if loc else ClaimStatus.needs_review, section="기타",
                extractor=Extractor(model="rules", prompt_version="kw1")))
    cs.notes.append(f"규칙 분류(저품질) {len(cs.claims)}건 — LLM 이 없을 때의 대체")
    return cs


# --------------------------------------------------------------------------- 저장
def save_claimset(cs: ClaimSet, out_dir: Path = CLAIMS_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{cs.report_id}.json"
    p.write_text(cs.model_dump_json(indent=2), encoding="utf-8")
    return p


def load_claimset(path: Path) -> ClaimSet:
    return ClaimSet.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def load_claimsets(dir_: Path = CLAIMS_DIR, *, report_ids: list[str] | None = None) -> list[ClaimSet]:
    if not dir_.is_dir():
        return []
    out = []
    for p in sorted(dir_.glob("*.json")):
        if report_ids and p.stem not in report_ids:
            continue
        try:
            out.append(load_claimset(p))
        except Exception:  # noqa: BLE001
            continue
    return out


__all__ = ["Chunk", "CLAIMS_DIR", "MAX_CHUNK_CHARS", "MAX_CHUNKS", "page_of", "chunk_report", "locate_quote", "claims_from_answer",
           "dedupe", "extract_claims", "rules_claims", "facts_summary", "save_claimset", "load_claimset", "load_claimsets"]
