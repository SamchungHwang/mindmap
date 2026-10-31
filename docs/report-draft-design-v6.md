# 밸류에이션 & 시나리오(A3) 재설계 — 설계 v6 (2026-10-24 검토 → 같은 날 S1~S5 구현 · 10-26 로컬 LLM 실측 §9 · 보강 과제 §10 · 검토 의견 반영 §11)

작성 2026-10-24 · **사용자 결정(2026-10-24)**: ① 기존 id 유지 + A3.8~A3.11 신설 ② 적용 PER 기본값 = 목표가 중앙값÷EPS "기본값" 표시 ③ 정상화 EPS = 2026E~2028E 평균(1곳 셀 n 표시) ④ SOTP 멀티플은 사용자 입력만 ⑤ 배당은 별도 표시(ER_price · ER_total) ⑥ KR-SEMI Bear 템플릿은 라벨만 ⑦ 주식 수 기본값 = 공시 보통주 · 구현 결과는 §8 · 앞선 문서: `report-draft-design-v5.md`(A1 원인 드라이버 · A3.7 결과 지표 · 민감도 체인, R1~R5 구현 완료) · 입력: 사용자의 A3 개선 제안 — ① 흐름 A1 → 사업부별 실적 예측 → 가치평가 모델(PER 기본 + SOTP 검증) → Bear/Base/Bull(일관된 드라이버 가정 묶음) → 확률가중 적정가치 · 기대수익률(배당 포함) · 하방 위험 · 안전마진 → A6 ② 데이터 표(Forward PER 2.59 vs 리포트 3.2) ③ 노드 구조 A3.1 평가 기준 ~ A3.8 모델 신뢰도 ④ 설계 원칙 표 ⑤ 3단계 권고와 핵심 질문("DRAM 가격 지속성 → EPS vs 컨센 → 상승여력").

## 0. 결론

**방향은 타당하고, 지금 코드 위에서 구현할 수 있다.** 필요한 재료의 대부분이 이미 있다 — `Scenario` 모델(A3.1~3), 기대수익률·손익비 식(`engine/calc.py`: ER = Σ p·(TP/P0−1), RR), A6.1 제안 비중(`lifecycle.evaluate_new_map`), A3.6 밸류 근거(역사 밴드·피어·리비전·목표가 분포), A3.7 결과 지표, 민감도 공통 꼬리(t · N · PER_target, `engine/sensitivity.py`), **사업부 표의 연간 2026E·2027E 매출·영업이익(증권사 3곳, §2)**, 추정치 표의 PER·PBR·ROE·EV/EBITDA·DPS·BPS 행, 헤더의 현재가·시가총액·발행주식수. 없는 것은 **가치평가 모델 노드, "가정 묶음" 시나리오와 그것을 한 번에 계산하는 통합 모델, 확률가중·배당·하방·안전마진 묶음, 모델 신뢰도, 역산**이다 — 전부 덧붙이는 수준이고 기본값이 있어 저장된 맵은 그대로 읽힌다.

식을 삼성전자 3건 숫자로 손으로 돌려 보니(§3) 설계에서 **먼저 정해야 할 세 가지**가 드러났다.

1. **Base 가 증권사 목표가를 그대로 재현한다.** EPS 기준 = 컨센(2027E 평균 83,320원), 적용 PER = 증권사 목표 PER 중앙값(530,000 / 83,320 = 6.36배) 이면 Base = 530,000원 — 목표가 중앙값과 같다(순환). 모델이 의미를 갖는 건 **A1 내 추정**이 사업부 실적으로 들어갈 때다. 그래서 EPS 기준은 `컨센 | 내 추정 | 정상화 | 직접 입력` 넷을 두고, 기본값 "컨센" 과 적용 PER 기본값 6.36 은 둘 다 **"기본값" 표시**로 드러낸다(v5 전가율 k 와 같은 규칙).
2. **Bear 가 현재가 위로 뜬다.** "DRAM ASP −10%·NAND −10%·PER 4.5" 같은 완만한 Bear 는 327,700원(현재가 215,500 대비 **+52%**) 이다. 현재가는 2027E 컨센 EPS 기준 PER 2.59배 — 시장은 2027E 이익이 지속된다고 보지 않는다. 즉 **하락 시나리오는 "사이클 꺾임 + 멀티플 압축" 을 표현할 수 있어야** 한다: 드라이버 Δ 를 크게(메모리 ASP 경로 −30%), 적용 PER 을 따로(3.5배), 필요하면 기준 연도도 바꿀 수 있어야 하고(2028E 하강 국면), Bear ≥ 현재가이면 ⚠ "하락 시나리오가 아니다" 를 띄운다(오류 아님 — 사용자의 판단이 그렇다면 그대로 둔다). 사용자가 적은 **핵심 질문**은 이 모델의 **역산**으로 바로 답이 나온다: 현재가 215,500 은 PER 6.36 에서 EPS 33,900(컨센 −59%) 을, PER 3.5 에서는 메모리 ASP 경로 컨센 −21% 를 내재한다(§3.4).
3. **주식 수와 순이익 기준이 증권사마다 다르다.** IBK 는 전체 주식(우선주 포함 6,648,650천주) 기준 EPS, 유진은 전체 순이익(비지배 포함), DS 는 지배순이익·보통주 기준이다. 평균을 섞어 유도한 N = 6.02B 주는 공시 보통주 5,846,279천주와 3%, IBK 와는 14% 다르다. 주식 수는 **사용자 입력(공시 보통주) 을 기본**으로 두고, 기준 혼합은 **모델 신뢰도(A3.11) 의 경고**로 남긴다.

확률 · 멀티플 · 내 추정 · 시나리오 가정은 계속 사람 몫이다. 엔진은 입력에 출처를 달아 계산만 하고, 입력이 빠지면 "미계산 — 필요 입력: …" 로 드러낸다. LLM 은 어디에도 숫자를 만들지 않는다(v5 원칙 5 그대로).

## 1. 제안 항목별 판정

| 제안 | 판정 | 지금 코드 | 바꿀 것 |
|---|---|---|---|
| §1 흐름: A1 → 사업부별 실적 → 가치평가 → Bear/Base/Bull → 확률가중 → A6 | **타당 · 가능** | A1 민감도는 드라이버마다 따로(ceteris paribus), 합산 금지. A3.1~3 은 v3 규칙: 리포트가 케이스를 제시하면 채우고 아니면 공란(증권사 자체 후보만 `meta.draft.scenario_candidates`). A3.4/A3.5 는 확률이 있을 때 `lifecycle` 이 계산 | `engine/scenario_model.py` — 사업부별 매출 × (가격 Δ)(물량 Δ) − 원가 → OP → NP → EPS → 가격을 **한 시나리오 안에서 동시에** 계산. 시나리오 = 드라이버 가정 묶음(§4.2) |
| §2 데이터 표 — "Forward PER 2.59 vs 리포트 3.2" 를 EPS 정의 차이로 본 것 | **표는 맞고 해석은 수정** | 추출기는 PER 행을 증권사별로 읽어 평균한다(2027E: 3.8 · 2.9 · 3.0 → 3.2) | 차이는 **가격 시점**이다: 리포트 PER 은 각 리포트 가격(262,000~268,500) 기준, 2.59 는 215,500 기준(§2.2). 엔진은 **PER 행을 평균하지 않고 가격 ÷ EPS 로 재계산**한다. EPS 기준(지배/전체 · 우선주) 혼합은 별도 경고 |
| §3 PER 기본 모델 + SOTP 검증 | **타당 · 가능** | 없음(민감도 꼬리에 PER_target = TP_median/EPS_E 만) | A3.9 「평가 모델」: PER 가격 = EPS(기준 선택) × 적용 PER(기준 선택). SOTP 가격 = Σ 사업부 OP×(1−t)×멀티플 + 순현금, ÷ N. **둘의 차이(%)** 를 표시 — SOTP 는 검증용(§3.2). 세그먼트 표 2027E OP 가 3곳 모두 있어 입력은 이미 있다. 추출 보강: 순차입금 · FCF · CAPEX 행, 발행주식수 헤더(§2.1) |
| §4 Bear/Base/Bull = 일관된 가정 묶음, 계산기 | **타당 · 가능** | `Scenario(name, assumptions[str], probability, target_price, horizon_months, valuation_method)` | `Scenario` 에 `driver_assumptions`(노드별 Δ) · `eps_basis`/`per` · `conditions` · `indicators` · `falsifier` · `price_source: computed\|user\|broker` 추가(전부 기본값). target_price 는 계산 결과로 채우고 사용자가 손으로 넣은 값과 구분. 계산기는 HTML 편집 패널 + V12 서버 `/preview` 재사용(§4.5) |
| §5 확률가중 적정주가 · 기대수익률(배당 포함) · 하방 수익률 · 안전마진 | **가능 · 식은 거의 그대로** | ER = Σ p·(TP/P0−1) (= 확률가중 가치/P0 − 1), RR, A6.1 제안 비중 | A3.10: V = Σ p·TP ; ER_price = V/P0 − 1 ; ER_total = ER_price + DPS_E/P0(옵션) ; 하방 = TP_bear/P0 − 1 ; 안전마진 = 1 − P0/V (Base 기준도 표시). 배당은 **가격에 넣지 않고 수익률에만** 더해 FCF·주주환원 드라이버와 이중계산을 피한다. 확률은 사람(「예시 확률」 기능 유지) |
| §6 드라이버 → 가치 체인, 단순 합산 금지 | **타당** | 민감도 각각 따로, "서로 더하지 말 것" 표시 | 통합 모델은 같은 사업부의 가격·물량이 함께 움직일 때 교차항 a·v 를 포함한다: ASP +10% & B/G +10% 는 합산 20% 가 아니라 21% — DRAM 매출 540.7조 기준 **5.4조 차이**. 민감도(A1, 1변수) 와 시나리오(A3, 다변수) 는 같은 식·같은 입력 스냅숏(`meta.draft.financials/segments`) 을 쓴다 |
| §7 노드 구조를 A3.1 평가 기준 ~ A3.8 모델 신뢰도 로 재번호 | **내용 타당 · 재번호는 비권고** | A3.x id 를 코드 94곳 · 테스트 54곳이 참조. B3.1~B3.5 가 A3 를 미러(스냅숏·롤업), `COMPARE_DRIVER` 에 A3.7.x, 골든 테스트, 저장된 맵 | **기존 id 유지 + 신설**: A3.8 평가 기준 · A3.9 평가 모델(PER/SOTP) · A3.10 확률가중 투자 매력도 · A3.11 모델 신뢰도·갱신 이력(§4.1). 화면 순서는 `render` 의 표시 순서로 조정(id 와 분리) |
| §8 설계 원칙 표 | **수용** | 원칙 1·4·5 는 v5 에 있음 | 원칙별 코드 위치를 §4.6 에 적는다 — "모델은 투명해야 한다(입력마다 출처)", "시나리오는 가정의 묶음이다", "확률은 사람이 정한다", "가치 ≠ 목표가(검증 수단)" |
| §9 3단계 권고 + 핵심 질문 | **수용** | — | 단계는 §5(S1~S5). 핵심 질문은 역산(§3.4)으로: 현재가가 내재하는 EPS · 메모리 ASP 경로를 **사실(계산)** 로 보여 주고, A2.1 「현재 주가에 내재된 가정」 의 **제안값**(kind=`implied`) 으로 올린다 — 수락은 사용자 |

## 2. 데이터 근거 — 삼성전자 3건(IBK 10/8 · 유진 10/12 · DS 10/7) 재추출

### 2.1 지금 추출되는 것 / 안 되는 것

| 입력 | 상태 | 값 (2027E 기준) |
|---|---|---|
| 목표주가 | 추출 | 460,000 · 560,000 · 530,000 → 중앙값 530,000 · 평균 516,667 |
| EPS | 추출 (3곳) | 70,006 · 88,945 · 91,008 → 평균 **83,320** · 중앙값 88,945 ; 2026E 평균 50,312 ; 2028E 93,085(IBK 1곳) |
| 영업이익 | 추출 (3곳) | 571.6 · 633.2 · 612.1조 → 평균 **605.6조** |
| 순이익 | 추출 (2곳 지배: IBK 465.4 · DS 537.8조 ; 유진은 `basis=전체`) | 평균 501.6조 → t = 1 − NP/OP = **0.172** |
| PER 행 | 추출 (3곳) | 3.8 · 2.9 · 3.0 → 평균 3.2 — **각 리포트 가격 기준**(§2.2) |
| PBR · ROE · EV/EBITDA | 추출 (3곳) | PBR 1.6 · ROE 55.1% · EV/EBITDA 1.7 |
| DPS | **부분** (IBK 만) | IBK 18,000 ; 유진 16,100 · DS 13,120(26E) 는 현금흐름표와 한 줄에 붙어 미파싱 → 행 분리 보강 필요. DS 2027E 셀 1,440 은 의심값 → 중앙값·이상치 규칙 |
| BPS | 부분 (IBK) | 152,875 |
| 순차입금(순현금) | **미추출** (행은 3곳 다 있음) | 2025A −100.6조(IBK·유진) · −93.5조(DS) ; 2027E 전망은 −496 · −636 · −747조로 **증권사 간 분산이 크다**(배당·자사주 가정) → SOTP 는 **최근 실적 연도** 를 기본값 |
| 발행주식수 | **미추출** (헤더에 있음) | IBK 6,648,650천주(우선주 포함) · 유진 5,846,279천주 · DS 5,846,279천주 |
| 시가총액 · 현재가 · 가격일 | 추출 | IBK 1,687조 @262,000(10/8) · 유진 1,532조 @262,000(10/12) · DS @268,500(10/7) |
| 사업부 매출·OP (연간) | 추출 (R4) | 2027E OP: 메모리 602.8조(2곳) · DRAM 458.5 · NAND 168.0 · HBM 82.9(1곳, ⊂ DRAM) · 파운드리 −0.57 · MX −4.3 · VD +0.1 · DX −4.1 · SDC 3.9 · Harman 2.2 ; 사업부 합 604.2조 vs 전사 605.6조(차이 1.4조 = 기타·연결조정) |
| FCF · CAPEX · 영업활동현금흐름 | **미추출** (행 있음) | IBK CAPEX · DS FCF 2027E 491.7조 등 |

### 2.2 "Forward PER 2.59 vs 3.2" — EPS 정의가 아니라 가격 시점

- 215,500 ÷ 83,320 = **2.59** (사용자 표의 값, 현재가 기준) ; 262,000 ÷ 83,320 = 3.14 ; 각 리포트 PER 행(3.8 · 2.9 · 3.0) 을 그 리포트 가격으로 역산하면 3.74 · 2.95 · 2.95 — 평균 3.2 는 **262,000~268,500 시점**의 수치다. 가격 비율 215,500/265,000 = 0.81 × 3.23 ≈ 2.6 → 차이는 가격 시점으로 설명된다.
- 따라서 엔진 규칙: **PER 는 저장하지 않고 가격 ÷ EPS 로 매번 계산**한다(가격은 A0.4 현재가 또는 사용자 지정일). 증권사 PER 행은 "그 증권사 EPS 와 가격의 조합" 참고치로만 표시한다.
- EPS 기준 혼합은 따로 있다: IBK 465.4조 ÷ 70,006원 = **6.65B 주**(우선주 포함) · DS 537.8조 ÷ 91,008 = 5.91B · 유진(전체 순이익 529.4조) ÷ 88,945 = 5.95B. 평균끼리 나눈 N = 6.02B. 공시 보통주 5.846B 와 3%, IBK 와 14% 차이 → **N 은 사용자 입력(기본 공시 보통주) 우선**, 유도값은 `derived` 로 표시하고 A3.11 에 "EPS 기준 혼합(지배/전체 · 우선주 포함)" 경고.

## 3. 식 검증용 예시 — 엔진 식을 손으로 돌린 것 (멀티플 · 확률 · 가정은 식 확인용 임의값, 제품에서는 전부 사용자 입력 또는 "기본값" 표시)

입력: P0 = 215,500 · 2027E OP 605.6조 · t = 0.172 · N = 6.02B(유도) · EPS 83,320 · 목표 PER 6.36(= 530,000/83,320) · DRAM 매출 540.7조 · NAND 221.6조 · HBM 107.1조 · 순현금(2025A) 100.6조.

### 3.1 PER 모델

| EPS 기준 | EPS | × 6.36 | 비고 |
|---|---|---|---|
| 컨센 2027E 평균 | 83,320 | 530,000 | = 목표가 중앙값 — **순환**. 내 추정이 들어가야 의미 |
| 정상화 ① 2026E~2028E 평균 | 75,572 | 480,600 | 2028E 는 IBK 1곳 — n 표시 |
| 정상화 ② 2026E~2027E 평균 | 66,816 | 425,000 | |
| 현재가 역산 | 33,884 | 215,500 | 컨센 −59% — 시장이 내재하는 2027E EPS(PER 6.36 가정) |

고점 EPS × 목표 PER 는 사이클 종목에서 **고점 × 고점** 이 될 수 있으므로 정상화 EPS 가격을 항상 나란히 보여 준다(선택은 사용자, §7 ③).

### 3.2 SOTP (검증용) — 2027E 사업부 OP × (1−t) × 멀티플 + 순현금

| 사업부 | OP(조) | NP(조) | 멀티플(임의) | 가치(조) |
|---|---|---|---|---|
| 메모리(DRAM+NAND) | 602.8 | 499.3 | 6.0 | 2,995.6 |
| 파운드리/LSI | −0.6 | −0.5 | 0 (적자 → 0, 또는 장부가) | 0 |
| DX(MX+VD) | −4.1 | −3.4 | 8.0 | −27.0 |
| SDC | 3.9 | 3.2 | 6.0 | 19.2 |
| Harman | 2.2 | 1.8 | 10.0 | 17.9 |
| 순현금 2025A | | | | 100.6 |
| **합** | | | | **3,106** → ÷ 6.02B = **516,000** · ÷ 5.846B = 531,400 |

PER 단일(530,000) 과 −3%/+0.3% — "검증 통과" 의 예. 규칙: HBM 은 DRAM 안에 있으므로 **세그먼트 목록에 동시에 넣지 않는다**(DRAM 과 HBM 중 하나), 메모리 = DRAM+NAND 와 DRAM·NAND 개별도 동시 금지(엔진이 포함 관계 사전으로 거절). 사업부 합과 전사 OP 차이(1.4조) 는 "기타" 행으로 드러낸다. 자회사(SDC·Harman) 는 지배지분 비율 입력이 있으면 곱한다(없으면 100% + 경고).

### 3.3 시나리오 = 가정 묶음 (통합 1차 모델)

| 시나리오 | 가정 묶음(임의) | OP(조) | EPS | 적용 PER | 적정주가 | vs 215,500 |
|---|---|---|---|---|---|---|
| Bear(완만) | DRAM ASP −10% · HBM B/G −10% · 파운드리 −2조 | 538.9 | 74,133 | 4.5 | 333,600 | **+55%** |
| Base | 컨센 그대로 | 605.6 | 83,320 | 6.36 | 529,900 | +146% |
| Bull | DRAM ASP +10% · HBM B/G +10% · 파운드리 BEP | 671.0 | 92,309 | 7.0 | 646,200 | +200% |
| 확률 30/45/25(예시) | 확률가중 가치 **500,100** · 기대수익률 +132% · 배당 포함 +139.5%(DPS 16,100 = 7.5%) · 하방 **+55%**(Bear 가 현재가 위) · 손익비 ∞ · 안전마진 59%(vs Base) |

**읽는 법**: 식이 틀린 게 아니라 Bear 정의가 약하다. 현재가는 "2027E 이익은 지속되지 않는다" 를 내재하므로, 하락 시나리오는 사이클 꺾임을 담아야 한다 — 예: DRAM·NAND ASP 경로 −30% · HBM B/G −10% · 파운드리 −2조 · PER 3.5 → OP 364조 · EPS 50,109 · **175,400원(−19%)**. 이때 손익비 = (529,900−215,500)/(215,500−175,400) = 7.8 로 계산된다. 엔진은 Bear ≥ P0 이면 ⚠ 를 띄우고(오류 아님), 사이클 섹터의 Bear 기본 템플릿(라벨만: "메모리 ASP 경로 · 멀티플 압축 · 기준 연도") 을 제안한다 — 숫자는 사용자.

### 3.4 역산 — 핵심 질문 "DRAM 가격 지속성 → EPS vs 컨센 → 상승여력" 에 대한 계산 답

| 적용 PER | 현재가가 내재하는 2027E EPS | vs 컨센 | Bear 가 현재가 아래로 가려면 메모리(DRAM+NAND) ASP 경로가 컨센 대비 |
|---|---|---|---|
| 6.36 (목표 PER 중앙값) | 33,900 | −59% | **−47%** 이하 (OP 246조) |
| 4.5 | 47,900 | −43% | −34% 이하 (OP 348조) |
| 3.5 | 61,600 | −26% | −21% 이하 (OP 448조) |
| 2.59 (현재 Fwd PER) | 83,320 | 0% | 컨센이 그대로면 현재가 = 적정 |

DRAM ASP 경로가 컨센 대비 −20% 면 OP 498조 · EPS 68,443(−18%) → PER 6.36 적용 435,300원(+102%) · PER 4.5 적용 308,000원(+43%). 이 표가 바로 "시장과 내가 어디서 다른가"(A2) 의 **숫자 버전**이다 — 그래서 A2.1 의 제안값으로 올린다(수락은 사용자, 기존 제안값 수락 기록 그대로).

## 4. 설계 초안 (데이터 계약 — 전부 기본값, 하위 호환)

### 4.1 노드 (기존 id 유지 + 신설)

| id | 라벨 | node_type | 내용 | 제안 §7 대응 |
|---|---|---|---|---|
| A3.1~A3.3 | Bear/Base/Bull | estimate | `Scenario` 확장(§4.2). target_price 는 `price_source=computed` 면 모델 결과 | 시나리오 |
| A3.4 / A3.5 | 기대수익률 / 손익비 | computed | 그대로(`lifecycle`) | |
| A3.6.x | 밸류 근거 | | 그대로 | 역사 밴드·피어 |
| A3.7.x | 재무 전망(결과 지표) | estimate | 그대로(v5) — 시나리오 모델의 Base 입력 | 사업부별 실적 예측 |
| **A3.8** | 평가 기준 | judgment | `{eps_basis, eps_year, per_basis, shares, shares_source, dividend_in_return}` — 모델이 읽는 선택값. 기본값 표시 | A3.1 평가 기준 |
| **A3.9** | 평가 모델 (PER · SOTP) | computed | `ValuationModel`(§4.3): PER 가격 · SOTP 가격 · 차이% · 정상화 EPS 가격 · 역산(내재 EPS·ASP 경로) · 입력 출처 표 | 가치평가 모델 |
| **A3.10** | 확률가중 투자 매력도 | computed | V = Σ p·TP · ER_price · ER_total(배당) · 하방 · 안전마진(vs Base · vs V) · ⚠ 목록 | 확률가중·기대수익률·하방·안전마진 |
| **A3.11** | 모델 신뢰도 · 갱신 이력 | computed | `ModelConfidence`(§4.4) | A3.8 모델 신뢰도 |

화면 순서(평가 기준 → 결과 지표 → 평가 모델 → 시나리오 → 매력도 → 신뢰도 → 밸류 근거) 는 `render` 의 A3 자식 정렬표로 정한다 — id 와 분리. B 맵은 B3.4/B3.5 미러 그대로, B3 에 `A3.10` 스냅숏 한 줄(선택).

### 4.2 `Scenario` 확장 (`models/sub.py`)

```python
class DriverAssumption(BaseModel):
    node_id: str                 # A1.1
    driver_id: str | None = None # dram_asp
    variable: str | None = None  # asp | volume | cost | op | per | eps_year …
    delta_pct: float | None = None   # 컨센(또는 내 추정) 대비 변화 %
    value: float | None = None       # 절대값 지정 (delta 와 택일)
    unit: str | None = None
    note: str = ""

class Scenario(BaseModel):
    name; assumptions: list[str]; probability; target_price; horizon_months; valuation_method   # 기존
    driver_assumptions: list[DriverAssumption] = []
    eps_basis: str | None = None      # consensus | my_estimate | normalized | user  (None → A3.8 기본)
    eps_year: str | None = None       # 2027E
    per: float | None = None          # 적용 PER (None → A3.8 기본)
    conditions: list[str] = []        # 이 시나리오가 성립하는 조건(문장)
    indicators: list[str] = []        # 확인 지표 (A1.x check_indicator 참조)
    falsifier: str | None = None      # 무엇이 보이면 이 시나리오를 버리나
    price_source: str = "user"        # computed | user | broker
    model: ScenarioResult | None = None   # 계산 결과 스냅숏 (op, np, eps, price, inputs, status, missing, computed_at)
```

`price_source="computed"` 인 시나리오는 저장 시 `model` 과 `target_price` 를 함께 갱신한다(편집 → `finalize_after_edit` 에서 `compute_all` 과 같은 자리). 사용자가 target_price 를 손으로 고치면 `price_source="user"` 로 바뀌고 모델 결과는 참고로만 남는다.

### 4.3 `engine/scenario_model.py` · `ValuationModel`

- 입력 스냅숏은 민감도와 같다: `meta.draft.financials`(연도·np/op/eps/revenue 평균·n·증권사별, tp_median) · `meta.draft.segments`(사업부 × 지표 × 열) · 사용자 입력(`sensitivity_inputs` 와 같은 꼴의 `valuation_inputs`: shares · net_cash · per · multiples · dps · dividend_in_return). 입력마다 `source: table | segment | claim | user | default | derived | missing`.
- 통합 1차 모델: 사업부 i 의 매출 R_i, 영업이익 O_i, 원가 C_i = R_i − O_i. 가정 묶음 적용 → R_i' = R_i·(1+a_i)(1+v_i), C_i' = C_i·(1+v_i)·(1+c_i)(물량 비례 원가 + 원가율 Δ), O_i' = R_i' − C_i' ; `direct_op` 드라이버(파운드리 손익) 는 O_i' = O_i + Δ. 전사 OP' = Σ O_i' + 기타(= 전사 OP − Σ O_i, 고정). NP' = OP'·(1−t), EPS' = NP'·1e8/N, 가격 = EPS'(기준 선택) × PER. 전가율 k 는 민감도와 같은 기본값 1.0.
- `ValuationModel`: `price_per`, `price_sotp`, `gap_pct`, `price_normalized`, `implied_eps(P0, per)`, `implied_asp_path(P0, per, segments)`, `inputs`, `status/missing/method/computed_at`. SOTP 포함 관계 사전: `{"메모리": ["DRAM","NAND"], "DRAM": ["HBM"], "DX": ["MX","VD"], "DS": ["메모리","파운드리"]}` — 겹치면 거절.
- 민감도(A1, 1변수) 와 시나리오(A3, 다변수) 가 **같은 함수** 를 쓰도록 `sensitivity.py` 의 사업부 식을 이 모듈로 옮기고 `compute_sensitivity` 는 가정 1개짜리 시나리오 호출로 바꾼다(결과는 동일해야 한다 — 회귀 테스트 `test_v5` 의 54.1조/7,438/47,316).

### 4.4 `ModelConfidence` (A3.11)

규칙 기반 H/M/L + 경고 목록: 입력 커버리지(필수 입력 중 `missing`·`default` 수), 증권사 수(EPS n, 세그먼트 n), EPS 기준 혼합(지배/전체 · 우선주 — `basis` 와 N 유도치 vs 입력치 차이 >5%), 사업부 합 vs 전사 OP 차이(>3%), 데이터 나이(리포트 날짜 − 오늘), PER·SOTP 차이(>15%), Bear ≥ P0, 확률 미입력, 2028E 같은 1곳 셀 사용. 갱신 이력은 `history` 와 `meta.draft.valuation_log`(계산 시각 · 입력 해시 · 결과) 로.

### 4.5 화면 · CLI · 편집

- HTML(V12 편집 모드): A3 에 「시나리오 계산기」 패널 — Bear/Base/Bull 열 × 드라이버 행(A1.x) 에 Δ% 입력, 적용 PER · EPS 기준 · 주식 수 · 순현금 입력, 「미리보기」 는 로컬 서버 `/preview` 를 호출해 **서버 식 하나로** 계산(클라이언트에 식을 복제하지 않는다 — 단일 진실). 결과 표: OP · EPS · 가격 · vs 현재가 · 입력 출처 색(표/세그먼트/사용자/기본값/없음). 저장은 기존 편집 ops(`set` A3.1~3 scenario · A3.8) 로.
- CLI: `mindmap valuation <map> [--eps-basis consensus|my|normalized|<값>] [--per 6.36] [--shares 5846279000] [--net-cash 억원] [--sotp 메모리=6,SDC=6,...] [--save]` · `mindmap scenario <map> --set Bear.dram_asp=-30 --set Bear.per=3.5 … [--probs 30/45/25] [--save]` · `mindmap implied <map> [--per 6.36]`. 콘솔 메뉴에도 같은 셋.
- `edit_schemas` 에 Scenario 확장 필드 추가(`model` 은 `_SCHEMA_SKIP`); 검증(`validate.py`): `price_source=computed` 면 `driver_assumptions` 비어 있지 않고 `model.status=계산됨` 이어야 함(아니면 경고), Bear ≥ P0 경고, 확률 합 ≠ 1 오류(기존).

### 4.6 원칙 → 코드 위치

| 원칙 | 코드 |
|---|---|
| 모델은 투명하다 — 입력마다 출처, 안 되면 "미계산 — 필요 입력" | `SensitivityInput` 재사용, `ValuationModel.inputs/missing`, 화면 출처 색 |
| 시나리오는 가정의 묶음이다 — 목표가가 아니라 가정이 1차 | `Scenario.driver_assumptions` 필수(computed 일 때), `target_price` 는 결과 |
| 확률은 사람이 정한다 | `apply_example_probs` 유지, 자동 확률 없음, A3.10 은 확률 없으면 "미계산" |
| 가치 ≠ 목표가 — SOTP 는 검증, 증권사 목표가는 분포(A3.6.4) | `gap_pct`, A3.6.4 그대로 |
| 민감도와 시나리오를 더하지 않는다 | 한 함수, 교차항 포함, 화면 문구 유지 |
| LLM 은 숫자를 만들지 않는다 | 프롬프트에 밸류 항목 없음, 제안값은 Claim 조건부 명제 → `conditions` 텍스트 제안만 |

## 5. 단계

| 단계 | 내용 | 산출 |
|---|---|---|
| **S1** 평가 모델 PER + 역산 + 추출 보강 | A3.8 · A3.9 노드, `ValuationModel`(PER · 정상화 · 역산), ROW_LABELS 에 순차입금 · FCF · CAPEX · 영업활동현금흐름, 헤더 발행주식수 → `ReportFacts.shares_out`, DPS 결합 줄 분리, `mindmap valuation` | 삼성 맵에 PER 530,000 / 정상화 480,600 / 내재 EPS 33,900 이 출처와 함께 뜬다 |
| **S2** 시나리오 = 가정 묶음 + 통합 모델 | `Scenario` 확장, `engine/scenario_model.py`, `compute_sensitivity` 를 이 위로 재배치(회귀 동일), `mindmap scenario`, 검증 경고(Bear ≥ P0 등) | 가정 묶음 3개 → 가격 3개, 교차항 포함 |
| **S3** 확률가중 투자 매력도 | A3.10, 배당 옵션, 하방 · 안전마진, `lifecycle` 과 A6.1 연결(RR 은 기존 식 유지) | 확률 입력 시 V · ER · ER_total · 하방 · 안전마진 |
| **S4** SOTP 검증 + 모델 신뢰도 | 포함 관계 사전, 멀티플 입력, 순현금 연도 선택, `gap_pct`, A3.11 규칙 | 삼성 맵 SOTP 516,000 vs PER 530,000 (−3%) · 신뢰도 M(EPS 기준 혼합 · 2028E 1곳) |
| **S5** 화면 · 문서 · 테스트 | HTML 계산기 패널(`/preview`), 콘솔 메뉴, README · ARCHITECTURE, 테스트(`test_v6.py`: 식 재현 · 포함 관계 거절 · Bear 경고 · 하위 호환 로드), 골든 `valuation/005930.json`(§3 숫자) | 테스트 195 → 약 215 |

S1~S2 가 핵심이고 각 1~2 세션. S3 는 식이 다 있어 짧다. S4·S5 는 각 1~2 세션.

## 6. 받지 않는 것 · 보류

- **DCF · RIM**: WACC · 영구성장률 · 자본비용이 전부 사람 가정 — 입력만 늘고 검증은 안 된다. 보류. PBR–ROE(목표 PBR × BPS) 는 BPS 행이 있으니 `per_basis` 의 선택지로만 뒤에 추가.
- **자동 확률 · 자동 멀티플**: 안 한다. 피어 멀티플 수집 어댑터는 없고, 있어도 선택은 사람.
- **재번호(A3.1 평가 기준 …)**: 비권고(§1 §7 행). 저장된 맵·B 스냅숏·테스트가 깨지고 얻는 것은 표시 순서뿐 — 표시 순서는 렌더에서 정한다.
- **클라이언트(JS) 에 식 복제**: 안 한다. 계산기는 서버 `/preview` 로만(내려받기 모드에서는 "서버 없음 — 계산기 비활성, CLI `mindmap scenario` 사용" 표시).
- **LLM 이 시나리오 가정 숫자를 쓰는 것**: 금지 유지. Claim 의 조건부 명제("…하면 …") 는 `conditions` 문장 제안값으로만.
- **역사 밴드(A3.6.1) 자동 계산**: 가격·EPS 시계열이 DB 에 있어야 한다 — 이번 범위 밖(있으면 S4 뒤에 붙인다).
- **세그먼트 멀티플 기본값**: 전사 목표 PER 를 모든 사업부에 깔지 않는다(SOTP 가 PER 복제가 된다). 비어 있으면 "미계산 — 필요 입력: 메모리 멀티플 …".

## 7. 정해야 할 것 (사용자)

1. **id 전략** — 기존 id 유지 + A3.8~A3.11 신설(권고) vs 제안대로 재번호.
2. **적용 PER 기본값** — 증권사 목표 PER 중앙값(530,000/83,320 = 6.36) 을 "기본값" 표시로(권고) vs 기본값 없음(사용자가 넣기 전까지 "미계산").
3. **정상화 EPS 정의** — ① 2026E~2028E 평균(75,572 ; 2028E 1곳 n 표시) ② 2026E~2027E 평균(66,816) ③ 사용자 입력만. 권고 ①에 n 경고.
4. **SOTP 멀티플 출처** — 사용자 입력만(권고) vs 전사 목표 PER 를 기본값으로 깔기(비권고, §6).
5. **배당 포함 기본값** — 기대수익률에 DPS_E/P0 를 ① 별도 표시(ER_price · ER_total 둘 다, 권고) ② 기본 포함 ③ 기본 제외.
6. **Bear 기본 템플릿(KR-SEMI)** — 라벨만 깔아 둘지("메모리 ASP 경로 · 멀티플 압축 · 기준 연도", 숫자 공란) vs 아무것도 깔지 않을지.
7. **주식 수 기본값** — 공시 보통주(헤더 추출 5,846,279천주, 권고) vs NP/EPS 유도(6.02B).

결정이 오면 S1 부터 시작한다. 코드 변경은 이 문서 승인 뒤.

## 8. 구현 결과 (2026-10-24, S1~S5 완료 — 테스트 195 → 208)

- **S1** `models/sub.py` `ValuationModel`·`SotpPart`, `SensitivityInput.source` 에 `header`·`criteria`; `templates/questions.py` A3.8~A3.11; `engine/valuation.py` — `Criteria.from_map`(A3.8 dict), `common_tail`(t · N · PER: 드라이버 입력 > A3.8 > 표/머리 > 기본값), `compute_valuation`(PER · 정상화 · SOTP · 역산), `write_valuation`(A3.9 값 + `meta.draft.valuation` + `valuation_log`). 추출: `ROW_LABELS` 에 `net_debt`·`net_cash`·`fcf`·`capex`(크기만)·`ocf`, **`_midline_row`**(표 두 개가 나란히 찍힌 줄의 오른쪽 표 — DPS·BPS·PER·순차입금·FCF…; 핵심 지표는 줄 머리만), `_merge_tables` 가 핵심 지표 없는 표(재무상태표·현금흐름표) 의 보조 지표를 받음, `_to_num` 이 깨진 토큰 `(1,045,` 을 버림, `find_shares_out` → `ReportFacts.shares_out`/`shares_note` → `ReportDigest.shares_out`(중앙값)·`shares_by_broker`; `AMOUNT_METRICS` 확장; `financial_snapshot` 에 `years`(연도별 지표 평균·중앙값·n·증권사별) · `latest_actual_year` · `shares_out` · `np_basis` · `report_dates`. 민감도 꼬리를 `common_tail` 로 교체(결과 동일), payout 템플릿이 표의 FCF(또는 OCF − CAPEX) 를 먼저 씀. CLI `valuation`.
- **S2** `Scenario` + `driver_assumptions`(`DriverAssumption`) · `eps_basis` · `eps_year` · `per` · `conditions` · `indicators` · `falsifier` · `price_source` · `model`(`ScenarioResult`), **`target_price` 는 Optional**(computed 인데 미계산) — `NewMap.scenarios()` 는 가격 있는 것만, `scenario_nodes()` 는 전부; `engine/scenario_model.py` — `SegState.delta`(가격·물량 교차항, 물량 비례 원가, 원가율 %p, 손익 직접 %·억원·절대값), `evaluate_scenario`(EPS' = 기준 EPS × NP'/NP — 가정 없으면 Base = 평가 모델, 가정 하나면 민감도와 동일: 54.1조/7,438/47,316 회귀), `compute_scenarios`, `apply_bear_template`(라벨만), `BEAR_TEMPLATES`; CLI `scenario`·`implied`. 검증: 빠진 시나리오는 **노드별** 오류("Bear 시나리오 누락" / "…미계산 — 필요: …"), Bear ≥ 현재가 경고.
- **S3** `engine/attractiveness.py::compute_attractiveness` — V · ER_price · ER_total(DPS 중앙값 ÷ P0, 모드 separate/include/exclude) · 하방 · 안전마진(vs 가중 · vs Base) · 손익비; `dps_estimate`(중앙값 + 흩어짐 메모). `lifecycle.evaluate_new_map` 은 그대로(A3.4/A3.5) — 편집 저장 뒤 다시 부른다.
- **S4** SOTP: 포함 관계 사전 `SEGMENT_CONTAINS`(겹치면 자식 제거 + 경고), 지배지분 `sotp_stakes`(없으면 100% + 경고), 순현금 = 최근 실적 연도 순차입금 × −1(또는 A3.8 `net_cash`), 기타(전사 OP − 사업부 합) 표시, `gap_pct`(>15% 경고); `compute_confidence` — 커버리지(기본값·누락 제외) · 증권사 수 · 순이익 기준 혼합 · 유도 주식 수 vs 공시(>5%) · 증권사별 발행주식수 차이 · 사업부 합 vs 전사(>3%) · 데이터 나이(>45일) · PER/SOTP 차이 · SOTP 미계산 · 정상화 1곳 셀 · 고점 vs 정상화(>25%) · 시나리오 경고 · 확률 미입력 → H/M/L.
- **S5** HTML 「밸류 계산기」(`#calc`: 드라이버 × Bear/Base/Bull 가정 표 + PER·EPS 기준·확률 + A3.8 기준; 「미리보기」 = `/preview` 의 통합 모델, 「적용」 = 편집 연산), 시나리오 노드 한 줄에 가정·OP·EPS·PER 표시, `edit_schemas` 의 Scenario 확장(가정 묶음은 JSON, price_source·eps_basis 는 select, `model` 제외), `finalize_after_edit` 가 `compute_valuation_all` 과 `evaluate_new_map` 을 다시 부름, draft 가 사업부 전부를 스냅숏하고 밸류 묶음을 돌림, 텍스트 콘솔 「V」, `ops.valuation_text`/`set_valuation_criteria`, README·ARCHITECTURE, `tests/test_v6.py` 13건(추출 · 꼬리 · PER/정상화/역산 · 기준 · SOTP/포함 관계 · Base=모델 · 1가정=민감도 · 교차항 · Bear 경고 · 미계산/템플릿 · 확률가중/배당/신뢰도 · 편집·검증 · 옛 맵), 골든 `tests/golden/valuation/005930.json`(PDF 가 있을 때만 재현).
- **삼성전자 실측 (리포트 현재주가 262,000)**: PER 530,000(컨센 83,320 × 6.36 기본값) · 정상화 480,717(26E~28E 75,572; 28E 1곳) · 역산 EPS 41,188(컨센 −51%) · DRAM ASP 경로 −57% 내재 · SOTP(임의 멀티플 메모리 6 · 파운드리 0 · DX 8 · SDC 6 · Harman 10) 537,428(PER 대비 +1%, 순현금 2025A 98.2조) · Bear(DRAM ASP −30% · 파운드리 −2조 · PER 3.5) 212,553(−19%) · Base 530,000 · Bull(ASP +10% · 파운드리 BEP · PER 7) 635,852(+143%) · 확률 30/45/25(예시) → V 461,255 · 기대수익률 +76.1% · 배당 포함 +82.2%(DPS 16,100) · 하방 −19% · 손익비 5.4 · 신뢰도 M(목표 PER 기본값 · 순이익 기준 혼합 · 발행주식수 차이 · 28E 1곳). 완만한 Bear(ASP −10% · PER 4.5) 는 349,400(+33%) 로 경고가 뜬다 — §0 2 그대로.
- **문서의 수정**: §2.2 "주식 수는 사용자 입력(공시 보통주) 우선" 은 **주당 환산(SOTP·주당 환원)** 에 적용하고, EPS 환산용 N 은 `NP_E/EPS_E` 로 두어 가정 없는 Base 가 컨센 EPS × PER 과 같게 했다(둘의 차이는 신뢰도 경고로). 역산의 분모는 가격·물량 템플릿 원인 드라이버의 사업부 매출(포함 관계 정리 후) — 삼성전자는 DRAM 만(NAND 는 드라이버가 아님) 이라 "DRAM ASP 경로" 로 표시한다.

## 9. 로컬 LLM 투입 실측 (2026-10-26, DGX Spark · OpenShell 샌드박스 · Ollama `nemotron-3-nano:latest`)

- 경로: `mindmap draft --reports data/reports/samsung/*.pdf --llm --llm-kind local --claims llm --no-fetch` — `OpenAICompatFiller`(사고 끔 `LLM_THINK=none`, `max_tokens` 8000, 자격증명 핸들 `NEMOCLAW_LOCAL_INFERENCE_TOKEN`) 로 NemoClaw 인증 프록시(11435) → Ollama. 테스트 209건 통과(샌드박스 `.env` 에 로컬 LLM 이 있어도 "LLM 없음" 테스트가 흔들리지 않게 `tests/helpers.py::NO_LLM_ENV`).
- 결과: 끝까지 돈다 — Claim 추출(LLM) → 쟁점 3(공통 1) → A1 원인 드라이버 5(수치 1 · 정성 4) → 민감도 5 → 평가 모델(PER 530,000 · 정상화 480,717 · 역산 EPS 41,188 / DRAM ASP 경로 −57%) → 신뢰도 L(경고 6). 밸류 숫자는 클라우드(Claude) 실측(§8) 과 같다 — **엔진이 같은 표 숫자를 썼다는 뜻**이지(밸류는 LLM 이 만들지 않는다), 로컬 모델이 증권사 의견을 정확히 읽었다는 뜻은 아니다(§11.3).
- Claim 품질: 리포트 3건에서 26건 중 **verified 5(19%) · 검토 대기 21** (96588 1/5 · 96597 3/8 · 96634 1/13). Claude 캐시(`data/claims_llm`) 대비 낮다. 어느 단계(① 인용 위치 ② 숫자 ③ 의미 판정) 에서 떨어지는지 분해가 없어 원인은 미확정 → §10 C1·C2.
- 환경 메모: 샌드박스 네트워크는 정책으로 막혀 pypi 프리셋(`nemoclaw <name> policy-add pypi`) 이 필요했고, NemoClaw v0.0.136+ 의 로컬 provider 자격증명 핸들 버그(#13553) 는 `openclaw.json` 의 `apiKey` 를 범위 핸들로 바꿔 우회. 현재가는 오프라인이라 리포트 기준일(14일 전) 값 — §10 B3.

## 10. 보강 과제 목록 (2026-10-26 검토 — 릴리스 순서는 §11.4)

§8 구현은 삼성전자(KR-SEMI) 3건으로 검증했다. 같은 코드를 NAVER(6건)·두산에너빌리티(4건) 리포트에 돌려 보면 **A1 원인 드라이버 0개 → 민감도·가정 묶음·역산 전부 미계산**이고 평가 모델은 PER·정상화만 뜬다. 원인은 v6 식이 아니라 그 앞단 두 가지(A1·A2) 다. 우선순위 순.

| # | 과제 | 왜 | 어디 | 크기 |
|---|---|---|---|---|
| **A1** | 섹터 원인 KPI 사전 확장 — KR-INTERNET(광고 성장 · 커머스 거래액/테이크레이트 · 핀테크 결제액 · 콘텐츠 · 마케팅/인프라 비용) · KR-UTIL(신규수주/수주잔고 `orders` · 원전 매출 · 가스터빈 · 자회사(밥캣) `direct_op` · 원가율) 을 v5 형식(driver_id · variable · template · segment · proxies) 으로 | `cause_kpis()` 가 `driver_id` 있는 KPI 만 쓰므로 지금은 KR-SEMI 만 A1 이 채워진다(옛 MAU·테이크레이트 항목은 후보가 아님). 다른 섹터는 쟁점 테마로만 채워져 템플릿이 없어 민감도가 안 돈다 | `generate/kpi_catalog.py` SECTOR_KPIS, `engine/sensitivity.py`(orders 템플릿 입력 확인) | 중 |
| **A2** | 사업부 표 추출 일반화 — `SEGMENT_ALIASES` 가 삼성 사업부 고정 → 종목/섹터별 별칭(NAVER 서치플랫폼·커머스·핀테크·콘텐츠·클라우드, 두산 원자력·가스터빈·밥캣·산업차량 …) + "사업부별/부문별" 캡션 표 안의 모르는 행도 사업부로 받기(SKIP_ROWS 만 버림) | NAVER·두산 `financials.segments` 가 `null` — 가격·물량 템플릿의 분모(사업부 매출) 가 없다 | `reports/segments.py` | 중 |
| **C1** | `mindmap claims stats <dir>` — ClaimSet 의 ① 인용 ② 숫자 ③ 의미 단계별 통과/실패와 검토 대기 사유(`review_reason`) 집계 | Nano verified 19% 의 원인을 모른다 (인용 패러프레이즈? 숫자 불일치? ③ 판정 실패?) | `cli.py` · `reports/validate_claims.py` | 소 |
| **C2** | `mindmap claims compare <dirA> <dirB>` — 같은 리포트의 두 ClaimSet(Claude 캐시 vs 로컬) 비교: (driver_id · variable · 수치) 셀 재현율 · 방향 일치 · 인용 위치 겹침 · 증권사별 표 | Spark 에서 Nano 를 계속 쓸지, Super/Qwen 으로 갈지 숫자로 정한다 | `generate/evals.py` · `cli.py` | 소~중 |
| **C3** | 로컬 모델용 프롬프트 프로파일(`--prompt-profile local`): JSON 스키마·"인용은 원문 그대로"·청크당 Claim 상한 강조, `CLAIM_PROMPT_VERSION` c4 로 캐시 구분; ③ 의미 판정을 로컬 모델로 할지 끌지(`--semantic off`) 옵션 | 작은 모델은 긴 지시를 흘린다 · 판정 모델이 약하면 verified 가 통째로 빠진다 | `generate/prompts.py` · `reports/claims.py` | 소 |
| **B3** | `draft/changes --price 262000 --price-date 2026-10-26` 수동 현재가 | 오프라인 샌드박스에서 2~3주 묵은 리포트 가격으로 역산·매력도가 계산된다. 에이전트가 호스트에서 받은 값을 넘길 통로 | `cli.py` · `generate/draft.py`(price_source=user) | 소 |
| **D1** | `mindmap batch --reports-dir data/reports --maps-dir data/maps [--db]` — 종목 폴더마다 맵이 없으면 `draft`, 있으면 `changes`; 결과를 요약 Markdown/JSON 한 장과 종료 코드로 | 에이전트(OpenClaw heartbeat) 가 콘솔 없이 부른다 | `cli.py` · `ops.py` 재사용 | 중 |
| **D2** | `mindmap alerts <map>` — 체크포인트(A7.1) 도래 · Kill 규칙 발동 · 리비전 · 현재가 기준일 경고를 알림 텍스트(한 줄/상세) 로. 전송(Telegram/카카오) 은 에이전트 몫 | 운영 단계의 핵심 산출. 판단은 사람(알림만) | `cli.py` · `engine/lifecycle.py` | 소 |
| **D3** | `mindmap export-knowledge --out knowledge/` — 맵·ClaimSet·IssueSet → 종목별 Markdown(논제 · A1 · 쟁점 · 시나리오 · 평가 모델 · Kill 한 장) | OpenClaw `memory_search` 가 읽는 2층 지식(docs/openshell.md §6). 판단 필드는 그대로 적되 "사람 판단" 표시 | `render/`(markdown) · `cli.py` | 소~중 |
| **D4** | `draft/changes --json` 요약 출력 | 에이전트가 파싱 (현재는 사람용 텍스트만) | `cli.py` | 소 |
| **B1** | 발행주식수 라벨 구분 — 보통주 / 우선주 포함 / 자기주식 제외 를 머리 문구에서 읽어 **공시 보통주(결정 ⑦)** 를 고르고 출처 표시 | 삼성 5,919 vs 6,742 백만주, NAVER 159.3 vs 152.1 백만주 — 지금은 중앙값 + 신뢰도 경고 | `reports/extract.py::find_shares_out` · `digest.py` | 소 |
| **B2** | 추정치 표 단위 추정 검증 — "단위 못 읽어 십억원 가정"(NAVER 한화) 때 다른 증권사 매출 크기와 비교해 ×1000 어긋나면 보정·경고 | 단위 하나가 틀리면 컨센 중앙값이 밀린다 | `reports/digest.py` | 소 |
| **A3** | Bear 템플릿 라벨 — KR-INTERNET(광고 성장 둔화 · 커머스 투자 비용 · 멀티플 압축) · KR-UTIL(수주 지연 · 원전 일정 · 멀티플) 추가(라벨만, 결정 ⑥) | 삼성만 있음 | `engine/scenario_model.py::BEAR_TEMPLATES` | 소 |
| **A4** | 손실 사업부의 `direct_op` 는 % 대신 절대값(조원) 기본 | "파운드리 +10% → +50원" 처럼 읽히지 않는 민감도 | `engine/sensitivity.py` | 소 |
| **E1** | 문서: README 에이전트/Spark 절, `docs/openshell.md` 에 진행 기록, ARCHITECTURE 표 갱신 | — | docs | 소 |

보류 유지(§6): DCF·RIM, 자동 확률·멀티플, 재번호, JS 식 복제, LLM 의 가정 숫자, 역사 밴드 자동 계산. 카카오 "나에게 보내기" 전송기는 D2 의 텍스트가 먼저고, 전송은 에이전트/스킬 쪽에서.

순서 제안(초안): A1·A2 → C1·C2 → B3·D1·D2 → 나머지. **→ §11 검토 의견으로 순서를 바꿨다**: 정합성(P0-1) · 해석(P0-2) · Claim 품질(P0-3) 을 v6.1 로 먼저, 섹터 확장(A1·A2) 과 운영 CLI 는 v6.2.

## 11. 검토 의견 반영 (2026-10-26) — v6.1 = 정합성 · 해석 · Claim 품질

외부 검토("반드시 수정할 3가지") 를 코드와 대조한 결과 세 지적이 모두 맞다. 각 항목에 **지금 코드가 하는 일 → 고칠 설계 → 숫자 영향** 을 적는다. v6.1 은 새 기능이 아니라 이 세 기반을 고치는 릴리스다.

### 11.1 P0-1 회계 기준 정규화 — EPS · 순이익 · 주식 수

**지금.** `digest` 는 순이익(NP) 만 다수 기준으로 평균한다("지배 2 · 전체 1 → 지배만"). **EPS 는 기준을 보지 않고 3곳을 그대로 평균**한다(83,320) — §2.2 에 적힌 대로 IBK EPS 는 우선주 포함 6.65B 주, 유진은 전체 순이익, DS 는 지배 ÷ 5.91B 로 서로 다른 기준이다. `common_tail` 은 이 혼합 EPS 로 N = NP/EPS(6.02B, `derived`) 를 유도해 쓰고, 차이는 A3.11 경고로만 남긴다. 시나리오의 EPS' = EPS × NP'/NP 는 비율 보정이라 **최초 EPS 의 기준 불일치를 없애지 못한다**(검토 의견 그대로).

**설계.**
- A3.8 평가 기준에 `earnings_basis`(`지배` | `전체`, 기본 지배), `share_count_basis`(`보통주` | `보통주+우선주` | `자기주식 제외`, 기본 보통주), `shares_std`(표준 주식 수 — 머리 발행주식수 중 "우선주 포함" 문구가 없는 것의 중앙값; 사용자 입력 우선) 를 둔다.
- 증권사별 분류 `BrokerBasis(np_basis, share_basis, n_implied)`: NP 기준은 표의 `basis.np`, 주식 수 기준은 머리 문구(우선주 포함) 와 N_i = NP_i/EPS_i 를 공시 보통주와 비교해 정한다(±4% → 보통주, +12~16% → 우선주 포함, 그 밖 → 불명).
- **EPS 정규화**: EPS_i^std = NP_i(지배) ÷ shares_std — 지배 순이익이 있는 증권사만. 전체 순이익만 있는 증권사는 `not_comparable` 로 평균에서 **제외**하고 표에 회색으로 남긴다(지배 비율로 환산하는 추정은 하지 않는다 — 검증 불가). 컨센 EPS = 정규화 EPS 의 평균, n 표시.
- **호환성 검사** `check_basis(digest, criteria) -> BasisCheck(status, per_broker, notes)` 를 `compute_valuation()` 과 `compute_sensitivity()` 진입 전에 돈다. `status`: `comparable`(정규화 2곳 이상) · `mixed`(1곳만 정규화 — 값은 계산하되 "참고") · `not_comparable`(0곳 — PER·정상화·역산·시나리오 가격을 **확정 표시하지 않음**: A3.9 값 칸 "기준 불일치 — 참고값", HTML 회색, 검증에 미확정 1건). 상태는 `ValuationModel.basis_status` · `ScenarioResult.status` 로 전파되고 A3.11 은 경고가 아니라 이 상태를 읽는다.
- **N 통일**: EPS 환산 N 도 `shares_std` 로. §8 의 "EPS 환산은 유도 N, 주당 환산은 공시 주식 수" 이원화는 없앤다 — 정규화 뒤에는 EPS^std × N_std = NP(지배) 가 성립하므로 Base(가정 없음) = EPS^std × PER 가 그대로 유지된다. 민감도 EPS 환산(ΔNP ÷ N) 도 같은 N 을 쓴다.
- 발행주식수 라벨 구분(§10 B1) 과 표 단위 검증(B2) 은 이 항목의 일부로 함께 한다.

**삼성전자 숫자(2027E).** IBK 465.4조 ÷ 5.846B = **79,606** · DS 537.8조 ÷ 5.846B = **91,990** · 유진(전체 529.4조) 제외 → 컨센 EPS^std **85,798 (2곳, mixed 아님 — comparable)** vs 지금 83,320(+3.0%). 목표가 유도 PER 는 530,000/85,798 = 6.18 로 바뀌고, 정상화·역산·SOTP 주당 환산의 분모가 한 종류가 된다. 유진 리포트는 EPS 표에 `not_comparable` 표시 — 그 증권사 목표가(560,000) 는 분포에 그대로 쓴다(목표가는 기준 문제가 없다).

**구현 (2026-10-27, v7 T1)** — v7 §12.3. 삼성 컨센 EPS 86,693(3곳) · PER 6.11; 전체만 적은 증권사는 "제외" 대신 측정 지배 비율(≥0.9) 로 환산해 넣는다(표시). 테스트 217.

### 11.2 P0-2 역산 결과의 조건부 해석 — "내재" 를 쓰지 않는다

**지금.** 기본 PER = 목표가 중앙값 ÷ 컨센 EPS(`per_source=default`) 이고, A3.9 한 줄이 "역산: 현재가는 EPS 41,188 (컨센 −51%) · DRAM ASP 경로 −57% **내재**" 로 단정형이다. `mindmap implied --per …` 는 여러 PER 를 받지만 초안 요약·HTML 은 단일 값만 보인다. Base(가정 없음) = EPS × 목표가 유도 PER = 목표가 중앙값 재현(순환) 이 라벨 없이 뜬다.

**설계.**
- `per_source` 열거를 바꾼다: `broker_target_implied`(지금의 default) | `user` | `historical_normalized`(과거 PER 밴드 — 가격·EPS 시계열 DB 가 생기면) | `peer`(피어 멀티플 — 수집 어댑터가 생기면). 당장 쓸 수 있는 것은 앞 둘이고 뒤 둘은 자리만 둔다(§6 보류 유지).
- `ValuationModel.implied_status`: `per_source == broker_target_implied` 면 **`assumption_dependent`**, 그 밖은 `anchored`. 문구: "PER 6.18(목표가 유도) 와 현재 비용 구조가 유지된다는 가정에서 현재가 262,000 을 설명하는 EPS = 41,188(컨센 −51%), DRAM ASP 변화 = −57%". 단어 "내재" · "시장은 …를 반영" 은 쓰지 않는다. A2.1 "시장과 내 견해 차이" 가 이 값을 쓸 때 `assumption_dependent` 면 질문형으로만 적는다.
- **역산 표** `implied_table`: PER 여러 개에서 EPS · 컨센 대비 · 가격 드라이버 경로를 한 표로 — 기본 행은 목표가 유도 PER · 증권사 PER 행 중앙값(각 리포트 가격 기준, §2.2) · 사용자 PER(있으면) · 사용자 지정 목록(`--per`). A3.9 상세와 HTML 계산기에 표로 보이고, 한 줄에는 "PER 6.18 → −57% · 4.5 → −40% · 3.5 → …" 식으로 범위를 보인다.
- Base 라벨: 가정 없는 Base 가 목표가 유도 PER 를 쓰면 "**Base = 목표가 중앙값 재현(순환) — 내 PER 입력 전 참고**" 를 시나리오 줄과 A3.10 에 붙인다. 사용자가 `per` 를 넣으면 라벨이 사라진다.

**구현 (2026-10-27, v7 T2)** — v7 §12.4. `per_source`·`implied_status`·`implied_table`(목표가 유도 · 증권사 PER 행 중앙값 · 실적 컨센 그대로 · 사용자 · 지정 · 중간점), 조건부 문구("…를 설명하는 EPS", "다른 PER 면", "[가정 의존]"), Base 순환 라벨. 두 변수 격자 조합표는 v7 T6.

### 11.3 P0-3 로컬 LLM Claim 품질 게이트

**지금.** §9 실측 — 26건 중 verified 5(19%) · 검토 대기 21. 어느 단계(① 인용 위치 ② 숫자 ③ 의미 판정) 에서 떨어지는지 집계가 없고, Claim 품질이 낮아도 초안은 "완료" 로 표시된다. 검토 의견대로 **19% 는 정확도가 아니라 검증기 통과율**이다 — ③ 의미 판정을 같은 작은 모델이 하므로 판정기가 병목일 수도 있다.

**설계.**
- **C1 `mindmap claims stats <dir>`**: ClaimSet 별·전체로 ① quote ok/fail ② numbers ok/none/fail ③ support supported/unchecked/contradicted 의 교차표, `review_reason` 사유 집계, 추출 모델 · `CLAIM_PROMPT_VERSION` 별 분리. 결과는 표 + JSON.
- **C2 `mindmap claims compare <기준 dir> <비교 dir>`**: 같은 리포트 파일끼리 — (a) 원인 KPI 셀 재현율: 기준의 (driver_id · variable · col · 값±1%) 가 비교에 있는 비율 (b) 증권사별 방향(상향/하향/유지) 일치율 (c) 중요 이견 누락률: 기준 쟁점 표의 핵심 이견 테마가 비교 쪽 Claim 으로 재구성되는지 (d) 인용 위치 겹침. 표 + JSON.
- **품질 게이트**: `draft` 가 `meta.draft.quality` 에 `claim_gate = {verified_ratio, core_verified_ratio, recall_vs_reference?, status}` 를 적고, **핵심 Claim**(원인 KPI 셀 · 목표가/추정치 변경 · 조건부 리스크) 의 검증률 < 50% 또는 기준 캐시가 있을 때 재현율 < 70% 면 `draft_status = needs_claim_review` — 요약 첫 줄 "⚠ Claim 품질 게이트 미달 (검증 19% · 재현율 —)", HTML 배지, A1 노드 question 에 "Claim 검토 전" 표시, 검증 결과에 미확정 1건. 완료 표시는 사람이 검토함을 처리해 비율이 올라가거나 게이트를 명시적으로 끌 때(`--no-claim-gate`, 기록 남김) 만. 문턱 50/70 은 첫 값이고 C2 결과로 보정한다.
- 순서: C1 → C2 → 결과에 따라 C3(로컬용 프롬프트 · ③ 판정을 Claude 또는 사람으로) 또는 모델 교체(Super/Qwen). Claude 와 로컬 모델의 밸류 숫자가 같았던 것은 **엔진이 같은 숫자를 썼다는 뜻**이고 Claim 분석의 정확성을 말하지 않는다 — §9 문구를 그렇게 고쳤다.

### 11.4 수정된 순서

| 릴리스 | 내용 | 크기 |
|---|---|---|
| **v6.1 정합성·해석·품질** | P0-1 회계 기준 정규화(+B1 발행주식수 라벨 · B2 표 단위 검증) → P0-2 `per_source`·`assumption_dependent`·역산 표·순환 라벨 → P0-3 C1 · C2 · 품질 게이트. 테스트: 삼성 3건 정규화 숫자(85,798 · PER 6.18) 골든 갱신, `not_comparable` 차단, 문구, 게이트. | 중 · 소~중 · 중 (3~4 세션) |
| **v6.2 확장·운영** | A1·A2 섹터 원인 KPI · 사업부 표 일반화(NAVER·두산), A3·A4, B3 `--price`, D1 `batch` · D2 `alerts` · D3 `export-knowledge` · D4 `--json`, E1 문서 | §10 표대로 |

검토 의견과 다른 점 두 가지만 적는다. ① `not_comparable` 은 "값을 비우는 차단" 이 아니라 "확정 표시를 막는 상태" 다 — 참고값은 회색으로 남겨야 사용자가 무엇이 빠졌는지 볼 수 있다. ② `historical_normalized` · `peer_based` PER 는 데이터(가격·EPS 시계열, 피어 멀티플) 가 없어 열거값만 두고 보류한다 — 당장은 `user` 가 독립 PER 의 유일한 경로다.
