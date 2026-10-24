"""mindmap CLI (패키지 kstock).

  mindmap new       --ticker 005930 --name 삼성전자 --type growth -o maps/A_005930.json
  mindmap validate  maps/A_005930.json
  mindmap eval      maps/A_005930.json                      # A: ER·RR·제안 비중·게이트 / B: 상태등·매트릭스
  mindmap snapshot  maps/A_005930.json --entry-price 58000 --qty 100 --w-target 0.05 -o maps/B_005930.json
  mindmap market    maps/B_005930.json --price 76000 --w-now 0.072 [--eps-fwd 3392 --band 82 --total-assets 1e9]
  mindmap status    maps/B_005930.json                      # = eval (보유 맵)
  mindmap render    maps/B_005930.json -o out/B.html [--format html|markmap|md|mermaid] [--layout balanced|radial|right] [--summary]
  mindmap rollup    maps/B_*.json --cash 80000000 -o maps/C.json
  mindmap demo      -o out/demo                             # 가온로보틱스 예시 끝까지
  mindmap console   [--db data/db/kstock.sqlite]            # GUI 콘솔 (관심종목·맵 관리) · --text 면 번호 메뉴 콘솔
  mindmap draft     --reports a.pdf b.pdf [--ticker 005930] [--llm] [--db data/db/kstock.sqlite | -o maps/A_draft.json] [--html]
                                                            # 증권사 리포트 여러 건 → A 맵 초안 (판단은 WATCH, 값은 draft 태그)
  mindmap sensitivity <맵> [--driver A1.1 --delta 10 --set k=v --save]   # 원인 드라이버 민감도 (설계 v5)
  mindmap valuation   <맵> [--per 6.36 --eps-basis normalized --sotp 메모리=6 … --save]   # 평가 모델 PER·정상화·SOTP·역산·매력도·신뢰도 (설계 v6)
  mindmap scenario    <맵> --set Bear.A1.1=-30 --set Bear.per=3.5 --probs 30/45/25 --save   # 시나리오 = 가정 묶음 → 통합 모델 (설계 v6)
  mindmap implied     <맵> [--per 6.36/4.5/3.5]                              # 현재가가 내재하는 EPS·변수 경로 (설계 v6)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from . import __version__
from .engine import (
    evaluate_holding,
    evaluate_new_map,
    rollup,
    snapshot_to_holding,
    update_market,
    validate,
)
from .env import load_dotenv
from .models import HoldingMap, MarketState, NewMap, Position, load_map_file
from .render import to_markdown, to_mermaid, write_html, write_markmap_html
from .templates import new_map_skeleton


def _today(s: str | None) -> date:
    return date.fromisoformat(s) if s else date.today()


def _expand(patterns: list[str]) -> list[Path]:
    """Windows 셸은 글롭을 안 풀어 주므로 여기서 푼다. 파일이면 그대로."""
    import glob

    out: list[Path] = []
    for pat in patterns:
        hits = sorted(glob.glob(pat)) if any(ch in pat for ch in "*?[") else [pat]
        out.extend(Path(h) for h in hits)
    return out


def _print_issues(issues) -> int:
    for i in issues:
        print(" ", i)
    errs = [i for i in issues if i.level == "error"]
    print(f"→ 오류 {len(errs)}건, 경고 {len(issues) - len(errs)}건")
    return 1 if errs else 0


# --------------------------------------------------------------------------- commands
def cmd_new(a: argparse.Namespace) -> int:
    m = new_map_skeleton(a.ticker, a.name, a.type, _today(a.date), n_drivers=a.drivers)
    out = Path(a.output or f"maps/A_{a.ticker}.json")
    m.save(out)
    print(f"A 맵 스켈레톤 생성: {out}  (노드 {len(m.nodes)}개, 프로파일 {a.type})")
    print("다음: A0 논제 → A1 드라이버(current/my_estimate) → A3 시나리오(확률 합 1) → A4.4 Kill criteria → A6.5 판단")
    return 0


def cmd_validate(a: argparse.Namespace) -> int:
    m = load_map_file(a.path)
    print(f"{m.kind.value} {m.map_id}")
    return _print_issues(validate(m, today=_today(a.date)))


def cmd_eval(a: argparse.Namespace) -> int:
    m = load_map_file(a.path)
    today = _today(a.date)
    if isinstance(m, NewMap):
        issues = validate(m, today=today)
        res = evaluate_new_map(m, today=today)
        print(f"기대수익률 {res.expected_return:+.1%}" if res.expected_return is not None else "기대수익률 계산 불가")
        print(f"손익비 {res.risk_reward:.2f}" if res.risk_reward is not None else "손익비 계산 불가(하락위험 0)")
        if res.suggested_weight is not None:
            print(f"제안 비중 {res.suggested_weight:.2%}  (A6.1 에 confirmed 를 적어 확정)")
        print(f"rr_min 게이트: {'통과' if res.rr_gate_passed else '미통과'}  판단: {m.decision.value if m.decision else '-'}")
        for n in res.notes:
            print(" !", n)
        if res.checkpoints:
            print("체크포인트:", ", ".join(d.isoformat() for d in res.checkpoints))
        rc = _print_issues(issues)
    elif isinstance(m, HoldingMap):
        res = evaluate_holding(m, today=today)
        print(res.summary())
        rc = _print_issues(validate(m, today=today))
    else:
        print("eval 은 A/B 맵만 지원")
        return 2
    if not a.dry_run:
        m.save(a.path)
        print(f"저장: {a.path}")
    return rc


def cmd_snapshot(a: argparse.Namespace) -> int:
    m = load_map_file(a.path)
    if not isinstance(m, NewMap):
        print("snapshot 은 A 맵에서만")
        return 2
    today = _today(a.date)
    issues = validate(m, today=today)
    if any(i.level == "error" for i in issues) and not a.force:
        print("A 맵이 완료 상태가 아닙니다 (--force 로 강제):")
        return _print_issues(issues)
    w_target = a.w_target
    if w_target is None:
        v = m["A6.1"].value if "A6.1" in m else None
        w_target = (v or {}).get("confirmed") if isinstance(v, dict) else None
    if w_target is None:
        print("--w-target 또는 A6.1.confirmed 가 필요합니다")
        return 2
    pos = Position(entry_date=today, entry_price=a.entry_price, quantity=a.qty, w_target=w_target,
                   eps_fwd_entry=a.eps_fwd)
    b = snapshot_to_holding(m, pos, today=today)
    out = Path(a.output or f"maps/B_{m.ticker}.json")
    b.save(out)
    print(f"B 맵 생성: {out}  (B1 스냅숏 해시 {b.snapshot_hash}, 규칙 {len(b.rules())}개)")
    return 0


def cmd_market(a: argparse.Namespace) -> int:
    m = load_map_file(a.path)
    if not isinstance(m, HoldingMap):
        print("market 은 B 맵에서만")
        return 2
    mk = MarketState(as_of=_today(a.date), price=a.price, eps_fwd=a.eps_fwd, band_percentile=a.band,
                     w_now=a.w_now, total_assets=a.total_assets, adv_shares=a.adv, cash_yield=a.cash_yield)
    update_market(m, mk)
    m.save(a.path)
    print(f"시장 상태 갱신: 가격 {a.price:,.0f}, 비중 {a.w_now if a.w_now is not None else '-'} → {a.path}")
    return 0


def cmd_render(a: argparse.Namespace) -> int:
    m = load_map_file(a.path)
    fmt = a.format
    out = Path(a.output) if a.output else Path(a.path).with_suffix({"html": ".html", "markmap": ".html", "md": ".md", "mermaid": ".mmd"}[fmt])
    out.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "html":
        write_html(m, out, summary=a.summary, layout=a.layout)
    elif fmt == "markmap":
        write_markmap_html(m, out, summary=a.summary)
    elif fmt == "md":
        out.write_text(to_markdown(m, summary=a.summary), encoding="utf-8")
    else:
        out.write_text(to_mermaid(m), encoding="utf-8")
    print(f"렌더링: {out}")
    return 0


def cmd_rollup(a: argparse.Namespace) -> int:
    maps = [load_map_file(p) for p in a.paths]
    holdings = [m for m in maps if isinstance(m, HoldingMap)]
    if not holdings:
        print("B 맵이 없습니다")
        return 2
    c = rollup(holdings, cash=a.cash, total_assets=a.total_assets, today=_today(a.date), cash_yield=a.cash_yield)
    out = Path(a.output or "maps/C.json")
    c.save(out)
    c0 = c.value("C0")
    print(f"C 맵: {out}")
    print(f"  총자산 {c0['total_assets']:,.0f} · 현금 {c0['cash_weight']:.1%} · 종목 {c0['n_holdings']}개 · 상태 {c0['status_dist']}")
    print(f"  순매수 {c.value('C3.1')['net_buy']:,.0f} / 순매도 {c.value('C3.1')['net_sell']:,.0f} → 실행 후 현금 {c.value('C3.2'):.1%}")
    for w in c.meta.get("warnings", []):
        print("  !", w)
    for cf in c.value("C3.4") or []:
        print("  ⚠", cf)
    return 0


def cmd_console(a: argparse.Namespace) -> int:
    rest = list(a.rest)
    if "--text" in rest:
        rest.remove("--text")
        from .console_text import main as text_main

        return text_main(rest)
    from .console import main as gui_main

    return gui_main(rest)


def cmd_draft(a: argparse.Namespace) -> int:
    from .generate import DraftError, draft_from_reports, llm_status, make_filler
    from .symbols import default_book, research_db_path

    today = _today(a.date)
    if a.llm:
        print("LLM: " + llm_status())
    paths = _expand(a.reports)

    def progress(label: str, i: int, n: int) -> None:
        print(f"  [{i}/{n}] {label}", flush=True)

    if a.db:
        from .ops import Ops, OpsError
        from .store import Store

        with Store(a.db) as store:
            ops = Ops(store, today=today)
            try:
                r = ops.draft_from_reports(paths, {"ticker": a.ticker, "name": a.name, "invest_type": a.type, "use_llm": a.llm,
                                                   "llm_kind": a.llm_kind, "save": not a.no_save, "fetch_price": not a.no_fetch,
                                                   "claims": a.claims if a.claims != "auto" else None, "issue_check": not a.no_issue_check,
                                                   "mode": a.mode, "prefill_suggestions": a.prefill_suggestions},
                                           progress=progress)
            except OpsError as e:
                print(f"오류: {e}")
                return 2
            print(r.summary_text())
            if not a.no_save:
                print(f"\n저장소: {store.db_path} → 맵 {r.map.map_id} (콘솔 「다시 읽기」)")
            out_html = Path(a.html) if isinstance(a.html, str) else (store.out_path(r.map.map_id.replace(":", "_") + ".html") if a.html else None)
            if out_html:
                write_html(r.map, out_html)
                print(f"HTML: {out_html}")
            return 0 if r.complete else 1
    filler = None
    if a.llm:
        try:
            filler = make_filler(a.llm_kind)
        except RuntimeError as e:
            print(f"LLM 을 쓸 수 없습니다 — {e}. --llm 없이 규칙 채움만으로 진행할 수 있습니다.")
            return 2
    try:
        r = draft_from_reports(paths, ticker=a.ticker, name=a.name, invest_type=a.type or "growth", today=today, symbols=default_book(),
                               filler=filler, research_db=research_db_path(), progress=progress, fetch_price=not a.no_fetch,
                               claims=a.claims, claims_dir=Path(a.claims_dir) if a.claims_dir else None, issue_check=not a.no_issue_check,
                               prefill_suggestions=a.prefill_suggestions)
    except DraftError as e:
        print(f"오류: {e}")
        return 2
    print(r.summary_text())
    out = Path(a.output or f"maps/A_{r.map.ticker}_draft.json")
    r.map.save(out)
    print(f"\nJSON: {out}")
    if a.html:
        out_html = Path(a.html) if isinstance(a.html, str) else out.with_suffix(".html")
        write_html(r.map, out_html)
        print(f"HTML: {out_html}")
    return 0 if r.complete else 1


def cmd_eval_issues(a: argparse.Namespace) -> int:
    """종목별 쟁점 정답(tests/golden/issues/<ticker>.json) 대비 IssueSet(data/issues/<ticker>.json) 의 테마 재현·분류 일치·이견 누락 (설계 v4 V9)."""
    from .generate.evals import eval_issues
    from .models import IssueSet

    rows = []
    for gp in sorted(Path(a.golden).glob("*.json")):
        g = json.loads(gp.read_text(encoding="utf-8"))
        if not g.get("required_issues"):
            continue
        ip = Path(a.issues) / f"{g.get('ticker') or gp.stem}.json"
        if not ip.is_file():
            print(f"  {gp.stem}: IssueSet 없음 ({ip}) — mindmap issues --out {ip} 또는 draft 로 만들 것")
            continue
        iss = IssueSet.model_validate(json.loads(ip.read_text(encoding="utf-8")))
        ev = eval_issues(g, iss)
        rows.append(ev)
        print(f"  {ev.ticker}: 테마 {ev.found}/{ev.required} ({ev.theme_recall:.0%}) · 분류 일치 {ev.display_ok}/{ev.required} ({ev.display_accuracy:.0%})"
              f" · 이견 누락 {ev.missed_disagreements}" + ("" if g.get("verified") else " · (정답표 verified:false)"))
        for mi in ev.misses:
            print(f"      ✗ {mi}")
    if not rows:
        print("쟁점 정답표가 없다 — tests/golden/issues/<ticker>.json 의 required_issues 를 채울 것")
        return 2
    md = sum(r.missed_disagreements for r in rows)
    print(f"합계: 이견 누락 {md}건 (목표 0)")
    return 0 if md == 0 else 1


def cmd_llm_check(a: argparse.Namespace) -> int:
    """ANTHROPIC_API_KEY 가 실제로 동작하는지 — 출처·계열·인증·모델·작은 호출 1회. 키 값은 찍지 않는다."""
    from .generate import llm_check

    r = llm_check(model=a.model, call=not a.no_call)
    print("\n".join(r.lines()))
    return 0 if r.ok else 1


def cmd_golden_draft(a: argparse.Namespace) -> int:
    """리포트 파일들 → 정답표 초안 (tests/golden/extract/<report_id>.json, verified: false). 사람이 고치고 true 로 바꾼다."""
    from .generate.evals import write_golden_drafts

    paths = _expand(a.reports)
    written = write_golden_drafts(paths, Path(a.out), today=_today(a.date), root=Path(a.root), overwrite=a.overwrite)
    for w in written:
        print(f"초안: {w}")
    print(f"{len(written)}건 작성 (이미 verified 인 것은 건너뜀)")
    return 0


def cmd_eval_extract(a: argparse.Namespace) -> int:
    """정답표 대비 추출기 정확도."""
    from .generate.evals import eval_extract

    ev = eval_extract(Path(a.golden), root=Path(a.root), today=_today(a.date), verified_only=a.verified_only)
    print(ev.table())
    if a.misses:
        for s in ev.fields.values():
            for m in s.misses:
                print(f"    {s.field}: {m}")
    return 0 if ev.overall >= a.min_acc else 1


def cmd_claims(a: argparse.Namespace) -> int:
    """리포트 파일들 → Claim 추출(LLM) + 3단 검증 → data/claims/<report_id>.json. --rules 면 LLM 없이 키워드 분류(저품질)."""
    from .generate import guess_sector, make_filler
    from .reports import extract_facts, load_reports
    from .reports.claims import extract_claims, rules_claims, save_claimset
    from .reports.validate_claims import run_all

    today = _today(a.date)
    paths = _expand(a.reports)
    docs = [d for d in load_reports(paths) if not d.is_empty]
    if not docs:
        print("읽을 수 있는 리포트가 없습니다")
        return 2
    filler = None
    if not a.rules:
        try:
            filler = make_filler(a.llm_kind)
        except RuntimeError as e:
            print(f"LLM 을 쓸 수 없습니다 — {e}. --rules 로 키워드 분류(저품질)만 할 수 있습니다.")
            return 2
    out_dir = Path(a.out)
    total = {"verified": 0, "needs_review": 0, "rejected": 0}
    facts = [extract_facts(d, hint_ticker=a.ticker, today=today) for d in docs]
    from collections import Counter

    codes = Counter(f.ticker for f in facts if f.ticker)
    ticker = a.ticker or (codes.most_common(1)[0][0] if codes else None)
    names = Counter(f.name for f in facts if f.name)
    name = names.most_common(1)[0][0] if names else None
    from .reports.validate_claims import mark_review_required

    done: list = []
    for d, f in zip(docs, facts, strict=False):
        sector = a.sector or guess_sector(d.text, name=name)[0]
        if filler is None:
            cs = rules_claims(d, f, sector=sector, ticker=ticker)
        else:
            cs = extract_claims(d, f, filler, sector=sector, name=name, ticker=ticker,
                                progress=(lambda lbl, i, n: print(f"  [{i}/{n}] {lbl}", flush=True)))
        counts = run_all(cs, f, filler, semantic=not a.no_semantic, fulltext=d.text)
        done.append((cs, f, counts))
    n_req = mark_review_required([cs for cs, _, _ in done], facts_by_report={cs.report_id: f for cs, f, _ in done})   # 증권사 간 수치 충돌은 전부 모아서 (v4 V2)
    for cs, f, counts in done:
        p = save_claimset(cs, out_dir)
        for k in total:
            total[k] += counts[k]
        print(f"{cs.report_id} {f.broker or '?'} {f.date or '?'} · Claim {len(cs.claims)} (verified {counts['verified']} / 검토 {counts['needs_review']} / 반려 {counts['rejected']}) → {p}")
        for n in cs.notes:
            print(f"    · {n}")
        if a.show:
            for c in cs.claims:
                mark = {"verified": "✓", "needs_review": "?", "rejected": "✗"}[c.status.value]
                flag = " ⚑" if c.review_required else ""
                print(f"    {mark}{flag} [{c.tag}|{','.join(r.value for r in c.roles) or '-'}|{c.direction.value[:3]}|{c.driver_id or c.driver_label or '-'}] {c.proposition[:110]}")
    print(f"합계: verified {total['verified']} · 검토 대기 {total['needs_review']} · 반려 {total['rejected']}"
          + (f" · 확인 권고 ⚑ {n_req} (자동 통과했지만 판단에 직접 쓰이는 수치·증권사 간 충돌·조건부 리스크 — claims-review 에 함께 뜬다)" if n_req else ""))
    if filler is not None and sum(total.values()) == 0:
        print("⚠ Claim 이 하나도 안 나왔다 — 위 '실패' 메모를 본다. 저장된 빈 결과는 다음 draft 에서 캐시로 쓰지 않는다 (다시 추출).")
        return 1
    return 0


def cmd_claims_review(a: argparse.Namespace) -> int:
    """검토 대기함: 목록 보기 / 승인 / 반려."""
    from .reports.claims import load_claimset, save_claimset
    from .reports.validate_claims import review

    p = Path(a.out) / f"{a.report_id}.json"
    if not p.is_file():
        print(f"없음: {p}")
        return 2
    cs = load_claimset(p)
    if a.recheck:
        # 코드 검증(①인용 ②숫자) 만 다시 — LLM 호출 없음. 저장된 의미 판정은 그대로 두고 상태만 다시 확정한다
        # (예: 숫자 규칙이 바뀐 뒤 '명제에 없는 수치' 때문에 검토 대기에 있던 것이 verified 로).
        from .reports import extract_facts, load_report
        from .reports.validate_claims import check_numbers, finalize

        facts, fulltext = None, None
        if cs.path and Path(cs.path).is_file():
            doc = load_report(Path(cs.path))
            facts = extract_facts(doc, hint_ticker=cs.ticker)
            fulltext = doc.text
        changed = 0
        for c in cs.claims:
            before = c.status
            c.validation.quote = "ok" if c.span_ids else "miss"
            if c.verified_by != "human":
                c.review_required, c.review_reason = False, ""
            c.validation.numbers = check_numbers(c, cs, facts, fulltext=fulltext)
            if not c.inferred:
                finalize(c, require_semantic=(cs.quality != "rules"))
            if c.status != before:
                changed += 1
                print(f"재검증 {c.claim_id}: {before.value} → {c.status.value}")
        from .reports.validate_claims import mark_review_required

        if a.llm:
            # 검토 대기(사람이 안 본 것) 의 의미 판정만 LLM 에 다시 묻는다 — 판정 프롬프트가 바뀐 뒤 (수치는 코드 몫) 재판정용
            from .generate import make_filler
            from .models import SupportVerdict
            from .reports.validate_claims import semantic_check

            try:
                filler = make_filler(a.llm_kind)
            except RuntimeError as e:
                print(f"LLM 을 쓸 수 없어 의미 재판정은 건너뜀 — {e}")
                filler = None
            if filler is not None:
                targets = [c for c in cs.claims if c.status.value == "needs_review" and c.verified_by != "human" and c.span_ids]
                prior = {c.claim_id: c.validation.support for c in targets}
                for c in targets:
                    c.validation.support = SupportVerdict.unchecked
                n_notes = len(cs.notes)
                semantic_check(cs, filler)
                for n in cs.notes[n_notes:]:
                    print(f"    ! {n}")                                   # 배치 실패(네트워크 등) 는 여기 보인다
                for c in targets:
                    if c.validation.support == SupportVerdict.unchecked:
                        c.validation.support = prior[c.claim_id]            # 답을 못 받았으면 이전 판정 유지
                    before = c.status
                    finalize(c, require_semantic=(cs.quality != "rules"))
                    if c.status != before:
                        changed += 1
                        print(f"의미 재판정 {c.claim_id}: {before.value} → {c.status.value} ({c.validation.support.value})")
                got = sum(1 for c in targets if c.validation.support != prior[c.claim_id] or c.status.value == "verified")
                print(f"의미 재판정 {len(targets)}건 (답 받음 {got})")
        mark_review_required([cs], facts_by_report={cs.report_id: facts} if facts is not None else None)
        cs.notes.append(f"재검증(코드{' + 의미 재판정' if a.llm else ''}) · 상태 변경 {changed}")
        save_claimset(cs, Path(a.out))
        print(f"재검증: 상태 변경 {changed}건" + ("" if facts else " (리포트 파일을 못 찾아 표 대조는 생략)"))
    for cid in a.approve or []:
        try:
            review(cs, cid, "approve", note=a.note or "")
        except ValueError as e:
            print(f"! {e}")
            continue
        print(f"승인 {cid}")
    for cid in a.reject or []:
        review(cs, cid, "reject", note=a.note or "")
        print(f"반려 {cid}")
    if a.approve or a.reject:
        save_claimset(cs, Path(a.out))
    queue = cs.review_queue()
    n_req = sum(1 for c in queue if c.status.value == "verified")
    print(f"{cs.report_id} · 검토 대기 {len(queue)} / 전체 {len(cs.claims)}" + (f" (자동 통과·확인 권고 ⚑ {n_req} 포함)" if n_req else ""))
    for c in queue:
        sp = cs.span(c.span_ids[0]) if c.span_ids else None
        mark = "⚑" if c.status.value == "verified" else "?"
        print(f"  {mark} {c.claim_id} [{c.tag}|{','.join(r.value for r in c.roles) or '-'}] {c.proposition[:100]}")
        if c.review_required:
            print(f"      확인 권고: {c.review_reason}")
        print(f"      검증: 인용 {c.validation.quote} · 숫자 {c.validation.numbers} · 의미 {c.validation.support.value}" + (f" · {c.validation.note[:80]}" if c.validation.note else "")
              + (" · 승인하려면 --note 필요" if c.validation.support.value == "insufficient" else ""))
        if sp:
            print(f"      인용 p.{sp.page}: {sp.text[:120]!r}")
    return 0


def cmd_issues(a: argparse.Namespace) -> int:
    """data/claims 의 ClaimSet 들 → 증권사 간 쟁점(Issue) 표. --ticker 로 종목 필터, --out 으로 IssueSet JSON 저장."""
    from .generate.issues import build_issues, issues_table
    from .reports.claims import load_claimsets

    css = load_claimsets(Path(a.claims), report_ids=a.report_ids)
    if a.ticker:
        css = [c for c in css if c.ticker == a.ticker]
    if not css:
        print("ClaimSet 이 없다 — 먼저 mindmap claims 를 돌릴 것")
        return 2
    filler = None
    if a.llm and not a.no_issue_check:
        from .generate import make_filler

        try:
            filler = make_filler(a.llm_kind)
        except RuntimeError as e:
            print(f"LLM 을 쓸 수 없어 방향 통계로 판정한다 — {e}")
    iss = build_issues(css, ticker=a.ticker or css[0].ticker, include_review=a.include_review, filler=filler, issue_check=not a.no_issue_check)
    print("리포트: " + ", ".join(f"{c.broker} {c.report_date}" for c in css))
    print(issues_table(iss))
    for n in iss.notes:
        print("  · " + n)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(iss.model_dump_json(indent=2), encoding="utf-8")
        print(f"저장: {a.out}")
    return 0


def cmd_changes(a: argparse.Namespace) -> int:
    """기존 A 맵 + 새 리포트 → 변경안 (맵은 바꾸지 않는다). --apply 로 승인/반려를 적용."""
    from .generate.changes import changeset_table, load_changeset
    from .ops import Ops, OpsError
    from .store import Store

    with Store(a.db) as store:
        ops = Ops(store, today=_today(a.date))
        try:
            m = store.load_map(a.map)
        except KeyError:
            print(f"맵 없음: {a.map}")
            return 2
        if a.apply:
            cs = load_changeset(Path(a.apply))
            decisions = {cid: "approve" for cid in (a.approve or [])}
            decisions.update({cid: "reject" for cid in (a.reject or [])})
            try:
                done = ops.apply_changes(m, cs, decisions)
            except OpsError as e:
                print(f"오류: {e}")
                return 2
            print(f"적용 {len(done)}건 → {m.map_id} v{m.meta.get('version')}")
            for x in done:
                print("  " + x)
            return 0
        try:
            cs = ops.propose_changes(m, _expand(a.reports or []), {"fetch_price": not a.no_fetch, "use_llm": a.llm, "llm_kind": a.llm_kind})
        except OpsError as e:
            print(f"오류: {e}")
            return 2
        print(changeset_table(cs))
        print(f"저장: {sorted(store.changes_dir.glob('*.json'))[-1]}")
        print("적용: mindmap changes --map <id> --apply <파일> --approve ch1 ch2 --reject ch3")
    return 0


def _edit_target(a: argparse.Namespace) -> tuple[Any, Any]:
    """`--map` 이 파일이면 FileTarget, 아니면 저장소(--db) 의 맵 id. (target, store|None)"""
    from .render.server import FileTarget, StoreTarget
    from .store import Store

    ref = a.map
    if Path(ref).is_file():
        return FileTarget(ref), None
    store = Store(a.db)
    try:
        store.load_map(ref)
    except KeyError:
        store.close()
        raise SystemExit(f"맵 없음: {ref} (파일 경로나 저장소의 맵 id)") from None
    return StoreTarget(store, ref, log=print), store


def cmd_edit(a: argparse.Namespace) -> int:
    """마인드맵 HTML 을 로컬 편집 서버로 연다 — 「저장」 이 맵 JSON 에 바로 들어간다 (Ctrl+C 로 종료)."""
    from .render.server import EditServer

    target, store = _edit_target(a)
    try:
        srv = EditServer(target, today=_today(a.date), port=a.port, layout=a.layout, log=print if a.verbose else None)
        print(f"편집 서버: {srv.url}")
        print("브라우저에서 노드를 더블클릭해 고치고 「저장」 — 이 창을 닫으면(Ctrl+C) 서버가 멈춘다. 저장 전 편집은 사라진다.")
        if not a.no_browser:
            srv.open_browser()
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            srv.shutdown()
            print(f"편집 서버 종료 (저장 {srv.saves}회)")
    finally:
        if store is not None:
            store.close()
    return 0


def cmd_apply_edits(a: argparse.Namespace) -> int:
    """file:// 로 연 HTML 이 내려준 `*.edits.json` 을 맵에 반영한다."""
    from .generate.edits import EditError, load_edit_file
    from .render.server import save_edits

    try:
        payload = load_edit_file(a.file)
    except (EditError, OSError, ValueError) as e:
        print(f"오류: {e}")
        return 2
    if not a.map:
        a.map = payload.get("map_id") or ""
        if not a.map:
            print("오류: 편집 파일에 map_id 가 없다 — --map 으로 지정")
            return 2
    target, store = _edit_target(a)
    try:
        out = save_edits(target, payload, today=_today(a.date), check_hash=not a.force)
    except EditError as e:
        print(f"거절: {e}")
        return 2
    finally:
        if store is not None:
            store.close()
    print(f"적용 {len(out['applied'])}건 → {out['map_id']} v{out['version']}  ({out['path']})")
    for x in out["applied"]:
        print("  " + x)
    for w in out["warnings"]:
        print("  ⚑ " + w)
    print(f"HTML: {out['html']}")
    return 0


def cmd_suggest(a: argparse.Namespace) -> int:
    """이미 있는 맵에 리포트 기반 제안값을 (다시) 만든다 — 컨센·진입 가격대는 맵의 meta 만으로 되고, 쟁점 소수 의견은 draft 때 만들어진다."""
    from .generate.suggest import build_suggestions, stats_text

    target, store = _edit_target(a)
    try:
        m = target.load()
        sugg = build_suggestions(m)
        if not a.dry_run:
            target.save(m)
            target.render(m)
        print(f"{m.map_id}: 제안값 {sum(len(v) for v in sugg.values())}개 — " + ", ".join(f"{k}({len(v)})" for k, v in sugg.items()) + (" (저장 안 함)" if a.dry_run else ""))
        print("  " + stats_text(m))
    finally:
        if store is not None:
            store.close()
    return 0


def cmd_sensitivity(a: argparse.Namespace) -> int:
    """원인 드라이버 민감도 (설계 v5 §3) — 보기 · 충격 크기 바꾸기 · 입력 넣기(--set segment_revenue=5400000) · 저장."""
    from .engine.sensitivity import compute_all, compute_sensitivity, sensitivity_table

    target, store = _edit_target(a)
    try:
        m = target.load()
        if a.set:
            if not a.driver:
                print("오류: --set 은 --driver 와 함께")
                return 2
            d = m[a.driver].driver
            for kv in a.set:
                k, _, v = kv.partition("=")
                d.sensitivity_inputs[k.strip()] = None if v.strip() in ("", "none", "null") else float(v)
        if a.driver:
            m[a.driver].driver.sensitivity = compute_sensitivity(m, a.driver, delta_pct=a.delta, today=_today(a.date))
        else:
            compute_all(m, today=_today(a.date))
        print(sensitivity_table(m))
        if a.save:
            target.save(m)
            target.render(m)
            print(f"저장: {m.map_id}")
        elif a.set:
            print("(저장하려면 --save)")
    finally:
        if store is not None:
            store.close()
    return 0


def _parse_kv(items: list[str] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kv in items or []:
        k, _, v = kv.partition("=")
        v = v.strip()
        if v in ("", "none", "null"):
            out[k.strip()] = None
            continue
        try:
            out[k.strip()] = float(v)
        except ValueError:
            out[k.strip()] = v
    return out


def cmd_valuation(a: argparse.Namespace) -> int:
    """평가 모델 (설계 v6 A3.8/A3.9) — 기준·PER·주식 수·순현금·SOTP 멀티플을 A3.8 에 넣고 PER·정상화·SOTP·역산을 계산한다."""
    from .engine.attractiveness import attractiveness_line, compute_valuation_all, confidence_line
    from .engine.valuation import Criteria, ensure_valuation_nodes, valuation_table
    from .models import ValuationModel

    target, store = _edit_target(a)
    try:
        m = target.load()
        ensure_valuation_nodes(m)
        crit = dict(m["A3.8"].value) if isinstance(m["A3.8"].value, dict) else {}
        changed = False
        if a.eps_basis:
            crit["eps_basis"] = a.eps_basis
            changed = True
        for key, val in (("eps", a.eps), ("eps_year", a.eps_year), ("per", a.per), ("shares", a.shares), ("net_cash", a.net_cash), ("dps", a.dps),
                         ("dividend_in_return", a.dividend)):
            if val is not None:
                crit[key] = val
                changed = True
        if a.sotp:
            mult = dict(crit.get("sotp_multiples") or {})
            mult.update({k: v for k, v in _parse_kv(a.sotp).items()})
            crit["sotp_multiples"] = {k: v for k, v in mult.items() if v is not None}
            changed = True
        if a.stake:
            st = dict(crit.get("sotp_stakes") or {})
            st.update(_parse_kv(a.stake))
            crit["sotp_stakes"] = {k: v for k, v in st.items() if v is not None}
            changed = True
        if a.clear:
            crit = {}
            changed = True
        if changed:
            m["A3.8"].value = crit or None
            m["A3.8"].as_of = _today(a.date)
            m["A3.8"].tags = sorted(set(m["A3.8"].tags) | {"user"})
        notes = compute_valuation_all(m, today=_today(a.date))
        vm = ValuationModel.model_validate((m.meta.get("draft") or {}).get("valuation") or {})
        c = Criteria.from_map(m)
        print(f"{m.map_id} 평가 기준(A3.8): EPS 기준 {c.eps_basis} · 연도 {c.eps_year or '표 기준'} · PER {c.per if c.per is not None else '기본값'} · 주식 수 {c.shares if c.shares else '유도/공시'}"
              f" · 순현금 {c.net_cash if c.net_cash is not None else '표'} · 배당 {c.dividend_in_return} · SOTP 멀티플 {c.sotp_multiples or '없음'}")
        print(valuation_table(vm, m.current_price))
        att = (m.meta.get("draft") or {}).get("attractiveness") or {}
        print("매력도(A3.10): " + attractiveness_line(att))
        print("신뢰도(A3.11): " + confidence_line((m.meta.get("draft") or {}).get("confidence") or {}, len((m.meta.get("draft") or {}).get("valuation_log") or [])))
        for n in notes:
            if "⚠" in n or "미계산" in n:
                print("  " + n[:240])
        if a.save:
            from .engine import evaluate_new_map

            evaluate_new_map(m, today=_today(a.date), enforce_gate=True)
            target.save(m)
            target.render(m)
            print(f"저장: {m.map_id}")
        elif changed:
            print("(저장하려면 --save)")
    finally:
        if store is not None:
            store.close()
    return 0


def cmd_scenario(a: argparse.Namespace) -> int:
    """시나리오 = 드라이버 가정 묶음 (설계 v6 §4.2) — `--set Bear.A1.1=-30 --set Bear.per=3.5 --set Bear.A1.4=-20000억원` 로 가정을 넣고 통합 모델로 가격을 계산한다."""
    from .engine.attractiveness import attractiveness_line, compute_valuation_all
    from .engine.scenario_model import SCENARIO_IDS, scenario_line
    from .models import DriverAssumption, Scenario

    target, store = _edit_target(a)
    try:
        m = target.load()
        today = _today(a.date)
        changed = False
        for kv in a.set or []:
            key, _, val = kv.partition("=")
            parts = key.strip().split(".", 1)
            if len(parts) != 2 or parts[0].capitalize() not in SCENARIO_IDS:
                print(f"오류: --set 은 'Bear.A1.1=-30' · 'Bear.per=3.5' · 'Base.eps_basis=my_estimate' · 'Bull.clear' 꼴 ({kv})")
                return 2
            name, field = parts[0].capitalize(), parts[1].strip()
            n = m[SCENARIO_IDS[name]]
            sc = n.scenario or Scenario(name=name, price_source="computed")
            sc.price_source = "computed"
            val = val.strip()
            if field == "per":
                sc.per = float(val) if val else None
            elif field == "eps_basis":
                sc.eps_basis = val or None
            elif field == "eps_year":
                sc.eps_year = val or None
            elif field in ("prob", "probability"):
                sc.probability = float(val) if val else None
            elif field == "clear":
                sc.driver_assumptions = []
            elif field in ("condition", "conditions"):
                sc.conditions = [x.strip() for x in val.split(";") if x.strip()]
            elif field == "falsifier":
                sc.falsifier = val or None
            elif field.startswith("A1."):
                unit = None
                var = None
                if ":" in field:            # A1.1:volume
                    field, var = field.split(":", 1)
                if val.endswith("억원"):
                    unit, val = "억원", val[:-2]
                sc.driver_assumptions = [d for d in sc.driver_assumptions if not (d.node_id == field and (d.variable or None) == (var or None))]
                if val:
                    sc.driver_assumptions.append(DriverAssumption(node_id=field, variable=var, delta_pct=float(val), unit=unit))
            else:
                print(f"오류: 모르는 필드 {field}")
                return 2
            n.scenario = sc
            n.as_of = today
            n.tags = sorted((set(n.tags) | {"user"}) - {"pending", "draft"})
            changed = True
        if a.probs:
            ps = [float(x) for x in a.probs.replace("/", ",").split(",")]
            if len(ps) != 3:
                print("오류: --probs 30/45/25")
                return 2
            tot = sum(ps)
            for name, pv in zip(("Bear", "Base", "Bull"), ps, strict=True):
                n = m[SCENARIO_IDS[name]]
                if n.scenario is None:
                    print(f"오류: {name} 시나리오가 비어 있다")
                    return 2
                n.scenario.probability = pv / tot if tot > 1.5 else pv
            changed = True
        notes = compute_valuation_all(m, today=today)
        from .engine import evaluate_new_map

        ev = evaluate_new_map(m, today=today, enforce_gate=True)
        print(f"{m.map_id} 시나리오 (통합 1차 모델 · 현재가 {m.current_price:,.0f})" if m.current_price else f"{m.map_id} 시나리오")
        for name, nid in SCENARIO_IDS.items():
            sc = m[nid].scenario
            if sc is None:
                print(f"  {name}: 공란")
                continue
            print("  " + scenario_line(sc) + (f" · p={sc.probability:.2f}" if sc.probability is not None else " · p=미입력") + (f" [{sc.price_source}]" if sc.price_source != "computed" else ""))
            for d in sc.driver_assumptions:
                lab = m[d.node_id].label.split(" (")[0] if d.node_id in m else d.node_id
                print(f"      {d.node_id} {lab}" + (f":{d.variable}" if d.variable else "") + f" {d.delta_pct:+g}{d.unit or '%'}" if d.delta_pct is not None else f"      {d.node_id} {lab} (숫자 없음)")
            if sc.model is not None:
                for w in sc.model.warnings:
                    print(f"      ⚠ {w}")
                for x in sc.model.missing:
                    print(f"      미계산 — 필요: {x}")
        print("  " + attractiveness_line((m.meta.get("draft") or {}).get("attractiveness") or {}))
        if m["A3.4"].value is not None:
            print(f"  A3.4 기대수익률 {m['A3.4'].value:+.1%} · A3.5 손익비 {m['A3.5'].value}")
        for x in ev.notes:
            print("  " + x)
        for n in notes:
            if n.startswith("신뢰도"):
                print("  " + n[:200])
        if a.save:
            target.save(m)
            target.render(m)
            print(f"저장: {m.map_id}")
        elif changed:
            print("(저장하려면 --save)")
    finally:
        if store is not None:
            store.close()
    return 0


def cmd_implied(a: argparse.Namespace) -> int:
    """역산 (설계 v6 §3.4) — 현재가(또는 --price) 가 내재하는 EPS 와 가격 드라이버 경로를 PER 몇 개에 대해 표로."""
    from .engine.valuation import common_tail, drop_contained, price_driver_segments, segment_values

    target, store = _edit_target(a)
    try:
        m = target.load()
    finally:
        if store is not None:
            store.close()
    p0 = a.price or m.current_price
    if not p0:
        print("현재가가 없다 (A0.4 또는 --price)")
        return 2
    tail = common_tail(m)
    if tail.eps is None or tail.t is None or tail.op is None or not tail.shares:
        print("표 입력이 모자라 역산할 수 없다 — 필요: " + "; ".join(tail.missing))
        return 2
    segs, _ = drop_contained(price_driver_segments(m))
    rev = segment_values(m, tail.year, "revenue")
    base_rev = sum(rev[s][0] for s in segs if s in rev)
    pers = [float(x) for x in (a.per or "").replace("/", ",").split(",") if x.strip()] or [x for x in (tail.per, 4.5, 3.5, p0 / tail.eps) if x]
    print(f"{m.map_id} 역산 — 가격 {p0:,.0f} · {tail.year} 컨센 EPS {tail.eps:,.0f} · OP {tail.op / 1e4:,.1f}조 · t {tail.t:.3f}"
          + (f" · 가격 드라이버 매출 {'+'.join(s for s in segs if s in rev)} {base_rev / 1e4:,.1f}조" if base_rev else ""))
    print(f"  {'적용 PER':>8} {'내재 EPS':>10} {'vs 컨센':>8} {'내재 OP(조)':>11} {'ASP 경로':>9}")
    for per in pers:
        eps_i = p0 / per
        op_req = eps_i / tail.eps * tail.np / (1.0 - tail.t) if tail.np else None
        path = ((op_req - tail.op) / base_rev * 100.0) if (op_req is not None and base_rev) else None
        print(f"  {per:>8.2f} {eps_i:>10,.0f} {eps_i / tail.eps - 1:>+8.0%} {(op_req or 0) / 1e4:>11,.0f} " + (f"{path:>+8.0f}%" if path is not None else "        -"))
    print("  (ASP 경로 = 가격 드라이버 사업부 매출 대비, 전가율 k=1.0 · 다른 변수 고정 — 시장이 내재하는 변수 경로의 1차 근사)")
    return 0


def cmd_eval_drivers(a: argparse.Namespace) -> int:
    """원인 드라이버 정답표(tests/golden/drivers/<ticker>.json) 대비 맵의 A1 — 재현·순서·결과 지표 금지·수치·민감도 (설계 v5 R5)."""
    from .generate.evals import eval_drivers

    target, store = _edit_target(a)
    try:
        m = target.load()
    finally:
        if store is not None:
            store.close()
    gp = Path(a.golden) / f"{m.ticker}.json"
    if not gp.is_file():
        print(f"정답표 없음: {gp}")
        return 2
    g = json.loads(gp.read_text(encoding="utf-8"))
    ev = eval_drivers(g, m)
    print(f"{ev.ticker}: 원인 드라이버 {ev.found}/{ev.expected} ({ev.recall:.0%}) · 순서 {'일치' if ev.order_ok else '다름'} · 결과 지표 혼입 {len(ev.forbidden_hits)}"
          f" · 수치 {ev.quant_ok}/{ev.quant_expected} · 민감도 계산 {ev.sensitivity_ok}/{ev.sensitivity_expected}" + ("" if g.get("verified") else " · (정답표 verified:false)"))
    for x in ev.forbidden_hits:
        print(f"      ✗ A1 에 결과 지표: {x}")
    for mi in ev.misses:
        print(f"      ✗ {mi}")
    return 0 if (ev.found == ev.expected and not ev.forbidden_hits) else 1


def cmd_eval_suggestions(a: argparse.Namespace) -> int:
    """제안값 수락률 · 수락 뒤 수정률 (설계 v4 §6.3) — 맵 파일 또는 저장소의 맵 id."""
    from .generate.suggest import stats_text, suggestion_stats

    if Path(a.map).is_file():
        m = load_map_file(a.map)
    else:
        from .store import Store

        with Store(a.db) as store:
            try:
                m = store.load_map(a.map)
            except KeyError:
                print(f"맵 없음: {a.map}")
                return 2
    print(f"{m.map_id}: {stats_text(m)}")
    st = suggestion_stats(m)
    for row in st["log"][-20:]:
        print(f"  {row['date']} {row['node']} {row['field']} ← {row['kind']}: {str(row['value'])[:60]}")
    return 0


def cmd_eval_claims(a: argparse.Namespace) -> int:
    """정답표(required_claims/required_risks/candidate_review) 대비 ClaimSet 의 필수 주장 재현율·근거 불일치율."""
    from .generate.evals import eval_claims
    from .reports.claims import load_claimset

    rows = []
    for gp in sorted(Path(a.golden).glob("*.json")):
        g = json.loads(gp.read_text(encoding="utf-8"))
        if not (g.get("required_claims") or g.get("required_risks")):
            continue
        cp = Path(a.claims) / f"{g['report_id']}.json"
        if not cp.is_file():
            print(f"  {g['report_id']}: ClaimSet 없음 ({cp})")
            continue
        ev = eval_claims(g, load_claimset(cp))
        rows.append(ev)
        print(f"  {ev.report_id}: 필수 {ev.found}/{ev.required} (재현율 {ev.recall:.0%}"
              + (f", 검토 대기에 {ev.found_pending}" if ev.found_pending else "") + ")"
              + f" · 후보 {ev.candidates} · 검토표 {ev.reviewed} · 근거 불일치 {ev.mismatched} ({ev.mismatch_rate:.0%})")
        if a.misses:
            for m in ev.misses:
                print(f"      ✗ {m}")
    if rows:
        req = sum(r.required for r in rows)
        found = sum(r.found for r in rows)
        pend = sum(r.found_pending for r in rows)
        rev = sum(r.reviewed for r in rows)
        mis = sum(r.mismatched for r in rows)
        mv = sum(r.machine_verified for r in rows)
        mm = sum(r.machine_mismatched for r in rows)
        hm = sum(r.human_mismatched for r in rows)
        print(f"합계: 재현율 {found}/{req} ({found / req:.0%})" + (f" · 검토 대기함에 {pend}건 더 있음 (승인하면 {(found + pend) / req:.0%})" if pend else "")
              + f" · 근거 불일치율 {mis}/{rev} ({(mis / rev) if rev else 0:.0%})"
              + ("" if rev else " — 검토표(candidate_review) 가 이 Claim 들과 안 맞아 불일치율은 측정 안 됨"))
        if rev:
            print(f"      잘못된 Claim 자동 승인률 {mm}/{mv} ({(mm / mv) if mv else 0:.0%}) · 사람 승인 뒤 '지지 안 됨' {hm}건  (설계 v4 V9; 목표 ≤ 5%)")
        print(f"      골든: {a.golden} — holdout 폴더(tests/golden/holdout) 로 재면 프롬프트 튜닝에 안 쓴 리포트 기준이다")
        return 0 if (found / req if req else 1) >= a.min_recall else 1
    print("정답표에 필수 주장이 없다 — tests/golden/extract/*.json 의 required_claims 를 채울 것")
    return 2


def cmd_demo(a: argparse.Namespace) -> int:
    from .demo import build_all

    out = Path(a.output or "out/demo")
    _, _, _, rep = build_all(out)
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    print(f"\n파일: {out.resolve()}  →  gaon_A.html / gaon_B.html / gaon_B_summary.html / gaon_C.html 을 브라우저로 열기")
    return 0


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mindmap", description="주식투자 마인드맵 생성·대응 엔진")
    p.add_argument("--version", action="version", version=f"mindmap (kstock-mindmap) {__version__}")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("new", help="A 맵 스켈레톤 생성")
    s.add_argument("--ticker", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--type", default="growth", choices=["growth", "value", "turnaround", "momentum", "event"])
    s.add_argument("--drivers", type=int, default=3)
    s.add_argument("--date")
    s.add_argument("-o", "--output")
    s.set_defaults(fn=cmd_new)

    s = sp.add_parser("validate", help="검증 (§3.3 완료 조건, §10)")
    s.add_argument("path")
    s.add_argument("--date")
    s.set_defaults(fn=cmd_validate)

    for name in ("eval", "status"):
        s = sp.add_parser(name, help="A: 계산·게이트 / B: 상태등·매트릭스")
        s.add_argument("path")
        s.add_argument("--date")
        s.add_argument("--dry-run", action="store_true")
        s.set_defaults(fn=cmd_eval)

    s = sp.add_parser("snapshot", help="매수 체결 → A 를 B 로 스냅숏")
    s.add_argument("path")
    s.add_argument("--entry-price", type=float, required=True)
    s.add_argument("--qty", type=float, required=True)
    s.add_argument("--w-target", type=float)
    s.add_argument("--eps-fwd", type=float)
    s.add_argument("--date")
    s.add_argument("--force", action="store_true")
    s.add_argument("-o", "--output")
    s.set_defaults(fn=cmd_snapshot)

    s = sp.add_parser("market", help="B 맵 시장 상태 갱신")
    s.add_argument("path")
    s.add_argument("--price", type=float, required=True)
    s.add_argument("--w-now", type=float)
    s.add_argument("--eps-fwd", type=float)
    s.add_argument("--band", type=float, help="밸류 밴드 백분위 0~100")
    s.add_argument("--total-assets", type=float)
    s.add_argument("--adv", type=float, help="일평균 거래량(주)")
    s.add_argument("--cash-yield", type=float)
    s.add_argument("--date")
    s.set_defaults(fn=cmd_market)

    s = sp.add_parser("render", help="HTML(양쪽/방사형) / markmap / markdown / mermaid")
    s.add_argument("path")
    s.add_argument("-o", "--output")
    s.add_argument("--format", default="html", choices=["html", "markmap", "md", "mermaid"],
                   help="html=자체 렌더러(양쪽/방사형, 오프라인) · markmap=오른쪽 한 방향(CDN)")
    s.add_argument("--layout", default="balanced", choices=["balanced", "radial", "right"], help="html 형식의 초기 레이아웃")
    s.add_argument("--summary", action="store_true", help="보유 맵 요약 뷰 (🟢 종목용)")
    s.set_defaults(fn=cmd_render)

    s = sp.add_parser("edit", help="마인드맵 HTML 을 로컬 편집 서버로 열기 — 노드 편집·추가·삭제를 맵 JSON 에 바로 저장")
    s.add_argument("map", help="맵 JSON 경로 또는 저장소의 맵 id (예: A:005930:2026-10-21)")
    s.add_argument("--db", default="data/db/kstock.sqlite")
    s.add_argument("--port", type=int, default=0, help="고정 포트 (기본: 빈 포트)")
    s.add_argument("--layout", default="balanced", choices=["balanced", "radial", "right"])
    s.add_argument("--date")
    s.add_argument("--no-browser", action="store_true", help="브라우저를 자동으로 열지 않음")
    s.add_argument("-v", "--verbose", action="store_true", help="요청 로그 출력")
    s.set_defaults(fn=cmd_edit)
    s = sp.add_parser("apply-edits", help="HTML 「편집 파일 내려받기」 로 받은 *.edits.json 을 맵에 반영 (file:// 로 연 경우)")
    s.add_argument("file", help="*.edits.json")
    s.add_argument("--map", help="맵 JSON 경로 또는 맵 id (기본: 편집 파일의 map_id 를 저장소에서)")
    s.add_argument("--db", default="data/db/kstock.sqlite")
    s.add_argument("--date")
    s.add_argument("--force", action="store_true", help="페이지를 연 뒤 맵이 바뀌었어도(base_hash 불일치) 적용")
    s.set_defaults(fn=cmd_apply_edits)
    s = sp.add_parser("rollup", help="B 맵들 → C 포트폴리오")
    s.add_argument("paths", nargs="+")
    s.add_argument("--cash", type=float, required=True)
    s.add_argument("--total-assets", type=float)
    s.add_argument("--cash-yield", type=float, default=0.03)
    s.add_argument("--date")
    s.add_argument("-o", "--output")
    s.set_defaults(fn=cmd_rollup)

    s = sp.add_parser("demo", help="가온로보틱스 예시 A→B→C")
    s.add_argument("-o", "--output")
    s.set_defaults(fn=cmd_demo)

    s = sp.add_parser("draft", help="증권사 리포트 파일들 → A 맵 초안 (규칙 채움, --llm 이면 LLM 그룹 채움까지)")
    s.add_argument("--reports", nargs="+", required=True, metavar="FILE", help="PDF/TXT/MD/HTML/DOCX 리포트 파일들")
    s.add_argument("--ticker", help="종목코드 (없으면 리포트 본문의 '(005930)' 에서 읽음)")
    s.add_argument("--name", help="종목명 (없으면 종목 목록/리포트에서)")
    s.add_argument("--type", choices=["growth", "value", "turnaround", "momentum", "event"], help="투자유형 프로파일 (기본 growth, 관심종목에 있으면 그 값)")
    s.add_argument("--llm", action="store_true", help="LLM 그룹 채움 (Claude: ANTHROPIC_API_KEY / 로컬: LLM_BASE_URL·LLM_MODEL)")
    s.add_argument("--llm-kind", default="auto", choices=["auto", "claude", "local"], help="auto=Claude 키가 되면 Claude, 아니면 로컬 OpenAI 호환 서버")
    s.add_argument("--claims", default="auto", choices=["auto", "llm", "rules", "off", "cached"],
                   help="Claim 추출 방식 — auto: --llm 이면 LLM, 아니면 규칙(저품질) / cached: --claims-dir 의 저장본만 (설계 v3)")
    s.add_argument("--claims-dir", metavar="DIR", help="ClaimSet 저장·재사용 폴더 (예: data/claims_llm; 같은 리포트는 다시 뽑지 않음). --db 를 쓰면 저장소의 data/claims")
    s.add_argument("--no-issue-check", action="store_true", help="쟁점의 기준 명제 판정(LLM) 을 생략하고 방향 통계만 (설계 v4 V3)")
    s.add_argument("--prefill-suggestions", action="store_true",
                   help="리포트 기반 제안값(내 추정=컨센 등) 을 값 칸에 미리 넣는다 — suggested 태그·pending 유지 (설계 v4 §6; 기본은 편집 폼의 「채우기」 로만)")
    s.add_argument("--db", help="저장소 SQLite — 주면 관심종목 등록 + 맵 저장 (콘솔과 같은 저장소)")
    s.add_argument("--mode", default="replace", choices=["replace", "incremental"],
                   help="--db 와 함께: replace(기본)=그 종목의 기존 A 맵 초안과 이 리포트들의 Claim 저장본을 보관 폴더로 옮기고 전부 다시 / incremental=저장본 재사용, 기존 맵 유지 (기존 맵이 있으면 `changes` 를 쓸 것)")
    s.add_argument("--no-save", action="store_true", help="--db 를 줘도 저장하지 않고 요약만")
    s.add_argument("--no-fetch", action="store_true", help="작성 시점 현재가를 네이버에서 조회하지 않음 (기본은 조회; 실패하면 DB·리포트 값)")
    s.add_argument("-o", "--output", help="--db 없이 쓸 때 JSON 경로 (기본 maps/A_<코드>_draft.json)")
    s.add_argument("--html", nargs="?", const=True, default=False, metavar="PATH", help="HTML 도 만든다 (경로 생략 시 JSON 옆 / 저장소 out 폴더)")
    s.add_argument("--date")
    s.set_defaults(fn=cmd_draft)

    s = sp.add_parser("golden-draft", help="리포트 파일들 → 정답표 초안 (tests/golden/extract/, verified:false)")
    s.add_argument("--reports", nargs="+", required=True)
    s.add_argument("--out", default="tests/golden/extract")
    s.add_argument("--root", default=".")
    s.add_argument("--date", help="추출 기준일 YYYY-MM-DD (기본 오늘)")
    s.add_argument("--overwrite", action="store_true")
    s.set_defaults(fn=cmd_golden_draft)
    s = sp.add_parser("eval-extract", help="정답표 대비 추출기 정확도 (필드별)")
    s.add_argument("--golden", default="tests/golden/extract")
    s.add_argument("--root", default=".")
    s.add_argument("--date")
    s.add_argument("--verified-only", action="store_true", help="사람이 확인한 정답표만")
    s.add_argument("--misses", action="store_true", help="틀린 항목 전부 출력")
    s.add_argument("--min-acc", type=float, default=0.0, help="이 정확도 미만이면 종료 코드 1")
    s.set_defaults(fn=cmd_eval_extract)
    s = sp.add_parser("claims", help="리포트 파일들 → Claim 추출(LLM) + 3단 검증 → data/claims/ (설계 v3 P2)")
    s.add_argument("--reports", nargs="+", required=True)
    s.add_argument("--ticker")
    s.add_argument("--sector", help="KR-SEMI 같은 섹터 코드 (기본: 본문으로 추정)")
    s.add_argument("--date")
    s.add_argument("--out", default="data/claims")
    s.add_argument("--llm-kind", default="auto", choices=["auto", "claude", "local"])
    s.add_argument("--rules", action="store_true", help="LLM 없이 키워드 분류 (저품질)")
    s.add_argument("--no-semantic", action="store_true", help="의미 지지 판정(LLM 배치) 생략 → 전부 검토 대기")
    s.add_argument("--show", action="store_true", help="Claim 목록 출력")
    s.set_defaults(fn=cmd_claims)
    s = sp.add_parser("claims-review", help="검토 대기함 보기 / 승인 / 반려")
    s.add_argument("report_id")
    s.add_argument("--out", default="data/claims")
    s.add_argument("--approve", nargs="*")
    s.add_argument("--reject", nargs="*")
    s.add_argument("--note")
    s.add_argument("--recheck", action="store_true", help="코드 검증(인용·숫자) 을 다시 하고 상태 재확정 — LLM 호출 없음 (--llm 을 더하면 검토 대기의 의미 판정만 다시 묻는다)")
    s.add_argument("--llm", action="store_true", help="--recheck 와 함께: 검토 대기 Claim 의 의미 판정을 LLM 에 다시 묻는다 (사람이 정한 것은 제외)")
    s.add_argument("--llm-kind", default="auto", choices=["auto", "claude", "local"])
    s.set_defaults(fn=cmd_claims_review)
    s = sp.add_parser("issues", help="ClaimSet 들 → 증권사 간 쟁점 표 (공통 견해·핵심 이견·독립 주장·미해결) (설계 v3 P3)")
    s.add_argument("--claims", default="data/claims")
    s.add_argument("--ticker")
    s.add_argument("--report-ids", nargs="*")
    s.add_argument("--include-review", action="store_true", help="검토 대기 Claim 도 포함 (언급으로만)")
    s.add_argument("--llm", action="store_true", help="테마마다 기준 명제에 대한 증권사별 판정을 LLM 에 묻는다 (설계 v4 V3; 종목당 호출 1~2회)")
    s.add_argument("--llm-kind", default="auto", choices=["auto", "claude", "local"])
    s.add_argument("--no-issue-check", action="store_true", help="LLM 판정 없이 방향 통계만")
    s.add_argument("--out", help="IssueSet JSON 저장 경로 (예: data/issues/005930.json)")
    s.set_defaults(fn=cmd_issues)
    s = sp.add_parser("changes", help="기존 A 맵 + 새 리포트 → 변경안 / --apply 로 승인분 적용 (설계 v3 P5)")
    s.add_argument("--map", required=True, help="맵 id (예: A:005930:2026-10-21)")
    s.add_argument("--db", default="data/db/kstock.sqlite")
    s.add_argument("--reports", nargs="*", help="새 리포트 파일 (기존 리포트는 같은 폴더에서 이름으로 찾는다)")
    s.add_argument("--date")
    s.add_argument("--no-fetch", action="store_true")
    s.add_argument("--llm", action="store_true")
    s.add_argument("--llm-kind", default="auto", choices=["auto", "claude", "local"])
    s.add_argument("--apply", help="적용할 변경안 JSON (data/changes/…)")
    s.add_argument("--approve", nargs="*")
    s.add_argument("--reject", nargs="*")
    s.set_defaults(fn=cmd_changes)
    s = sp.add_parser("eval-claims", help="정답표 대비 Claim 재현율·근거 불일치율 (설계 v3 P6)")
    s.add_argument("--golden", default="tests/golden/extract")
    s.add_argument("--claims", default="data/claims")
    s.add_argument("--min-recall", type=float, default=0.0)
    s.add_argument("--misses", action="store_true", help="못 찾은 필수 주장을 줄마다 출력 ([미추출] / [검토 대기])")
    s.set_defaults(fn=cmd_eval_claims)
    s = sp.add_parser("suggest", help="이미 있는 맵에 제안값(내 추정=컨센 · 진입 가격대) 을 다시 만들어 저장 — HTML 편집 폼의 「채우기」 에 쓰인다")
    s.add_argument("map", help="맵 JSON 경로 또는 저장소의 맵 id")
    s.add_argument("--db", default="data/db/kstock.sqlite")
    s.add_argument("--dry-run", action="store_true", help="만들기만 하고 저장하지 않음")
    s.set_defaults(fn=cmd_suggest)
    s = sp.add_parser("sensitivity", help="원인 드라이버 민감도 — 변수 Δ% → 영업이익 → EPS → 적정주가 (설계 v5 §3; 입력 출처와 미계산 사유를 보여 준다)")
    s.add_argument("map", help="맵 JSON 경로 또는 저장소의 맵 id")
    s.add_argument("--db", default="data/db/kstock.sqlite")
    s.add_argument("--driver", help="A1.x 하나만 (기본: 원인 드라이버 전부)")
    s.add_argument("--delta", type=float, help="충격 크기 (% 또는 %p; 기본 템플릿별 10 / 원가율 1)")
    s.add_argument("--set", nargs="*", help="사용자 입력 key=value (segment_revenue 억원 · segment_op · pass_through · tax_rate · shares · per_target · fcf · orders …)")
    s.add_argument("--save", action="store_true", help="맵에 저장하고 HTML 다시 쓰기")
    s.add_argument("--date")
    s.set_defaults(fn=cmd_sensitivity)
    s = sp.add_parser("valuation", help="평가 모델 (설계 v6 A3.8/A3.9) — EPS 기준·적용 PER·주식 수·순현금·SOTP 멀티플을 넣고 PER·정상화·SOTP·역산·매력도·신뢰도")
    s.add_argument("map", help="맵 JSON 경로 또는 저장소의 맵 id")
    s.add_argument("--db", default="data/db/kstock.sqlite")
    s.add_argument("--eps-basis", choices=["consensus", "my_estimate", "normalized", "user"], help="기준 EPS (기본 consensus)")
    s.add_argument("--eps", type=float, help="eps_basis=user 일 때 EPS (원)")
    s.add_argument("--eps-year", help="기준 연도 (예 2027E)")
    s.add_argument("--per", type=float, help="적용 PER (기본 = 목표가 중앙값 ÷ EPS, '기본값' 표시)")
    s.add_argument("--shares", type=float, help="주식 수 (주) — EPS 환산·SOTP 둘 다")
    s.add_argument("--net-cash", type=float, help="순현금 (억원; 기본 = 최근 실적 연도 순차입금 × −1)")
    s.add_argument("--dps", type=float, help="DPS 전망 (원; 기본 = 표 중앙값)")
    s.add_argument("--dividend", choices=["separate", "include", "exclude"], help="기대수익률에 배당을 어떻게 (기본 separate = 둘 다 표시)")
    s.add_argument("--sotp", nargs="*", action="extend", metavar="SEG=MULT", help="SOTP 멀티플 (사용자 입력만) 예: 메모리=6 파운드리=0 DX=8 SDC=6 Harman=10")
    s.add_argument("--stake", nargs="*", action="extend", metavar="SEG=RATIO", help="자회사 지배지분 비율 예: SDC=0.85 Harman=1.0")
    s.add_argument("--clear", action="store_true", help="A3.8 평가 기준을 비운다 (전부 기본값)")
    s.add_argument("--save", action="store_true", help="맵에 저장하고 HTML 다시 쓰기")
    s.add_argument("--date")
    s.set_defaults(fn=cmd_valuation)
    s = sp.add_parser("scenario", help="시나리오 = 드라이버 가정 묶음 (설계 v6) — --set Bear.A1.1=-30 --set Bear.per=3.5 --set Bear.A1.4=-20000억원 --probs 30/45/25")
    s.add_argument("map", help="맵 JSON 경로 또는 저장소의 맵 id")
    s.add_argument("--db", default="data/db/kstock.sqlite")
    s.add_argument("--set", nargs="*", action="extend", metavar="NAME.FIELD=VAL",
                   help="Bear|Base|Bull . A1.x[:variable]=Δ% (…억원 이면 금액) | per=배 | eps_basis=consensus·my_estimate·normalized·user | eps_year=2027E | prob=0.3 | conditions=a;b | falsifier=… | clear (여러 번 가능)")
    s.add_argument("--probs", help="확률 Bear/Base/Bull (예 30/45/25 — 사람의 판단; 자동 초안은 만들지 않는다)")
    s.add_argument("--save", action="store_true", help="맵에 저장하고 HTML 다시 쓰기")
    s.add_argument("--date")
    s.set_defaults(fn=cmd_scenario)
    s = sp.add_parser("implied", help="역산 (설계 v6) — 현재가가 내재하는 EPS 와 가격 드라이버 경로를 적용 PER 별로")
    s.add_argument("map", help="맵 JSON 경로 또는 저장소의 맵 id")
    s.add_argument("--db", default="data/db/kstock.sqlite")
    s.add_argument("--price", type=float, help="현재가 대신 쓸 가격")
    s.add_argument("--per", help="적용 PER 목록 (예 6.36/4.5/3.5; 기본 = 목표 PER·4.5·3.5·현재 Fwd PER)")
    s.set_defaults(fn=cmd_implied)
    s = sp.add_parser("eval-drivers", help="원인 드라이버 정답표(tests/golden/drivers) 대비 A1 재현·순서·결과 지표 혼입·수치·민감도 (설계 v5)")
    s.add_argument("map", help="맵 JSON 경로 또는 저장소의 맵 id")
    s.add_argument("--db", default="data/db/kstock.sqlite")
    s.add_argument("--golden", default="tests/golden/drivers")
    s.set_defaults(fn=cmd_eval_drivers)
    s = sp.add_parser("eval-suggestions", help="리포트 기반 제안값의 수락률·수락 뒤 수정률 (설계 v4 §6 — 앵커링 신호)")
    s.add_argument("map", help="맵 JSON 경로 또는 저장소의 맵 id")
    s.add_argument("--db", default="data/db/kstock.sqlite")
    s.set_defaults(fn=cmd_eval_suggestions)
    s = sp.add_parser("eval-issues", help="쟁점 정답표(tests/golden/issues) 대비 테마 재현·분류 일치·이견 누락 (설계 v4 V9)")
    s.add_argument("--golden", default="tests/golden/issues")
    s.add_argument("--issues", default="data/issues", help="IssueSet JSON 폴더 (<ticker>.json)")
    s.set_defaults(fn=cmd_eval_issues)

    s = sp.add_parser("llm-check", help="ANTHROPIC_API_KEY 동작 확인 (출처·계열·인증·모델·작은 호출 1회; 키 값은 안 보임)")
    s.add_argument("--model", help="확인할 모델 (기본 KSTOCK_LLM_MODEL, 없으면 사용 가능 목록에서 자동 선택)")
    s.add_argument("--no-call", action="store_true", help="인증·모델 목록만 보고 메시지 호출은 하지 않음")
    s.set_defaults(fn=cmd_llm_check)
    s = sp.add_parser("console", help="GUI 콘솔 (--text 면 번호 메뉴 콘솔). 나머지 옵션은 콘솔에 전달")
    s.add_argument("rest", nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_console)
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # 프로젝트 루트 .env (ANTHROPIC_API_KEY, KSTOCK_RESEARCH_DB …) — 셸 환경변수가 우선
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
