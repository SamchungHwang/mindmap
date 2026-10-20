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
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

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
    from .generate import ClaudeGroupFiller, DraftError, draft_from_reports, llm_available, llm_status
    from .symbols import default_book, research_db_path

    today = _today(a.date)
    if a.llm:
        print("LLM: " + llm_status())
    paths = [Path(p) for p in a.reports]

    def progress(label: str, i: int, n: int) -> None:
        print(f"  [{i}/{n}] {label}", flush=True)

    if a.db:
        from .ops import Ops, OpsError
        from .store import Store

        with Store(a.db) as store:
            ops = Ops(store, today=today)
            try:
                r = ops.draft_from_reports(paths, {"ticker": a.ticker, "name": a.name, "invest_type": a.type, "use_llm": a.llm,
                                                   "save": not a.no_save}, progress=progress)
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
        if not llm_available():
            print("LLM 을 쓸 수 없습니다 — 프로젝트 루트 .env 에 ANTHROPIC_API_KEY=… 를 적고 `pip install -e \".[llm]\"` 하세요. --llm 없이 규칙 채움만으로 진행할 수 있습니다.")
            return 2
        filler = ClaudeGroupFiller()
    try:
        r = draft_from_reports(paths, ticker=a.ticker, name=a.name, invest_type=a.type or "growth", today=today, symbols=default_book(),
                               filler=filler, research_db=research_db_path(), progress=progress)
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
    s.add_argument("--llm", action="store_true", help="LLM 그룹 채움 (anthropic + ANTHROPIC_API_KEY)")
    s.add_argument("--db", help="저장소 SQLite — 주면 관심종목 등록 + 맵 저장 (콘솔과 같은 저장소)")
    s.add_argument("--no-save", action="store_true", help="--db 를 줘도 저장하지 않고 요약만")
    s.add_argument("-o", "--output", help="--db 없이 쓸 때 JSON 경로 (기본 maps/A_<코드>_draft.json)")
    s.add_argument("--html", nargs="?", const=True, default=False, metavar="PATH", help="HTML 도 만든다 (경로 생략 시 JSON 옆 / 저장소 out 폴더)")
    s.add_argument("--date")
    s.set_defaults(fn=cmd_draft)

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
