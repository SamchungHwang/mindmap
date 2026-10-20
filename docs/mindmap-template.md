# 주식투자 마인드맵 템플릿 v1.0

> **목적** — 신규 투자 판단(맵 A), 보유 종목 대응(맵 B), 포트폴리오 롤업(맵 C)을 **하나의 노드 스키마**로 생성·갱신·복기할 수 있게 하는 기준 문서. 마인드맵 자동 생성 시스템의 데이터 모델과 규칙의 출발점.
> **작성** 2026-10-19 · v1.0 · 작성자 hsc65 + Claude

---

## 0. 설계 원칙

**논제 중심.** 모든 종목 맵의 중심은 종목명이 아니라 한 문장 투자 논제다. 모든 가지는 그 논제를 지지·반박·숫자화·실행하는 역할만 한다. 논제와 무관한 정보는 근거 레이어(A8)로 내려가 접힌다.

**매입가 제거.** 보유 맵에서 매입가·평가손익은 세금 계산과 복기용 메타데이터일 뿐, 어떤 판단 노드의 입력도 아니다. 보유 맵의 중심 질문은 "오늘 현금으로 이 가격·이 비중에 새로 사겠는가"이다.

**숫자화와 사전 약속.** 시나리오는 확률과 목표가를 갖고 기대수익률·손익비로 환산된다. 대응은 사건이 터진 뒤 정하는 것이 아니라 If-Then 규칙으로 미리 적어 둔다.

**선별(Triage).** 종목마다 상태등(🟢🟡🔴)을 규칙으로 산출하고, 사람의 주의는 🟡🔴에만 배분한다. 🟢 종목은 분기 5분 확인으로 끝나야 한다.

**분리와 참조.** 매크로·섹터는 공통 맵으로 분리해 캐시하고 종목 맵은 링크로 참조한다. 모든 노드는 출처·기준일·확신도를 갖고, 오래된 노드는 자동으로 stale이 된다.

---

## 1. 전체 구조

```
[공통 참조 레이어]     M 매크로 맵 (월 1회 갱신)        S 섹터 맵 (섹터별, 월 1회)
                             ▲ link                           ▲ link
                             │                                │
[종목 레이어]           A 신규 투자 맵 ──(매수 체결 → 스냅숏)──▶ B 보유 종목 맵
                        "살 것인가"                           "계속 들고 있을 자격이 있는가"
                                                                   │ 집계(roll-up)
[포트폴리오 레이어]                                           C 포트폴리오 롤업
                                                              "전체로 보면 무엇을 해야 하는가"
```

| 맵 | 접두어 | 생성 시점 | 갱신 주기 | 주 사용자 질문 |
|---|---|---|---|---|
| A 신규 투자 맵 | `A` | 관심 종목 등록 시 | 판단 전까지 수시 | 지금 이 가격에 사야 하나 |
| B 보유 종목 맵 | `B` | 매수 체결 시 자동 (또는 논제 복원 위자드) | 이벤트·임계·정기 | 추가/유지/축소/교체/매도 중 무엇인가 |
| C 포트폴리오 롤업 | `C` | 항상 존재 (단일) | B 변경 시 재계산 | 전체 노출·현금·실행 순서 |
| M 매크로 맵 | `M` | 단일 | 월 1회 또는 FOMC/금통위 직후 | 레짐이 바뀌었나 |
| S 섹터 맵 | `S.<시장>-<섹터>` | 섹터별 1개 | 월 1회 또는 주요 이벤트 | 사이클 위치·교체 후보 |

---

## 2. 공통 노드 스키마

모든 맵의 모든 노드는 아래 하나의 스키마를 공유한다. 맵별 차이는 `id`·`question`·`value_type`과 하위 스키마(§2.3)로만 표현된다.

### 2.1 노드 필드

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `id` | string | ✓ | 맵 접두어 + 계층 번호. 예 `A1.2`, `B6`, `C3.1`, `S.KR-SEMI.3` |
| `map` | enum | ✓ | `NEW` / `HOLD` / `PORT` / `MACRO` / `SECTOR` |
| `parent` | string \| null | ✓ | 상위 노드 id. 중심 노드는 `null` |
| `label` | string | ✓ | 표시 이름 |
| `question` | string | ✓ | 이 노드가 답해야 할 질문. LLM이 노드를 채울 때의 프롬프트 기준 |
| `node_type` | enum | ✓ | `fact` / `estimate` / `question` / `judgment` / `rule` / `event` / `link` / `computed` |
| `value` | any | | 노드의 값 (비어 있으면 미답 질문) |
| `value_type` | enum | ✓ | `number` / `percent` / `price` / `probability` / `date` / `text` / `enum` / `range` / `ref` / `bool` |
| `unit` | string | | `%`, `원`, `배`, `억원`, `개월` 등 |
| `enum_options` | string[] | enum일 때 | 허용 값 목록 |
| `source` | object | fact·estimate | `{name, url, retrieved_at}` |
| `as_of` | date | ✓ | 값의 기준일 |
| `confidence` | enum | | `H` / `M` / `L` |
| `status` | enum | ✓ | `confirmed` / `estimated` / `unverified` / `stale` |
| `next_check` | date | | 다음 확인일. 스케줄러의 입력 |
| `weight` | number 0~1 | | 형제 노드 간 상대 가중치. 투자 유형 프로파일(§8)이 덮어씀 |
| `links` | string[] | | 참조하는 다른 맵의 노드 id |
| `history` | array | | `[{as_of, value, source}]` 시계열. 보유 맵의 변화량 계산 근거 |
| `tags` | string[] | | 자유 태그 (`팩터:성장`, `통화:USD` 등 — C2에서 집계) |
| `formula` | string | computed | 계산 노드의 수식 (§3.4, §4.4 참조) |

### 2.2 node_type와 status 의미

| node_type | 의미 | 필수 조건 |
|---|---|---|
| `fact` | 확인된 사실 | `source` 필수 |
| `estimate` | 추정치. 컨센서스/내 추정은 하위 스키마 또는 tag로 구분 | `confidence` 권장 |
| `question` | 아직 답이 없는 질문. 비어 있어도 맵에 남겨 미확인 상태를 드러낸다 | — |
| `judgment` | 사람 또는 에이전트의 판단 (논제 상태, 대응 결정 등) | 근거 노드 `links` 권장 |
| `rule` | If-Then 규칙 | `rule` 하위 스키마 |
| `event` | 날짜가 있는 사건 (촉매, 공시, 실적발표) | `event` 하위 스키마 |
| `link` | 다른 맵의 노드 참조 | `links` 필수 |
| `computed` | 다른 노드로부터 계산 | `formula` 필수 |

`status` 전환: `unverified` → (출처 확보) → `confirmed`. `estimate` 노드는 `estimated`. `as_of`가 프로파일의 `stale_days`(§8, 기본 90일)를 넘으면 자동으로 `stale` → 보유 맵 🟡 후보.

### 2.3 하위 스키마

**Driver** — 핵심 드라이버 노드(`A1.x`, `B2.1.x`)에 붙는다.

```json
{
  "id": "A1.1", "map": "NEW", "parent": "A1", "node_type": "estimate",
  "label": "영업이익률", "value_type": "percent", "unit": "%",
  "question": "이 변수가 틀리면 논제가 무너지는가? (아니면 드라이버가 아니다)",
  "driver": {
    "current":      { "value": 11.2, "as_of": "2026-08-14", "source": { "name": "분기보고서", "url": "", "retrieved_at": "2026-10-19" } },
    "consensus":    { "value": 12.0, "as_of": "2026-10-10", "source": { "name": "컨센서스 집계", "url": "", "retrieved_at": "2026-10-19" } },
    "my_estimate":  { "value": 13.5, "as_of": "2026-10-19", "rationale": "원가 구조 개선 + 믹스 변화" },
    "check_indicator": "분기보고서 영업이익률, 월별 수출 단가",
    "next_check": "2026-11-14",
    "at_entry": null,
    "direction": null
  }
}
```

`at_entry`·`direction`은 보유 맵에서만 채워진다 (`direction` ∈ `강화` / `유지` / `약화`).

**Scenario** — `A3.1~3`, `B3.1~3`.

```json
{
  "id": "A3.2", "label": "Base", "node_type": "estimate", "value_type": "price",
  "scenario": {
    "assumptions": ["2027E 영업이익률 13%", "PER 밴드 중앙값 12배 적용"],
    "probability": 0.50,
    "target_price": 85000,
    "horizon_months": 12,
    "valuation_method": "PER 밴드 × 2027E EPS"
  }
}
```

세 시나리오의 `probability` 합은 1.0이어야 한다 (검증 규칙).

**Rule** — `A6.3~4`, `A4.4`, `B7.x`.

```json
{
  "id": "B7.1", "node_type": "rule", "label": "마진 미달 시 절반 축소",
  "rule": {
    "trigger_type": "fact",
    "condition": "driver[A1.1].current < 10.0",
    "action": "trim",
    "size": "50%",
    "escalate_to": "RED",
    "status": "armed",
    "fired_at": null
  }
}
```

`trigger_type` ∈ `fact` / `price` / `weight` / `time` / `event`. `action` ∈ `add` / `hold` / `trim` / `switch` / `exit` / `hedge` / `review`. `escalate_to` ∈ `YELLOW` / `RED` / `null`. `status` ∈ `armed` / `fired` / `disabled`.

**Event** — `A5.1.x`, `B2.4` 사실 로그.

```json
{
  "id": "B2.4.014", "node_type": "event", "label": "3Q 가이던스 하향",
  "event": {
    "date": "2026-10-15",
    "fact": "회사가 3Q 영업이익 가이던스를 10% 하향",
    "affects": ["A1.1"],
    "direction": "약화",
    "priced_in": "부분",
    "materiality": "H",
    "source": { "name": "공시", "url": "", "retrieved_at": "2026-10-15" }
  }
}
```

`direction` ∈ `강화` / `약화` / `중립`. `priced_in` ∈ `예` / `부분` / `아니오`. `materiality` ∈ `H` / `M` / `L`. `affects`가 비어 있고 `materiality`가 `L`이면 로그만 남기고 알림하지 않는다.

**DecisionLog** — `B8.4`, `C6.1`.

```json
{
  "date": "2026-10-19",
  "decision": "trim",
  "size": "비중 8% → 5%",
  "reason": "멀티플 확장만으로 상승, 목표 비중 초과",
  "price": 92000,
  "thesis_state": "유효",
  "valuation_state": "고평가",
  "weight_state": "과다",
  "matrix_default": "trim",
  "override": false,
  "bias_check": { "anchoring": false, "sunk_cost": false, "disposition": true, "endowment": false, "confirmation": false, "breakeven_plan": false, "note": "이익 종목이라 팔기 쉬운 쪽으로 기울었는지 확인함" }
}
```

---

## 3. 맵 A — 신규 투자 맵 (Thesis Map)

### 3.1 구조

```
A0 투자 논제 (한 문장)  ← 중심
│  "○○은 [핵심 드라이버] 덕분에 [기간] 내 [목표가]. 시장은 아직 [X]를 반영하지 않음"
│  투자유형 · 투자기간 · 확신도 · 현재가 · 작성일
│
├─ A1 핵심 드라이버 (3~5개)                 논제의 성패를 가르는 변수만
│    └─ A1.x : Driver 스키마 (현재값 → 컨센서스 → 내 추정 → 확인지표 → 다음 확인일)
│
├─ A2 컨센서스 vs 내 뷰 (Variant Perception)  기대수익의 원천
│    ├─ A2.1 현재 주가에 내재된 가정
│    ├─ A2.2 내가 다르게 보는 것 + 근거
│    └─ A2.3 시장이 틀린 이유 (시간축 / 정보 / 심리 / 구조)
│
├─ A3 밸류에이션 & 시나리오
│    ├─ A3.1 Bear · A3.2 Base · A3.3 Bull        Scenario 스키마
│    ├─ A3.4 기대수익률 (computed)
│    ├─ A3.5 손익비 (computed)
│    └─ A3.6 밸류 근거: 역사 밴드 위치 · 피어 비교 · EPS 리비전 방향
│
├─ A4 리스크 & 무효화 조건
│    ├─ A4.1~3 하락 리스크 Top 3 (확률 × 영향)
│    ├─ A4.4 Kill criteria (Rule, 사실 기준)        "이 사실이 확인되면 논제 폐기"
│    └─ A4.5 조기 경보 지표 (무엇을 · 어디서 · 얼마나 자주)
│
├─ A5 촉매 & 타임라인
│    ├─ A5.1 날짜 박힌 이벤트 (Event 스키마)
│    └─ A5.2 수급/기술 (진입 타이밍용 · 프로파일 가중치)
│
├─ A6 실행 계획
│    ├─ A6.1 목표 비중 (computed 제안 → judgment)
│    ├─ A6.2 진입 가격대 · 분할 횟수
│    ├─ A6.3 손절 규칙 (가격 + 사실)  · A6.4 익절/축소 규칙
│    └─ A6.5 판단: BUY / WATCH / PASS
│
├─ A7 모니터링 & 복기
│    ├─ A7.1 체크포인트 (A1.x next_check 집계, computed)
│    ├─ A7.2 업데이트 로그
│    └─ A7.3 사후 복기 (B 맵 종료 시 채움)
│
└─ A8 근거 자료 (기본 접힘)
     ├─ A8.1 기업: 비즈니스모델 · 경쟁우위 · 경영진/지배구조/주주환원 · 재무(성장·수익·현금·건전성)
     ├─ A8.2 산업 → link S.<섹터>
     └─ A8.3 매크로 → link M
```

### 3.2 노드 표

| id | label | node_type | value_type | 필수 | question (채움 기준) |
|---|---|---|---|---|---|
| A0 | 투자 논제 | judgment | text | ✓ | 이 종목이 왜, 언제까지, 얼마로 가는가를 한 문장으로. 시장이 안 보는 것은? |
| A0.1 | 투자유형 | judgment | enum `growth/value/turnaround/momentum/event` | ✓ | 어떤 유형의 베팅인가 (프로파일 선택) |
| A0.2 | 투자기간 | judgment | enum `3M/1Y/3Y+` | ✓ | 논제가 실현되는 데 필요한 시간 |
| A0.3 | 확신도 | judgment | enum `H/M/L` | ✓ | 근거의 질과 양 기준 |
| A0.4 | 현재가 | fact | price | ✓ | 작성 시점 종가 |
| A1.x | 핵심 드라이버 | estimate | Driver | ✓ (3~5개) | 이 변수가 틀리면 논제가 무너지는가 |
| A2.1 | 내재 가정 | estimate | text | ✓ | 현재 주가가 성립하려면 시장은 무엇을 믿고 있어야 하나 |
| A2.2 | 내 뷰 | judgment | text | ✓ | 어디서 다르게 보고, 근거는 무엇인가 |
| A2.3 | 시장이 틀린 이유 | judgment | enum `시간축/정보/심리/구조` + text | ✓ | 내가 맞다면 왜 시장은 아직 모르는가 |
| A3.1~3 | Bear/Base/Bull | estimate | Scenario | ✓ | 각 시나리오의 가정·확률·목표가 |
| A3.4 | 기대수익률 | computed | percent | — | §3.4 |
| A3.5 | 손익비 | computed | number | — | §3.4 |
| A3.6.1 | 역사 밴드 위치 | fact | percent (백분위) | ✓ | 현재 멀티플이 자기 역사 분포의 몇 %에 있나 |
| A3.6.2 | 피어 비교 | fact | text/number | | 피어 대비 프리미엄/디스카운트와 그 정당성 |
| A3.6.3 | EPS 리비전 방향 | fact | enum `up/flat/down` | ✓ | 최근 3개월 컨센서스 EPS 방향 |
| A4.1~3 | 하락 리스크 | estimate | `{probability, impact_pct, text}` | ✓ (≥2) | 무엇이, 어느 확률로, 얼마나 깎는가 |
| A4.4 | Kill criteria | rule | Rule (trigger_type=fact) | ✓ (≥1) | 어떤 사실이 확인되면 손실 크기와 무관하게 논제를 버리나 |
| A4.5 | 조기 경보 | fact | text + next_check | | 그 사실을 미리 알려주는 지표는 |
| A5.1.x | 촉매 이벤트 | event | Event | | 날짜가 박힌 사건만 |
| A5.2.1 | 추세 | fact | enum `상승/횡보/하락` | | |
| A5.2.2 | 거래량 | fact | number | | 20일 평균 대비 |
| A5.2.3 | 외국인/기관 순매수 | fact | number | | 20일 누적 |
| A5.2.4 | 공매도/신용잔고 | fact | percent | | 유통주식 대비 |
| A6.1 | 목표 비중 | computed→judgment | percent | ✓ | §3.4 제안식 → 확정 |
| A6.2 | 진입 계획 | judgment | `{price_range, tranches}` | ✓ | 어느 가격대에서 몇 번에 나눠 사나 |
| A6.3 | 손절 규칙 | rule | Rule (price + fact) | ✓ | 가격 기준 하나, 사실 기준 하나 |
| A6.4 | 익절/축소 규칙 | rule | Rule | | |
| A6.5 | 판단 | judgment | enum `BUY/WATCH/PASS` | ✓ | |
| A7.1 | 체크포인트 | computed | date[] | — | A1.x.next_check ∪ A5.1.x.date |
| A7.2 | 업데이트 로그 | event[] | | | |
| A7.3 | 사후 복기 | judgment | text | | 매도 완료 후 |
| A8.* | 근거 자료 | fact/link | | | 논제를 뒷받침하는 것만 |

### 3.3 완료 조건 (판단 가능 상태)

A0 논제가 비어 있지 않고, A1 드라이버가 3개 이상이며 각각 `current`와 `my_estimate`가 있고, A3 세 시나리오의 확률 합이 1.0이고, A4.4 Kill criteria가 1개 이상이며, A6.5 판단이 있을 때만 맵을 "완료"로 표시한다. 미완료 맵의 A6.5는 `WATCH`로 강제한다.

### 3.4 계산식

```
A3.4 기대수익률  ER = Σ_i  p_i × (TP_i / P0 − 1)            i ∈ {Bear, Base, Bull}, Σ p_i = 1
A3.5 손익비      RR = ((TP_base − P0) / P0) / ((P0 − TP_bear) / P0)
                 (설정 rr_upside = "base" | "bull" ; 기본 base — 보수적)
A6.1 목표 비중   w = clamp( w_base × f_conf × f_rr , 0 , w_max )
                 w_base = 5% (설정) ; f_conf = {H:1.5, M:1.0, L:0.5}
                 f_rr   = clamp(RR / 2, 0.5, 1.5)
                 포트폴리오 상관 패널티는 C4에서 적용 (A에서는 단독 제안값)
A7.1 체크포인트  = sort( {A1.x.driver.next_check} ∪ {A5.1.x.event.date} )
```

RR이 프로파일의 `rr_min`(§8) 미만이면 A6.5는 `BUY`를 허용하지 않는다 (경고 후 `WATCH`).

---

## 4. 맵 B — 보유 종목 맵 (Holding Map)

### 4.1 구조

```
B0 [보유: ○○]  상태등 🟢 유지 / 🟡 재검토 / 🔴 즉시대응   ← 중심 (computed)
│  핵심 질문: "오늘 현금으로 이 가격·이 비중에 새로 사겠는가?"  → 예/아니오 + (아니오면) 보유 이유
│  현재 비중 · 목표 비중 · 보유기간 · 현재가 · [평가손익: 메타데이터, 판단 입력 금지]
│
├─ B1 매수 당시 논제 복원 (A 맵 스냅숏 또는 재구성)
│    ├─ B1.1 원래 논제 (A0)  · B1.2 당시 드라이버 값 (A1 → at_entry)
│    ├─ B1.3 당시 목표가/기간 (A3)  · B1.4 당시 무효화 조건 (A4.4)
│    ├─ B1.5 복원 품질: 기록 / 재구성 / 불가   → "불가"는 '논제 없는 보유' 태그
│    └─ B1.6 섹터·매크로 링크 (A8.2, A8.3 복사)  → C2 노출 집계의 키
│
├─ B2 그 이후 무엇이 바뀌었나
│    ├─ B2.1 드라이버별 at_entry → now → direction (강화/유지/약화)
│    ├─ B2.2 주가 변화 분해 (computed): ΔP = (1+ΔEPS)(1+ΔPE) − 1
│    ├─ B2.3 논제 상태 판정: 강화 / 유효 / 약화 / 무효 / 변질
│    └─ B2.4 사실 로그 (Event[]): 날짜 · 사실 · 영향 드라이버 · 방향 · 반영 여부
│
├─ B3 앞으로의 기대수익 (과거 손익과 무관)
│    ├─ B3.1~3 시나리오 재추정 · B3.4 기대수익률 · B3.5 손익비
│    ├─ B3.6 밸류 상태 (computed): 저평가 / 적정 / 고평가  (밴드 백분위 기준)
│    └─ B3.7 대안 비교: 현금 · 관심종목 1순위(A 맵 ref) · 섹터 대체 종목(S6 ref)
│         └─ B3.8 상대 우위 (computed) = ER_this − max(ER_alt)
│
├─ B4 포트폴리오 맥락 (C에서 내려받음)
│    ├─ B4.1 비중 이탈 (computed) → 비중 상태: 과소 / 적정 / 과다
│    ├─ B4.2 평가익이 키운 비중인가 (비중 증가분 중 손익 기여)
│    ├─ B4.3 섹터/팩터 중복 · 상위 보유종목과 상관
│    ├─ B4.4 세금: 매도 시 실현손익 · 적용 세율 · 손익통산 페어 · 대주주 요건
│    └─ B4.5 유동성: 보유수량 / 일평균거래량 → 분할 매도 일수
│
├─ B5 편향 체크 (보유자 전용, bool 6개)
│    ├─ B5.1 매입가/본전에 앵커링?    · B5.2 매몰비용?
│    ├─ B5.3 처분효과 (이익 종목만 팔고 싶은가)?  · B5.4 보유효과 (안 갖고 있어도 이 뷰?)
│    └─ B5.5 확증편향 (좋은 뉴스만 찾는가)?  · B5.6 "본전 오면 판다"는 계획이 아님
│
├─ B6 대응 결정 (judgment) = f(B2.3 논제 상태, B3.6 밸류 상태, B4.1 비중 상태)
│    └─ add / hold / trim / switch / exit / hedge   — 매트릭스 기본값 + 오버라이드 사유
│
├─ B7 사전 약속 (Rule[])
│    ├─ 사실 기준 · 가격 기준 · 비중 기준 · 시간 기준
│    └─ 실행 파라미터: 분할 횟수 · 주문 방식 · 세금 타이밍
│
└─ B8 모니터링 & 결정 로그
     ├─ B8.1 정기 주기 (프로파일) · B8.2 이벤트 트리거 · B8.3 임계값
     ├─ B8.4 결정 로그 (DecisionLog[])
     └─ B8.5 사후 복기 (exit 완료 시): 논제 적중 여부 · 틀린 가정 · 발생한 편향
```

### 4.2 노드 표 (A와 다른 노드만)

| id | label | node_type | value_type | 필수 | question / 규칙 |
|---|---|---|---|---|---|
| B0 | 상태등 | computed | enum `GREEN/YELLOW/RED` | — | §4.3 |
| B0.1 | 핵심 질문 답 | judgment | bool | ✓ | 오늘 현금으로 이 가격·비중에 새로 사겠는가 |
| B0.2 | 보유 이유 (아니오일 때) | judgment | enum `세금/유동성/촉매 임박/없음` | B0.1=false | "없음"이면 B2.3 재판정을 요구한다. 새로 사지 않을 종목을 이유 없이 들고 있다면 대개 무효/변질에 해당 → exit |
| B0.3 | 현재 비중 | fact | percent | ✓ | C1.1에서 내려받음 |
| B0.4 | 목표 비중 | judgment | percent | ✓ | A6.1 스냅숏, 리밸런싱 시 갱신 |
| B0.5 | 평가손익 | fact | percent | | **meta_only: true** — 어떤 computed/judgment의 입력으로도 참조 금지 (검증 규칙) |
| B1.1~4 | 스냅숏 | fact | ref | ✓ | 매수 체결 시 A0/A1/A3/A4.4 복사 (불변) |
| B1.5 | 복원 품질 | judgment | enum `기록/재구성/불가` | ✓ | 기록=A 맵 존재, 재구성=위자드로 사후 작성, 불가=논제 없음 |
| B1.6 | 섹터·매크로 링크 | link | ref[] | ✓ | A8.2(S 맵), A8.3(M 맵) 링크 복사. C2 집계와 S3 사이클 변경 전파의 키 |
| B2.1.x | 드라이버 | estimate | Driver(+at_entry, direction) | ✓ | at_entry 대비 now가 내 추정에 가까워졌나 멀어졌나 |
| B2.2 | 주가 분해 | computed | `{dP, dEPS, dPE}` | — | §4.4 |
| B2.3 | 논제 상태 | judgment | enum `강화/유효/약화/무효/변질` | ✓ | 변질 = 처음과 다른 이유로 들고 있음 (예: 성장 → 배당) |
| B2.4 | 사실 로그 | event[] | Event | | 수집기가 자동 적재 |
| B3.6 | 밸류 상태 | computed | enum `저평가/적정/고평가` | — | 밴드 백분위 <30 저평가, 30~70 적정, >70 고평가 (설정) |
| B3.7.x | 대안 | link | ref + ER | | 현금 ER = MMF 수익률 |
| B3.8 | 상대 우위 | computed | percent | — | ER_this − max(ER_alt) |
| B4.1 | 비중 상태 | computed | enum `과소/적정/과다` | — | \|w − w_target\| > min(dev_abs, dev_rel × w_target) → 과소/과다. 둘 중 먼저 걸리는 쪽 (기본 dev_abs 3%p, dev_rel 30%) |
| B4.2 | 손익 기여 비중 | computed | percent | — | (w_now − w_at_entry_cost_basis) 중 가격 변동 기여분 |
| B4.4 | 세금 | computed | `{realized_gain, tax_rate, tax_amount, harvest_pair}` | | 시장·계좌별 세율 테이블 참조 |
| B4.5 | 유동성 | computed | number (일) | | 보유수량 / (ADV × 참여율 10%) |
| B5.1~6 | 편향 체크 | judgment | bool | ✓ (🟡🔴 리뷰 시) | 하나라도 true면 DecisionLog.bias_check에 기록 |
| B6 | 대응 결정 | judgment | enum `add/hold/trim/switch/exit/hedge` | ✓ | §4.5 매트릭스 기본값. 오버라이드 시 `override: true` + 사유 필수 |
| B7.x | 사전 약속 | rule | Rule | ✓ (≥1) | 사실 기준 1개 이상 필수 |
| B8.4 | 결정 로그 | DecisionLog[] | | | 모든 B6 변경 시 자동 1건 |
| B8.5 | 사후 복기 | judgment | `{thesis_correct, wrong_assumption, biases, lesson}` | exit 시 ✓ | A7.3으로 복사 |

### 4.3 상태등 규칙

```
RED    if  any(B7.x.rule.status == "fired" and escalate_to == "RED")
       or  any(B1.4 kill_criteria 충족)
       or  B2.3 ∈ {무효, 변질}

YELLOW if  B2.3 == 약화
       or  any(B2.1.x.driver.direction == 약화)
       or  B4.1 ∈ {과소, 과다}
       or  (B3.6 == 고평가 and B4.1 == 과다)
       or  drawdown_from_peak > profile.dd_threshold
       or  any(node.status == stale)
       or  B1.5 == 불가                       ('논제 없는 보유')
       or  any(B5.x == true)
       or  any(B7.x.rule.status == "fired" and escalate_to == "YELLOW")
       or  M6 레짐 변경 이후 미검토            (설정으로 끌 수 있음)
       or  today > min(B2.1.x.next_check)      (확인일 경과)

GREEN  otherwise
```

🟢 종목의 정기 리뷰는 B2.1 드라이버 값 갱신과 B2.4 로그 확인만으로 끝낸다 (목표 5분). 🟡은 B2.3·B3·B5 갱신 후 B6 결정. 🔴는 즉시 알림, B6 기본값은 매트릭스상 `exit` 또는 `trim`.

### 4.4 계산식

```
B2.2 주가 변화 분해 (12M forward EPS 기준, 배당 제외)
     r_total = P_now / P_entry − 1
     ΔEPS    = EPS_fwd_now / EPS_fwd_entry − 1
     ΔPE     = (P_now / EPS_fwd_now) / (P_entry / EPS_fwd_entry) − 1
     검증    : (1+ΔEPS)(1+ΔPE) − 1 ≈ r_total
     해석    : |ΔPE| > |ΔEPS| and r_total > 0  → 기대감 주도 상승 (고평가 경계)
               ΔEPS > 0 and r_total < 0         → 멀티플 압축 (논제 유효 시 add 후보)
               ΔEPS < 0 and B2.3 == 유효        → 판정 재검토 요구 (모순)
     적자 기업: EPS 대신 매출(PSR) 또는 EBITDA(EV/EBITDA)로 대체, 설정 decomp_metric

B3.4 / B3.5  = A3.4 / A3.5 와 동일 (P0 = 현재가, 시나리오는 재추정값)

B3.8 상대 우위  edge = ER_this − max(ER_cash, ER_watchlist_top, ER_sector_substitute)

B4.1 비중 상태  dev = w_now − w_target ;  band = min(dev_abs, dev_rel × w_target)
               과다 if dev > band ; 과소 if dev < −band ; 아니면 적정
               (예: w_target 5% → band = min(3%p, 1.5%p) = 1.5%p → 6.5% 초과 시 과다)

drawdown_from_peak = 1 − P_now / max(P since entry)
```

### 4.5 대응 결정 매트릭스

입력은 세 가지뿐이다: 논제 상태(B2.3), 밸류 상태(B3.6), 비중 상태(B4.1). 매입가·평가손익은 입력이 아니다.

| 논제 상태 | 밸류 | 비중 | 기본 대응 | 비고 |
|---|---|---|---|---|
| 강화 / 유효 | 저평가 | 과소 / 적정 | **add** | A6.1 재계산 비중까지 |
| 강화 / 유효 | 저평가 | 과다 | hold | 비중 상한 유지, 추가 금지 |
| 강화 / 유효 | 적정 | 과소 | add (소폭) 또는 hold | 프로파일 기본값 |
| 강화 / 유효 | 적정 | 적정 | **hold** | `next_check` 반드시 지정 |
| 강화 / 유효 | 적정 | 과다 | trim | 목표 비중까지 |
| 강화 / 유효 | 고평가 | 과소 / 적정 | hold 또는 trim | 모멘텀 프로파일은 hold (추세 유지 시) |
| 강화 / 유효 | 고평가 | 과다 | **trim** | |
| 유효 | any | any | **switch** | B3.8 edge > switch_threshold(기본 +10%p) 이고 세금·거래비용 < edge 일 때만 |
| 약화 | any | any | trim 또는 hold + 조건 | B7에 확인 규칙 추가, `next_check` 단축 |
| 무효 / 변질 | any | any | **exit** | 손실 크기 무관. 세금·유동성 사유로 분할 매도만 허용 |
| 논제 없는 보유 (B1.5=불가) | any | any | 논제 복원 → 재판정 | 복원 실패 시 exit 후보 |
| any | any | any (이벤트 임박) | hedge | 선택. 이벤트 전 노출 조절 |

매트릭스는 모든 종목에 동일하게 적용되는 공통 규칙 테이블이다. 종목별로 결과를 바꾸려면 오버라이드 사유를 로그에 남겨야 한다. 오버라이드 빈도는 C5에서 추적한다.

---

## 5. 맵 C — 포트폴리오 롤업

C 맵은 직접 채우는 노드가 거의 없다. B 맵들과 증권사 포지션 데이터에서 계산된다.

### 5.1 구조

```
C0 [포트폴리오]  총자산 · 현금 비중 · 종목 수 · 상태등 분포 (🔴n 🟡n 🟢n) · 기준일
│
├─ C1 구성 & 집중도
│    ├─ C1.1 holdings[] : {B맵 ref, w_now, w_target, status, thesis_state, decision, ER}
│    ├─ C1.2 상위 3종목 비중 합 · C1.3 HHI = Σ w_i²  · C1.4 최대 단일 비중 vs 상한
│
├─ C2 노출
│    ├─ C2.1 섹터별 비중 (S 링크 집계) · C2.2 팩터 (tag 집계: 성장/가치/배당/금리민감/환율민감)
│    └─ C2.3 통화 노출 · C2.4 매크로 민감도 요약 (M 링크)
│
├─ C3 대응 집계
│    ├─ C3.1 각 B6 결정 × 금액 → 순매수 / 순매도 합계 · C3.2 실행 후 현금 비중
│    ├─ C3.3 실행 순서 (세금·유동성 고려) · C3.4 충돌 검사 (현금 부족, 동일 섹터 add+exit)
│
├─ C4 리밸런싱 & 세금
│    ├─ C4.1 drift (w_now − w_target 벡터) · C4.2 리밸런싱 밴드 · C4.3 상관 패널티 적용 후 목표 비중
│    ├─ C4.4 손익통산 페어링 (실현이익 후보 ↔ 실현손실 후보) · C4.5 대주주 요건·연말 기준일 (KR) · C4.6 거래비용 추정
│
├─ C5 품질 지표
│    ├─ C5.1 논제 없는 보유 수 / 비중 · C5.2 stale 노드 비율 · C5.3 규칙 커버리지 (B7 ≥1인 종목 비율)
│    ├─ C5.4 포트 기대수익률 Σ w_i × ER_i (참고용, 상관 무시) · C5.5 평균 손익비 · C5.6 매트릭스 오버라이드 빈도
│
└─ C6 복기
     ├─ C6.1 결정 로그 통합 · C6.2 결정 유형별 적중률 (6/12개월 후) · C6.3 편향 발생 빈도
     └─ C6.4 월간 복기 노트 (judgment)
```

### 5.2 집계 규칙

| 노드 | 계산 |
|---|---|
| C0 현금 비중 | 현금(예수금+MMF) / 총자산. 현금은 `ER = MMF 수익률`인 보유 종목으로 취급 (B3.7 대안의 기준값) |
| C1.1 | 모든 open 상태 B 맵에서 `{B0, B2.3, B6, B3.4}` 수집. w_now = 수량 × 현재가 / 총자산 |
| C1.3 HHI | Σ w_i² (현금 제외). 0.10 초과 시 집중 경고 (설정) |
| C2.1 | 각 B 맵의 B1.6 섹터 링크(S 맵)로 섹터 매핑 후 w 합산 |
| C2.2 | 각 B 맵 tags 중 `팩터:*` 집계 |
| C3.1 | B6 ∈ {add, trim, exit, switch} 인 종목의 목표 변화량 × 현재가 합산. switch는 exit + add 두 건으로 분해 |
| C3.4 충돌 | (현금 + 순매도 − 순매수) < 0 → 실행 불가 경고 ; 같은 S 맵 안에서 add와 exit 동시 발생 → 교체 후보로 묶어 표시 |
| C4.3 | w_target_adj = A6.1 제안 × (1 − ρ_penalty), ρ_penalty = clamp(avg corr with top3 − 0.5, 0, 0.5) (설정) |
| C4.4 | 실현이익 후보(exit/trim, 평가익 > 0) 와 실현손실 후보(exit, 평가손 < 0)를 과세 구간 내에서 페어링. 해외주식·대주주 양도세 대상에만 적용 |
| C5.1 | B1.5 == 불가 인 B 맵의 수와 w 합 |
| C5.4 | Σ w_i × B3.4_i + w_cash × ER_cash |
| C6.2 | DecisionLog를 decision별로 묶어 6/12개월 후 가격·논제 결과와 대조. add 후 ER 실현률, exit 후 회피 손실 등 |

평가손익은 C4.4 세금 계산과 C6 복기에서만 사용된다 (B0.5 `meta_only` 제약은 C에서도 동일).

---

## 6. 공통 참조 맵 M, S

### 6.1 M 매크로 맵 (단일, 월 1회 또는 FOMC/금통위 직후)

```
M0 매크로 레짐 (judgment enum: 확장/둔화/침체/회복 × 리스크온/오프)
├─ M1 금리 : 기준금리(KR/US) · 10Y · 방향 (enum up/flat/down)
├─ M2 환율 : USDKRW · 방향 · 수출주/내수주 함의
├─ M3 유동성/경기 : M2 · PMI · 경기선행지수 · 신용스프레드
├─ M4 정책/지정학 : Event[] (날짜 박힌 것만)
├─ M5 시장심리 : VIX · 신용잔고 · 투자자예탁금 · 외국인 누적 순매수
└─ M6 레짐 변경 이력 : [{date, from, to, note}]  → 변경 시 모든 B 맵 🟡 후보 (설정)
```

종목 맵은 M을 복사하지 않고 `A8.3 → links: ["M0"]`로 참조한다. 투자 유형이 `momentum`·`event`가 아니면 M은 논제의 입력이 아니라 배경이다.

### 6.2 S 섹터 맵 (섹터별 1개, 월 1회 또는 주요 이벤트)

```
S.<시장>-<섹터코드>   예: S.KR-SEMI, S.US-AUTO-EV, S.KR-ROBOT
├─ S1 TAM / 성장률 (fact + source)
├─ S2 경쟁구도 : 상위 플레이어 · 점유율 · 진입장벽
├─ S3 사이클 위치 (judgment enum: 초기/확장/피크/둔화/바닥)
├─ S4 섹터 밸류 밴드 : PER/PBR 역사 분포 (A3.6.1·B3.6의 비교 기준)
├─ S5 섹터 촉매/리스크 : Event[]
└─ S6 섹터 내 종목 리스트 : [{A 또는 B 맵 ref, ER, 상태}]  → B3.7 교체(switch) 후보 소스
```

S3 사이클 위치가 바뀌면 해당 섹터에 링크된 모든 B 맵의 B2.1 드라이버 재확인을 요청한다 (`next_check` 를 오늘로 당김).

---

## 7. 라이프사이클 & 상태 기계

```
[관심 등록] ──▶ A 작성 중 (WATCH)
                 │ 완료 조건 충족 (§3.3)
                 ▼
            A6.5 판단 ──── PASS ──▶ 보관 (관심 해제, 복기용 보존)
                 │ WATCH ──▶ A7.1 체크포인트 대기 → 재평가 → A6.5
                 │ BUY
                 ▼
            매수 체결 ──▶ snapshot(A0, A1.current→at_entry, A3, A4.4, A6.1, A8.2·A8.3 링크)
                 ▼
            B 생성 (open, B1.5 = 기록, reconstructed = false)
                 │
    ┌────────────┴─────────────────────────────────────────────┐
    │ 운영 루프                                                │
    │  데이터 수집 (가격·공시·실적·뉴스·수급)                 │
    │   → 이벤트 분류 {affects, direction, priced_in, materiality}
    │   → B2.1 드라이버 갱신 · B2.4 로그 적재                   │
    │   → B7 규칙 평가 (fired?) · stale 검사 · 임계 검사        │
    │   → B0 상태등 산출                                        │
    │      🟢 : next_check까지 대기 (정기 리뷰 5분)              │
    │      🟡 : 리뷰 요청 → B2.3 · B3 · B5 갱신 → B6 결정 → B8.4 로그 → 실행 → C 재계산
    │      🔴 : 즉시 알림 → B6 (기본 exit/trim) → 로그 → 실행 → C 재계산
    └────────────┬─────────────────────────────────────────────┘
                 │ B6 = exit 완료
                 ▼
            B 종료 (closed) ──▶ B8.5 사후 복기 ──▶ A7.3 복사 ──▶ C6 집계

[맵 없이 이미 보유] ──▶ 논제 복원 위자드 ──▶ B1 (B1.5 = 재구성 | 불가, reconstructed = true)
                                            └─▶ B2부터 정상 운영 (불가면 상태등 최소 🟡)
```

**이벤트 분류기 출력 계약.** 수집기는 모든 뉴스·공시를 `Event` 스키마로 변환한다. `affects`는 해당 B 맵의 드라이버 id 목록(없으면 빈 배열), `direction`은 영향받는 드라이버 기준, `priced_in`은 공시 후 주가 반응과 컨센서스 변화로 판정, `materiality`는 논제 영향 크기. `affects = []` 이고 `materiality = L` 이면 로그만 남기고 알림하지 않는다. 같은 사실이 여러 종목에 영향을 주면 종목별로 별도 Event를 만든다.

**스냅숏 불변성.** B1.1~B1.4는 생성 후 수정하지 않는다. 논제가 바뀌면 B2.3을 `변질`로 판정하고 새 논제는 B2.3의 메모에 적는다. 이것이 "처음 산 이유"와 "지금 들고 있는 이유"의 괴리를 드러내는 장치다.

---

## 8. 투자 유형 프로파일

`A0.1 투자유형`이 정해지면 아래 기본값이 노드 `weight`, 리뷰 주기, 임계값을 덮어쓴다. 모두 설정 가능하다.

| 항목 | growth | value | turnaround | momentum | event |
|---|---|---|---|---|---|
| 핵심 드라이버 예시 | 매출성장률 · TAM 침투율 · 마진 레버리지 | FCF 수익률 · 자산가치 할인율 · 주주환원 | 마진 회복 · 부채 감축 · 가동률 | 이익 리비전 · 상대강도 · 수급 | 이벤트 일정 · 성공 확률 · 결과별 가치 |
| A5.2 수급/기술 weight | 0.2 | 0.1 | 0.2 | 0.6 | 0.3 |
| A8 근거 자료 weight | 0.6 | 0.8 | 0.6 | 0.2 | 0.4 |
| 정기 리뷰 주기 | 분기 | 반기 | 분기 | 주간 | 이벤트 전후 |
| `stale_days` | 90 | 180 | 90 | 14 | 이벤트일까지 |
| `dd_threshold` (🟡) | 25% | 20% | 30% | 10% | 15% |
| 고평가 시 기본 대응 | trim | trim | hold | hold (추세 유지 시) | n/a |
| `rr_min` (BUY 허용 최소 손익비) | 2.0 | 1.5 | 2.5 | 1.5 | 2.0 |
| `rr_upside` 기준 | base | base | bull | base | base |
| 수급/기술 노드의 역할 | 진입 타이밍 | 거의 무시 | 진입 타이밍 | 논제 본체 | 이벤트 전 포지셔닝 |

---

## 9. 예시 인스턴스 (축약, 가상 종목)

아래는 가상의 종목 "가온로보틱스"(예시용, 실제 기업 아님)에 대한 A → B → C 데이터 흐름의 최소 예시다. 숫자는 모두 임의값이다.

```json
{
  "A": {
    "A0": { "value": "가온로보틱스는 휴머노이드 액추에이터 양산 전환으로 12개월 내 OPM 8%→13%, 주가 85,000원. 시장은 아직 2027년 수주잔고 전환율을 반영하지 않음",
            "A0.1": "growth", "A0.2": "1Y", "A0.3": "M", "A0.4": 58000 },
    "A1": [
      { "id": "A1.1", "label": "영업이익률", "driver": { "current": {"value": 8.1}, "consensus": {"value": 10.0}, "my_estimate": {"value": 13.0}, "check_indicator": "분기보고서", "next_check": "2026-11-14" } },
      { "id": "A1.2", "label": "수주잔고(억원)", "driver": { "current": {"value": 4200}, "consensus": {"value": 4500}, "my_estimate": {"value": 6000}, "check_indicator": "IR 자료·공시", "next_check": "2026-11-14" } },
      { "id": "A1.3", "label": "양산 라인 가동률", "driver": { "current": {"value": 55}, "consensus": {"value": 65}, "my_estimate": {"value": 80}, "check_indicator": "탐방·공급사 코멘트", "next_check": "2026-12-01" } }
    ],
    "A3": [
      { "id": "A3.1", "label": "Bear", "scenario": { "probability": 0.25, "target_price": 48000, "assumptions": ["양산 지연 6개월", "OPM 7%"] } },
      { "id": "A3.2", "label": "Base", "scenario": { "probability": 0.50, "target_price": 85000, "assumptions": ["OPM 13%", "PER 18배"] } },
      { "id": "A3.3", "label": "Bull", "scenario": { "probability": 0.25, "target_price": 120000, "assumptions": ["해외 고객 추가", "PER 25배"] } },
      { "id": "A3.4", "formula": "Σ p_i × (TP_i/P0 − 1)", "value": 0.457 },
      { "id": "A3.5", "formula": "((85000−58000)/58000) / ((58000−48000)/58000)", "value": 2.70 }
    ],
    "A4.4": [ { "id": "A4.4.1", "rule": { "trigger_type": "fact", "condition": "driver[A1.2].current < 3500", "action": "exit", "escalate_to": "RED", "status": "armed" } } ],
    "A6": { "A6.1": { "suggested": 0.0675, "confirmed": 0.05, "formula": "5% × f_conf(M)=1.0 × f_rr=clamp(2.70/2,0.5,1.5)=1.35" },
            "A6.2": { "price_range": [55000, 60000], "tranches": 3 }, "A6.5": "BUY",
            "_note": "RR 2.70 ≥ rr_min(growth)=2.0 → BUY 허용. 제안 비중 6.75%를 신규 섹터라는 이유로 5%로 낮춰 확정 (judgment, 사유 기록)" }
  },

  "B": {
    "B0": { "status": "YELLOW", "B0.1": true, "B0.3": 0.072, "B0.4": 0.05, "B0.5": { "value": 0.31, "meta_only": true } },
    "B1": { "B1.1": "ref:A0 snapshot", "B1.2": { "A1.1": 8.1, "A1.2": 4200, "A1.3": 55 }, "B1.5": "기록" },
    "B2": {
      "B2.1": [ { "id": "B2.1.1", "driver": { "at_entry": 8.1, "current": {"value": 9.0}, "direction": "유지" } },
                { "id": "B2.1.2", "driver": { "at_entry": 4200, "current": {"value": 4400}, "direction": "유지" } } ],
      "B2.2": { "P_entry": 58000, "P_now": 76000, "r_total": 0.31, "dEPS": 0.06, "dPE": 0.236, "_interp": "기대감 주도 상승 — 고평가 경계" },
      "B2.3": "유효"
    },
    "B3": { "B3.4": 0.12, "B3.5": 0.6, "B3.6": "고평가", "B3.8": -0.03, "_note": "시나리오 재추정(Bear 62000 / Base 85000 / Bull 110000) 기준" },
    "B4": { "B4.1": "과다", "B4.2": 0.7, "B4.4": { "realized_gain": 0.31, "tax_amount": 0 }, "_note": "band = min(3%p, 30%×5%) = 1.5%p → 6.5% 초과 → 과다" },
    "B5": { "B5.3": true },
    "B6": { "value": "trim", "matrix_default": "trim", "override": false },
    "B7": [ { "id": "B7.1", "rule": { "trigger_type": "weight", "condition": "w_now > 0.065", "action": "trim", "size": "to_target", "escalate_to": "YELLOW", "status": "fired" } } ]
  },

  "C": {
    "C0": { "total_assets": 1000000000, "cash_weight": 0.08, "n_holdings": 9, "status_dist": { "RED": 0, "YELLOW": 2, "GREEN": 7 } },
    "C1": { "C1.2": 0.41, "C1.3": 0.085 },
    "C3": { "C3.1": { "net_buy": 0, "net_sell": 22000000 }, "C3.2": 0.10 },
    "C5": { "C5.1": { "count": 1, "weight": 0.04 }, "C5.3": 0.78, "C5.6": 0.11 }
  }
}
```

예시가 보여주는 흐름: A 단계에서 손익비 2.70이 growth 프로파일의 최소치 2.0을 넘어 `BUY`가 허용되었고, 계산기가 제안한 비중 6.75%를 사용자가 5%로 낮춰 확정했다(제안→판단의 분리). 매수 후 B 단계에서는 주가가 58,000원에서 76,000원으로 31% 올랐지만 분해 결과 EPS 기여는 6%, 멀티플 확장이 23.6%로 기대감 주도 상승이었고, 비중 7.2%가 밴드 상한 6.5%를 넘어 B7.1 규칙이 발동해 🟡가 켜졌다. 매트릭스(유효 × 고평가 × 과다)는 `trim`을 제안했고, 편향 체크에서 처분효과 항목이 true로 기록되었다. C 롤업은 이 trim을 2,200만 원 순매도로 집계해 실행 후 현금 비중 10%를 계산했다. 평가손익 31%는 세금 노드(B4.4)와 복기 외에 어디에도 입력되지 않았다.

---

## 10. 검증 규칙 (시스템이 강제할 것)

| 규칙 | 대상 | 위반 시 |
|---|---|---|
| 시나리오 확률 합 = 1.0 (±0.01) | A3, B3 | 저장 거부 |
| 드라이버 3~5개 | A1 | 완료 불가 |
| Kill criteria ≥ 1 (trigger_type = fact) | A4.4 | 완료 불가 |
| `fact` 노드는 `source` 필수 | 전체 | status = unverified 강제 |
| `B0.5 평가손익` 은 어떤 computed/judgment의 입력이 될 수 없음 | B, C | 저장 거부 (의존 그래프 검사) |
| B1.1~B1.4 스냅숏 수정 금지 | B | 저장 거부 |
| B6 가 매트릭스 기본값과 다르면 `override = true` + 사유 필수 | B | 저장 거부 |
| B7 에 `trigger_type = fact` 규칙 ≥ 1 | B | 상태등 최소 🟡 |
| `as_of` + `stale_days` < today → status = stale | 전체 | 자동 전환 |
| C3.4 현금 부족 | C | 실행 계획 경고 |

---

## 11. 구현 로드맵 (메모)

1. 이 문서의 스키마를 `node.schema.json`, `driver/scenario/rule/event/decision_log.schema.json`으로 정식화 (JSON Schema draft 2020-12).
2. 템플릿 스켈레톤 생성기: 투자유형 입력 → A 맵 빈 노드(질문 포함) 생성. 노드의 `question`이 LLM 채움 프롬프트가 된다.
3. 수집기 + 이벤트 분류기: 뉴스·공시 → `Event` 스키마. 분류 품질을 사람이 교정하는 피드백 루프 포함.
4. 규칙 엔진: B7 조건식 평가, stale 검사, 임계 검사 → 상태등 산출 → 알림.
5. 매트릭스 엔진: (B2.3, B3.6, B4.1) → B6 기본값. 오버라이드 로깅.
6. C 롤업 계산기: 포지션 데이터 + B 맵 → C 노드. 손익통산 페어링은 시장별 세율 테이블 분리.
7. 렌더러: 노드 트리 → markmap / mermaid mindmap / XMind 내보내기. 🟢 종목은 B0·B2.3·B6·next_check 네 노드만 펼친 요약 뷰.
8. 복기 통계: DecisionLog 누적 → C6.2 적중률, C6.3 편향 빈도. 이 데이터가 장기적 차별점.

---

## 12. 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1.0 | 2026-10-19 | 초안. A/B/C 맵 구조, 공통 노드 스키마와 5개 하위 스키마, 상태등 규칙, 대응 결정 매트릭스, 계산식, 투자 유형 프로파일, 라이프사이클, 검증 규칙, 예시 인스턴스 |
