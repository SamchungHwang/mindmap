# 증권사 리포트 → 투자 마인드맵: 설계 v4

작성 2026-10-22 · **갱신 2026-10-23** (V11 노드 복사 · V12 노드 편집 · §6 리포트 기반 제안값(V13) — 셋 다 구현 완료) · 앞선 문서: `report-draft-improvements.md` → `report-draft-design-v2.md` → `report-draft-design-v3.md`(P0~P6 구현 완료, 삼성전자 3건 실측) → **v4 = 검토의견 3(mindmap-review3, "필수 수정 10개") 중 타당·구현 가능한 것을 반영**.
v3 와 달리 v4 는 **이미 돌아가는 코드** 위에서 쓴다. 각 항목마다 "지금 코드가 어디까지 하는가 / 무엇을 바꾸는가 / 바꾸지 않는 것" 을 적고, 실측 수치(아래 §0)로 필요성을 확인했다.

## 0. v4 를 쓰게 한 실측 (2026-10-21, Claude Sonnet 5.5, 프롬프트 c2)

삼성전자 리포트 3건(IBK·유진·DS) → Claim 40 (verified 35 · 검토 대기 5 · 반려 0) · 필수 주장 재현율 19/22 (86%, 검토 대기 승인 시 95%) · 근거 불일치 0/23 · 테마 13 · 핵심 이견 1([모바일·세트 수요] DS↑ IBK↓ 유진↓) · 맵 A2.1 쟁점 8 · A4 리스크 3 · A3.6.3 리비전 5 · A5 촉매 2.

이 결과에서 드러난 것이 v4 의 근거다.

| 관찰 | 뜻 | v4 항목 |
|---|---|---|
| "3Q26 잠정 영업이익 107.4조"(기업 발표 사실) 와 "2026E 영업이익 373조"(증권사 추정) 가 같은 테마·같은 노드에 섞여 보임 | 사실/전망/계획 구분이 `epistemic_type` 만으로는 화면에 안 드러남 | **V1** |
| 추출 LLM 과 의미 판정 LLM 이 같은 모델 | 같은 방식으로 같이 틀릴 수 있는데 `verified` 한 단어로 끝남 | **V2** |
| 테마 층 coverage 는 "같은 드라이버 Claim 의 방향" 으로만 판정 | "공급 증가에는 동의, 이익 효과는 반대" 를 테마 층에서 이견으로 오판할 수 있음. 리포트가 청크 한도를 넘으면 "침묵" 이 "안 읽음" 일 수 있음 | **V3** |
| 증권사별 최신 1건만 집계 | 최신 리포트가 분기 실적만 다루면 그 증권사의 2027E·2028E 가 컨센서스에서 빠짐 | **V4** |
| 시나리오 후보 3종을 사용자가 Bear/Base/Bull 에 넣음 | 증권사 기준 목표가를 Bear/Bull 로 넣은 것은 사용자 재해석인데 기록이 없음 | **V5** |
| Kill 은 "조건부 리스크 Claim 수치가 표 드라이버에 대응될 때만 Rule" | 그 예외도 "수익성 개선 지연 ≠ 논제 붕괴" 를 무시함 | **V6** |
| 변경안은 노드 단위 승인 | A1 컨센서스만 승인하고 A3.6 분포·A2.1 쟁점은 거절하면 시점이 섞임 | **V7** |
| LLM 실패 시 규칙 분류 폴백이 A2.1·A4·A5 까지 채움 (실측 1회차: 쟁점 `etc` 1개) | 저품질이 정상 맵처럼 보임 | **V8** |
| 골든 13건으로 프롬프트 c1→c2 를 고치고 같은 13건으로 재현율을 잼 | 과적합 — 86% 는 상한값 | **V9** |
| v3 문서의 P2.5 가 P3·P4 결과물을 요구 | 실제로는 P3·P4 최소 기능을 먼저 만들고 통합 실험을 했음 | **V10** (문서) |

## 1. 바뀌지 않는 것 (v3 원칙 유지)

정성 노드는 전부 Claim(인용 span) 에서 · 사람 몫(내 추정·확률·Kill·내재 가정) 은 `pending` · 시나리오 자동 완성 없음 · 판단은 WATCH · 새 리포트는 변경안으로 · 폴백은 `quality` 표기 · 키 값은 어디에도 안 찍힘. **아키텍처를 다시 만들지 않는다** — v4 는 필드·규칙·화면 표기의 보강이다.

## 2. 항목별 설계

### V1. Claim 의 사실·전망·계획 구분 (review P0-1)

**지금**: `epistemic_type = fact | estimate | opinion`. **문제**: "기업이 공급을 30% 늘릴 계획이라고 밝혔다" 는 fact(발표) 이지만 공급 증가가 일어난 것은 아니다. 화면에 fact 로만 보이면 실적처럼 읽힌다.

**바꾸는 것** (`models/claims.py` `Claim`, 전부 기본값 있음 → 저장된 c1/c2 ClaimSet 그대로 읽힘):

| 필드 | 값 | 기본 | 뜻 |
|---|---|---|---|
| `asserted_by` | `analyst` \| `company` \| `market` \| `authority` \| `other` | `analyst` | 누구의 주장인가 — 증권사 분석 / 기업 발표·가이던스 / 시장·컨센서스 / 정부·규제기관 |
| `certainty` | `realized` \| `announced` \| `planned` \| `expected` \| `conditional` | `expected` | 이미 일어남(실적) / 공식 발표됨 / 계획·예정 / 전망·추정 / 조건부 |
| `as_of` | `date \| None` | None → 리포트 날짜 | 정보의 기준일 (실적은 분기 말, 발표는 발표일, 전망은 리포트 날짜) |

`epistemic_type` 은 유지한다(역할 분류의 기준). 규칙: `certainty=realized` ⇒ `epistemic_type=fact`; `conditional` ⇒ `condition` 필수.

**프롬프트 c3** (`CLAIM_PROMPT_VERSION="c3"`): 두 필드를 묻고 예시 4문장(검토의견의 표)을 그대로 넣는다. **화면**: 노드 값·쟁점 표·A4 인용에 접두어 `[실적]` `[기업 발표]` `[기업 계획]` `[증권사 전망]` `[조건부]`. `issues` 의 메커니즘 요약과 `mapping` 의 A2.1.x 값 줄에 적용. **집계 영향**: V4 의 셀 키에 `certainty` 가 들어간다 — 잠정치(announced) 와 추정치(expected) 를 같은 셀에 섞지 않는다.

### V2. 기계 검증과 사람 승인의 분리 (review P0-2)

**지금**: `status=verified` 하나. 사람 승인은 `validation.note` 에 "user approve 시각" 문자열만. **문제**: 자동 통과와 사람 확인이 구별되지 않고, 중요한 Claim 도 자동으로 맵에 들어간다.

**바꾸는 것**:
- `Claim.verified_by: "" | "machine" | "human"` — `finalize()` 가 verified 로 만들면 `machine`, `review(approve)` 는 `human`.
- `Claim.review_required: bool` + `review_reason: str`. **규칙**(코드, `validate_claims.mark_review_required`): ① **전사 수준·미래 기간(…E)** 의 판단 지표(op/revenue/eps/np/opm/목표가) 수치가 **그 리포트의 표에도 없을 때**(표에 있으면 기계가 대조한 것이므로 ⚑ 아님) ② 같은 (metric, col) 의 전사·미래 수치가 다른 증권사와 15% 넘게 다를 때 — **단위를 억원으로 환산**해 비교하고 **사업부 수치**(DRAM 83.8조·MX −2.0조 — 표에 없는 수치 = 사업부로 판정, 표가 없으면 '사업부명+이익/매출' 문장 휴리스틱) 와 **목표가**(차이는 A3.6.4 분포가 보여 준다) 는 제외 ③ `certainty=conditional` 인 리스크 Claim ④ `epistemic_type=fact` 인데 `asserted_by=analyst`(애널리스트가 사실로 단정) ⑤ 명제의 숫자가 인용엔 없고 **리포트 본문(사업부 표 등) 에만** 있을 때(`check_numbers` 의 fulltext 폴백 — 불일치 대신 verified+⚑) — 이들은 자동 통과해도 `review_required=True` 로 **검토 대기함에 같이 뜬다**(상태는 verified 유지 — 초안에는 쓰되 ⚑ 표시).
  *실측 보정(2026-10-22)*: 첫 c3 실측에서 ①② 를 "판단 지표 수치 전부 · 같은 (op, 3Q26) 전부" 로 두었더니 42건 중 23건이 ⚑ 였고 그 대부분이 표에 있는 숫자 또는 사업부 수치끼리의 가짜 충돌(메모리 110.1조 vs Foundry −1.5조) 이었다 → 위 규칙으로 좁혀 삼성전자 3건에서 ⚑ 0 + 검토 대기 4 가 됐다.
- `review(approve)` 는 `validation.support == insufficient` 였으면 `note` 가 **필수**(빈 note 는 `ValueError`). CLI `--note`, GUI 입력란.
- `usable` 은 그대로(verified ∧ ¬inferred). 맵 노드 출처 줄에 `자동 검증` / `사람 확인` 을 적고, `DraftResult.summary_text` 에 "Claim 35건 (자동 30 · 사람 5 · 확인 권고 7)".

**바꾸지 않는 것**: 모든 Claim 에 사람 승인을 요구하지 않는다(검토의견도 같은 의견).

### V3. Issue 의 비교 기준 명제와 '침묵' 의 구분 (review P0-3) — v4 의 핵심

**지금**: 테마(driver) → 메커니즘(driver→target) 2층. coverage 는 그 묶음 안 Claim 의 **방향**으로 판정. **문제**: "HBM4 공급 증가 긍정" 과 "공급은 늘지만 수익성엔 부정" 은 테마 층에서 ↑/↓ 이견으로 보이지만 실제 이견은 '이익 효과' 한 가지다. 또 `silent` 가 "리포트에 없다" 인지 "청크 한도(6×18k자) 밖이라 안 읽었다" 인지 모른다.

**바꾸는 것**:
- `Issue.reference_proposition: str` — 비교 기준 명제 하나. 테마 층은 "<드라이버> 가 <기본 대상 지표> 에 <컨센 방향> 영향" 꼴(예: "DRAM 가격 상승이 영업이익을 늘린다"), 메커니즘 층은 대표 Claim 의 명제. `issues.build_issues` 가 코드로 만든다.
- **명제 판정 LLM 배치** (`generate/prompts.py::issue_prompt`, `issues.judge_coverage`): 테마마다 `reference_proposition` + 증권사별 Claim 명제들을 보내 증권사마다 `agree | disagree | partial | unrelated` 와 한 줄 이유를 받는다 (support_prompt 와 같은 배치 25건, 종목당 호출 1~2회). 결과가 있으면 `coverage` 는 이것으로, `directions` 는 방향 통계 그대로. `partial`(공급 증가엔 동의, 이익엔 반대) 은 `Coverage.partial`(새 값) → 화면 `◐`. 판정 근거는 `Issue.judgement_notes[broker]`.
- 판정이 없으면(LLM 없음, `--no-issue-check`) 방향 기반 판정을 쓰고 `Issue.coverage_basis = "direction"`(있으면 `"llm"`) 을 표에 적는다.
- **침묵 분리**: `Coverage.unanalyzed`(새 값) — 그 증권사 ClaimSet 의 `analyzed_ratio < 0.95` 이면 silent 대신 unanalyzed(`?`). `ClaimSet.analyzed_chars / total_chars` 를 `reports/claims.extract_claims` 가 기록한다.
- 테마 `display`: `partial` 은 disagree 로 세지 않는다. 반대가 없고 agree+partial ≥ 2 면 **공통 견해**(요약에 "부분 동의 n곳") — 실측에서 "주주환원 → EPS" 처럼 기준 명제의 대상 지표가 어색하면 판정자가 전부 partial 을 주어 '미해결 쟁점' 이 됐기 때문. 같은 이유로 기준 명제의 대상 지표는 테마 Claim **둘 이상이 같은 target_metric_id 를 가리킬 때만** 붙인다.
- `numeric_spread`(수치 이견 ⚑magnitude) 는 **전사 수준 수치만**(V2 와 같은 판정) · 단위 환산 뒤 비교 — 첫 실측의 [DRAM 가격]·[실적·영업이익] "핵심 이견 ⚑magnitude" 는 사업부 수치(83.8·110.1·−1.5) 가 (op, 3Q26) 한 셀에 섞인 가짜였다.
- 의미 판정(`SUPPORT_SYSTEM`) 은 **숫자의 유무로 insufficient 를 주지 않는다** — 숫자는 코드(인용 → 표 → 본문) 몫. `claims-review --recheck --llm` 으로 검토 대기 건의 의미 판정만 다시 묻는다.

### V4. 수치 집계: 셀 단위 최신 유효 추정치 (review P0-4)

**지금**: 증권사별 최신 1건(`latest_per_broker`) 만 집계. basis(지배/연결) 섞지 않음, 45일, 단위 환산. **문제**: 최신 리포트(분기 Preview) 가 2028E 를 안 실으면 그 증권사의 2028E 가 사라진다 — 이전 리포트의 장기 추정은 여전히 그 증권사의 공식 숫자다.

**바꾸는 것** (`reports/digest.py`):
- 집계 키 = `증권사 × 지표 × 기간(col) × 기준(basis) × certainty(A/P/E)`. 증권사별 **대표 문서**는 그대로 최신 1건이지만, 셀을 채울 때 대표 문서에 없는 (지표, 기간) 은 **같은 증권사의 이전 리포트(45일 안) 에서 가져오고** `EstimateCell.sources[broker] = (report_name, date, "보강")` 으로 표시. 요약 표에 보강 셀은 `*` 를 붙이고 각주 "`*` 최신 리포트에 없어 같은 증권사 이전 리포트에서".
- `A`(확정) / `P`(잠정 — 열 라벨 "3Q26P" 또는 본문 "잠정") / `E`(추정) 를 열 라벨 접미로 구분해 **다른 접미는 같은 셀에 넣지 않는다**.
- "조정 영업이익" / "지배주주 순이익(조정)" 라벨은 `op_adj` / `np_adj` 로 분리(`extract.ROW_LABELS`).
- **하지 않는 것**(검출 불가, 하면 "검증했다" 는 착시만): EPS 의 주식 수·주식 종류 동일성, 환율 적용 기준. 대신 요약에 고정 문구 "EPS 는 증권사별 주식 수 가정이 다를 수 있다" 를 `warnings` 로 한 줄.

### V5. 증권사 목표가 ≠ Bear/Base/Bull (review P0-5)

**지금**: 후보 3종(리포트 제시 / 산식 재계산 / 목표가) 을 `meta.draft.scenario_candidates` 에 두고 사용자가 고른다. 자동 완성 없음. **문제**: 사용자가 A 증권사 목표가 40만 → Bear, B 증권사 50만 → Bull 로 넣으면 그것은 **사용자의 재해석**인데 맵에 "리포트 근거" 처럼 남는다.

**바꾸는 것** (`generate/scenarios.py`):
- `ScenarioCandidate.origin: "broker_case" | "broker_tp" | "broker_recalc" | "user"` (kind 와 1:1 대응 + user). 
- `apply_candidate(m, cand, name)`: `origin != broker_case` 또는 후보의 `name != 선택한 name` 이면 Scenario `valuation_method` 앞에 `[사용자 재해석]`, `HistoryPoint(source="사용자 재해석 — <증권사> 기준 목표가를 <name> 에 배치")` 기록, `meta.draft.scenario_origin[name] = {...}`. 콘솔 대화상자에 같은 문구 경고.
- 맵 요약 `Bear: …(사용자 재해석)`.

### V6. Kill 은 후보까지만 (review P0-6)

**지금**: 조건부 리스크 Claim 의 수치가 표 드라이버에 대응될 때만 Rule 자동 생성, 아니면 질문. **바꾸는 것**: 그 예외를 없앤다. `mapping` 은 `meta.draft.kill_candidates = [{kpi, op, threshold, unit, source_claim, quote, observable: None, update_cadence: None, action: None}]` 만 만들고 A4.4.1 은 `pending` + 질문에 후보 수를 적는다. 콘솔 「Kill 후보…」/텍스트 `K` 에서 사용자가 후보를 고르고 **관측 가능 여부·갱신 주기·위반 시 행동** 세 칸을 채우면 `Rule(origin="user_confirmed")` 로 들어간다 (`ops.apply_kill_candidate`). 세 칸이 비면 넣지 않는다.

### V7. 변경안의 정합성 (review P1-1)

**지금**: `map_hash` 검사 · 적용 전 버전 보존 · 승인분만 적용. **바꾸는 것** (`generate/changes.py`):
- `ChangeSet.input_snapshot_id` = 입력 리포트 집합(report_id 정렬) 의 sha1[:10] + 기준일. 다른 입력으로 만든 변경안을 같은 맵에 섞어 적용하면 경고.
- `Change.affects: list[str]` — 코드 규칙으로 채운다: A1.i 추정치 변화 → `A3.6.4`(목표가 분포)·`A2.1.<테마 노드>`; A3.6 TP 리비전 → `A3.6.3`; 새 리스크 → `A4.4.1` 후보. 승인 화면에 "함께 바뀌는 노드" 로 보이고, 한쪽만 승인하면 ⚑ "부분 승인 — 분석 시점이 섞임" 을 A7.2 로그에 남긴다.
- `apply_changes` 는 **복사본**에 먼저 적용 → `validate_new_map` 에 진짜 오류(pending 제외) 가 생기면 원본을 건드리지 않고 `ChangeError`. 저장은 임시 파일 → `os.replace` (원자적). 버전 파일도 같은 방식.

### V8. 규칙 폴백의 범위 제한 (review P1-2)

**지금**: `quality=rules` 표시만. **바꾸는 것** (`generate/mapping.py`, `draft.py`): 폴백 Claim 으로는 **A0 논제·A2.1.x 쟁점·A3.6.3 리비전 사유·A4 리스크·A5 촉매·A8.4.x 를 만들지 않는다**. 해당 노드는 비우고 질문에 "LLM 없이 생성 — Claim 기반 노드는 미생성 (`mindmap claims --out …` 뒤 다시)". 숫자 노드(현재가·A1 컨센·목표가 분포·추정치 표·A8.4 리포트 목록) 는 그대로. 요약 머리에 **"⚠ 규칙 모드 — 정성 노드 미생성"**. `mindmap claims --rules` 로 명시적으로 만든 규칙 ClaimSet 은 `issues` 에서 볼 수 있되 표 머리에 같은 경고.

### V9. 평가 데이터 분리와 지표 추가 (review P1-4)

**지금**: `tests/golden/extract` 13건이 개발·평가 겸용. 지표는 재현율·근거 불일치율. **바꾸는 것**:
- 폴더 규약: `tests/golden/extract`(개발·회귀 — 프롬프트·사전을 고칠 때 본다) / `tests/golden/holdout`(최종 검증 — **프롬프트·사전 튜닝에 쓰지 않는다**; 새 종목 리포트가 들어오면 먼저 여기에). `eval-extract`/`eval-claims` 의 `--golden` 으로 선택. README 에 규약.
- 지표 추가 (`generate/evals.py`):
  - **잘못된 Claim 승인률** `approved_invalid_rate` = 사람이 "지지 안 됨" 으로 표기한 것 중 `verified_by=machine` 으로 통과한 비율 (이미 있는 `mismatched` 를 verified_by 로 나눠 계산) + `human` 승인 뒤 반려된 비율.
  - **중요 이견 누락률**: 종목별 골든 `tests/golden/issues/<ticker>.json` = `{required_issues: [{driver_id, display, brokers_disagree: [...]}]}`. `eval-issues` 명령이 테마 일치·분류(공통/이견) 일치·누락 이견 수를 낸다.
- 목표치는 v3 그대로(재현율 ≥ 80%, 불일치율 ≤ 5%) 에 **holdout 기준** 을 붙이고, 승인 오류율 ≤ 5%, 이견 누락 0 (골든에 적은 것 기준).

### V10. 문서 정정 (review P1-3)

v3 §3 의 P2.5 를 실제 순서 — "P3·P4 최소 기능 → 삼성전자 3건 통합 실험(사람 Claim) → P5·P6 → 실제 LLM 실측" — 로 고치고 실측 수치를 적는다. 코드 변경 없음.

### V11. 마인드맵 HTML 노드 복사 (사용자 요청 2026-10-23 — 구현 완료)

**지금(전)**: 자체 렌더러(`render/native.py`) 는 노드를 `user-select:none` 에 클릭=접기/펼치기로만 써서 글자를 긁을 수도, 값·출처를 가져갈 수도 없었다. **바꾼 것**: 트리 노드마다 `text`(마크다운 기호 뺀 한 줄) 와 `detail`(`[id] 라벨` + 값/드라이버·시나리오·규칙·이벤트 필드 + 질문·출처·기준일·상태·태그·연결) 을 넣고, 글자 긁기 · `⧉` · 우클릭 메뉴(한 줄/상세/가지 Markdown/id) · `Ctrl+C`(Shift=상세) · 「전체 복사」(맵 전체 Markdown) 를 붙였다. 파이프라인·데이터 계약 변화 없음. 2026-10-23 이전 HTML 은 다시 렌더링해야 한다.

### V12. 마인드맵 HTML 노드 편집 — 사람 몫을 채우는 길 (사용자 요청 2026-10-23 — 구현 완료)

**지금(전)**: v3·v4 는 사람 몫(내 추정·내재 가정·확률·Kill·판단) 을 `pending` 으로 비워 두되, 채우는 길은 콘솔(A 맵 폼 · 시나리오 후보 · Kill 후보 확정 · 텍스트 콘솔) 뿐이었다. **문제**: 맵을 **보는** 곳(HTML) 과 **고치는** 곳(콘솔 폼) 이 달라서, 쟁점·리스크를 읽다가 바로 내 생각을 적을 수 없다. 사용자 질문으로 정한 범위: 저장은 **로컬 서버 + 내려받기 둘 다**, 범위는 **필드 편집 + 노드 추가·삭제**(드래그 이동은 뒤로).

**바꾼 것**:
- **편집 연산 계약** (`generate/edits.py`) — HTML 은 연산 목록만 만든다: `{"op":"set","id","fields"}` · `{"op":"add","parent","id":"new:n","fields"}` · `{"op":"delete","id"}`. `set` 은 `ALLOWED_FIELDS`(라벨·질문·값·value_type·단위·출처·기준일·확신도·상태·다음 확인일·가중치·연결·태그·계산식·노드 종류·드라이버/시나리오/규칙/이벤트) 만. 새 노드 id 는 서버가 매긴다(`next_child_id`: 부모 아래 다음 번호 `A4.6`, 루트 아래 새 가지 `A9`, 「기본 정보」 묶음 아래 `A0.5`), 클라이언트 임시 id 는 `id_map` 으로 뒤 연산에서 치환.
- **검증 규칙 = "편집 전보다 검증 오류가 늘면 거절"** — 복사본에 먼저 적용해 pydantic(`Node.model_validate`) 과 `validate_new_map` 을 돌리고, 편집 전 오류 집합과의 **차이**(`new_errors`) 가 비어야 원본에 쓴다. 초안에 원래 있던 미입력 오류(확률 없음 등) 는 저장을 막지 않고, A1 드라이버를 지워 3개 미만이 되는 식의 구조 훼손만 막는다. 서버 모드에서는 연산마다 `/preview` 로 미리 보여 주고 저장 버튼을 잠근다.
- **출처·꼬리표(provenance)** — 값·드라이버·시나리오·규칙·이벤트가 바뀐 노드는 이전 상태를 `history` 에 남기고(`source="사용자 편집 (HTML)"` — V7 의 `_user_touched` 가 잡는 문구), `user` 태그를 얻고 `draft`·`pending`·`warn` 을 잃는다. 따라서 **변경안(V7) 은 사용자가 고친 노드를 자동 적용하지 않고 후보로만** 올린다. 기준일은 오늘로(사용자가 따로 주지 않으면).
- **저장 경로 둘** (`render/server.py`): ① `mindmap edit <맵 id|파일>` / 콘솔 「고른 맵 HTML 편집…」 → 127.0.0.1 로컬 편집 서버(토큰) 의 `/save` 가 `apply_edits` → 계산 노드 재계산(`finalize_after_edit`) → 저장 → HTML 재생성, 콘솔은 저장을 감지해 맵을 다시 읽는다(메모리의 옛 맵으로 덮지 않도록); ② `file://` 로 연 정적 HTML 은 `<맵>.edits.json` 을 내려주고 `mindmap apply-edits` / 콘솔 「편집 파일 반영…」 이 같은 함수로 넣는다. 둘 다 `base_hash`(노드 전체 + version 해시) 가 다르면 거절, 적용 전 원본을 `maps/versions/<map>.v<N>.json` 에 보존, `meta.version`+1, `meta.edit_log`, A7.2 한 줄.
- **화면** — 더블클릭/`✎`/우클릭/`F2` 편집 폼(값 칸은 value_type 별 입력, 하위 모델은 `edit_schemas()` 가 평탄화한 필드 + 「세부 필드」 토글, 값이 dict 이거나 질문에 `{suggested, confirmed, reason}` 구조가 적혀 있으면 JSON 틀), 「자식 노드 추가…」(`Insert`, 하위 스키마 선택), 「이 노드 삭제」(`Delete`, 두 번), 「고침 n건 · 되돌리기 · 저장 · 모두 취소」 막대.

**v3·v4 원칙과의 관계**: "판단은 논제의 주인 몫" 은 그대로다 — 편집은 **주인이 직접 쓰는 길**을 연 것이지 코드가 판단을 채우는 것이 아니다. 자동 초안의 WATCH·pending 은 사용자가 편집으로 바꿀 때만 바뀌고, 바뀐 사실은 `user` 태그·history 로 남아 평가(승인 오류율 등) 에서 사람 입력과 자동 입력이 갈린다.

**바꾸지 않는 것**: 드래그로 부모·순서 바꾸기(템플릿 id `A1.1…` 재부여가 필요 — §5), Claim·쟁점 자체의 편집(Claim 은 `claims-review`, 쟁점은 재생성), 루트·「기본 정보」 묶음 삭제, 템플릿 노드의 종류(node_type) 변경(사용자가 추가한 노드만).

## 3. 데이터 계약 변경 요약 (전부 하위 호환 — 기본값 있음)

```
Claim      + asserted_by · certainty · as_of · verified_by · review_required · review_reason
ClaimSet   + analyzed_chars · total_chars (analyzed_ratio)
Coverage   + partial · unanalyzed
Issue      + reference_proposition · coverage_basis ("direction"|"llm") · judgement_notes{broker: str}
EstimateCell + sources{broker: (report, date, "대표"|"보강")}
ScenarioCandidate + origin ;  meta.draft.scenario_origin · meta.draft.kill_candidates
Change     + affects ;  ChangeSet + input_snapshot_id
Rule(A4.4.x) origin = "user_confirmed" (meta)
편집(V12)   edits.json = {map_id, base_hash, created_at, edits:[{op:set|add|delete, id|parent, fields}]}
           Node.history += HistoryPoint(source="사용자 편집 (HTML)") · tags += user − {draft, pending, warn, suggested}
           meta.version · meta.edit_log[{date, version, source:"html", ops}] · maps/versions/<map>.v<N>.json
제안값(V13)  meta.draft.suggestions{node_id: [{field, value, label, basis, kind, report_ids, quote?, accepted_at?, prefilled?}]}
           meta.draft.drivers{nid: {metric, col, by_broker}} · meta.draft.suggestion_log · meta.draft.prefilled
           set 연산의 fields._accepted[{field, kind, report_ids, value}] → history source "리포트 제안 수락 — …" (노드엔 저장 안 함)
           미리 채움: tags += suggested (출처 "리포트 제안(미확정) — …")
```

## 4. 단계

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| **Q1** 데이터 계약 | V1(필드·c3·표기) · V2(verified_by·review_required·note 필수) · V3(reference_proposition·명제 판정 배치·unanalyzed) · V5(origin·재해석 기록) | 기존 c2 ClaimSet 이 그대로 읽히고, 삼성전자 3건 `issues` 에 `coverage_basis=llm` 과 ◐/? 가 나온다. 테스트 통과 |
| **Q2** 집계·Kill·폴백 | V4(셀 단위 보강·A/P/E·조정 영업이익) · V6(Kill 후보만) · V8(규칙 모드 제한) | 두산에너빌리티 4건처럼 리포트 날짜가 벌어진 종목에서 보강 셀 `*` 가 보인다. 규칙 모드 초안에 A2.1.x 가 없다. Kill Rule 은 사용자 확정만 |
| **Q3** 변경안·평가·문서 | V7(input_snapshot_id·affects·사전 검증·원자적 저장) · V9(holdout·eval-issues·승인 오류율) · V10 | 합성 4번째 리포트 테스트에서 부분 승인 ⚑ 와 검증 실패 롤백이 재현된다. `eval-issues` 가 삼성전자 골든 이견 1건을 맞춘다 |
| **Q4** 재실측 | 삼성전자 3건 c3 추출 → `claims-review`(review_required 만) → `issues` → `draft`; NAVER·두산에너빌리티는 **holdout** 으로 첫 측정 | 재현율·검토 대기율·이견 누락이 v3 실측과 같거나 좋다. 결과를 devlog 에 |
| **Q5** 사용자 편집 (완료 2026-10-23) | V11(복사) · V12(편집 연산·검증 차이 규칙·provenance·로컬 서버·편집 파일) | file:// 와 서버 모드 모두에서 드라이버 수정 → 자식 추가 → 저장 → 새로고침 → 재편집이 되고, 구조 훼손(A1 삭제) 은 ⚠ 로 잠긴다. 테스트 178 |
| **Q6** 리포트 기반 제안값 (§6, 완료 2026-10-23) | 제안값 계산(`meta.draft.suggestions`, 4종) · 편집 폼 「채우기」 · 수락 기록(history 출처 "리포트 제안 수락") · `--prefill-suggestions` · `suggest`/`eval-suggestions` | 제안을 수락한 노드가 `history` 에 근거와 함께 남고(테스트), 수락률·수락 뒤 수정률이 명령 한 줄로 나온다. 테스트 183 |

## 5. 받지 않은 것과 이유

- P0-4 의 주식 수·환율 동일성 검증: 리포트 표에서 읽을 수 없다 → 경고 문구로만.
- "모든 Claim 사람 승인": 검토의견 스스로 불필요하다고 했고, `review_required` 규칙으로 중요한 것만 올린다.
- 명제 판정을 사람이 전부 하는 것: 종목당 LLM 1~2회로 대신하고, 결과는 `coverage_basis` 로 드러낸다. 끄는 길(`--no-issue-check`) 을 둔다.
- (V12) HTML 에서 드래그로 노드를 옮기는 것: 템플릿 id(`A1.1`, `A4.4.1`) 가 자리 의미를 가져서 옮기면 id 를 다시 매기고 links·formula·변경안 affects 를 전부 고쳐야 한다. 필요해지면 "사용자 노드(user 태그) 끼리만 순서 바꾸기" 부터.
- (§6) 판단 노드에 리포트 값을 **기본으로** 써 넣는 것: 아래 §6 의 이유로 기본은 제안값(사용자가 수락해야 값이 됨) 까지. 자동 기입은 사용자가 켜는 옵션(`--prefill-suggestions`, `suggested` 태그) 으로만.
- (§6) 시나리오 확률·Kill 조건·A6.5 판단에 대한 제안: 리포트에 근거가 없다(확률) 거나 사람이 관측·행동을 채워야 한다(Kill, V6).

## 6. 노드 초기값을 리포트 정보로 채우는 방안 (사용자 제안 2026-10-23 — V13 구현 완료)

**질문**: 지금 비워 두는 노드(사람 몫) 에 리포트에서 얻은 값을 **초기값**으로 넣을 수 있는가, 어디까지가 안전한가.
**사용자 결정(2026-10-23)**: ① A1 내 추정 제안의 기본값 = **컨센**(맵의 컨센 값; 증권사별 값은 label 에) ② `draft --prefill-suggestions` 옵션을 둔다 —
`suggested` → 사람이 저장하면 `user`; "빈 칸보다 틀린 숫자가 낫다" 는 사용자가 켠다(기본 꺼짐).

### 6.1 지금 비어 있는 노드와 리포트가 줄 수 있는 것

| 노드 | 지금 | 리포트에서 얻을 수 있는 것 | 초기값으로 쓸 때의 문제 |
|---|---|---|---|
| A1.x `my_estimate` (내 추정) | None, rationale "논제의 주인이 쓴다 — 비워 두면 계산은 컨센서스 기준(임시)" | 컨센서스 중앙값 · 증권사별 값과 폭 · 최신 리비전 방향 | **앵커링**(B5.1): 내 추정 = 컨센 복사가 되면 A2.2 "내가 다르게 보는 것" 이 사라진다. 또 값이 들어가는 순간 `validate` 의 "my_estimate 없음" 오류가 사라져 맵이 '완성' 처럼 보인다 |
| A2.1 내재 가정 | 비움(쟁점·질문에만 관측) | 증권사 5곳 평균 목표가·PER 등 "시장이 믿는 것" | 이미 질문 줄에 관측으로 적는다 — 값으로 올리면 리포트 종합이 '시장 가정' 으로 둔갑 |
| A2.2 내가 다르게 보는 것 | pending | 쟁점 표의 **소수 의견**(핵심 이견에서 다수 방향과 다른 증권사의 Claim) | 증권사 의견을 '내 견해' 로 둔갑시킨다. 제안 **문장**(인용 포함) 으로는 쓸 만하다 |
| A2.3 시장이 틀린 이유 | pending, **열거형**(시간축/정보/심리/구조) | 목표가 리비전 방향은 관측일 뿐(이미 A3.6.3 에 있다) | 선택지가 아닌 글은 들어갈 수 없고, 리비전은 '시장이 틀린 이유' 가 아니다 → 제안 없음 |
| A3.1~3 시나리오 | 후보 3종 → 사용자 선택 (V5, 재해석 기록) | broker_case / recalc / tp | **이미 설계됨** — 후보가 곧 제안값. 바꿀 것 없음 |
| A3.x 확률 | None | 리포트에 없음 | 통계 근거 없음 → ER·손익비가 가짜가 된다. 예시 30/45/25 는 이미 "미검증 가정" 으로 분리돼 있다 |
| A4.1~3 리스크 확률·영향 | None (키워드 후보는 질문에만, V6) | 조건부 리스크 Claim 의 조건문·인용 | 숫자가 없다. 문장은 제안 가능, 확률·영향은 불가 |
| A4.4 Kill | 후보만 (V6) | 조건부 Claim 수치 | **이미 설계됨**(관측·주기·행동을 사람이 채워야 Rule) |
| A6.1 목표 비중 `confirmed` | 사람 (suggested 는 계산) | — | 제안할 리포트 정보가 없다 |
| A6.2 진입 계획 | pending | 현재가 · 목표가 분포(min/median) · Bear 앵커 | 가격대(`price_range`) 는 제안 가능, "몇 번에 나눠" 는 사람 |
| A6.5 판단 | WATCH 고정 | 투자의견(BUY n/n) | 증권사 의견 ≠ 내 판단. A8.x 에 표시만 (지금도 그렇다) |
| A1.x `next_check` · A7.1 | 실적 발표일·이벤트로 계산 | — | 이미 채운다 |

정리하면 **숫자를 줄 수 있는 곳은 A1 내 추정(컨센 중앙값) 과 A6.2 가격대뿐**이고, 나머지는 문장(소수 의견·조건문) 이거나 이미 후보 제도가 있다.

### 6.2 결론 — "초기값" 이 아니라 "제안값(suggestion)"

값 칸에 자동으로 써 넣지 않고, **편집 폼의 빈 칸 옆에 리포트 근거와 함께 보여 주고 사용자가 한 번 눌러 받아들이면 그때 값이 된다.** 이유 셋:
1. v3 §2.5 원칙 — 판단 노드가 채워지면 `validate`/`evaluate_new_map` 이 완성으로 보고 ER·손익비·비중을 계산한다. 컨센 복사값으로 나온 숫자는 "내 논제" 가 아니라 "증권사 평균의 재계산" 인데 맵에서는 구별되지 않는다.
2. 앵커링 — 빈 칸은 "쓰라" 는 신호이고, 채워진 칸은 "맞다" 는 신호다. 제안값은 **빈 칸 옆**에 있어야 "다르게 볼 것인가" 를 묻는다.
3. V7(user_touched) 과 V9(승인 오류율) 가 사람 입력과 자동 입력을 구분해야 한다 — 수락은 사람 행동이므로 `user` 태그 + 근거 history 로 남고, 자동 기입은 둘을 섞는다.

### 6.3 구현 (V13)

**제안값 계산** (`generate/suggest.py::build_suggestions`): `draft_from_reports` 가 맵을 다 만든 뒤 `meta.draft.suggestions = {node_id: [{field, value, label, basis, report_ids, quote?, kind, accepted_at?, prefilled?}]}` 로 저장한다 (값이 이미 있는 칸엔 만들지 않음; 열거형 노드엔 글 제안을 붙이지 않음). `mindmap suggest <맵>` 이 기존 맵에 다시 만든다(컨센·가격대는 맵 meta 만으로, 소수 의견은 draft 때). 시나리오(A3.1~3) 는 기존 `scenario_candidates` 가 곧 제안이고, 확률·Kill·A6.5 에는 제안을 만들지 않는다.

| kind | 노드 · 필드 | 값 | 근거 |
|---|---|---|---|
| `consensus_median` | A1.x `driver.my_estimate.value` | **컨센**(맵의 컨센 값 = 증권사 평균; `meta.draft.drivers[nid].by_broker` 의 증권사별 값을 label 에) | V4 셀 집계 — `rules.fill_drivers` 가 셀의 by_broker 를 meta 에 남긴다 |
| `issue_minority` | A2.2 값 | 핵심 이견 테마에서 **다수 방향과 다른 증권사**(mixed 포함) 의 대표 Claim 명제 + 인용, `[증권사 전망 · 소수 의견]` 접두어, label "DS↕ 가 IBK↓ · 유진↓ 와 다르게 본다" | V3 쟁점 표 (`display=핵심 이견`, `directions`) |
| `conditional_claim` | A4.1~3 값 (비어 있을 때만 — LLM 초안은 이미 채운다) | 조건부 리스크 Claim 의 조건문 + 인용 (확률·영향은 비움) | V6 `kill_candidates` |
| `price_band` | A6.2 `{price_range, tranches: null}` | Bear 앵커(`scenario_anchors`) ~ 현재가; 앵커·목표가 최저가 현재가 위면 현재가 −10% | A3.6.4 분포 · A0.4 |

**화면** (`render/native.py`): 노드 `edit` 스냅숏에 `suggest` 를 싣고, 편집 폼에서 제안이 있는 칸 아래 "제안: **컨센 725조원 (하나 740 · KB 735 · 키움 700)** [채우기]" 한 줄(이미 수락·미리 채움 표시). 「채우기」 는 입력 칸에 값을 넣을 뿐이고 저장은 평소대로. 폼은 **저장 시점에 입력 값이 제안값 그대로인 것만** `fields._accepted = [{field, kind, report_ids, value}]` 로 보내고, `edits._apply_set` 이 `suggest.record_acceptance` 로 `accepted_at`·`meta.draft.suggestion_log` 를 적고 history 의 source 를 `"리포트 제안 수락 — 컨센서스 (hk_…, hk_…)"` 로 쓴다 (`_accepted` 는 노드에 저장하지 않음). 받아들인 뒤 고쳐서 저장하면 보통 편집(`사용자 편집 (HTML)`). 노드 줄에는 표시 없음 — 사용자가 받아들인 값은 사용자 값이다.

**옵션 (기본 꺼짐)** `draft --prefill-suggestions` / 콘솔 체크박스 「제안값 미리 채우기」 / 텍스트 콘솔 질문: `suggest.apply_prefill` 이 제안값을 값 칸에 넣되 `tags += suggested·pending·draft`, 출처 "리포트 제안(미확정) — 컨센서스". `pending` 이 남아 DraftResult 는 미확정으로 세고, 확률이 None 이라 ER·손익비는 계산되지 않는다. 사람이 편집 폼에서 저장하면 `edits.DROP_TAGS` 가 `suggested` 를 떼고 `user` 를 붙인다. **권장하지 않는다** (6.2 의 2번) — 사용자 결정으로 옵션은 둔다.

**평가** (`suggest.suggestion_stats`, CLI `mindmap eval-suggestions <맵>`): **제안 수락률**(제안 중 `accepted_at` 있는 비율) 과 **수락 뒤 수정률**(수락한 노드에 그 뒤 '사용자 편집 (HTML)' history 가 생긴 비율). 수락 3건 이상에 수정률 0 이면 "⚑ 앵커링 신호" 를 붙인다. 실측값은 devlog 에.

**실측(2026-10-23, 삼성전자 fixture 3건 규칙 모드)**: 제안 4개(컨센 3 · 진입 가격대 1). c3 Claim 저장본으로는 [모바일·세트 수요] 핵심 이견에서 DS 의 소수 의견 1건이 A2.2 제안으로 나온다. 테스트 `tests/test_suggest.py` 5건.
