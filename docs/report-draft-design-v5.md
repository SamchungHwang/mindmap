# 핵심 드라이버(A1) 재설계 — 설계 v5 (2026-10-24 검토 → 같은 날 R1~R5 구현)

작성 2026-10-24 · **사용자 결정(2026-10-24)**: ① KR-SEMI 원인 드라이버 = DRAM ASP · HBM · 메모리 원가 · 파운드리 · FCF·주주환원 (이 순서) ② DRAM 매출은 **세그먼트 표 추출(R4) 먼저** ③ 전가율 k 기본값 1.0 을 "기본값" 으로 표시 ④ 결과 지표는 A3.7 ⑤ 다른 섹터의 원인 KPI 는 그 종목 맵을 만들 때 정의 · 구현 결과는 §9 · 앞선 문서: `report-draft-design-v4.md`(V1~V13 구현 완료) · 입력: 사용자의 재설계 제안("핵심 드라이버 = 원인 변수", 삼성전자 기준 5개, 드라이버 공통 필드 10개, 설계 원칙 5개, 첫 기능 = DRAM ASP → 메모리 영업이익 → EPS → 적정주가).

## 0. 결론

**방향은 타당하고, 지금 코드 위에서 구현할 수 있다.** 구조 변경(A1 = 원인 변수 5개, 결과 지표는 A3 아래로 이동, 드라이버 필드 확장) 은 모델·검증·화면에 **덧붙이는** 수준이고 전부 기본값이 있어 저장된 맵이 그대로 읽힌다. 어려운 것은 둘이다.

1. **원인 변수의 숫자는 표가 아니라 본문에서 온다.** 지금 A1 이 매출·영업이익·EPS 가 된 이유는 `kpi_catalog.kpi_candidates` 가 "추정치 표에서 바로 채울 수 있는 지표"(`metric` 있는 KPI) 를 우선하기 때문이다(`DRIVER_METRIC_ORDER`). DRAM ASP·HBM 출하·파운드리 손익은 표에 없고 **Claim 의 `quant_refs`** 에 있다 — c3 저장본에 이미 들어 있다(§2). 다만 지표 id 가 `dram_asp_qoq` / `dram_asp_change` 처럼 제각각이라 **원인 지표 사전**이 필요하다.
2. **민감도 모델은 사업부 매출이 있어야 돈다.** DRAM ASP 10% → 메모리 영업이익 변화를 계산하려면 DRAM(또는 메모리) 매출이 필요한데, 추출기는 사업부 표를 **일부러** 읽지 않는다(전사 집계를 섞지 않기 위해 — `extract.py` "사업부 전망 제외"). 길은 둘: 세그먼트 표 추출(R4, 가장 위험) 또는 사용자가 편집 폼에서 한 번 입력(바로 가능). **첫 기능은 사용자 입력으로 먼저 돌리고 R4 를 뒤에** 붙이는 것을 권한다.

사용자 요청대로 첫 기능은 **DRAM ASP → 메모리 영업이익 → EPS → 적정주가** 체인(§3) 이다. 식은 1차 선형 근사이고, 입력마다 출처(표/Claim/사용자/기본값) 를 달아 **계산되지 않으면 "미계산 — 필요 입력: …" 로 드러낸다**(원칙 5). LLM 은 어디에도 숫자를 만들지 않는다.

## 1. 제안 항목별 판정

| 제안 | 판정 | 지금 코드 | 바꿀 것 |
|---|---|---|---|
| A1 을 원인 변수 5개로 (DRAM ASP · HBM4 · 메모리 원가 · 파운드리 · FCF·주주환원) | **타당 · 가능** | 사전엔 이미 있다: `DRIVERS` 의 dram_price · hbm · hbm_mix · foundry · shareholder_return · cost, `SECTOR_KPIS["KR-SEMI"]` 의 dram_price(계약가 변동률) · hbm_share · capex · inventory_days. 그러나 `kpi_candidates` 는 표에서 채울 수 있는 결과 지표를 먼저 고른다 | `Kpi`/`DriverDef` 에 `role: cause \| result`. 섹터별 **원인 KPI 5개** 정의(KR-SEMI: `dram_asp` · `hbm` · `memory_cost`(신설) · `foundry` · `fcf_payout`(신설)). 선택 함수 `select_drivers`(§1 원칙 2). 스켈레톤 기본 드라이버 5 |
| 매출·영업이익·EPS·OPM 은 삭제하지 않고 A3 로 이동 | **가능** | A3 = Bear/Base/Bull · ER · 손익비 · A3.6 밸류 근거(역사 밴드·피어·리비전·목표가 분포) | 템플릿에 **A3.7 「재무 전망 (결과 지표)」** + A3.7.1 매출 · .2 영업이익 · .3 EPS · .4 영업이익률 (Driver 구조 그대로, `role=result`). `rules.fill_drivers` 의 표 채움 로직을 A3.7 로 옮긴다. A2.1.x 쟁점·변경안 `affects` 의 A1 참조를 A3.7 로 |
| 드라이버 공통 필드 10개 (metric · actual · consensus · my_estimate · unit · period · sensitivity · confidence · next_check · invalidation_condition) | **대부분 있음, 4개 추가** | `Driver`: current(=actual) · consensus · my_estimate · next_check · check_indicator · direction; `Node`: unit · confidence | `Driver` + `metric`(원인 지표 id) · `period`("3Q26" / "2027E") · `kind: quant \| qualitative` + `proxies: list[str]`(대체 관측 지표) · `invalidation_condition`(문장 + 있으면 A4.4 Kill 규칙 id) · `sensitivity: Sensitivity \| None`(§3). 전부 기본값 → 하위 호환 |
| "모든 드라이버에 숫자를 강제하지 않는다" | **타당 · 검증 규칙 완화 필요** | `validate_new_map`: 드라이버 3개 이상, 각각 `current`·`my_estimate` 필수(오류) | `kind=quant` 만 숫자 필수. `qualitative` 는 `check_indicator`(관측 지표) 와 `invalidation_condition` 이 있어야 하고 `direction` 은 사람이 적는다. B 스냅숏(B1.2 at_entry) 과 `compute_direction` 은 None 을 이미 처리한다 |
| 원칙 1 — 숫자를 바로 드라이버로 승격하지 않고 원인/결과 판정 | **가능** | 없음 (표 지표 = 드라이버) | `role` 사전 + Claim `quant_refs.metric` 을 **원인 지표 사전**(`CAUSE_METRICS`: dram_asp_qoq · dram_bg_qoq · nand_asp_qoq · hbm_bg_qoq · hbm_asp_qoq · foundry_op · memory_op · capex · fcf · payout_ratio …) 으로 정규화. 결과 지표(revenue/op/np/eps/opm/per…) 는 A3.7 로만 |
| 원칙 2 — 리포트 등장 빈도가 아니라 논제 영향·관측 가능성으로 선택 | **부분** — "논제 성패 영향" 은 계산할 수 없다 | 쟁점 테마는 Claim 수·증권사 수로 묶인다 | 대리 점수 = 쟁점 표의 증권사 수 × display 가중(핵심 이견 > 공통 > 독립) + driver→op 메커니즘 Claim 수(§3 의 "직접 영향") + 관측 가능성(KPI 에 `check_indicator`·데이터 출처가 있으면 +) + 섹터 원인 KPI 우선순위. **최종 선택은 사람**(V12 편집으로 교체·순서 변경) 이고 선택 근거를 A1.x 질문에 적는다 |
| 원칙 4 — 전망값·실제값을 같은 기간·기준으로 | **이미 절반 있음** | V4 셀 키 = 증권사 × 지표 × 기간 × 기준 × A/P/E (표). Claim 의 `col` | 원인 변수에도 `period` 필수. `current` 는 `certainty=realized/announced` Claim(또는 공시) 만, `consensus` 는 `expected` Claim 들의 증권사별 값(표 셀과 같은 `EstimateCell` 꼴로 Claim 셀 집계). 기간이 다르면 ⚑ "기간 불일치" |
| 원칙 5 — 민감도가 계산되지 않았으면 명시, LLM 임의 생성 금지 | **가능 · 설계의 뼈대** | 없음 | `engine/sensitivity.py` 는 코드만. 입력마다 `source: table \| claim \| user \| default`, 빠지면 `status="미계산"` + 필요 입력 목록. 프롬프트에는 민감도 항목이 없다 |
| 첫 기능: DRAM ASP → 메모리 OP → EPS → 적정주가 | **가능** | — | §3. DRAM 매출만 밖에서(사용자 또는 R4) 온다 |
| "사업부별 매출·영업이익·FCF·EPS → 시나리오별 적정주가 → 매수·보유·축소·매도" | **시나리오·판단은 이미 있음**, FCF·사업부는 추가 | A3.1~3 시나리오(후보 → 사람), A6.5 판단, A6.3/6.4 규칙 | FCF 는 표 행 추가(`영업활동현금흐름`·`CAPEX`·`FCF` 를 `ROW_LABELS` 에) 로 가능. 사업부별 매출·OP 는 R4(세그먼트 표) |
| A1.1~A1.3 중복 계산 금지 | **타당** | — | 민감도는 드라이버마다 **다른 것은 고정(ceteris paribus)** 으로 따로 계산하고 합산하지 않는다. 화면에 "서로 더하지 말 것" 한 줄 |

## 2. 실측 근거 — c3 Claim 저장본(삼성전자 3건) 에 원인 변수가 이미 있다

| 증권사 | driver_id | quant metric | 기간 | 값 | 명제(앞부분) |
|---|---|---|---|---|---|
| 유진투자증권 | dram_price | `dram_asp_qoq` | 3Q26 | +21% | 3Q26 메모리 부문 영업이익 110.1조원(+20% qoq), 서버 DRAM·eSSD 중심 … |
| 유진투자증권 | dram_price | `nand_asp_qoq` | 3Q26 | +22% | (같은 명제) |
| 유진투자증권 | hbm | `hbm_bg_qoq` / `hbm_asp_qoq` | 3Q26 | +25% / +13% | HBM 은 HBM4 중심으로 출하량 확대, 3Q26 B/G +25% qoq, ASP +13% qoq |
| DS투자증권 | dram_price | `dram_asp_change` / `nand_asp_change` | 3Q26 | +19% / +13% | 3Q26 DRAM B/G 4%, ASP 19% 상승, NAND B/G 4%, ASP 13% 상승 |

- 같은 변수가 `dram_asp_qoq` 와 `dram_asp_change` 로 다르게 적혔다 → **원인 지표 사전으로 정규화**해야 증권사 간 비교(DRAM ASP 3Q26: 유진 +21 · DS +19) 가 된다. 이것이 A1.1 의 `consensus` 셀이다.
- 메모리 부문 OP 110.1조 · Foundry/LSI −1.5조 · MX −2.0조 는 **명제 본문**에만 있고 quant 는 전사 op 로만 잡혔다 → 세그먼트 수치를 쓰려면 Claim 의 `quant_refs` 에 `segment` 를 두거나(프롬프트 c4) 표에서 읽어야(R4) 한다.
- 참고: 제안문의 "유진투자증권 3Q26 DRAM ASP +19%" 는 저장본 기준 **DS 의 수치**이고 유진은 +21% 다. 어느 쪽이 맞는지는 원문 확인이 필요하다 — 바로 이런 혼동을 원인 지표 셀(증권사별 값 + 인용) 이 막는다.

## 3. 첫 기능 — DRAM ASP 민감도 체인 (`engine/sensitivity.py`)

1차 선형 근사. 물량·원가·비영업손익은 고정, 세금은 유효세율로 비례.

```latex
\Delta OP_{mem} = Rev_{DRAM} \times \Delta ASP\% \times k      \quad (k = 전가율, 기본 1.0)
\Delta NP = \Delta OP_{mem} \times (1 - t),\qquad t = 1 - NP_E / OP_E
\Delta EPS = \Delta NP / N,\qquad N = NP_E / EPS_E
\Delta P = \Delta EPS \times PER_{target},\qquad PER_{target} = TP_{median} / EPS_E
```

| 입력 | 출처(우선순위) | 지금 있나 |
|---|---|---|
| DRAM 매출 (같은 기간) | ① 세그먼트 표(R4) ② 사용자 입력(편집 폼 「민감도 입력」) | **없음** — 첫 기능은 ② 로 |
| ΔASP% | A1.1 `my_estimate − consensus` (Claim 셀) 또는 ±10% 표준 충격 | Claim 에 있음(정규화 뒤) |
| 전가율 k | 기본값 1.0 (가격 변화가 전부 이익으로), 사용자 조정 | 기본값 — 화면에 "기본값" 표시 |
| 유효세율 t | 표의 NP_E / OP_E | 있음 (`digest.cell("np"/"op", ye)`) |
| 주식 수 N | 표의 NP_E / EPS_E | 있음 (유도) |
| 목표 PER | 목표가 중앙값 / EPS_E (증권사별도 가능) | 있음 (A3.6.4 분포 · eps 셀) |

출력 `Sensitivity = {input_delta_pct, op_delta, np_delta, eps_delta, price_delta, price_delta_pct, inputs: [{name, value, source, period}], status: 계산됨 \| 미계산, missing: [...], computed_at}` 을 `Driver.sensitivity` 에 두고 A1.x 값 줄에 "민감도: ASP ±10% → 영업이익 ±x조 · EPS ±y원 · 적정주가 ±z원 (입력: 표 3 · 사용자 1 · 기본값 1)" 또는 "민감도 미계산 — 필요 입력: DRAM 매출(3Q26)". 삼성전자 2027E 로 손계산하면 자릿수가 맞는지 바로 검증된다(테스트).

**확장**(같은 틀): HBM = HBM 매출 × (ΔB/G% + ΔASP%) × k · 메모리 원가 = 메모리 매출 × Δ원가율 · 파운드리 = Δ적자 축소액(직접 OP) · FCF·주주환원 = ΔFCF × 환원율 / N (주당 환원). HBM4 단독 수치는 공개되지 않으므로 HBM 전체 + `kind=qualitative` 의 대리 지표(인증·수율·패키징 CAPA) 로 둔다 — 제안문의 지적 그대로.

**하지 않는 것**: 드라이버 민감도의 합산(중복), 비선형 모델(고정비 레버리지는 k 로 흡수), LLM 에 민감도 질문.

## 4. 데이터 계약 변경 (초안, 전부 기본값)

```
Kpi / DriverDef      + role: cause | result                    (결과 지표는 A3.7 로만)
CAUSE_METRICS        원인 지표 사전 {id: (label, unit, synonyms, driver_id, kind)}  — Claim quant_refs.metric 정규화
Driver               + metric · period · kind(quant|qualitative) · proxies[] · invalidation_condition{text, rule_id?} · sensitivity
Sensitivity          {input_delta_pct, op_delta, np_delta, eps_delta, price_delta, price_delta_pct, inputs[{name,value,source,period}], status, missing[], computed_at}
템플릿 A             A1.1~A1.5 (원인; 라벨은 섹터 원인 KPI) · A3.7 재무 전망 + A3.7.1~.4 (결과; 표에서)
validate_new_map     드라이버 ≥3 · quant 는 current·my_estimate · qualitative 는 check_indicator·invalidation_condition
ReportFacts (R4)     + segments{segment: {metric: {col: value}}}  · ROW_LABELS + cfo · capex · fcf
meta.draft.drivers   + selection{nid: {score, reasons[], alternatives[]}}
```

## 5. 단계

| 단계 | 내용 | 완료 기준 | 크기 |
|---|---|---|---|
| **R1** 계약·템플릿 | `role`, `Driver` 확장, `Sensitivity`, A3.7, validate 완화, B1.2 None, 저장본 호환 테스트 | 기존 맵 전부 읽히고 테스트 통과. 결과 지표가 A3.7 에, A1 은 비어 있어도 검증은 "미확정" | 중 |
| **R2** 원인 변수 선택·채움 | `CAUSE_METRICS` 정규화(c3 저장본 6건 전부 매핑) · Claim 셀 집계(증권사별, 기간, A/P/E) · `select_drivers` 점수 + 근거 · `current`(realized/announced) / `consensus`(expected) 분리 · 정성 드라이버 proxies | 삼성전자 c3 로 A1 = DRAM ASP(유진 +21 · DS +19) · HBM(B/G +25 · ASP +13) · 메모리 원가(정성) · 파운드리(정성, −1.5조 인용) · 주주환원(정성). 선택 근거가 질문에 보인다 | 중~대 |
| **R3** 민감도 (첫 기능) | `engine/sensitivity.py` DRAM ASP 체인 · 편집 폼 「민감도 입력」(DRAM 매출·k) · 값 줄 표기 · CLI `mindmap sensitivity <맵> --driver A1.1 --delta 10` | 삼성전자 2027E 손계산과 일치(테스트), 입력이 빠지면 "미계산 — 필요 입력" | 중 |
| **R4** 세그먼트 표 | `extract.segments`(DS/메모리/DRAM/NAND/파운드리/MX × 매출·OP × 기간) · Claim 본문 수치와 교차 확인 · R3 의 DRAM 매출 자동 입력 | 삼성전자 3건 중 2건 이상에서 DRAM 또는 메모리 매출이 잡히고 Claim 의 110.1조와 맞는다 | 대 · **가장 위험**(증권사별 표 양식) |
| **R5** 평가 | `tests/golden/drivers/<ticker>.json`(기대 원인 드라이버) · `eval-drivers`(선택 일치율) · 민감도 부호·자릿수 검사 · holdout(NAVER·두산) 의 섹터 원인 KPI 정의 | 삼성전자 5/5, NAVER·두산 첫 측정 | 소 |

R1→R2→R3 가 첫 기능까지의 최단 경로(R3 는 DRAM 매출을 사용자 입력으로). R4 는 그 뒤.

## 6. 받지 않는 것 · 보류

- 민감도를 LLM 에 묻는 것 — 금지(원칙 5). 프롬프트에 민감도 항목을 넣지 않는다.
- 드라이버 자동 확정 — 선택은 점수 + 근거까지, 확정은 사람(V12 편집). "리포트에 많이 나온 순서" 로 고르지 않는다.
- 시나리오 자동 구성 — v4 V5 그대로(후보 → 사람).
- HBM4 단독 출하·수익성 수치 — 공개되지 않으므로 HBM 전체 + 정성 대리 지표. "많이 팔리는가" 와 "이익이 남는가" 는 B/G 와 ASP·믹스로 나눠 적는다.
- 매출·영업이익·EPS 삭제 — 하지 않는다(A3.7 로 이동).

## 7. 정해야 할 것 (사용자)

1. KR-SEMI 원인 드라이버 5개의 라벨·순서를 제안대로 쓸지 — DRAM ASP · HBM · 메모리 원가 · 파운드리 · FCF·주주환원.
2. 첫 기능의 DRAM 매출: **사용자 입력으로 먼저(R3)** 가고 세그먼트 표(R4) 는 뒤에 — 권고안 — 인지, R4 를 먼저 할지.
3. 전가율 k 의 기본값 1.0 을 쓰고 "기본값" 으로 표시할지, 아니면 입력 전까지 "미계산" 으로 둘지.
4. 결과 지표의 자리 — A3.7 「재무 전망」(권고) 인지 A1 아래 하위 묶음인지.
5. 다른 섹터(인터넷 NAVER · 원전 두산) 의 원인 KPI 5개는 그 종목 맵을 만들 때 정의(섹터 사전에 추가) 하는 것으로 할지.

## 8. 다른 회사에도 같은 방식이 되는가 (2026-10-24 추가)

**된다 — 파이프라인이 회사별이 아니라 섹터·Claim 기반이기 때문이다.** 회사마다 손으로 드라이버를 적는 것이 아니라, ① Claim 추출이 모든 리포트에서 `driver_id`(사전 매칭, 없으면 LLM 자유 라벨 → `lbl_` 테마) 와 `quant_refs` 를 뽑고 ② 쟁점 표가 증권사 간 테마를 묶고 ③ 원인/결과 판정은 사전의 `role` 로 하므로, 새 회사는 **그 섹터 사전이 있으면 바로**, 없어도 Claim 테마만으로 후보가 나온다. PC 의 Claim 저장본(NAVER 6건 · 두산에너빌리티 4건) 으로 확인한 것:

| 종목 (섹터) | 쟁점 표의 테마 → 원인 드라이버 후보 | Claim 에 이미 있는 원인 변수 숫자 (증권사별) | 결과 지표(→ A3.7) |
|---|---|---|---|
| NAVER (KR-INTERNET, 리포트 6건 · Claim 103) | 광고 성장(핵심 이견 4곳) · 커머스(핵심 이견 5) · 핀테크·결제(공통 2) · AI 데이터센터·클라우드(공통 5, 정성) · 마케팅·인프라 비용(공통 5) · 규제·정책(이견 2) | 광고 매출 YoY 3Q26: 교보 +4.5 · 유진 +6.3 / 커머스(서비스) 매출 YoY: 교보 +10.2 · 유진 +12.0 / 핀테크 매출 YoY: 교보 +15.9 · 유진 +15.5 / 영업비용 YoY: 교보 +16.7 · 유진 +16.7 · 하나 +17.6 / 마케팅비: 교보 YoY +28.2 · 한화 QoQ +7.8 / GPUaaS 가격 MW당 300억(메리츠). **사업부 매출 절대액도 Claim 에 있다**(광고 1조4,866억 · 서비스 4,689억 · 파이낸셜 4,936억 — 유진) → 민감도 입력을 표 없이 채울 수 있다 | 실적·영업이익 · 영업이익률 · 매출 성장 · 추정치 변경 · 밸류에이션 배수 |
| 두산에너빌리티 (KR-UTIL, 4건 · Claim 92) | 원전 수주 · 수주잔고/신규수주 · 가스터빈 · SMR · 자회사 실적 · 주주환원 | 신규수주 1H26: 대신 7.1조 · iM 7.1조 / 2026 수주 가이던스 13.3조(대신) / 수주잔고 2025 23.2조 → 1H26 26.4조(iM) / 가스터빈 2028 12기(대신) · 수주 4.7조(삼성) / SMR CAPA 20모듈/년(iM) / 원전 용량 31.3GW(대신) | 실적 · 밸류에이션(EV/EBITDA 2030E·2035F, WACC) · 추정치 변경 |

따라서 삼성전자용 특수 코드가 아니라 다음 **일반 규칙** 넷으로 구현한다.

1. **원인/결과는 사전의 `role`** — 결과 = 재무제표·배수 지표(`earnings` · `estimate_revision` · `revenue_growth` · `opm` · `valuation_multiple` 과 표 지표 revenue/op/np/eps/opm/per…). 그 밖의 driver_id 와 `lbl_` 테마는 전부 원인 후보.
2. **원인 변수 셀의 키 = (driver_id, 단위, 기간, YoY/QoQ/수준)** — Claim 의 `metric` 이름은 증권사마다 다르지만(`ad_revenue_yoy` vs `revenue_yoy`), 이 네 가지로 묶으면 "광고 매출 YoY 3Q26: 교보 4.5 · 유진 6.3" 같은 증권사별 셀이 회사·섹터와 무관하게 생긴다. `CAUSE_METRICS` 사전은 라벨을 예쁘게 하고 동의어를 합치는 보조이지 전제가 아니다.
3. **민감도는 드라이버 종류별 템플릿** — 가격·물량(사업부 매출 × Δ% × k) · 비용률(매출 × Δ원가율) · 직접 손익(적자 축소액) · 환원(ΔFCF × 환원율 / 주식 수) · 수주(수주 × 매출 전환율 × 마진 — 두산 같은 수주 산업용). 회사마다 다른 것은 "어느 사업부 매출을 쓰는가" 뿐이고, 그 매출은 Claim 의 사업부 매출(위 NAVER) → 세그먼트 표(R4) → 사용자 입력 순으로 찾는다.
4. **섹터 사전이 없는 회사** — 테마는 `lbl_` 자유 라벨로 묶이고(difflib) 선택 점수는 쟁점 표 기준으로만 매긴다. 질이 떨어지는 만큼 요약에 "섹터 사전 없음 — 드라이버 라벨은 리포트 표현 그대로" 를 적고, 그 종목을 만들 때 섹터 사전 항목(원인 KPI 5개 + 동의어) 을 추가하는 것을 운영 규칙으로 둔다 (§7 결정 5).

**한계**: ⓐ 정성 중심 섹터(바이오 임상, 로봇 양산) 는 드라이버가 `qualitative` 가 되고 민감도는 선형 체인이 아니라 A3 시나리오 확률로 다룬다. ⓑ 증권사 1곳만 숫자를 준 변수는 셀이 아니라 "독립 주장" 으로만 보인다(≥2곳이어야 컨센 셀). ⓒ Claim 추출 품질에 종속 — 두산 하나증권 1건은 Claim 10개 전부 `driver_id` 가 비어 있었다(LLM 이 드라이버를 안 적음) → 키워드 폴백(`normalize_driver`) 을 추출 뒤에 한 번 더 돌려야 한다(R2 에 포함). ⓓ YoY 와 QoQ, 십억원과 조원을 한 셀에 섞지 않도록 키에 넣는다(V4 와 같은 원칙).

## 9. 구현 결과 (2026-10-24, R1~R5 완료 — 테스트 183 → 194)

| 단계 | 구현 | 실측 (삼성전자 리포트 PDF 3건 + c3 Claim 저장본) |
|---|---|---|
| R1 계약·템플릿 | `Driver` + metric · period · kind(quant/qualitative) · role(cause/result) · proxies · invalidation_condition · invalidation_rule · sensitivity · sensitivity_inputs; `Sensitivity`/`SensitivityInput`; `Kpi`/`DriverDef.role`, `RESULT_DRIVER_IDS`; 템플릿 **A3.7 재무 전망** + A3.7.1 매출액 · .2 영업이익 · .3 EPS · .4 영업이익률; `validate_new_map` — quant 는 current·my_estimate, qualitative 는 check_indicator/proxies + invalidation_condition; B 스냅숏은 at_entry None 허용 | 옛 맵(gaon_A, NAVER) 그대로 읽히고 렌더링됨 (role=None = 판정 전) |
| R4 세그먼트 표 | `reports/segments.py` — 기간 머리 줄 + 블록 라벨(매출액/영업이익/영업이익률/매출비중) + 사업부 행, `SEGMENT_ALIASES` 로 이름 정규화, 금액은 억원 · 비율은 %; `ReportFacts.segments` → `ReportDigest.segments` (증권사별 셀, 접미 없는 기간은 …P/…E 합침) | **3건 전부** 잡힘 — IBK 297셀(십억원) · 유진 363셀(조원, HBM 행 포함) · DS 요약 132 + 상세 297셀. DRAM 매출 2027E: IBK 486.1조 · 유진 589.1조 · DS 546.9조. Claim 본문과 일치: 유진 메모리 OP 3Q26 110.1조 · Foundry −1.5조 · DS DRAM 83.8조 |
| R2 원인 변수 | `generate/cause.py` — `cause_cells`(driver_id · 변수 · 측정 · 단위 · 기간 · 확실성; `change` 는 분기면 QoQ 로), `pick_cell`, `fill_cause_drivers`(섹터 원인 KPI 순서 → 없으면 쟁점 테마 점수), 사업부 표로 손익 드라이버 보강, 선택 근거를 질문에, A4.5 조기 경보 지표 갱신; `rules.fill_drivers` 는 결과 지표를 A3.7 로; mapping 의 A2.1.x ↔ A1 링크는 driver_id 로; Claim 에 driver 가 없으면 명제 키워드 폴백 | A1.1 DRAM ASP (QoQ 3Q26) 컨센 **20%** (유진 21 · DS 19) · A1.2 HBM (B/G QoQ 3Q26) 25% (유진) · A1.3 메모리 원가 정성 · A1.4 파운드리 손익 (op 4Q26) 현재 −1.66조(3Q26 잠정, 3곳) → 컨센 −1.27조(4Q26E, 3곳) · A1.5 FCF·주주환원 정성. 결과 지표 A3.7: 분기 영업이익(3Q26) 53.7 → 107.4조, 연간 매출 971.5조(2027E), EPS 83,320원, OPM 61.6% |
| R3 민감도 | `engine/sensitivity.py` — 템플릿 price_volume · cost_ratio · direct_op · payout · orders, 공통 꼬리(t · N · PER 유도), 입력 출처 꼬리표, `미계산 + missing`, 템플릿별 기본 충격(10% / 원가율 1%p); `financial_snapshot`·`segment_snapshot` 이 표 숫자를 `meta.draft` 에 남겨 편집 뒤 재계산; 노드 줄 "민감도 +10% → 적정주가 +47,316원 (+18.1%) · k 기본값"; 편집 폼 `sensitivity_inputs`; CLI `mindmap sensitivity` | DRAM ASP +10% → 영업이익 +54.1조 → EPS +7,438원 → 적정주가 **+47,316원 (+18.1%)** (입력: 사업부 표 1 · 기본값 2 · 유도 3; t=17.2%, N=60.2억주, PER 6.36 = 530,000/83,320); HBM +10% → +9,372원 (+3.6%); 메모리 원가 +1%p → −6,674원 (−2.5%); 파운드리 +10% → +50원; FCF·주주환원 미계산(FCF 입력 필요 — 표에 현금흐름 행 없음). k=0.8 로 바꾸면 +37,853원 |
| R5 평가 | `tests/golden/drivers/005930.json`(기대 5 · 금지 결과 지표 · 수치 3 · 민감도 3), `evals.eval_drivers`, CLI `mindmap eval-drivers`; `tests/test_v5.py` 11건 | **5/5 · 순서 일치 · 결과 지표 혼입 0 · 수치 3/3 · 민감도 3/3** |

**실측에서 바로잡은 것**: ⓐ Claim metric 이름 `hbm_bg_qoq` 는 `\b` 경계가 밑줄에 안 먹어 ASP 로 잘못 묶였다 → 밑줄을 경계로 보는 패턴. ⓑ DS 의 "ASP 19% 상승"(change) 과 유진의 "+21% qoq" 가 다른 셀이었다 → 분기 기간의 change 는 QoQ. ⓒ 옛 섹터 KPI(MAU·테이크레이트) 가 `role=cause` 기본값이라 NAVER 의 A1 을 차지했다 → `driver_id` 가 있는 v5 KPI 만 A1 후보, 나머지 섹터는 쟁점 테마로(결정 ⑤). ⓓ `draft --mode replace` 가 `--claims cached` 여도 저장본을 치웠다 → cached 면 그대로 재사용(구조만 바꿀 때 LLM 비용 없음).

**남은 것**: 다른 섹터(NAVER · 두산) 의 원인 KPI 5개 정의(결정 ⑤ — 테마 경로로는 지금도 나온다: NAVER 광고 성장 · 커머스 · 마케팅·인프라 비용 …); 현금흐름 행(`영업활동현금흐름`·`CAPEX`) 표 추출로 FCF 템플릿 자동화; Claim 프롬프트 c4 에 사업부 수치의 `segment` 축; 변경안(V7) 의 원인 드라이버 비교는 같은 KPI 순서라 id 로 맞물린다 — 실측은 다음 리포트 때.
