# 마인드맵 자동 생성 — 구현 계획 v1

> 목표: **종목코드 하나 → 검증을 통과하는 A 맵 초안**(논제·드라이버·시나리오·무효화 조건·촉매·근거, 전부 출처 포함)을
> 자동으로 만들고, 보유 종목(B 맵)은 시장 상태·사실 로그·상태등을 **사람 손 없이** 갱신한다. 사람이 하는 일은
> 「내 추정」과 「판단」을 확정하는 것뿐이다.
> 작성 2026-10-20. 첫 골든 예시: `examples/samsung_005930/` (삼성전자, 손으로 만든 결과물의 모양).

## 0. 원칙 (템플릿 §11 의 분업을 그대로)

| 누가 | 하는 일 | 하지 않는 일 |
|---|---|---|
| 수집기 | 사실을 가져와 `Source(name, url, retrieved_at)` 를 붙인다 | 해석 |
| 규칙 채움 | 숫자로 결정되는 노드를 계산한다 (현재가·밴드 백분위·리비전·수급·촉매 일정·재무 요약) | 판단 |
| LLM 채움 | 노드의 `question` 에 답한다 (논제·드라이버 선정·뷰·시나리오 가정·리스크·근거 요약) | 숫자를 지어내기 — current 값은 규칙 채움이 고정 |
| 엔진 | 기대수익률·손익비·비중·검증·게이트·상태등·매트릭스 | — |
| 사람 | 「내 추정」 확정, 판단(BUY/WATCH/PASS), 결정 기록 | — |

자동으로 채운 값은 전부 `status=estimated` 또는 `unverified` 에 `tags=["draft"]` 를 달고, 사람이 저장하면 `draft` 가
떨어진다. 자동 생성 맵의 판단(A6.5)은 **항상 WATCH** — BUY 는 사람만 쓴다.

## 1. 파이프라인

```
ticker ─▶ ① Fact Pack 수집 ─▶ ② 노드 채움 (규칙 → LLM → 교차검증) ─▶ ③ 엔진 (평가·검증·게이트) ─▶ ④ 저장·렌더·콘솔
                                                                                                   │
B 맵 ◀── ⑤ 운영 루프 (일일 배치: 시장 상태·사실 로그·드라이버·상태등·알림·C 롤업) ◀────────────────┘
```

삼성전자 예시를 손으로 만들며 실제로 한 일이 곧 ①~③의 명세다.

| 손으로 한 일 | 자동화 모듈 |
|---|---|
| 리서치 DB `prices` 에서 종가·52주 고저·월별 흐름 조회 | ① `ResearchDbSource.prices()` |
| `reports`+`report_details` 에서 최근 90일 목표가 분포(21건, 35만~63만, 평균 51.6만)·의견·제목 | ① `ResearchDbSource.consensus()` |
| 뉴스룸 3Q 잠정실적, 기사 3건(KB 전망·외국인 수급·CXMT) 읽기 | ① `NewsSource` / `DisclosureSource` + LLM 요약 |
| 현재가·밴드 백분위·리비전·추세·외국인 순매수 노드 채움 | ② 규칙 채움 |
| 드라이버 3개 고르고 current/consensus 채움, 논제·뷰·시나리오 가정·리스크·Kill 조건 작성 | ② LLM 채움 (섹터 KPI 사전 + Fact Pack) |
| 확률 합·목표가 순서·컨센 분포와의 거리 확인 | ② 교차 검증 |
| `evaluate_new_map` · `validate` · rr_min 게이트 | ③ 엔진 (이미 있음) |
| `store.save_map` · `write_html` · 콘솔에서 열기 | ④ (이미 있음) |

## 2. ① Fact Pack — 수집 결과의 고정된 모양

`collect/factpack.py` 의 pydantic 모델. 모든 항목은 값 + `Source`. 비어 있어도 된다(없으면 그 노드는 `unverified` 로 남는다).

```
FactPack
  ticker, name, market, sector_code, as_of
  price      : last, date, chg_1d, high_52w, low_52w, ret_1m, ret_3m, volume, adv_20d, market_cap, shares
  financials : [분기: 매출, 영업이익, 순이익, QoQ, YoY]  (최근 4~8분기)
  consensus  : target_prices[(date, broker, tp, prev_tp, opinion, title)], tp_mean/min/max/n (90일),
               opinion_dist, eps_fwd_12m, revision_3m(up/flat/down), tp_mean_3m_ago
  valuation  : per_now, pbr_now, per_band_pct(역사 분포 백분위), pbr_band_pct
  flows      : foreign_net_20d, inst_net_20d, foreign_net_mtd, short_ratio
  disclosures: [(date, title, flag_risk, flag_event)]  (최근 60일)
  news       : [(date, source, title, url, summary?)]   (최근 30일)
  calendar   : [(date, label, kind)]  — 잠정실적·확정실적·주총·배당 기준일 (통상 일정 + DART 일정)
  peers      : [(code, name, per, pbr, market_cap)]
  kpis       : {kpi_key: (value, as_of, source)}  — 섹터 KPI 사전에서 수집 가능한 것만 (예: 분기 영업이익)
```

소스 우선순위와 어댑터:

1. **리서치 DB** (`C:\kstock\research\data\db\kstock.sqlite`, 읽기 전용) — `prices`, `reports`, `report_details`, `disclosures`, `signal_events`, `features`. 이미 2,700종목×시세, 3.5만건 리포트가 있다. **1차 소스이자 M1 의 전부.** `collect/adapters/research_db.py`.
2. **DART** — 분기보고서 본문(매출·영업이익·사업부별), 공시 일정. `collect/adapters/dart.py` (골격 있음).
3. **컨센서스 웹** — 네이버 금융 종목분석 / 한경 컨센서스(리서치 프로젝트가 이미 받는다 → DB 로 들어오므로 2차).
4. **뉴스** — 네이버 뉴스 검색 또는 LLM 웹검색. 제목·URL·날짜만 수집하고 요약은 LLM.
5. **캐시** — `data/cache/factpack/<ticker>/<YYYY-MM-DD>.json`. 같은 날 재생성은 캐시로.

## 3. ② 노드 채움

### 3a. 규칙 채움 (`generate/rules.py`) — LLM 없이 결정되는 노드

| 노드 | 계산 |
|---|---|
| A0.4 현재가 | price.last + Source |
| A1.x `driver.current` | 섹터 KPI 사전의 `collector` 로 Fact Pack `kpis` 에서 (예: 분기 영업이익 = financials[-1].op) |
| A1.x `driver.consensus` | 컨센서스가 있는 KPI 만 (예: 4Q OP 컨센) |
| A3.6.1 역사 밴드 백분위 | per 시계열(prices.close / eps_ttm) 의 백분위 — `KrxPriceSource.percentile` 재사용 |
| A3.6.3 EPS 리비전 | consensus.revision_3m (tp_mean 3개월 변화 ±3%) |
| A5.1 촉매 | calendar → Event (잠정실적 = 분기 말 +7일, 확정 = +30일 통상 일정, DART 일정 있으면 대체) |
| A5.2 수급/기술 | 추세(20일 vs 60일 평균), 거래량 배수, 외국인/기관 20일 누적 |
| A8.1.4 재무 요약 | financials 최근 분기 문자열 |
| A8.2 / A8.3 링크 | 섹터 매핑 테이블(`symbols.csv` sector → `S.<코드>`), `M0` |
| A3.1~3 **앵커** | 목표가 분포로 초기값: Bear = min(tp)×0.9 또는 52주 저가, Base = 분포 하위 1/3, Bull = 평균 — LLM 은 이 앵커를 받아 가정을 쓰고 ±조정만 |

### 3b. LLM 채움 (`generate/prompts.py`, `llm/filler.py` 확장) — 노드 그룹별 전용 프롬프트

지금의 `fill_map` 은 단일 값 노드만 채운다. 그룹 단위로 **JSON 스키마를 강제**하는 프롬프트를 추가한다.

| 그룹 | 입력 | 출력 JSON |
|---|---|---|
| 드라이버 선정 | 섹터 KPI 사전 후보 + 리포트 제목 20개 + Fact Pack | `[{kpi_key, label, unit, higher_is_better, my_estimate, rationale, check_indicator, next_check}]` 3~5개. **current 는 규칙 채움 값으로 덮어쓴다** |
| 논제 (A0) | 드라이버 + 컨센서스 + 밸류 | `{thesis, horizon, confidence, invest_type}` |
| 뷰 (A2) | 밸류 + 뉴스 + 리포트 제목 | `{implied, mine, why ∈ 시간축/정보/심리/구조}` |
| 시나리오 (A3) | 앵커 + 드라이버 추정 | `{Bear/Base/Bull: {probability, target_price, assumptions[], method}}` |
| 리스크·Kill (A4) | 드라이버 + 뉴스 + 공시 flag | `{risks:[{text, probability, impact_pct}]×3, kill:[{driver_key, op, threshold, note}], early_warning}` → `build_condition` 으로 조건식 |
| 근거 (A8.1) | 공시 본문 요약 + 리포트 | `{business_model, moat, governance_payout}` |

프롬프트 공통 규칙은 `llm/prompts.py` 의 SYSTEM_FILLER 그대로: 모르면 null, 출처 없는 사실은 confidence L, 한국어.

### 3c. 교차 검증 (`generate/check.py`)

- 확률 합 1.0, Bear < Base < Bull, Bear ≤ 현재가 ≤ Bull (아니면 플래그).
- 목표가가 컨센서스 분포 [min×0.7, max×1.2] 밖이면 `warn` 태그 — 틀렸다는 뜻이 아니라 **사람이 봐야 한다**는 뜻.
- 드라이버 `current` 는 LLM 값이 아니라 규칙 채움 값 (LLM 이 숫자를 지어내지 못하게).
- `my_estimate` 는 전부 `rationale` 앞에 "초안" 을 붙인다.
- 리포트가 전부 매수(삼성전자 21건 중 20건 매수)인 종목은 A2.1 에 "컨센서스 편향: 매도 의견 0건" 메모를 자동으로 단다.

## 4. 섹터 KPI 사전 (`generate/kpi_catalog.py`)

드라이버 후보는 섹터마다 정해져 있다. 사전이 없으면 LLM 이 매번 다른 변수를 고른다.

```
KR-SEMI   분기 영업이익(조원, 수집: financials) · DRAM 계약가 변동률(%, DRAMeXchange) · HBM 점유율(%, 트렌드포스/컨콜)
          · 설비투자(조원) · 재고일수
KR-BATT   셀 출하량(GWh) · 양극재 판가 · 수주잔고(조원) · 가동률
KR-ROBOT  수주잔고 · 양산 가동률 · 영업이익률
KR-AUTO   글로벌 판매량 · ASP · 인센티브 · 환율
KR-BIO    임상 단계(enum) · 매출 성장률 · 기술이전 마일스톤
KR-BANK   NIM · 대손비용률 · CET1 · 주주환원율
(기본)    매출 성장률 · 영업이익률 · 컨센서스 EPS 리비전
```

항목마다 `label, unit, higher_is_better, collector(fact pack 경로 또는 None=수동), check_cadence`. LLM 은 후보 중 3~5개를 고르고 최대 1개를 새로 제안할 수 있다.

## 5. ③④ 엔진·저장·콘솔 (대부분 이미 있음)

- `evaluate_new_map` → ER·RR·제안 비중·게이트. 게이트 미통과면 WATCH 유지 + 사유 노트.
- `validate` 오류 0 이어야 "초안 완료". 오류가 남으면 콘솔 개요 탭에 "자동 생성이 채우지 못한 노드" 목록.
- 콘솔: 「작업 → A 맵 자동 생성(초안)…」 — 종목 고르고 누르면 진행바(수집 → 규칙 → LLM 6~8회 → 검증), 끝나면 A 탭에 로드.
  `draft` 노드는 라벨 앞에 ✎ 표시, 사람이 저장하면 떨어진다. LLM 키가 없으면 규칙 채움까지만 하고 "LLM 없이 생성" 로 표시.
- CLI: `mindmap draft 005930 [--no-llm] [--save] [--out …]`.

## 6. ⑤ 운영 루프 자동화 (B 맵)

`mindmap daily` 배치 (리서치 콘솔의 「자동 실행 등록」 과 같은 Windows 작업 스케줄러 패턴):

1. 리서치 DB `prices` 최신일 → 모든 열린 B 맵 `update_market` (가격·거래량·밴드 백분위; 비중은 KIS 잔고 또는 설정 총자산으로).
2. `reports`/`disclosures` 신규 행 → 이벤트 분류기(규칙 → LLM) → `apply_event`. `flag_risk`/`flag_event` 는 materiality H.
3. 분기 실적 공시가 뜨면 financials 파싱 → `update_driver`(A1 의 financials 수집형 KPI 만). 수동 KPI 는 `next_check` 경과로 🟡.
4. `evaluate_holding` → 상태등 → 🟡🔴 만 알림(카카오/메일 — 리서치 프로젝트 `kakao_login.py` 재사용).
5. `rollup` → C 맵 → 콘솔 신선도 줄.

## 7. 평가 (evals) — 자동 초안이 쓸 만한가

골든 셋: 사람이 확정한 맵(1호 = 삼성전자). 자동 초안과 비교해 세 숫자를 본다.

- 드라이버 일치율: 골든 드라이버 중 자동 초안이 고른 비율.
- 시나리오 목표가 오차: |자동 Base − 골든 Base| / 골든 Base.
- 수정률: 사람이 저장하기 전에 고친 노드 비율 — **이 숫자가 낮아지는 것이 목표**다.

`generate/evals.py` 가 `data/golden/<ticker>.json` 과 비교해 표를 찍는다. 프롬프트·KPI 사전을 바꿀 때마다 돌린다.

## 8. 마일스톤

| | 내용 | 완료 기준 | 상태 |
|---|---|---|---|
| M0 (2026-10-20 완료) | **리포트 파일 입력 경로** — 사용자가 준 증권사 리포트 n건 → 추출(`reports/`) → 규칙 채움 + LLM 그룹 채움 + 교차검증(`generate/`) → `mindmap draft --reports` · 콘솔 「리포트로 자동 생성…」 · 텍스트 콘솔 | 픽스처 3건(하나·KB·키움 스타일)에서 검증 오류 0, 판단 WATCH, draft/warn 태그, LLM 환각 방어 테스트 | 완료 — 아래 §8a |
| M1 | Fact Pack(리서치 DB) + 규칙 채움 + `mindmap draft <ticker> --no-llm` (리포트 파일 없이 DB 의 `pdf_url` PDF 를 받아 M0 경로로) | 삼성전자·SK하이닉스·가온전선 3종목에서 검증 오류 0, 리서치 DB 복제본 테스트 | 일부 — `collect/adapters/research_db.py` (시세·52주·추세·DB 목표가) 는 M0 에서 보강용으로 들어감 |
| M2 | LLM 그룹 채움 + 섹터 KPI 사전 v1(반도체·2차전지·로봇·자동차·바이오·은행) + 교차 검증 | `mindmap draft 005930` 전체 초안; 골든(삼성전자) 대비 드라이버 일치 ≥ 2/3, Base 오차 ≤ 25% | 코드 완료(M0 안에) — 실제 API 로 골든 비교는 미실행 |
| M3 | 콘솔 통합(자동 생성 버튼, draft 표시, 진행바) | GUI 에서 종목 → 초안 → 사람 확정 → 저장 흐름 | 완료 (M0) |
| M4 | 일일 배치 + 알림 + 스케줄러 | 5 종목 B 맵이 사람 손 없이 매일 갱신, 🟡🔴 알림 도착 |
| M5 | 웹 어댑터(DART 본문·뉴스) + 캐시 | 리서치 DB 에 없는 종목도 초안 생성 |
| M6 | 평가 루프 | 골든 10종목, 수정률 추세 하락 |

### 8a. M0 에서 정한 것 (리포트 입력 경로)

- 입력은 **파일**이다 (PDF/TXT/MD/HTML/DOCX). 리서치 DB 의 PDF 다운로드는 M1 로 미룬다 — 네트워크·쿠키 문제와 분리.
- 추출은 규칙(정규식·표 파서) 이다. 실제 표지의 모양("세아제강지주 (003030) 목표주가 190,000원 / BUY 현재주가 104,500원",
  "Neutral I TP 45,000원 I CP 39,800원", "현재주가(26.8.19) 52,000원 투자의견 BUY(유지)", "목표주가 18만원 유지",
  투자의견 변동 내역 표) 를 테스트 픽스처로 박아 두었다. 2단 조판이 섞여도 「후보를 모아 표지 안 최다 빈도값」 으로 고른다.
- 드라이버는 추정치 표에서 바로 나오는 것(분기/연간 영업이익·매출·EPS) 만 규칙으로 채운다. 섹터 KPI(점유율·판가 등) 는
  LLM 이 리포트 인용(evidence) 을 붙여 제안해야 받는다. 숫자 환각 방어의 핵심.
- 시나리오 앵커: Bear = min(최저 목표가×0.9, 52주 저가) · Base = 목표가 분포 하위 1/3 · Bull = 평균. LLM 은 ±25% 안에서만 조정.
- 「내 추정」은 컨센서스와 같은 값의 초안이다. 다르게 보는 것은 사람이 쓴다 — 초안은 '증권사들이 말하는 것' 의 정리다.
- 평가 지표(§7) 는 아직 안 돌렸다: 골든(삼성전자 손 맵) 대비 자동 초안의 드라이버 일치율·Base 오차·수정률.

## 9. 모듈 배치

```
src/kstock/
  reports/loader.py                [M0 ✓] 파일 → 텍스트 (pypdf/pdfminer/pdfplumber/pdftotext, html, docx)
  reports/extract.py               [M0 ✓] 텍스트 → ReportFacts (의견·목표가·현재가·추정치 표·문장)
  reports/digest.py                [M0 ✓] n건 → ReportDigest (컨센서스·리비전·추정치 평균·문장 가산·억원/조원)
  collect/factpack.py              [M1] FactPack 모델 + 빌더 (어댑터 순서·캐시)
  collect/adapters/research_db.py  [M0 ✓ 일부] 리서치 DB 읽기 전용 (prices 시세·52주·추세, reports+report_details 목표가)
  generate/kpi_catalog.py          [M0 ✓] 섹터 KPI 사전 + 본문 단어로 섹터 추정
  generate/rules.py                [M0 ✓] 규칙 채움 (현재가·드라이버·앵커·뷰·리스크·Kill·촉매·실행·근거)
  generate/prompts.py              [M0 ✓] 그룹별 프롬프트 + JSON 스키마
  generate/llm_fill.py             [M0 ✓] GroupFiller / Mock / Claude · 그룹 적용 + 환각 방어
  generate/check.py                [M0 ✓] 교차 검증 (확률·순서·컨센 범위·단위 의심·편향 메모·WATCH 고정)
  generate/draft.py                [M0 ✓] 파이프라인 (reports → rules → llm → check → evaluate → DraftResult)
  generate/evals.py                [M6] 골든 비교
  ops.py                           [M0 ✓] peek_reports / draft_from_reports (저장·관심종목 등록)
  cli.py                           [M0 ✓] mindmap draft --reports … / [M4] mindmap daily
  console.py · console_text.py     [M0 ✓] 「리포트로 A 맵 자동 생성 (초안)…」 (파일 대화상자 → 종목 확인 → 진행바 → A 탭)
tests/test_reports_draft.py        [M0 ✓] 추출·합산·규칙·Mock LLM·Ops·CLI (픽스처 tests/fixtures/reports, PDF 는 make_pdfs.py)
data/golden/<ticker>.json          사람이 확정한 맵
```

## 10. 비용·리스크

- LLM 호출은 종목당 그룹 6개(드라이버·논제·뷰·시나리오·리스크·근거) + 뉴스 요약 1~2회. 입력 토큰은 Fact Pack 발췌 기준 3만 내외, 종목당 수십 초.
- 환각 숫자 → current 는 규칙 채움으로 고정, 목표가는 컨센 범위 검사, 모든 추정에 "초안".
- 자료 신선도 → `as_of` + 프로파일 `stale_days` 로 🟡 (이미 있음). Fact Pack 날짜를 맵 메타에 남긴다.
- 컨센서스 편향(매수 일색) → A2.1 자동 메모 + Bear 시나리오를 컨센 최저가 아래로 앵커.
- 과신 → 판단은 항상 WATCH, 자동 초안으로는 BUY 를 만들 수 없다.
