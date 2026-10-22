# 증권사 리포트 → 투자 마인드맵: 설계 v3

작성 2026-10-21 · 앞선 문서: `report-draft-improvements.md`(개선안 A~L) → `report-draft-design-v2.md`(검토의견 1 반영) → **v3 = 검토의견 2(mindmap-review2) 중 타당한 것을 구현 가능성까지 확인해 반영**.
구현 가능성은 현재 코드(`src/kstock`, 테스트 109건)에 대보고 판정했다 — 어느 모듈을 어떻게 바꾸는지, 바꿀 수 없는 것은 무엇인지 적는다.

## 0. v2 → v3 에서 바뀐 것

| 영역 | v2 | v3 | 근거 |
|---|---|---|---|
| Claim 단위 | 문장에서 뽑은 주장 1개, `kind` 하나 | **검증 가능한 명제 1개**, `epistemic_type`(fact/estimate/opinion) + `roles`(복수) 분리, `condition` 필드 | 복합문 분해 안 하면 한쪽 의미가 사라짐 |
| 근거 | `evidence` 문자열, 원문 존재 확인 → "환각률 구조상 0" | **`EvidenceSpan`**(문서·페이지·오프셋) 참조 + 3단 검증(인용 존재·숫자 일치·**의미 지지**) → **근거 불일치율을 측정·억제** | 인용이 있어도 주장이 왜곡될 수 있다 |
| 실패 Claim | 폐기 | **검토 대기함** 보존 (맵에는 안 들어감) | 재현율·사람 검토 |
| Issue 묶기 | `driver` 하나 | **테마(driver) → 메커니즘(driver→target 경로)** 계층, 복수 상태(`consensus_direction / disagreement_dimensions / unresolved`) | 같은 driver·같은 방향도 다른 가정일 수 있다 |
| 이견 판정 | 수치 차이 25% | 상대 분산 + 절대 분산 + **목표가 영향 근사**, 수치 이견과 의미 이견 분리 | 25% 는 경고 기준일 뿐 |
| A2.1 | 증권사 컨센서스 = "내재 가정" 근사 | A2.1 의 자식은 **"리포트에서 관측된 컨센서스·이견"** 으로 표기, **내재 기대 자체는 미산출(질문 유지)** | 증권사 전망 ≠ 주가에 반영된 기대 |
| 증권사별 표시 | — | 쟁점마다 증권사별 **언급/동의/반대/침묵** 행렬 | "안 썼다" ≠ "반대한다" |
| 시나리오 | 리포트 제시 > **min(EPS)×min(PER)** > 분포 폴백, Bear 하한 −40% | 리포트 제시 > **리포트 내부 가정만으로 재계산** > 범위만 보이고 **시나리오 공란** > 사용자 입력. **교차 조합·분포 폴백·−40% 하한 삭제.** 증권사별 "자체 시나리오 후보" 를 보여 주고 사용자가 골라 넣음 | 어느 증권사도 제시하지 않은 가정을 만들지 않는다 |
| 확률 | 공란, 「기본 확률 30/45/25 넣기」 | 공란, 「**예시 확률 입력**(미검증 가정)」 경고 문구. **목표가별 상승여력·가격 범위는 계산** | 통계적 근거 오해 방지, 일괄 보류는 과함 |
| Kill | 지금 수준 −20% 자동 | **원문 근거(조건부 리스크 주장)가 있으면 제안, 없으면 질문** | 임의 가정 금지 |
| A6.5 | "WATCH" 와 "공란" 혼재 | A6.5 값은 템플릿대로 WATCH(미완료 강제), **meta 에 `draft_status`·`approval_status` 분리** | "미완성이라 WATCH" vs "분석상 보류" 구분 |
| 리비전 분해 | TP = EPS × PER | **`valuation_method` 분류 후 PER 방식·동일 증권사 2건일 때만** | PBR·DCF·SOTP 리포트에 적용 불가 |
| 실적 전/후 | 분기 말 +8일 추정 | **실제 발표일**(리서치 DB `disclosures` / DART) 우선, 없으면 추정 표기 | |
| LLM 호출 | 리포트당 1회 | **섹션·길이 가변 청킹**(투자포인트·실적 전망·밸류·리스크 우선) + 청크 간 중복 제거 | 인뎁스 리포트는 잘린다 |
| 폴백 | 키워드 분류로 자동 폴백 | 폴백은 **품질 등급 `quality: rules`** 표기 | |
| ChangeSet | 승인 후 노드 수정 | **원본 스냅숏·변경안·승인 결과를 각각 버전으로** | 승인 전후 재현 |
| 중간 산출물 | `data/claims/` | `ReportFacts`·`Claim`·`Issue`·`Map` 전부 파일로 | 단계별 실패 추적, 모델 교체 비교 |
| 정답표 | 핵심 주장 5 + 리스크 3 | **필수 주장표 + 전체 후보 검토표**(정밀도 측정), 나중에 미사용 리포트로 일반화 | 상위 5개만이면 정답표 밖 유효 주장이 오답 처리 |
| 순서 | P0~P6, 평가는 P6 | **P2 전 데이터 계약 확정, 평가는 모든 단계에**, P2 뒤 **최소 실험(삼성전자 3건) 관문** | |

받아들이지 않은 것은 없다. 조건을 붙인 것은 §2.3(묶기 키 계층), §2.5(시나리오 후보), §2.2(의미 검증 배치), §2.6(청크 중복 제거) — 각 절에 적었다.

## 1. 바뀌지 않는 것

- 입력은 같은 종목의 증권사 리포트 n건(파일). 리포트 밖의 사실·판단을 만들지 않는다.
- 읽기(`reports/loader`)·숫자 추출(`reports/extract`)·수치 집계(`reports/digest`)·교차검증(`generate/check`)·엔진(`engine`)·저장(`store`)·콘솔은 유지. 가운데 층(문장 분류 → Claim/Issue) 과 시나리오 의미, 증분 방식만 바뀐다.
- 사용자 판단 영역(A2.2·A2.3·A6.5 의 BUY/PASS·'내 추정')은 초안이 채우지 않는다. 자동 초안의 판단은 항상 WATCH.
- A0~A8 노드 정의(템플릿 v1.0 §3.2)는 바꾸지 않는다. 필요한 구분은 자식 노드·`links`·`tags`·`meta` 로 한다.

## 2. 설계

### 2.1 데이터 모델 (데이터 계약 — P2 전에 확정)

```
EvidenceSpan { span_id, report_id, page, start, end, text }        # 정규화된 리포트 텍스트의 오프셋. 페이지는 '\f' 개수로 복원
Claim {
  claim_id, report_id, broker, date,
  proposition        # 검증 가능한 명제 하나 (LLM 이 정리한 문장)
  epistemic_type     # fact | estimate | opinion
  roles              # [thesis | risk | catalyst | valuation | shareholder | business | revision_reason | scenario]
  driver_id          # 정규화 사전의 id (예: hbm4_shipments) | null
  target_metric_id   # 영향 대상 (예: memory_op, eps_2027e, target_per) | null
  mechanism          # driver → target 경로 라벨 (예: "인증 완료 → 출하") | null
  direction          # positive | negative | neutral
  condition          # 조건부 주장이면 조건문 ("초기 수율이 안정되지 않으면") | null
  horizon            # 2027E | 4Q26 | 2026-10-30 | null
  quant_refs         # [{metric, col, value, unit, source: table|text}] — 표나 인용 안에 있는 값만
  span_ids           # EvidenceSpan 참조 (여러 개 가능; 같은 span 이 여러 Claim 을 받칠 수 있음)
  validation         # {quote: ok|miss, numbers: ok|mismatch|none, support: supported|contradicted|insufficient|unchecked}
  status             # verified | needs_review | rejected
  inferred           # LLM 이 리포트에 없는 인과를 보탰으면 true (맵에 안 들어감, 검토함에만)
  extractor          # {model, prompt_version, chunk_id}
}
Issue {
  issue_id           # "<driver_id>" (테마) 또는 "<driver_id>/<mechanism_slug>" (메커니즘)
  level              # theme | mechanism
  driver_id, target_metric_id, mechanism
  claim_ids
  coverage           # {broker: mentioned|agree|disagree|silent}
  consensus_direction        # positive | negative | mixed | none
  disagreement_dimensions    # [direction | magnitude | timing | condition]
  numeric_spread             # {metric, col, min, max, rel, abs, tp_impact} | null
  unresolved_questions       # ["고객 인증 완료 시점"]
  summary                    # LLM 한 줄 (근거 claim_ids 필수)
  display                    # 공통 견해 | 핵심 이견 | 독립 주장 | 미해결 쟁점  ← 화면용, 코드가 규칙으로 계산
}
ChangeSet {
  base_map_id, base_version, base_snapshot_hash, new_report_ids,
  changes: [{ node_id, kind: 추정치 변화|새 주장|기존 논제와 충돌|TP 리비전|새 리스크|새 촉매|확률/판단 영향,
              before, after, claim_ids, user_touched, decision: pending|approved|rejected }],
  summary, created_at, decided_at, result_version
}
```

구현 가능성: `Node` 에 `links`·`history`·`tags`·`source` 가 이미 있어 Claim/Issue 를 노드에 연결하는 데 스키마 변경이 없다. 페이지는 `loader` 가 페이지를 `"\n\f"` 로 잇고 `normalize_text` 가 `\f` 를 지우지 않으므로 오프셋 앞의 `\f` 개수로 복원된다(확인함). `Scenario.probability` 는 지금 필수 float → **`float | None`** 으로 바꿔야 한다(§2.5). 새 모델은 `models/claims.py` 에 pydantic 으로.

### 2.2 Claim 추출과 3단 검증 (`reports/claims.py`, `reports/validate_claims.py`)

**청킹.** 리포트 텍스트를 소제목·페이지로 자르고 섹션을 태깅한다(표지 「투자포인트」·실적 전망·밸류에이션·리스크·기타). 청크 길이 상한 ~6k 토큰, 우선순위 섹션부터, 긴 인뎁스는 여러 청크. 각 청크에 LLM 1회 → Claim 후보 JSON. 청크 간 중복은 명제 유사도(`difflib` ratio ≥ 0.85 또는 같은 span) 로 합친다. 짧은 Brief 는 1회, 인뎁스는 3~5회.

**프롬프트 규칙.** 명제 하나에 Claim 하나(복합문은 나눠라), 리포트에 없는 인과는 `inferred=true` 로 따로, 모든 Claim 은 원문 인용(span) 을 붙여라, 숫자는 표나 인용에 있는 것만.

**검증 3단.**
1. 인용 존재 — 코드. 공백 정규화 후 리포트 텍스트에 그대로 있어야 한다(`llm_fill.evidence_in` 재사용). 없으면 `quote: miss` → `needs_review`.
2. 숫자 일치 — 코드. `quant_refs` 의 값이 `ReportFacts.estimates` 나 span 텍스트에 있는지(단위 환산 허용: 억/십억/조). 불일치 → `needs_review`.
3. 의미 지지 — **별도 LLM 판정, 배치**(한 호출에 20~30개: "이 인용이 이 명제를 지지하는가" → supported / contradicted / insufficient). `contradicted` → `rejected`, `insufficient` → `needs_review`. 리포트당 1회 추가 호출.

`verified` 만 Issue 통합과 맵에 들어간다. `needs_review` 는 콘솔 「검토 대기함」 에서 사람이 승인/반려(승인되면 verified). 측정 지표는 **근거 불일치율**(= 사람이 확인한 verified 중 실제로 원문이 지지하지 않는 비율) — 0 을 주장하지 않고 P0 정답표로 잰다.

**driver 정규화 사전** — `generate/kpi_catalog.py` 확장: id · 한국어/영어 동의어 · 섹터 · 기본 target. LLM 은 사전 id 로 매핑하되 없으면 `driver_id=null` + 자유 라벨 → 사전 후보로 쌓인다(사람이 승격).

LLM 이 없을 때는 지금의 문장 분류로 Claim 흉내(`epistemic_type=opinion`, `validation.support=unchecked`, 맵 `meta.draft.quality="rules"`) — 화면에 "규칙 분류(저품질)" 로 표기.

### 2.3 Issue 통합 (`generate/issues.py`)

**묶기는 계층**(검토의견의 5요소 키를 그대로 쓰면 Claim 마다 Issue 가 되어 비교가 안 된다):
- 테마 = `driver_id` (예: hbm4). 화면의 1차 묶음.
- 메커니즘 = `driver_id + target_metric_id + mechanism` (예: hbm4 / 인증 완료 → 출하, hbm4 / 수율 → 원가, hbm4 / 믹스 → ASP). 서로 독립적으로 실패할 수 있는 단위.
- `horizon`·`condition` 은 묶기 키가 아니라 **이견 차원**(timing / condition) 으로 기록한다.

**상태는 복수**(enum 하나가 아니다):
- `consensus_direction`: 메커니즘 안의 verified Claim 방향이 한쪽이면 그 방향, 섞이면 mixed.
- `disagreement_dimensions`: 방향이 갈리면 direction; `numeric_spread` 가 크면 magnitude; horizon 이 다르면 timing; 한쪽만 조건부면 condition.
- `unresolved_questions`: 방향이 갈리는데 어느 쪽도 `quant_refs` 가 없거나, 리포트가 "불확실·확인 필요" 라 쓴 것, `insufficient` 판정이 남은 것.
- `coverage`: 증권사별 mentioned / agree / disagree / **silent** — 침묵은 부정이 아니다.

**이견 크기** `numeric_spread` = 같은 metric·col 에 대한 증권사별 값의 {min, max, rel = (max−min)/|median|, abs, tp_impact}. `tp_impact` 는 근사: EPS 류면 `abs × 중앙 목표 PER / 현재가`, 영업이익 류면 EPS 로 환산 가능할 때만, 아니면 null. **핵심 이견 표시 조건**: direction 이견이거나, `rel ≥ 0.25 이고 tp_impact ≥ 0.10`(tp_impact 가 null 이면 rel ≥ 0.25 이고 abs 가 해당 metric 중앙값의 25% 이상). 수치 이견(magnitude) 과 의미 이견(direction/condition) 은 화면에서 분리.

**화면용 display** (코드 규칙): 공통 견해 = 증권사 2곳 이상 agree 이고 disagreement 에 direction 없음 · 핵심 이견 = 위 조건 · 독립 주장 = agree 1곳이고 `quant_refs` 또는 선행지표(수주·가동·인증·계약·단가) 있음 · 미해결 쟁점 = unresolved 가 있고 공통/이견에 들지 않음. 하나의 테마가 방향은 공통, 규모는 이견일 수 있으므로 테마 노드 아래 메커니즘별로 표시한다.

LLM 은 `summary` 한 줄과 메커니즘 라벨 제안만 한다. 라벨은 사전에 없으면 자유 라벨로 두고 재실행 시 안정성을 위해 `claim_ids` 집합이 같으면 같은 issue_id 를 유지한다(이전 Issue 파일과 대조).

### 2.4 맵 매핑 (`generate/rules.py` 재구성)

| 노드 | 채우는 것 | 출처 표기 |
|---|---|---|
| A0 논제 | 공통 견해(테마) 로 만든 한 문장 초안, `tags: [draft, 증권사 종합]` | 근거 issue_ids |
| A0.4 현재가 | 작성 시점 조회(기존) | |
| A1.x 드라이버 3~5 | verified Claim 이 가장 많이 걸리고 `quant_refs` 가 있는 테마. `current` = 최근 실적(표), `consensus` = 증권사 평균(표) — **표에 없는 변수는 리포트 본문 숫자 + 출처, 없으면 질문 노드**. `my_estimate` 는 **빈칸** | 표 셀 / span |
| A1.x.links | 그 테마의 Issue(A2.1.x) | |
| A2.1 내재 가정 | **값은 비움(질문 유지)**. 자식 `A2.1.1~` 에 Issue 를 "리포트 관측: 공통 견해 / 핵심 이견 / 독립 주장 / 미해결 쟁점" 라벨로. 각 자식에 coverage 행렬 표시 | claim_ids |
| A2.2 · A2.3 | 비움 (사람) | |
| A3.1~3 시나리오 | §2.5 | |
| A3.4 · A3.5 | 확률이 없으면 **보류**(None, 메모 "확률 입력 대기") | |
| A3.6.3 리비전 | 증권사별 TP·의견 변경 이력 + 사유(`revision_reason` Claim 인용) 또는 "사유 확인 불가" | |
| A3.6.4 (신설, fact) | 증권사 목표가 분포: min/median/mean/max/n/분산 + **목표가별 상승여력**(현재가 대비) | 리포트 |
| A4.1~3 리스크 | roles 에 risk 인 verified Claim 상위 3 (조건부 주장 우선) + 미해결 쟁점 | claim_ids |
| A4.4 Kill | **조건부 리스크 Claim 에 `quant_refs` 가 있으면 Rule 제안**("수율 60% 미달 시" → `driver[...] < 60`), 없으면 **질문 노드로 비움** | span |
| A5.1 촉매 | roles 에 catalyst 이고 `horizon` 이 날짜인 Claim | span |
| A6.5 | WATCH (미완료 강제, 템플릿) · `meta.draft.draft_status`(미완성/검토 필요/검증 완료) · `meta.draft.approval_status`(미승인/승인/반려) | |
| A7.2 로그 | 생성·변경안·승인 이벤트 | |
| A8 근거 | 리포트별: 증권사·날짜·제목·TP·의견 · verified Claim 목록(인용·페이지) · 추정치 표 · `valuation_method` | |

모든 자동 노드의 `source.name` = `"{broker} {date} p.{page} · {claim_id}"`. 콘솔에서 노드를 고르면 인용문이 뜨고 PDF 를 연다(`os.startfile`; 페이지 이동은 뷰어 의존).

**검증 결과 표시.** 비워 둔 노드(A2.2·A3 확률·A4.4·my_estimate)는 `validate` 에서 error 로 나오는데 이는 "초안이라 비움" 이다. `DraftResult.summary_text` 는 issues 를 **미확정 항목(의도적 공란)** 과 **오류** 로 나눠 보인다 — 노드 `tags` 에 `pending` 이 있으면 미확정.

### 2.5 시나리오 (`generate/scenarios.py`)

우선순위, 위에서부터 하나만:
1. **리포트가 Bull/Base/Bear(낙관/중립/비관 케이스) 와 가정을 명시** — 정규식(`Bull|Bear|낙관|비관|보수적 (케이스|시나리오)`) + roles=scenario Claim → 그대로 구조화, `valuation_method="리포트 제시: {broker}"`. 여러 리포트가 제시하면 **증권사별 후보로 두고 사용자가 고른다**(자동으로 섞지 않는다).
2. **리포트별 가치평가 산식이 추출됨** — `valuation_method` 분류(PER/PBR/EV·EBITDA/DCF/SOTP/RIM, 정규식 "PER 12배 적용", "Target P/B 1.5x") 과 그 리포트의 EPS·배수가 있으면 **그 리포트 안의 값만으로** TP 를 재계산해 "자체 시나리오 후보"(증권사·TP·EPS·배수·가정) 를 만든다. 후보는 `meta.draft.scenario_candidates` 에 저장되고 콘솔에서 **「후보를 Bear/Base/Bull 로 넣기」** 로 사용자가 고른다. 한 증권사 안의 가정만 쓰므로 새 가정을 만들지 않는다.
3. **목표가만 있음** — A3.6.4 에 범위·상승여력만. A3.1~3 은 공란(`pending`).
4. **근거 부족** — 공란, 질문 유지.

삭제: min(EPS)×min(PER) 교차 조합, 목표가 분포→시나리오 폴백, Bear 하한 −40%(`BEAR_FLOOR`). 52주 저가·현재가 −40% 는 A3.6.4 아래 "참고 범위" 한 줄로만.

**확률.** 자동 입력 없음. 콘솔 버튼 「예시 확률 입력(30/45/25 — 통계적 근거 없는 미검증 가정)」 → 사용자 행위로 `history` 에 기록. 확률이 없으면 A3.4·A3.5 보류, 그러나 **목표가별 상승여력과 Bear/Base/Bull 가격 범위는 계산**(A3.6.4).

구현: `Scenario.probability: float | None`(스키마), `calc.expected_return/risk_reward` 는 None 이 있으면 `CalcError("확률 없음")` → `evaluate_new_map` 이 이미 CalcError 를 메모로 받는다(확인함). `validate` 는 None 을 "확률 미입력" error 로(미완료 → WATCH, 템플릿 §3.3 과 일치).

### 2.6 수치 집계 (`reports/digest.py`, 대부분 코드)

- 증권사별 **최신 1건** 이 집계 대표(`latest_per_broker=True`, 기본). 이전 건은 리비전 이력.
- `basis` 태그(연결/별도, 지배/전체) 가 같은 값만 평균. 행 라벨 `(지배)`·표 머리 `K-IFRS 연결` 에서 읽는다.
- `spread(metric, col)` = min/max/median/mean/std/n/brokers — A3.6.4·A8·Issue `numeric_spread` 공용.
- **실적 전/후**: 리서치 DB `disclosures` 에 실적 공시일이 있으면 그 날짜, 없으면 분기 말 +8일 **추정** 표기. 발표 전 리포트는 "실적 전" 태그, 45일 초과는 집계 제외 옵션(기본 켬, 끌 수 있음).
- 리비전 분해는 `valuation_method == PER` 이고 같은 증권사 2건이 함께 있을 때만 (EPS 변화 × 배수 변화 = `calc.decompose_return` 재사용). 아니면 `revision_reason` Claim 인용 또는 "사유 확인 불가".
- 파일명의 한경 리포트 id → 리서치 DB `reports/report_details` 와 대조(증권사·날짜·제목·TP). 다르면 깃발.

### 2.7 변경안 (`generate/changes.py`, `store` 버전)

새 리포트 m건 → 같은 추출·검증·집계 → 기존 맵과 비교해 `ChangeSet`. 자동으로 노드를 바꾸지 않는다.
- 비교 대상: A1 consensus 값, A2.1.x Issue(새 Issue·방향 변화·coverage 변화), A3.6.3/A3.6.4(TP·분포), A4(새 리스크), A5(새 촉매), A8(리포트 목록). 사용자가 손댄 노드(`history` 에 사용자 기록 또는 `draft` 태그 없음) 는 `user_touched=true` 로 후보에만.
- 저장: `data/changes/<map_id>_<ts>.json`(원본 `snapshot_hash` 포함 — `engine/validate.snapshot_hash` 재사용). 승인 시 `data/maps/versions/<map_id>.v<N>.json` 에 원본 보존, 적용 결과는 v<N+1>, `meta.version`·`meta.draft.approval_status` 갱신, A7.2 에 이벤트. 반려도 기록. B 맵이 있으면 같은 ChangeSet 을 B2.4 사실 로그·B3 이벤트 후보로.
- 콘솔 「변경안 보기」: 표(노드·종류·before→after·근거 인용·체크박스) → 승인/반려.

### 2.8 LLM 경로

`GroupFiller` Protocol(`complete(system, user) -> str`) 이 공급자 독립 인터페이스다. Claude(`ClaudeGroupFiller`) 와 **OpenAI 호환 필러**(`.env` 의 spark vLLM) 를 같은 Protocol 로 두고, 처음엔 연결되는 하나로 간다. `mindmap llm-check` 로 연결을 확인한다(구현됨). 현재 `.env` 키 계열이 `sk-ant-usr-` 라 Console API 키(`sk-ant-api03-`) 인지 확인이 필요하다.

## 3. 단계 (P0 → P6)

| 단계 | 작업 | 완료 기준 | 크기 |
|---|---|---|---|
| **P0** | 13건 정답표 + **데이터 계약 확정**(§2.1 pydantic 모델·JSON 예시·프롬프트 v1) — 정답표는 **필수 주장표**(리포트당 핵심 5·리스크 3, span 포함) + **전체 후보 검토표**(AI 추출분의 지지 여부를 사람이 표기) + 종목별 쟁점 정답(공통/이견/독립/미해결). AI 초안 → 사용자 검수 | `mindmap eval-extract` 필드별 정확도 표, 정답 JSON 13건 | 2일 |
| **P1** | 수치 표준화 마무리: basis 태그·최신 1건·spread·실적 전/후(발표일)·45일 옵션·DB 교차 확인 | 13건 수치 정확도 ≥ 95%, 메리츠 중복 해소, 단위 오류 0 | 1~2일 |
| **P1.5** | LLM 인터페이스: 키 확인(`llm-check`), OpenAI 호환 필러, **Claim JSON 호출·실패 처리·재시도** 검증 | 그룹/Claim 호출 성공, 실패 시 상태 기록 | 0.5~1일 |
| **P2** | Claim 추출(청킹·프롬프트·중복 제거) + 3단 검증 + 검토 대기함 + 저장(`data/claims/`) | 필수 주장 재현율 ≥ 80%, **근거 불일치율 측정**(목표 ≤ 5%), 환각(inferred) 이 맵에 0건 | 4일 |
| **P2.5 최소 실험** (정정 2026-10-22, 검토의견 3 P1-3) | 원문은 P3·P4 의 결과물(공통 논거·이견·A0~A8 초안) 을 P2.5 완료 기준으로 요구해 선후가 맞지 않았다. **실제 진행 순서**: P3(쟁점)·P4(맵 매핑) 의 최소 기능을 먼저 만들고 → 삼성전자 3건에 **사람이 LLM 역할로 쓴 Claim 31건**(`examples/claims_experiment/`) 으로 통합 실험(쟁점 11·맵 A2.1.1~8) → P5·P6 → **실제 Claude 실측**(2026-10-21, Sonnet 5.5, 프롬프트 c2): Claim 40 · verified 35 · 검토 대기 5 · 재현율 86% · 핵심 이견 [모바일·세트 수요] 복원 | 통과 — 후속은 `report-draft-design-v4.md` | 실제 2일 |
| **P3** | Issue 통합: 사전 확장·계층 묶기·복수 상태·coverage·numeric_spread·display 규칙·LLM 요약 | 3종목 쟁점 표가 정답표와 일치(공통/이견/독립/미해결 각 ≥ 80%) | 3일 |
| **P4** | 맵 매핑 재구성(§2.4)·시나리오(§2.5)·`Scenario.probability` Optional·엔진 보류·미확정/오류 분리·출처 역추적 UI·예시 확률 버튼 | 검증 오류(진짜) 0, 모든 자동 노드 → claim/표 역추적, 임의 가정 0 | 4일 |
| **P5** | ChangeSet·버전·승인 UI·A7.2/B 맵 연동 | 가상 4번째 리포트: 사용자 수정 노드 불변, 승인 전후 재현 | 3~4일 |
| **P6** | 평가 루프 상시화(`generate/evals.py`: 추출·Claim·Issue·수정률), 미사용 리포트로 일반화, 콘솔 다듬기 | 프롬프트·사전·모델 변경마다 숫자 비교 | 지속 |

P0·P1·P1.5 는 바로 시작할 수 있고 서로 독립이다. **핵심 성공 기준은 P0 → P2 → P3** — 숫자 취합은 다른 도구도 하지만 "왜 전망이 갈리는가" 를 정확히 보이는 것이 차별점이다.

## 4. 모듈 배치 (추가·변경만)

```
src/kstock/
  models/claims.py            [신규] EvidenceSpan · Claim · Issue · ChangeSet (pydantic)
  models/sub.py               [변경] Scenario.probability: float | None
  reports/loader.py           [변경] 페이지 오프셋 helper (page_of(offset)), 섹션 태깅
  reports/claims.py           [신규] 청킹 · Claim 추출 프롬프트 · 중복 제거 · 저장/로드 (data/claims/<report_id>.json)
  reports/validate_claims.py  [신규] 3단 검증 (인용·숫자·의미 배치 판정) · 검토 대기함
  reports/digest.py           [변경] latest_per_broker · basis · spread · 실적 전/후 · 리포트 id ↔ DB 대조
  generate/kpi_catalog.py     [변경] driver 정규화 사전 (id·동의어·target·섹터) + 미등록 라벨 수집
  generate/issues.py          [신규] 계층 묶기 · 복수 상태 · coverage · numeric_spread · display 규칙 · LLM 요약
  generate/scenarios.py       [신규] 리포트 제시 시나리오 · valuation_method 분류 · 증권사별 후보 · (교차 조합·폴백 없음)
  generate/rules.py           [변경] Claim/Issue 기반 매핑 (§2.4), BEAR_FLOOR·자동 Kill 제거, pending 태그
  generate/check.py           [변경] 미확정 vs 오류 분리, 임의 가정 검사(출처 없는 자동 노드 = 오류)
  generate/changes.py         [신규] ChangeSet 생성·저장·적용·버전
  generate/evals.py           [신규] 정답표 대비 추출·Claim·Issue 지표
  generate/llm_fill.py        [변경] OpenAICompatFiller · 배치 판정 helper (기존 그룹 채움은 폴백/보조)
  engine/calc.py              [변경] 확률 None → CalcError("확률 없음")
  engine/validate.py          [변경] 확률 미입력 error 문구, pending 태그 인식
  store.py                    [변경] versions/ 보존 · meta.version
  console.py / console_text.py[변경] 검토 대기함 · 쟁점 표 · 시나리오 후보 넣기 · 예시 확률 · 변경안 보기 · 출처 열기
  cli.py                      [변경] claims / issues / changes / eval-* 서브커맨드
tests/golden/                 정답표 (PDF 는 저장소 밖, 경로만)
data/claims/ data/issues/ data/changes/ data/maps/versions/   중간 산출물
```

## 5. 확정할 것 (두 검토의견 모두 같은 권고 — 이의 없으면 v3 기본값)

1. '내 추정' 기본값 **빈칸** (계산은 "컨센 기준 임시" 표기).
2. 시나리오 확률 **공란**, 「예시 확률 입력」 은 미검증 경고와 함께.
3. LLM 은 **공급자 독립 인터페이스 + 연결되는 모델 하나**로 시작 (Anthropic 키 계열 확인 → 안 되면 spark vLLM).
4. P0 정답표는 **AI 초안 + 사용자 검수**.
