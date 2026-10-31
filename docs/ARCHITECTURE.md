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

generate ──▶ reports (파일 → 사실 → 합산 · Claim 추출 → 검증) · models.claims · templates · engine · llm(ClaudeFiller 재사용)
   ▲
ops.draft_from_reports / propose_changes / apply_changes  ◀── console 「리포트로 자동 생성…」「변경안」 / cli draft·claims·issues·changes / console_text
```

### 설계 v3 초안 파이프라인 (`docs/report-draft-design-v3.md`)

```
리포트 파일 ─loader→ 텍스트(\f 페이지 경계) ─extract→ ReportFacts(+basis) ─digest→ ReportDigest(증권사별 최신 1건 · 45일 · 실적 전 · spread)
      │                                                          │  dbcheck(리서치 DB 교차 확인)
      └─reports.claims→ 청크 → LLM(claim_prompt) 또는 규칙 → Claim(인용 span) ─validate_claims→ 인용 존재 · 숫자 일치 · 의미 지지(LLM 배치)
                                                                      │
                                              generate.issues ◀───────┘ ClaimSet(verified 만) → 테마(driver_id) → 메커니즘 → 공통/이견/독립/미해결
                                                                      │
   generate.rules(숫자 노드 · 사람 몫은 pending) + generate.scenarios(리포트 제시 케이스만) + generate.mapping(쟁점 → A0/A1/A2.1/A3.5/A5/A6/A9.4)
                                                                      │
                                          generate.check(교차검증 · warn) → engine.validate(오류 vs 미확정) → DraftResult
                                                                      │
                                   새 리포트: generate.changes.propose_changes(기존 맵 vs 새 초안) → ChangeSet → 사람 승인 → apply_changes(버전 보존)
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
- `generate` 는 `ReportDigest` 와 `ClaimSet` 을 A 맵 초안으로 옮긴다 (설계 v3). `rules` 가 숫자로 결정되는 노드를 채우고
  (전부 `draft` 태그), 사람 몫(내 추정 · 시나리오 확률 · Kill · 내재 가정 A2.x) 은 비워 `pending` 태그를 단다 — 빈 칸은 오류가
  아니라 **미확정** 이다. `claims` → `validate_claims` 로 검증된 Claim 만 `issues` 가 쟁점으로 묶고 `mapping` 이 노드에 옮긴다
  (모든 노드가 인용 span 으로 리포트 원문까지 추적된다). `scenarios` 는 리포트가 명시한 케이스만 쓰고 증권사 간 조합·분포 폴백은
  하지 않는다. `check` 가 모순을 고치고 사람이 볼 곳에 `warn` 태그를 단다. 판단(A7.5)은 항상 WATCH. 옛 `llm_fill` 그룹 채움은
  `llm_groups=True` 일 때만 (시나리오 그룹 제외).
- `generate.changes` 는 기존 맵과 새 초안을 비교해 `ChangeSet` 을 만들고, 사람이 승인한 항목만 적용한다. 적용 전 맵을
  `maps/versions/` 에 보존하고 `meta.version` 을 올린다. 사람이 손댄 노드(`user_touched`) 는 자동 적용하지 않는다.
- `generate.evals` 는 골든 초안(`tests/golden/extract`) 으로 추출 정확도와 Claim 재현율·불일치율을 잰다. 골든은
  `verified:false` 로 생성되며 사람이 확인한 뒤 true 로 바꾼다.

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
| 드라이버 사전 (Claim 의 driver_id 정규화) | `generate/kpi_catalog.py` 의 `DRIVERS` (`DriverDef` id · 동의어 · 섹터) |
| Claim 프롬프트 변경 | `generate/prompts.py` 의 `CLAIM_SYSTEM`/`claim_prompt`/`support_prompt` — 바꾸면 `CLAIM_PROMPT_VERSION` 도 올린다 (ClaimSet 에 기록) |
| 쟁점 묶기 임계값 (메커니즘 유사도 · 숫자 이견 폭) | `generate/issues.py` (`_mech_key` difflib 0.72, `MAGNITUDE_REL`, `MAGNITUDE_TP`) |
| 집계 규칙 (최신 1건 · 유효 기간 · 실적 전) | `reports/digest.py` (`MAX_AGE_DAYS`, `EARNINGS_LAG_DAYS`, `ReportDigest.build`) |
| 시나리오 후보 인식 (케이스 문구 · 밸류 산식) | `generate/scenarios.py` (`RE_CASE`, `RE_METHOD`, `candidates_from`) |
| 변경안 종류 추가 | `models/claims.py::ChangeKind` + `generate/changes.py::propose_changes`/`apply_changes` |
| 회계 기준 정규화 (v6 §11.1 / v7 T1) | `reports/digest.py` `BrokerBasis`·`shares_std`·`control_ratio`·`eps_std`·`basis_check` → `engine/sensitivity.py::financial_snapshot` (`financials.basis`·`shares_std`·`eps.basis=std`) → `common_tail`(N = 표준 주식 수)·`ValuationModel.basis_status`·`validate`(not_comparable 오류)·`compute_confidence` |
| 다른 LLM (로컬 OpenAI 호환 서버) | `.env` 의 `LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`(+`LLM_THINK` 사고 끄기 · `LLM_MAX_TOKENS`) → `generate.llm_fill.OpenAICompatFiller` (`make_filler("local")`) — `reasoning_effort` 로 사고 제어, `<think>` 제거 |
| 다른 렌더러 | `render/` 에 `to_xxx(map)` — `render.format.node_line` 을 재사용 |
| 섹터의 원인 드라이버 5개 정의 (설계 v5) | `generate/kpi_catalog.py` 의 `SECTOR_KPIS[<섹터>]` 에 `Kpi(..., driver_id=, variable=, template=, segment=, kind=, proxies=)` — `driver_id` 가 있어야 A1 후보가 된다; 없으면 쟁점 테마로 |
| 사업부 표의 행 이름 (증권사별 표기) | `reports/segments.py::SEGMENT_ALIASES` · 블록 라벨 `BLOCK_LABELS` |
| 민감도 템플릿 추가 | `engine/sensitivity.py::compute_sensitivity` 의 템플릿 분기 + `TEMPLATE_BY_VARIABLE` · `DEFAULT_DELTA` |
| 원인 변수 셀의 변수·측정 판정 | `generate/cause.py::VARIABLE_WORDS` · `MEASURE_WORDS` (Claim metric 이름·명제 단어) |
| 리포트 기반 제안값 종류 추가 | `generate/suggest.py` (`build_suggestions` 의 `_consensus/_issue_minority/_conditional/_price_band`, `KIND_LABEL`) — `field` 는 노드 필드 경로, 폼은 `render/native.py` 의 `renderSuggestions` |
| 평가 기준·모델 (설계 v6) | `engine/valuation.py` — `Criteria.from_map`(A3.2 키), `common_tail`(t · N · PER — 민감도·시나리오·평가 모델이 같이 쓴다), `compute_valuation`(PER·정상화·SOTP·역산), `SEGMENT_CONTAINS`(사업부 포함 관계), `NORMALIZE_YEARS` |
| 시나리오 통합 모델 · Bear 템플릿 | `engine/scenario_model.py` — `SegState.delta`(사업부 식), `evaluate_scenario`, `BEAR_TEMPLATES[<섹터>]`(라벨만) |
| 확률가중 매력도 · 신뢰도 규칙 | `engine/attractiveness.py` — `compute_attractiveness`(배당 모드), `compute_confidence`(경고 규칙·등급), `compute_valuation_all`(묶음 — draft·편집 저장·CLI 가 부른다) |
| 밸류 계산기 화면 | `render/native.py` 의 `buildCalc`/`calcOps`/`renderCalcResult` (계산은 `/preview`) · `edit_options(...)["valuation"]` 머리말 |
| 성립 조건 — 가정 표 · 역산(한 변수 닫힌 식 · 두 변수 조합) · 필요 경로 (설계 v7 T5·T6) | `engine/path.py` — `assumption_table`, `solve_one`(`RANGE` 변수별 허용 범위 · `NOT_INVERTIBLE`), `solve_pair`(`GRID_DEFAULT`), `build_required_path`(① Claim 분기 셀 ② 사업부 표 분기 열 ③ 등비 보간), `write_conditions` → `meta.draft.conditions` · A2.4 |
| 관측값 계약 · 같은 자 규칙 · 신호등 · 띠/경계 제안 · 증거 일정 (v7 T4·T7) | `models/sub.py` `Ruler`·`Observation`·`PathPoint`·`RequiredPath`·`Driver.thesis_role/observations/required/band/bound_*` → `engine/evidence.py` `observe`, `comparable`, `light_for`(🟢🟡🔴⚪ 규칙 · `cadence_days` 노후 기준), `propose_bounds`, `evidence_plan`(A5.5) · `evidence_schedule`(A8.1) · `propose_roles` |
| 판단 수정 규칙 · 이벤트 · 플래그 · 알림 (v7 T8) | `engine/revision.py` `add_rule`(A5.6.x), `rule_problems`, `evaluate_rules`(감지→검증→제안, `meta.draft.rule_events`), `approve_event`, `compute_flags`(`meta.flags`), `alerts`; BUY 보류는 `engine/lifecycle.py`(WATCH 강제) · `engine/validate.py`(A7.5 오류) · 묶음 실행 `engine/decision.py::compute_v7_all` |
| Claim 품질 통계 · 비교 · 게이트 (v7 T3) | `reports/claim_stats.py` (`is_core`, `claim_stats`, `claim_compare`, `claim_gate` — 문턱 `GATE_VERIFIED`·`GATE_RECALL`) → `generate/draft.py`(`meta.draft.claim_gate`, `draft_status`) |
| HTML 「성립 조건」 패널 | `render/conditions.py::conditions_html`(서버가 HTML 생성) → `render/native.py` `OPTS.conditions` · `#cond`/`#condbtn` |
| 일괄 실행 · 지식 내보내기 (v7 T10) | `generate/batch.py::run_batch` · `generate/knowledge.py::export_knowledge` (CLI `batch` · `export-knowledge`) |
| 노드 번호 바꾸기 (스키마 버전) | `models/migrate.py` — `ID_MAP_2_0`(옛 → 새 접두), `migrate_map_data`(읽을 때 `load_map` 이 부른다: id · parent · links · formula · meta 키), CLI `migrate`. 새 번호를 또 바꾸려면 표를 하나 더 두고 `SCHEMA_VERSION` 을 올린다 |
| HTML 노드 편집 (고칠 수 있는 필드 · 연산 종류) | `generate/edits.py` (`ALLOWED_FIELDS`, `apply_edit_ops` 의 set/add/delete, `edit_schemas` 가 폼 필드를 만든다) · 화면은 `render/native.py` 의 `_EDIT_KEYS`/편집 JS · 저장 경로는 `render/server.py` (`/preview`, `/save`) |

## 저장 형식

맵 하나 = JSON 하나 (`MindMap.save/load`). `kind` 로 클래스를 복원한다. 하위 스키마는 노드 안에 inline.
`exclude_none=True` 로 저장하므로 빈 필드는 파일에 없다. 스키마 버전은 `schema_version` (현재 "1.0").

설계 v3 부속 파일 (전부 JSON, `store` 가 경로를 안다):

| 무엇 | 어디 | 키 |
|---|---|---|
| ClaimSet (리포트 1건의 Claim 전부 + 검증 결과 + 검토 이력) | `data/claims/<report_id>.json` | `report_id` = `hk_<번호>` (`models.claims.report_id_for`) |
| IssueSet (종목 1개의 쟁점 트리) | `data/issues/<ticker>.json` | 초안 생성 때마다 다시 만든다 |
| ChangeSet (새 리포트가 기존 맵에 제안하는 변경안) | `data/changes/<map_id>_<생성시각>.json` | 승인/거절은 파일 안 `decision` |
| 맵 버전 (변경 적용 직전 스냅숏) | `maps/versions/<map_id>.v<N>.json` | `meta.version` 과 짝 |
| 골든 초안 (추출 정답지) | `tests/golden/extract/<report_id>.json` | `verified` 가 true 인 것만 평가에 넣을 수 있다 |
