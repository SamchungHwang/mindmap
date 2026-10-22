# 증권사 리포트 → 투자 마인드맵: 설계 v4

작성 2026-10-22 · 앞선 문서: `report-draft-improvements.md` → `report-draft-design-v2.md` → `report-draft-design-v3.md`(P0~P6 구현 완료, 삼성전자 3건 실측) → **v4 = 검토의견 3(mindmap-review3, "필수 수정 10개") 중 타당·구현 가능한 것을 반영**.
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
- `Claim.review_required: bool` + `review_reason: str`. **규칙**(코드, `validate_claims.mark_review_required`): ① 수치가 목표가·A1 드라이버 지표(op/revenue/eps/target_price) 에 직접 쓰이는 Claim(quant 의 metric 이 그것) ② 같은 (metric, col) 수치가 다른 증권사와 15% 넘게 다른 Claim ③ `certainty=conditional` 인 리스크 Claim ④ `epistemic_type=fact` 인데 `asserted_by=analyst`(애널리스트가 사실로 단정) — 이들은 자동 통과해도 `review_required=True` 로 **검토 대기함에 같이 뜬다**(상태는 verified 유지 — 초안에는 쓰되 ⚑ 표시).
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
- 테마 `display` 규칙은 coverage 를 그대로 쓰므로 바뀌지 않는다. `partial` 은 disagree 로 세지 않고 "부분 동의 n곳" 으로 요약에만.

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
```

## 4. 단계

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| **Q1** 데이터 계약 | V1(필드·c3·표기) · V2(verified_by·review_required·note 필수) · V3(reference_proposition·명제 판정 배치·unanalyzed) · V5(origin·재해석 기록) | 기존 c2 ClaimSet 이 그대로 읽히고, 삼성전자 3건 `issues` 에 `coverage_basis=llm` 과 ◐/? 가 나온다. 테스트 통과 |
| **Q2** 집계·Kill·폴백 | V4(셀 단위 보강·A/P/E·조정 영업이익) · V6(Kill 후보만) · V8(규칙 모드 제한) | 두산에너빌리티 4건처럼 리포트 날짜가 벌어진 종목에서 보강 셀 `*` 가 보인다. 규칙 모드 초안에 A2.1.x 가 없다. Kill Rule 은 사용자 확정만 |
| **Q3** 변경안·평가·문서 | V7(input_snapshot_id·affects·사전 검증·원자적 저장) · V9(holdout·eval-issues·승인 오류율) · V10 | 합성 4번째 리포트 테스트에서 부분 승인 ⚑ 와 검증 실패 롤백이 재현된다. `eval-issues` 가 삼성전자 골든 이견 1건을 맞춘다 |
| **Q4** 재실측 | 삼성전자 3건 c3 추출 → `claims-review`(review_required 만) → `issues` → `draft`; NAVER·두산에너빌리티는 **holdout** 으로 첫 측정 | 재현율·검토 대기율·이견 누락이 v3 실측과 같거나 좋다. 결과를 devlog 에 |

## 5. 받지 않은 것과 이유

- P0-4 의 주식 수·환율 동일성 검증: 리포트 표에서 읽을 수 없다 → 경고 문구로만.
- "모든 Claim 사람 승인": 검토의견 스스로 불필요하다고 했고, `review_required` 규칙으로 중요한 것만 올린다.
- 명제 판정을 사람이 전부 하는 것: 종목당 LLM 1~2회로 대신하고, 결과는 `coverage_basis` 로 드러낸다. 끄는 길(`--no-issue-check`) 을 둔다.
