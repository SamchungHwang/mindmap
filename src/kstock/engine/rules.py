"""If-Then 규칙 조건식 DSL 과 평가기 (템플릿 §2.3 Rule, §4.3).

조건식 문법 (eval 을 쓰지 않는다):

    expr    := clause (("and" | "or") clause)*       -- 왼쪽부터 순서대로, 괄호 없음
    clause  := operand OP operand
    OP      := < | <= | > | >= | == | !=
    operand := number | 'string' | "string" | path
    path    := ident ( "[" key "]" )? ( "." ident )*  -- 예: driver[A1.1].current, w_now, price

예)  driver[A1.1].current < 10.0
     w_now > 0.065
     drawdown > 0.2 and thesis_state == '약화'
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..models import HoldingMap, Rule

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<num>-?\d+(?:\.\d+)?)            |
        (?P<str>'[^']*'|"[^"]*")            |
        (?P<op><=|>=|==|!=|<|>)             |
        (?P<kw>\band\b|\bor\b)              |
        (?P<path>[A-Za-z_][\w]*(?:\[[^\]]+\])?(?:\.[A-Za-z_][\w]*)*)
    )""",
    re.VERBOSE,
)


class ConditionError(ValueError):
    pass


@dataclass
class RuleContext:
    """규칙 평가 시점의 상태. 수집기·엔진이 채운다."""

    today: date
    price: float | None = None
    w_now: float | None = None
    w_target: float | None = None
    drawdown: float | None = None
    days_held: int | None = None
    thesis_state: str | None = None
    valuation_state: str | None = None
    weight_state: str | None = None
    # driver id → {current, at_entry, consensus, my_estimate, direction}
    driver: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 임의 추가 변수 (이벤트 플래그 등)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_holding(cls, m: HoldingMap, today: date | None = None, **overrides: Any) -> RuleContext:
        today = today or date.today()
        mk = m.market
        pos = m.position
        drivers: dict[str, dict[str, Any]] = {}
        for n in m.drivers():
            d = n.driver
            assert d is not None
            drivers[n.id] = {
                "current": d.current.value, "at_entry": d.at_entry,
                "consensus": d.consensus.value, "my_estimate": d.my_estimate.value,
                "direction": d.direction.value if d.direction else None,
            }
            # A 맵 id 로도 접근 가능하게 (B2.1.1 ↔ A1.1)
            if n.id.startswith("B2.1."):
                drivers["A1." + n.id.split(".")[-1]] = drivers[n.id]
        dd = None
        if pos and pos.peak_price and mk.price:
            dd = max(0.0, 1 - mk.price / pos.peak_price)
        ctx = cls(
            today=today,
            price=mk.price,
            w_now=mk.w_now,
            w_target=pos.w_target if pos else m.value("B0.4"),
            drawdown=dd,
            days_held=(today - pos.entry_date).days if pos else None,
            thesis_state=m.value("B2.3"),
            valuation_state=m.value("B3.6"),
            weight_state=m.value("B4.1"),
            driver=drivers,
        )
        for k, v in overrides.items():
            if hasattr(ctx, k):
                setattr(ctx, k, v)
            else:
                ctx.extra[k] = v
        return ctx

    def resolve(self, path: str) -> Any:
        m = re.match(r"^([A-Za-z_]\w*)(?:\[([^\]]+)\])?((?:\.[A-Za-z_]\w*)*)$", path)
        if not m:
            raise ConditionError(f"bad path: {path}")
        head, key, rest = m.group(1), m.group(2), m.group(3)
        if head in ("today",):
            cur: Any = self.today
        elif hasattr(self, head) and head not in ("resolve", "from_holding", "extra"):
            cur = getattr(self, head)
        elif head in self.extra:
            cur = self.extra[head]
        else:
            raise ConditionError(f"unknown variable: {head}")
        if key is not None:
            if not isinstance(cur, dict):
                raise ConditionError(f"{head} is not indexable")
            cur = cur.get(key)
            if cur is None:
                return None
        for attr in filter(None, rest.split(".")):
            if cur is None:
                return None
            if isinstance(cur, dict):
                cur = cur.get(attr)
            else:
                cur = getattr(cur, attr, None)
        return cur


def _tokenize(cond: str) -> list[tuple[str, str]]:
    pos, out = 0, []
    cond = cond.strip()
    while pos < len(cond):
        m = _TOKEN.match(cond, pos)
        if not m or m.end() == pos:
            raise ConditionError(f"cannot parse condition at: {cond[pos:]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind is None:
            continue
        out.append((kind, m.group(kind)))
    return out


def _operand(tok: tuple[str, str], ctx: RuleContext) -> Any:
    kind, text = tok
    if kind == "num":
        return float(text)
    if kind == "str":
        return text[1:-1]
    if kind == "path":
        if text in ("true", "false", "True", "False"):
            return text.lower() == "true"
        if text in ("None", "null"):
            return None
        return ctx.resolve(text)
    raise ConditionError(f"unexpected token {text!r}")


def _compare(a: Any, op: str, b: Any) -> bool:
    if a is None or b is None:
        # 값이 없으면 비교는 거짓 (규칙은 발동하지 않는다). == None 은 허용.
        return (a is b) if op == "==" else (a is not b) if op == "!=" else False
    if isinstance(a, bool) or isinstance(b, bool):
        a, b = bool(a), bool(b)
    elif isinstance(a, (int, float)) and isinstance(b, str):
        try:
            b = float(b)
        except ValueError:
            pass
    elif isinstance(b, (int, float)) and isinstance(a, str):
        try:
            a = float(a)
        except ValueError:
            pass
    if isinstance(a, date) and isinstance(b, str):
        b = date.fromisoformat(b)
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == ">=":
        return a >= b
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    raise ConditionError(op)


def evaluate_condition(cond: str, ctx: RuleContext) -> bool:
    toks = _tokenize(cond)
    if not toks:
        raise ConditionError("empty condition")
    result: bool | None = None
    pending_kw: str | None = None
    i = 0
    while i < len(toks):
        if i + 2 >= len(toks) or toks[i + 1][0] != "op":
            raise ConditionError(f"expected '<operand> <op> <operand>' near {toks[i][1]!r}")
        val = _compare(_operand(toks[i], ctx), toks[i + 1][1], _operand(toks[i + 2], ctx))
        if result is None:
            result = val
        elif pending_kw == "and":
            result = result and val
        elif pending_kw == "or":
            result = result or val
        i += 3
        if i < len(toks):
            if toks[i][0] != "kw":
                raise ConditionError(f"expected and/or, got {toks[i][1]!r}")
            pending_kw = toks[i][1]
            i += 1
    return bool(result)


def validate_condition(cond: str) -> list[str]:
    """문법만 검사 (변수 존재 여부는 평가 시점에만 알 수 있다). 오류 메시지 목록을 돌려준다."""
    try:
        toks = _tokenize(cond)
    except ConditionError as e:
        return [str(e)]
    errs: list[str] = []
    i = 0
    while i < len(toks):
        if i + 2 >= len(toks) or toks[i + 1][0] != "op":
            errs.append(f"clause malformed near {toks[i][1]!r}")
            break
        i += 3
        if i < len(toks) and toks[i][0] != "kw":
            errs.append(f"expected and/or, got {toks[i][1]!r}")
            break
        i += 1
    return errs


@dataclass
class FiredRule:
    node_id: str
    rule: Rule
    reason: str


def evaluate_rules(m: HoldingMap, ctx: RuleContext, *, fire: bool = True) -> list[FiredRule]:
    """B7.x 와 B1.4.x(kill criteria) 를 평가한다. fire=True 면 발동한 규칙의 status/fired_at 을 갱신."""
    fired: list[FiredRule] = []
    for n in list(m.children("B7")) + list(m.children("B1.4")):
        r = n.rule
        if r is None or r.status == "disabled":
            continue
        try:
            hit = evaluate_condition(r.condition, ctx)
        except ConditionError as e:
            fired.append(FiredRule(n.id, r, f"조건식 오류: {e}"))
            continue
        if hit:
            if fire and r.status != "fired":
                r.status = "fired"
                r.fired_at = ctx.today
            fired.append(FiredRule(n.id, r, f"{r.condition} → {r.action}"))
    return fired


__all__ = ["RuleContext", "ConditionError", "FiredRule", "evaluate_condition", "validate_condition", "evaluate_rules"]
