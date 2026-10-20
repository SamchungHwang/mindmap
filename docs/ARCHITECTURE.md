# 아키텍처 메모

## 레이어와 의존 방향

```
console (tkinter GUI) / console_text (번호 메뉴) / cli
   │
   ▼
ops ──▶ store (SQLite 인덱스 + JSON 맵)
   │
   ▼
engine ──▶ templates ──▶ models
   │           │
   ▼           ▼
render      collect ──▶ (adapters: research_db(읽기 전용) / DART / KRX / KIS / 컨센서스)
   │
llm ──▶ collect.base (EventClassifier 인터페이스)

generate ──▶ reports (파일 → 사실 → 합산) · templates · engine · llm(ClaudeFiller 재사용)
   ▲
ops.draft_from_reports  ◀── console 「리포트로 자동 생성…」 / cli draft / console_text
```

- `ops` 는 화면이 부르는 유일한 문이다. 폼 값(문자열 dict)을 숫자·날짜로 바꾸고 engine/store 를 호출한다.
  입력 오류는 `OpsError` 로 올리고 화면은 그것을 경고창으로 보여 준다. tkinter 가 없어도 통째로 테스트된다.
- `console` 은 위젯을 놓고 값을 모아 `ops` 에 넘긴다. 계산·검증·상태등을 화면에 두지 않는다.

- `models` 는 아무것도 import 하지 않는다 (pydantic 만). 모든 맵은 `MindMap.nodes: dict[id, Node]` 위에 있다.
- `templates` 는 노드 정의 테이블(`questions.py`) 에서 빈 맵을 만든다. 질문 문구를 바꾸려면 이 테이블만 고친다.
- `engine` 은 순수 로직이다. 외부 I/O 가 없고, 모든 함수가 `today` 를 인자로 받아 테스트가 날짜에 고정된다.
- `collect` 는 "원문을 가져오는 것" 과 "Event 로 분류하는 것" 까지만 한다. 드라이버 값 갱신(`update_driver`) 은 engine 이 한다.
- `llm` 은 노드를 채우는 일만 한다. 상태등·매트릭스·검증은 코드가 한다 — 이것이 §11 의 핵심 분업이다.
- `reports` 는 파일만 안다 (맵을 모른다): `loader` 가 텍스트를, `extract` 가 `ReportFacts` 를, `digest` 가 n건 합산
  `ReportDigest` 를 만든다. 전부 규칙(정규식·표 파서) 이고 못 읽은 값은 None 이다 — 지어내지 않는다.
- `generate` 는 `ReportDigest` 를 A 맵 초안으로 옮긴다. `rules` 가 숫자로 결정되는 노드를 채우고(전부 `draft` 태그),
  `llm_fill` 이 그룹 6개를 JSON 으로 묻되 숫자 환각을 막는 검사(인용 확인·앵커 ±25%·확률 정규화·driver_id 검증) 를 통과한
  것만 적용하며, `check` 가 모순을 고치고 사람이 볼 곳에 `warn` 태그를 단다. 판단(A6.5)은 항상 WATCH.

## 데이터 흐름 (운영 루프, §7)

```
[가격·공시·컨센서스] ─collect→ Quote / RawDisclosure / Consensus
      │                              │
      │ build_market_state           │ classifier.classify → Event
      ▼                              ▼
 update_market(B)              apply_event(B)  → B2.4 사실 로그
      │                              │
      └────────── update_driver(B) ◀─┘ (값 갱신은 사람/LLM 추출 결과로)
                        │
                 evaluate_holding(B)
                   ├─ mark_stale
                   ├─ calc: B2.2 분해 · B3.4/5 ER·RR · B3.6 밸류 · B3.8 상대우위 · B4.1 비중 · B4.2 · B4.5
                   ├─ compute_light → B0 (🟢🟡🔴)   ← evaluate_rules (B7, B1.4)
                   └─ matrix.decide → meta.matrix_default
                        │
            🟡/🔴 → 사람: record_decision(B6) → DecisionLog → rollup(C)
```

## ID 규칙

- `A1.2` = A 맵 1번 가지의 2번 노드. 중심 노드는 `A0`/`B0`/`C0`/`M0`, 섹터 맵은 `S.<시장>-<섹터>` 가 루트.
- `parent` 는 id 에서 유도된다 (`parent_id_of`). `A1` 의 부모는 `A0`.
- B 맵 드라이버 `B2.1.i` 는 A 맵 `A1.i` 와 1:1 이며 `links` 에 원본 id 를 둔다. 조건식에서는 둘 다 쓸 수 있다
  (`RuleContext.from_holding` 이 `driver[A1.i]` 별칭을 만든다).

## 조건식 DSL

`operand OP operand (and|or …)`. 변수: `price, w_now, w_target, drawdown, days_held, thesis_state, valuation_state,
weight_state, driver[<id>].current|at_entry|consensus|my_estimate|direction`, 그리고 `RuleContext.extra` 의 임의 키.
값이 없으면 비교는 거짓이다(규칙은 발동하지 않는다). `eval` 을 쓰지 않는다.

## 확장 포인트

| 하고 싶은 것 | 어디를 |
|---|---|
| 노드/질문 추가 | `templates/questions.py` 의 `*_SPEC` |
| 임계값 변경 | `models.Settings`, `models.PROFILES` |
| 상태등 규칙 추가 | `engine/status.py::compute_light` |
| 매트릭스 행 추가 | `engine/matrix.py::decide` (테스트 `test_rules_status_matrix.py` 에 행을 함께 추가) |
| 데이터 소스 | `collect/base.py` 의 Protocol 을 구현하는 클래스를 `collect/adapters/` 에 |
| 다른 LLM | `llm.NodeFiller` Protocol (`fill(node, map, materials) -> FillResult`) · 초안 그룹 채움은 `generate.GroupFiller` (`complete(system, user) -> str`) |
| 리포트 표지 양식 추가 | `reports/extract.py` 의 정규식(`RE_TP`, `RE_PRICE`, `RE_OPINION`, `ROW_LABELS`) — 실제 표지 조각을 `tests/test_reports_draft.py::test_real_style_snippets` 에 같이 넣는다 |
| 섹터 KPI 추가 | `generate/kpi_catalog.py` 의 `SECTOR_KPIS` · `SECTOR_WORDS` (metric 이 있는 KPI 는 추정치 표에서 규칙으로 채워진다) |
| 초안 규칙 변경 (앵커·확률·Kill 임계값) | `generate/rules.py` (`scenario_anchors`, `SCENARIO_P`, `fill_risks`) |
| 다른 렌더러 | `render/` 에 `to_xxx(map)` — `render.format.node_line` 을 재사용 |

## 저장 형식

맵 하나 = JSON 하나 (`MindMap.save/load`). `kind` 로 클래스를 복원한다. 하위 스키마는 노드 안에 inline.
`exclude_none=True` 로 저장하므로 빈 필드는 파일에 없다. 스키마 버전은 `schema_version` (현재 "1.0").
