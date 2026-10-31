# kstock-mindmap — 주식투자 마인드맵 생성·대응 엔진

증권사 리포트를 읽어 **신규 투자 맵(A)** 초안을 만들고, 매수 뒤에는 **보유 종목 맵(B)** 으로 상태등·대응을 내고, 전체를 **포트폴리오 롤업(C)** 으로 합산하는 Python 패키지다. 세 맵은 하나의 노드 스키마를 쓰고, 기준 문서는 `docs/mindmap-template.md`(템플릿 v1.0) 이며 코드의 모듈·함수 이름은 그 문서의 섹션 번호를 따른다.

맵이 답하려는 질문은 네 가지다. ① 각 가격(현재가·목표가)이 전제하는 것은 무엇인가 ② 그 가격이 성립하려면 언제까지 무엇이 일어나야 하나 ③ 그것을 무슨 증거로 아는가 ④ 어떤 증거가 나오면 내 판단을 수정해야 하나. 코드는 표를 만들고 제안하며, **판단·확률·내 추정·역할·경계값 확정·제안 승인은 사람이 한다**. 자동으로 바뀌는 판단 상태는 "신규 BUY 보류" 플래그 하나뿐이고, 자동 생성 맵의 판단은 항상 WATCH 다. 이 도구는 판단 보조 도구이며 투자 조언이 아니다.

## 1. 한눈에 보기

| 맵 | 질문 | 누가 채우나 | 핵심 산출물 |
|---|---|---|---|
| **A 신규 투자** | 살 것인가 | 리포트 초안(코드) + 사람 | 논제 · 원인 드라이버(A1) · 성립 조건(A2.4) · 밸류에이션(A3) · 시나리오(A4) · 리스크·증거 계획·판단 수정 규칙(A5) · 촉매(A6) · 실행 계획·판단(A7) |
| **B 보유 종목** | 들고 있을 것인가 | 시장 상태(코드) + 논제 판정(사람) | 상태등 🟢🟡🔴 · 대응 매트릭스 기본값 · 주가 분해 · 결정 로그 · 편향 체크 |
| **C 포트폴리오** | 전체는 괜찮은가 | 코드(열린 B 맵 합산) | 비중·집중도(HHI) · 노출 · 순매수/순매도 · drift · 품질 지표 · 복기 |

코드에 박힌 원칙: 논제 중심 · 매입가 제거(평가손익 `B0.5` 는 `meta_only`, 판단 노드가 참조하면 검증 실패) · 숫자화와 사전 약속(규칙은 미리 적는다) · 선별(🟢🟡🔴 는 코드 규칙만) · 분리와 참조(A/B/C 는 링크로 잇는다). 대응 결정은 (논제 상태 × 밸류 상태 × 비중 상태) 세 입력만 받는 공통 매트릭스가 기본값을 내고, 다른 결정은 사유 없이는 기록되지 않는다.

## 2. 설치와 설정

conda 환경 `ksa` 를 그대로 쓴다 (Python 3.10 이상; 3.10 은 `StrEnum` 호환 shim 으로 지원).

```powershell
conda activate ksa
cd C:\kstock\mindmap
pip install -e ".[dev,reports]"  # pydantic + pytest + ruff + pypdf (editable 설치 → 소스 수정이 바로 반영)
# 선택: pip install -e ".[llm]"  # anthropic SDK (Claim 추출 · LLM 그룹 채움)
#       pip install -e ".[data]" # httpx, pandas (수집기 구현 시)
```

설치하면 콘솔 명령 **`mindmap`** 이 생긴다 (배포 이름 `kstock-mindmap`, import 이름 `kstock`). 설치 없이 쓰려면 `PYTHONPATH=src python -m kstock.cli …`.

### `.env` — API 키와 로컬 LLM

프로젝트 루트의 `.env` 에 둔다 (`copy .env.example .env`; git 제외). 값은 로그·화면 어디에도 찍히지 않고 출처만 보인다. 셸 환경변수가 이미 있으면 그것이 우선이라, 예전 키가 Windows 환경변수에 남아 있으면 `.env` 를 고쳐도 반영되지 않는다 — `mindmap llm-check` 가 충돌과 지우는 명령을 알려 주고, `KSTOCK_PREFER_DOTENV=1` 을 넣으면 `.env` 가 셸 변수를 덮는다.

```dotenv
ANTHROPIC_API_KEY=sk-ant-usr-…        # Claim 추출 · LLM 그룹 채움 (platform.claude.com 에서 발급; sk-ant-api03- 레거시도 됨)
ANTHROPIC_WORKSPACE_ID=wrkspc_…       # 키 범위가 '조직' 일 때만
KSTOCK_LLM_MODEL=claude-sonnet-5-5    # 선택 — 비우면 계정의 최신 Sonnet 자동 선택
KSTOCK_LLM_THINKING=off               # 선택 — Claude 5 계열의 사고(thinking) 끄기 (기본 off)
KSTOCK_PREFER_DOTENV=1                # 선택 — 이 파일의 키가 셸 환경변수를 덮는다

# 로컬 OpenAI 호환 서버 (DGX Spark 의 Ollama 등) — --llm-kind local, 또는 Claude 키가 없을 때 auto
LLM_BASE_URL=http://host.openshell.internal:11435/v1   # OpenShell 샌드박스 안: NemoClaw 프록시. 호스트 직접이면 http://127.0.0.1:11434/v1
LLM_MODEL=nemotron-3-nano:latest
LLM_API_KEY=local-ollama              # 샌드박스 안에서 비워 두면 NEMOCLAW_LOCAL_INFERENCE_TOKEN 핸들을 쓴다
LLM_THINK=none                        # 사고 끄기 (low|medium|high|auto)
LLM_MAX_TOKENS=8000
KSTOCK_OFFLINE=1                      # 가격·외부 조회를 하지 않는다 (네트워크가 막힌 곳)
KSTOCK_RESEARCH_DB=C:\kstock\research\data\db\kstock.sqlite   # 리서치 DB 경로 (기본 형제 폴더; off 면 쓰지 않음)
```

키가 실제로 동작하는지는 **`mindmap llm-check`** 로 본다 — 키 출처·계열·길이, 인증(모델 목록), 설정한 모델의 존재, 아주 작은 호출 1회. 콘솔 「작업 → LLM 연결 확인」, 텍스트 콘솔 「L」 도 같다. 패키지 없이 확인하려면 `python tools/check_api_key.py`. Claude 5 계열·anthropic SDK 1.x 의 인자 차이(temperature 제거, 사고 끄기 값, max_tokens 잘림) 는 코드가 알아서 맞춘다.

## 3. 빠른 시작

```powershell
mindmap demo -o out\demo                       # 가상 종목 '가온로보틱스' 로 A → B → C 전체 흐름 + HTML
mindmap draft --reports data\reports\삼성전자\*.pdf --db data\db\kstock.sqlite --html   # 리포트 여러 건 → A 맵 초안
mindmap edit A:005930:2026-10-28               # HTML 편집 서버 — 노드 고치기·추가·삭제가 맵 JSON 에 바로 저장
mindmap path A:005930:2026-10-28               # 성립 조건: 가정 표 · 역산 표 · 필요 경로 (A2.4)
mindmap evidence A:005930:2026-10-28 --node A1.1 --role core --band 5 --bear 95   # 역할·띠·경계 확정 → 증거 계획·신호등
mindmap rule add A:005930:2026-10-28 --driver A1.1 --grade shift --threshold 95 --pct 10   # 판단 수정 규칙
mindmap observe A:005930:2026-10-28 A1.1 --metric DRAM_revenue_index --period 26Q4 --value 112 --compare-at 26Q4 --source "실적 공시"
mindmap alerts A:005930:2026-10-28             # 제안 대기 · 🔴/🟡 · 일정 도래 · 관측값 노후
mindmap eval A:005930:2026-10-28               # 기대수익률 · 손익비 · 제안 비중 · rr_min 게이트
mindmap snapshot maps\A_005930.json --entry-price 258000 --qty 100 -o maps\B_005930.json   # 매수 → B 맵
mindmap market maps\B_005930.json --price 276000 --w-now 0.072 --eps-fwd 49500 --band 82 --total-assets 1e9
mindmap status maps\B_005930.json              # 상태등 + 매트릭스 기본 대응 + 주가 분해
mindmap rollup maps\B_*.json --cash 80000000 -o maps\C.json   # C 포트폴리오
mindmap render maps\B_005930.json -o out\B.html               # HTML (--layout radial|right, --format markmap|md|mermaid)
mindmap migrate --db data\db\kstock.sqlite --render        # 옛 맵(스키마 1.0) 을 2.0 노드 번호로 저장 (읽을 때는 자동 변환)
```

맵은 JSON 파일 하나다. 저장소(`--db`) 에 넣으면 맵 id(`A:005930:2026-10-28`) 로도 부를 수 있고, 에디터로 직접 채워도 된다(노드마다 `question` 이 적혀 있다). 명령 전체 목록은 `mindmap --help`.

## 4. 콘솔

관심종목을 올리고, 맵을 만들고, 보유 종목의 상태등을 보는 일상 작업은 콘솔에서 한다. tkinter 창(외부 의존성 없음) 과 번호 메뉴 텍스트 콘솔이 같은 기능을 제공한다.

```powershell
python tools\console.py                       # = mindmap console (GUI)
mindmap console --text                        # 텍스트 콘솔
```

```
┌ KSTOCK  마인드맵 콘솔                                                      준비됨 ┐
│ 관심종목 N · A 맵 N · B 맵 N (🟢🟡🔴) · 가장 가까운 확인일 · C 맵 날짜        판단 보조 도구입니다 │
├ 자료와 실행 ───────────────────────────────────────────────────────────────────┤
│ DB  data\db\kstock.sqlite                                              [다시 읽기] │
│ 관심종목  종목코드 [ ] 종목명 [ ] 시장 [KR] 섹터 [ ] 유형 [growth] 메모 [ ]  [추가] [추가 + A 맵] │
│ 보기  [전체 ▾]  N종목 · 주의 n                     [C 롤업] [모든 맵 렌더링] [모든 맵 검증] ▭ │
├ 관심종목과 맵 ─ 종목 · 이름 · 시장 · 섹터 · 유형 · A 맵 · B 맵 · 논제 · 비중 · 결정 · 다음 확인 · 메모 ┤
├ 자세히 ── [개요] [A 맵 채우기] [B 맵 운영] [마인드맵] [포트폴리오] [실행 로그] ──────────────┤
└ 고른 줄 요약                                [고른 맵 HTML 열기] [실행 로그 보기] [로그 지우기] [닫기] ┘
```

**흐름.** 관심종목 추가(종목코드나 종목명 **하나만** 적어도 나머지가 채워진다: `5930` → `005930 삼성전자`, `하이닉스` → `000660 SK하이닉스`; 여럿이면 시가총액 순 후보에서 고른다) → 「추가 + A 맵」 또는 「리포트로 자동 생성…」 → A 탭에서 채우고 「저장·검증」·「평가」 → 손익비 게이트를 통과하면 「매수 체결 → B 맵…」 → B 탭에서 시장 상태를 넣고 「평가」 → 매트릭스가 낸 기본 대응을 보고 「결정 기록」 → 「C 롤업」. 「개요」 탭은 평가 모델·회계 기준·증거·판단(v7) 과 다음 증거 일정을 한 줄씩 보여 준다.

**텍스트 콘솔 A 맵 메뉴 (2번).** 숫자 1~9 는 목록·생성·채우기·평가·스냅숏·렌더·삭제·리포트 초안·현재가 갱신, 글자는 다음과 같다. (Q 는 어디서나 종료 키라 메뉴 글자로 쓰지 않는다.)

| 키 | 하는 일 |
|---|---|
| **L** | LLM 연결 확인 |
| **C** / **P** | 시나리오 후보 넣기(증권사 자체 후보 → Bear/Base/Bull, 확률은 비움) / 예시 확률 30/45/25 (미검증 가정) |
| **V** / **E** | 평가 모델·시나리오(회계 기준 표 · 역산 표 · 매력도 · 신뢰도; SOTP 멀티플 입력) / 회계 기준 표만 |
| **D** | 성립 조건 — 가정 표 · 역산 표(한 변수 해 · 두 변수 조합) · 날짜별 필요 경로 → A2.4 |
| **O** | 관측값 입력 — 데이터 계약 8칸(직접) 또는 수집기·에이전트 JSON 파일 |
| **G** | 증거 계획·신호등·증거 일정·판단 수정 규칙·알림; 역할·띠·경계 확정, 규칙 추가, 제안 승인/거절, 플래그 해제 |
| **T** | Claim 품질 — 단계별 통계 · 기준 캐시 대비 비교 · 품질 게이트 |
| **K** / **I** / **R** / **S** / **N** | Kill 후보 확정 / 쟁점 표 / Claim 검토 대기함 / 노드 출처 보기 / 새 리포트 → 변경안 |

화면 코드(`console.py`·`console_text.py`) 는 위젯을 놓고 값을 모을 뿐이고, 폼 값을 맵으로 옮기는 일은 `ops.py` 가 한다. 그래서 tkinter 가 없는 환경에서도 `ops` 는 통째로 테스트되고, GUI 는 가짜 tkinter 위에서 스모크 테스트된다.

**저장 위치** 는 `data/` 아래다 (개인 데이터라 git 제외).

| 경로 | 내용 |
|---|---|
| `data/db/kstock.sqlite` | 관심종목 · 맵 인덱스 · 설정(현금 · 총자산 · 리서치 DB 경로) |
| `data/maps/*.json` | 맵 본문 (진실 원본; 인덱스는 「작업 → 맵 인덱스 재구성」 으로 다시 만든다) |
| `data/maps/versions/*.v<N>.json` | 변경안 적용·HTML 편집 저장 직전의 원본 |
| `data/claims/` · `data/issues/` · `data/changes/` | 리포트별 ClaimSet · 종목별 쟁점 · 변경안 |
| `data/out/*.html` | 렌더링 결과 |
| `data/ref/symbols.csv` | 종목 목록 스냅숏 (리서치 DB `prices` 약 2,800종목 → 「작업 → 종목 목록 갱신」) |

## 5. 리포트 → A 맵 초안

같은 종목의 증권사 리포트(PDF·TXT·MD·HTML·DOCX) 를 여러 건 주면 A 맵 초안을 만든다. 콘솔 「리포트로 자동 생성…」, CLI `mindmap draft`, 텍스트 콘솔 「A 맵 → 8」.

```
리포트 n건 ─▶ 읽기(loader) ─▶ 사실 추출(extract: 표지 · 추정치 표 · 사업부 표 · 회계 기준) ─▶ 수치 집계(digest)
          ─▶ Claim 추출(LLM, 명제 1개씩 · 인용 필수) ─▶ 3단 검증(인용 · 숫자 · 의미) ─▶ 검토 대기함 ─▶ 품질 게이트
          ─▶ 쟁점 통합(issues) ─▶ 원인 드라이버 선택(cause) + 규칙 채움 + 매핑 ─▶ 교차검증
          ─▶ 민감도 ─▶ 평가 모델·시나리오 ─▶ 성립 조건 · 증거 계획 · 규칙(v7) ─▶ 검증 ─▶ 저장 · HTML
```

```powershell
mindmap draft --reports *.pdf --ticker 005930 --db data\db\kstock.sqlite --html   # 규칙 + (키가 있으면) Claim 추출
mindmap draft --reports *.pdf --llm --llm-kind local                              # 로컬 LLM 으로 Claim 추출·의미 검증
mindmap draft --reports *.pdf --claims cached --mode replace                      # Claim 저장본 재사용, 맵만 새로
mindmap draft --reports *.pdf --claims-ref data\claims_llm                        # 기준 캐시 대비 재현율로 품질 게이트
mindmap claims --reports *.pdf --show · mindmap claims-review <report_id> [--recheck]   # Claim 만 · 검토 대기함
mindmap issues --ticker 005930 [--llm] · mindmap changes --map <id> --reports new.pdf   # 쟁점 표 · 변경안
mindmap golden-draft · eval-extract · eval-claims · eval-issues · eval-drivers · eval-suggestions   # 정답표와 평가
```

**무엇이 어디로 가나**

| 어디서 | 무엇을 | 들어가는 노드 |
|---|---|---|
| 리포트 표지·표 (규칙) | 증권사·날짜·의견·목표주가·현재주가·52주·추정치 표(+회계 기준)·사업부 표·발행주식수 | A0.4 현재가, A3.1 결과 지표(매출·OP·EPS 컨센), A3.5.3 리비전, A3.5.4 목표가 분포, A9.1.4 |
| Claim (LLM, verified 만) | 명제 · 역할(논거/리스크/촉매/밸류/주주환원) · driver · 메커니즘 · 방향 · 조건 · 시점 · 숫자 · 인용(페이지) | A0 논제 초안, A1 원인 드라이버의 컨센·현재값, A2.1.x 쟁점, A5.1~3 리스크(확률·영향 빈칸), A5.4 Kill 후보, A6.1 촉매, A9.4.x 리포트별 주장 |
| 시나리오 | 리포트가 Bull/Base/Bear 를 명시하면 그대로, 산식이 읽히면 그 리포트 안의 값만으로 재계산한 후보 | A4.2~4 은 기본 공란 — 콘솔 「시나리오 후보 넣기」 로 한 증권사의 후보를 고른다. 확률은 자동으로 넣지 않는다 |

**지키는 규칙**

- 서로 다른 증권사의 가정을 섞지 않는다(min(EPS)×min(PER) 같은 교차 조합 없음). 목표가 분포로 시나리오를 만들지 않는다.
- 확률 · 내 추정 · Kill · A2.2 내 뷰 · A2.3 은 사람이 쓴다. 초안은 비워 두고 **미확정** 으로 센다(오류가 아니다). 판단(A7.5)은 항상 WATCH.
- 모든 자동 노드는 `claim_id`·페이지로 원문까지 되짚는다(콘솔 「노드 출처 보기」). 증권사 컨센서스는 "현재 주가에 내재된 기대" 가 아니라 "리포트에서 관측된 것" 이다.
- Claim 에는 사실·전망·계획 구분(`certainty`: realized/announced/planned/expected/conditional, `asserted_by`) 이 붙고, 노드·표·인용에 `[실적]` `[기업 발표]` `[증권사 전망]` `[조건부]` 접두어로 보인다.
- LLM 이 없으면 키워드 문장 분류로 대체하고 `quality: rules`(저품질) 로 표시한다. 이때는 A0 논제·A2.1 쟁점·A5 리스크·A6 촉매를 만들지 않고 숫자 노드만 채운다(요약 머리에 "⚠ 규칙 모드").
- Kill 은 후보까지만(`meta.draft.kill_candidates`). 콘솔 「Kill 후보 확정」 에서 관측 지표·갱신 주기·위반 시 행동 세 칸을 채워야 Rule 이 된다.

**Claim 검증과 품질 게이트.** ① 인용이 원문에 그대로 있는가(코드) ② 숫자가 인용·표에 있는가(코드, 억/십억/조 환산) ③ 인용이 명제를 지지하는가(LLM 배치 판정). 셋 다 통과해야 `verified`, 아니면 검토 대기함에서 사람이 승인/반려(`claims-review`, 근거 불충분 승인은 `--note` 필수). 자동 통과해도 표에 없는 전사 수치, 증권사 간 15% 넘는 충돌, 조건부 리스크는 `review_required` ⚑ 로 함께 뜬다. 품질 게이트(`reports/claim_stats.py`): 핵심 Claim(원인 드라이버 셀 · 수치 · 조건부 · 추정치 변경) 검증률 < 50% 또는 기준 캐시 대비 원인 KPI 셀 재현율 < 70% 면 `needs_claim_review` — 초안은 완료로 표시되지 않는다. `mindmap claims-stats <폴더> [--ref 기준] [--json]`(미달이면 종료 코드 1) · `claims-compare` · 콘솔 「T」.

**수치 집계 규칙.** 증권사별 최신 1건만 집계(이전 건은 리비전 이력; 거기 없는 셀은 같은 증권사 45일 안 이전 리포트로 보강 `*`), 기준일보다 45일 넘게 오래된 리포트는 제외, 최신 실적 발표일 이전 리포트는 "실적 전" 표시, 파일명의 한경 리포트 번호로 리서치 DB `reports` 와 교차 확인.

**원인 드라이버(A1) = 원인 변수.** 매출·영업이익·EPS 는 결과라서 A3.1 로 가고, A1 은 섹터 사전의 원인 변수(KR-SEMI: DRAM ASP · HBM · 메모리 원가 · 파운드리 손익 · FCF·주주환원; KR-INTERNET: 광고 성장 · 커머스 · 핀테크 거래액 · 마케팅비 · AI/DC; KR-UTIL: 원전 수주 · 가스터빈 · 자회사 OP · 원가) 로 채운다. 숫자는 Claim 의 `quant_refs` 를 (driver, 변수, 측정 QoQ/YoY/수준, 단위, 기간, 확실성) 셀로 묶어 "DRAM ASP QoQ 3Q26: 유진 +21 · DS +19 → 컨센 20" 처럼 증권사별로 보이고, 숫자가 없으면 `qualitative` + 대체 관측 지표(지어내지 않는다). 사업부 표(`reports/segments.py`, 증권사별 이름 차이는 별칭 사전) 에서 사업부 매출·OP 를 읽어 **민감도**(변수 Δ → OP → EPS → 적정주가, 1차 선형 · 다른 변수 고정 · 입력마다 출처 · 빠지면 "미계산 — 필요: …") 를 계산한다. 드라이버끼리 더하지 말 것.

**현재가** 는 리포트 날짜의 값이 아니라 맵을 만드는 시점의 값이다: 네이버 금융 → 리서치 DB 종가 → 리포트 현재주가 순. `--no-fetch`, `KSTOCK_OFFLINE=1`. 작성일보다 3일 넘게 오래되면 ⚑ "갱신 필요".

**새 리포트 → 변경안.** 기존 맵은 그대로 두고 변경 후보(추정치 변화 · 새 쟁점 · TP 리비전 · 새 리스크·촉매) 만 보여 준다. 승인한 것만 복사본에 먼저 적용해 검증을 통과해야 들어가고, 원본은 `maps/versions/` 에 보존, A8.2 로그에 기록. 사용자가 손댄 노드는 자동 적용에서 뺀다. 콘솔 「리포트로 자동 생성…」 의 기본은 새로 만들기(기존 초안·Claim 저장본은 `_archive/` 로), 「기존에 더하기」 는 변경안으로 간다.

## 6. 밸류에이션 (A3) 과 시나리오 (A4)

두 가지는 **A1 드라이버 → 사업부 실적 → 평가 모델(A3) → Bear/Base/Bull = 드라이버 가정 묶음(A4) → 확률가중 가치 · 기대수익률 · 하방 · 안전마진 → A7 실행 계획** 으로 이어진다. A3 밸류에이션 = A3.1 재무 전망(결과 지표) · A3.2 평가 기준(사람) · A3.3 평가 모델(코드) · A3.4 모델 신뢰도·갱신 이력 · A3.5 밸류 근거; A4 시나리오 = A4.1 한눈에 · A4.2~4 Bear/Base/Bull · A4.5 기대수익률 · A4.6 손익비 · A4.7 확률가중 투자 매력도. (스키마 2.0, 2026-10-28 — 전에는 하나의 묶음 「밸류에이션 & 시나리오」(옛 A3) 였고 옛 A4~A8 은 A5~A9 가 됐다; 저장된 맵은 읽을 때 자동 변환, `mindmap migrate` 로 한꺼번에 저장.) 확률·멀티플·내 추정·가정은 사람 몫이고 LLM 은 숫자를 만들지 않는다.

- **회계 기준 정규화** — 증권사별 EPS 는 기준이 다르다(우선주 포함 주식 수 · 전체/지배 순이익). 평균하지 않고 `EPS^std = 지배 순이익 ÷ 공시 보통주` 로 맞춘 뒤 평균한다. 전체 순이익만 적은 증권사는 같은 표에 두 행을 다 적은 증권사의 **측정** 지배 비율(≥0.9) 로 환산(표시 '전체×비율'). 상태 `comparable` / `mixed`(경고) / `not_comparable`(A3.3 가격에 "⚠ 기준 불일치 — 참고값", 검증 오류, 신뢰도 L) / `unavailable`(발행주식수 없음 — 증권사 EPS 평균). 삼성전자 3건: 표 평균 83,320 → 정규화 86,693 · PER 6.11. 콘솔 「E」, `mindmap valuation` 의 "회계 기준" 표, HTML 계산기 머리말.
- **평가 모델** — PER 가격 = 기준 EPS(컨센 · 내 추정 · 정상화 · 사용자) × 적용 PER. 적용 PER 기본값은 목표가 중앙값 ÷ 컨센 EPS 로 "기본값(순환)" 표시 — 내 PER 를 넣어야 의미가 생긴다. 정상화 EPS 는 첫 추정 연도부터 3개 평균. SOTP = Σ 사업부 OP × (1−t) × 멀티플 + 순현금 (멀티플은 사용자 입력만, 포함 관계 메모리 ⊃ DRAM ⊃ HBM 은 자식을 뺀다).
- **역산은 조건부** — "PER ○(출처) · 다른 변수 고정 가정에서 현재가를 설명하는 EPS · 가격 드라이버 변화" 를 여러 PER 의 표로 보여 준다(목표가 유도 PER 면 "[가정 의존]", 사용자 PER 면 anchored). "내재" 라는 말은 쓰지 않는다 — 같은 가격을 설명하는 조합이 여럿이다. `mindmap implied <맵> --per 4.5/3.5`.
- **시나리오 한눈에 (A4.1)** — Bear/Base/Bull 바로 위에 코드가 쓰는 요약: 갈래마다 뜻 · 가정(드라이버 Δ) · 적용 PER · 확률 → 목표가 · 현재가 대비, 확률가중 V, 증권사 후보 목록, 빠진 것과 넣는 방법. HTML 상자에는 첫 줄만 보이고 우클릭 「상세 보기」 에 전부; 콘솔 「V」 와 `mindmap valuation` 머리에도 같은 표. 옛 맵은 밸류 묶음이 다시 돌 때(저장 · 「V」 · `valuation --save`) 생긴다.
- **시나리오 = 가정 묶음** — `Scenario.driver_assumptions`(A1.x 별 Δ% 또는 억원) · `per` · `eps_basis` · 조건 · 지표 · 반증. 통합 모델은 사업부마다 가격·물량 교차항을 포함해 한 번에 푼다(민감도를 더하면 빠지는 항). Bear ≥ 현재가면 ⚠ "하락 시나리오가 아니다".
- **확률가중 매력도 · 신뢰도** — V = Σ p·TP, 기대수익률(배당은 따로), 하방, 안전마진, 손익비. 신뢰도 H/M/L 은 입력 커버리지 · 증권사 수 · EPS 기준 혼합 · 데이터 나이 · PER/SOTP 차이 · 확률 미입력 등으로.
- **화면·명령** — HTML 편집 모드의 「밸류 계산기」(우클릭 ∑): 드라이버 × Bear/Base/Bull 가정 표 + 적용 PER·EPS 기준·확률 + A3.2. 계산은 로컬 편집 서버 `/preview` 가 한다(클라이언트에 식 복제 없음). `mindmap valuation <맵> [--eps-basis --per --shares --sotp 메모리=6 … --save]`, `mindmap scenario <맵> --set Bear.A1.1=-30 --set Bear.per=3.5 --probs 30/45/25 --save`, `mindmap sensitivity <맵> [--driver A1.1 --set pass_through=0.8]`, 콘솔 「V」. 저장 때마다 시나리오·평가 모델·매력도·신뢰도·v7 표가 다시 계산된다.

## 7. 성립 조건 · 증거 · 판단 수정 (설계 v7)

"증권사들이 얼마를 예상하는가" 를 넘어 "그 가격이 성립하려면 무엇이 일어나야 하며, 어떤 증거가 나오면 내 판단을 수정해야 하는가" 를 다섯 개 표로 답한다. 전부 코드가 만들고 사람이 고치며, 맵 JSON(`meta.draft.conditions` · `evidence_plan` · `lights` · `evidence_schedule` · `rule_events`, `meta.flags`, 드라이버의 `observations` · `required` · `band` · `bound_bear` · `thesis_role`) 에 저장된다.

| 표 | 무엇 | 어디서 보나 |
|---|---|---|
| **① 가정 표** | 증권사가 **적은** 전망 셀 × 목표가 · 컨센 · 내 가정(Base). 역산 숫자는 들어오지 않는다 | 콘솔 「D」 · `mindmap path` · HTML 「성립 조건」 |
| **② 역산 표** | 한 변수 해(통합 모델이 선형이라 두 점 닫힌 식; 해 없음 · 범위 밖 · 환원/수주는 역산 없음) · 두 변수 조합(핵심 수익 변수 × 적용 PER 격자, `--pair` 로 바꿈) | 콘솔 「D」 · `mindmap path [--price --pair A1.2]` |
| **③ 필요 경로** | 기준 가격(목표가 중앙값) 이 성립하려면 언제까지 변수가 어디에 있어야 하나 — ① Claim 분기 QoQ 셀 ② 사업부 표 분기 열(기준 = 오늘 이전 마지막 분기 = 100) ③ 연간 등비 보간(표시). 관측값이 쌓인 경로는 기준을 옮기지 않는다 | 콘솔 「D」 · A2.4 |
| **④ 증거 계획** | 드라이버마다 역할(필수·핵심·보조) · 관측 지표 · 최근 관측값 · 필요값 · 띠 · 경계 · 다음 확인일 · 신호등 | 콘솔 「G」 · `mindmap evidence` · A5.5 · A8.1 증거 일정 |
| **⑤ 판단 수정 규칙** | 드라이버 · 경계값 · 지속성(연속 n 회) · 등급(확률 이동 / 재검토 / 경고) · 제안 | 콘솔 「G」 · `mindmap rule` · A5.6 |

**관측값 데이터 계약.** 지표 · 기간 · 값의 종류(abs/pct/cum/state) · 기준(none/qoq/yoy/vs_consensus) · 값 · 관측일 · 비교 시점 · 출처, 여기에 검증 등급(확인됨 / 보도·추정 / 전망). 한 칸이라도 빠지면 저장되지 않고, 필요 경로와 **같은 자**(지표·종류·기준) 일 때만 신호등에 쓴다. 리포트의 전망 수치는 관측값이 아니다. 콘솔 「O」, `mindmap observe <맵> A1.1 --metric … --period 26Q4 --value 112 --compare-at 26Q4 --source …`, 수집기·에이전트는 `--from-file obs.json`.

**신호등.** 🟢 띠 안 / 🟡 띠 밖 · 띠 미입력 · 연속 2회 Bear 쪽으로 멀어지는 추세 · 관측 주기의 2배 넘게 오래됨 / 🔴 Bear 경계 초과 / ⚪ 증거 없음. 역할별로 세고 합산하지 않는다. ⚪ 가 드라이버 과반이면 "증거 없는 판단" 플래그. 역할·띠·경계는 코드가 **제안**(섹터 사전 첫 KPI = 핵심, 정성 = 필수 후보; 경계 = Base→Bear 가격 차이의 절반을 만드는 변수 변화, 띠 = 그 절반) 하고 사람이 확정한다(정성 드라이버의 경계는 상태·문장으로 사람이 적는다).

**규칙 엔진.** 감지(🔴) → 검증(확인된 관측값 · 같은 자 · 연속 n 회) → 영향(그 드라이버를 쓰는 시나리오 · 논제) → 제안 → **사람 승인**(확률 이동 %p 는 고칠 수 있다; 확률이 비어 있으면 적용되지 않는다). 자동으로 바뀌는 것은 `flags.hold_new_buy` 뿐 — 필수 조건 위반이 검증되면 켜지고 BUY 는 WATCH 로 강제된다(검증 오류 A7.5). 모든 단계는 A8.2 로그에 종류(observe · rule · approval · probability · thesis · flag) 와 함께 남는다. `mindmap rule add|list|decide|clear`, `mindmap alerts <맵> [--days 7] [--json]`(알림이 있으면 종료 코드 1 — 에이전트가 쓴다).

**운영.** `mindmap batch --reports-dir data\reports --maps-dir data\maps [--summary out\batch.md --json out\batch.json]` 은 종목별 하위 폴더마다 맵이 없으면 초안+HTML, 있으면 변경안(적용은 사람) 을 만들어 요약 한 장을 낸다. `mindmap export-knowledge data\maps\A_*.json --out out\knowledge` 는 종목별 Markdown(논제 · 드라이버 신호등 · 성립 조건 · 증거 계획 · 일정 · 플래그) 을 써서 에이전트 memory 로 쓴다.

**삼성전자 실측(리포트 3건, 2026-10-28).** 역할 제안 DRAM ASP 핵심 · 메모리 원가/FCF·주주환원 필수 · 나머지 보조. 필요 경로 DRAM 매출 지수 26Q3 = 100 → 27Q4 161.5(사업부 표 6점). 역산: 현재가 262,000 = DRAM ASP −57%(나머지 고정) 또는 적용 PER 3.02. 관측값이 없어 신호등은 전부 ⚪ → "증거 없는 판단". 관측값 26Q4 = 104 를 넣으면 🟢, 규칙(확률 이동, 경계 96.46) 뒤 27Q1 = 95 → 감지(1/2), 27Q2 = 93 → 검증 통과·제안 → `alerts` 에 "[제안 대기]" → `rule decide` 로 승인.

### 7.1 촉매 & 타임라인 (A6.1) — 2026-10-30

전에는 A6.1 이 「분기 말 + 8일」 달력 규칙 둘로만 채워졌다(출처 「통상 일정 기준 추정」). NAVER 의 4분기 발표는 2월 초인데 지도는 1월 8일이라 적었고, 그 날짜가 A8.1 체크포인트까지 복사됐으며, 같은 지도의 A9.4 에 이미 뽑혀 있던 `catalyst` Claim(「2027 상반기 55MW」 「2028년까지 200MW」) 은 날짜 정규식에 안 걸려 버려졌다. 지금은 **세 출처**를 합치고, 출처가 곧 등급이다.

| 출처 | 무엇 | 등급 (`certainty`) |
|---|---|---|
| DART 공시 본문 | 기업설명회(IR) 안내의 일시 · 유상증자결정의 납입일·발행가 확정일·신주 상장예정일 · 주주총회 일시. 목록은 리서치 DB `disclosures`, 본문은 OpenDART(`DART_API_KEY`, 리서치 저장소 `.env` 의 것도 읽는다; 캐시 `data/dart/`). 정정 공시가 이긴다 | 공시확정 · 공시상 예정 |
| 그 회사의 과거 공시일 | 리서치 DB 의 「영업(잠정)실적」 공시일 중앙값 오프셋으로 다음 발표일 추정. 4분기 확정은 사업보고서 90일 | 과거 주기 추정 (없으면 통상 일정 추정) |
| Claim (A9.4) | `catalyst` 역할 Claim 가운데 **시점**이 읽히는 것 — 날짜뿐 아니라 상반기·분기·연내 같은 기간도 (기준일 = 기간 끝, `period` 에 표기). 회사 발표·계획을 **리포트가 전한 것**은 「회사 계획 (리포트 인용)」 — 회사 원문(DART·IR 자료) 으로 확인한 뒤에야 「회사 목표」 | 회사 계획 (리포트 인용) · 리포트 전망 |

같은 사건이면 확실성이 높은 쪽이 남고 `affects` 를 넘겨받는다(`generate/catalysts.py: merge_events`). LLM 그룹 채움도 이제 A6.1 을 지우지 않고 합친다.

이벤트는 일정표가 아니라 **판단 항목**이다 — 칸이 늘었다 (`models/sub.py: Event`). 사건 전에 적는 것: `precheck`(사전 확인 지표) · `bull` / `bear`(상방·하방 증거) · `rule`(판단 규칙) · `next_check`(체크포인트, 없으면 날짜). 사건 뒤에 채우는 것: `outcome` — D-1 종가 대비 D+1·D+5 수익률과 같은 시장 종목 평균 대비(`ResearchDb.reaction`; 지수 자료가 없어 평균으로 대신)는 코드가 채우고, `verdict`(강화·약화·중립) 는 사람이 정한다. 사건 전 `direction` 은 「미정」, `priced_in` 은 「미판단」 으로 두고 사람이 바꾼다. 전부 편집 폼에 나오고 `edits.json` 으로 저장되므로 다음 생성에도 남는다 — HTML 을 직접 고치면 다음 초안에서 사라진다.

```
mindmap dart-events 035420                 # 과거 잠정실적 공시일 · 추정 일정 · 공시 본문의 확정 날짜 (맵 없이 미리보기)
mindmap catalysts A:035420:2026-10-30 --dart --outcomes   # A6.1 보기 · 공시확정 촉매 보충 · 지난 촉매 D+1/D+5 채우기 (저장)
mindmap dart-check 035420 005930 --days 365    # 공시 파서 적중률 — IR·유상증자·주총 본문마다 날짜가 읽혔나 (--cached-only 는 캐시만 · --all 은 OK 도)
```

**10-31 보강 (실제 생성본 검토 뒤).** ① 정정공시 원문은 셀마다 한 줄이고 「정정 전 / 정정 후」 날짜가 연달아 와서 첫 날짜를 읽으면 정정 전을 읽는다 — 줄 단위로 읽고 마지막(정정 후) 을 쓴다; 실제 원문이 `tests/fixtures/dart/` 에 있다. 납입일처럼 반드시 있어야 할 항목을 못 읽으면 메모에 남는다. ② `affects` 는 종류별 규칙표(실적 → A1 전부 + A3.1 · 유상증자 → A3.2/A3.3/A5.4 · 주총 → A9.1.3) 와 문장의 드라이버 낱말(AI 팩토리 → ai_dc → A1.5) 로 잇고, 종류별 상방·하방 조건 초안을 채운다 (`catalysts.link_catalysts`). ③ **회사가 발표·계획한 Claim 만 이벤트**가 되고 증권사 전망 Claim 은 같은 기간·같은 드라이버 이벤트의 bull/bear/precheck **증거**로 붙는다 (`mapping.apply_analyst_catalysts`); 제목은 시점이 든 절만. ④ `event_id`(`earnings:3Q26` · `rights:납입일` · `claim:<id>`) 로 **노드 번호를 고정**한다 — 새 사건이 끼어들어도 `edits.json` 의 `set A6.1.3` 이 다른 사건을 가리키지 않는다; A6.1 은 날짜순으로 보인다. `status`(scheduled·completed·delayed·cancelled) 가 생겼다 — 정정으로 날짜가 미뤄지면 delayed, 수익률이 채워지면 completed. ⑤ 「향후 6개월」 같은 상대 기간은 **리포트 날짜**부터 센다 (`~2027-04`); 법정 시한이 주말이면 다음 영업일; A8.1 은 중요도 H 거나 바꾸는 노드가 있는 사건만, HTML 증거 일정 표에 검증·후속·노드 열. 또 `A1.4 마케팅·인프라 비용` 에 영업이익 셀이 들어오던 것(비용 ↔ 이익 변수 혼동) 을 `cause.variables_compatible` 로 막았다.

**10-31 보강 3 (두 번째 생성본 검토 뒤).** ① **의미 검증** — 할인율·WACC·배수 같은 비율·평가 파라미터는 드라이버 값이 아니다 (`cause.is_relative_metric`): 「온디맨드 가격의 45% 할인」 이 「AI 데이터센터 ASP 45%」 로 들어가던 것을 막고, 그 문장은 드라이버의 「가정 메모」 로 남긴다 (`meta.draft.skipped_cells`). ② **증거 분리** — 증권사 전망 문장은 bull/bear 가 아니라 새 칸 `evidence`(관련 전망, (+)/(−) 표시) 에 붙고, bull/bear 는 판정 조건만. 실적·보고서 이벤트에는 기간이 맞고 실적을 말하는 문장만 붙는다 (실적 이벤트는 A1 전부에 걸려 어떤 전망이든 「일치」 하던 것). ③ **마일스톤** — Claim 수치 칸에 기간이 붙은 용량(55MW/1H27 · 100MW/2027E · 200MW/2028E) 이 있으면 이벤트 셋으로 쪼개고 `project`(드라이버 id) 로 묶는다; 연결은 제목만이 아니라 증거 문장까지 훑는다. ④ **번호** — 사람이 고친 노드가 없는 새 초안은 마지막에 날짜순으로 번호를 매기고(`renumber_fresh`), 그 뒤로는 고정. ⑤ A8.1 노드 본문에 「날짜 · 사건 — 검증 → 후속 [노드]」 줄; 체크포인트는 H 이거나 (affects 가 있고 출처가 공시·과거 주기·회사 목표) 인 사건만 (`event_matters`). ⑥ 예정 사건은 「예정」 표시, 노드의 confirmed 는 「자료 확인 상태」 로; 「증권발행실적보고서」 가 뜨면 납입 이벤트를 completed 로 (`apply_completions`).

**10-31 보강 4 (세 번째 검토 뒤).** ① **등급 하나 추가** — 「기존 목표인 2027 상반기 55MW」 처럼 증권사 리포트가 회사의 계획이라고 적은 것은 회사 원문을 본 게 아니므로 「회사 목표」 가 아니라 **「회사 계획 (리포트 인용)」** (`EventCertainty.회사계획인용`; 화면에는 「원문 미확인」). DART·IR 자료로 확인했거나 사람이 확인해 올린 것만 「회사 목표 (원문 확인)」. 순위는 회사 목표 > 회사 계획 (리포트 인용) > 리포트 전망. ② **마일스톤 판단 규칙** — 회사 계획·목표 이벤트에도 `rule` 초안이 든다 (「구축·달성만으로 수익성이 입증됐다고 보지 않는다 — 유상 계약, 가동률, 수익구조와 현금흐름이 확인되면 사업가치와 시나리오를 재평가한다 …」, `catalysts.DEFAULT_RULES`); 중요도 H 인 계획은 A3.3 평가 모델 · A4.1 시나리오에도 연결된다. 용량 마일스톤의 사전 확인은 「실제 가동·공급 개시 · 유상 계약 용량(MW)·계약 단가 · GPU 가동률 · EBITDA·FCF 기여」 — 구축은 수익이 아니다. ai_dc 드라이버의 관측 지표에 「유상 계약 용량 · GPU 가동률 · 계약 단가 · AI DC EBITDA·FCF」 가 들었다 (`kpi_catalog`). ③ **원문 보존** — A8.1 본문과 증거 일정 표는 사건·검증·후속 문장을 **자르지 않는다** (전엔 `[:28]`·`[:50]` 로 「… 유상 계약, 가」 처럼 끊겼다); 길이는 HTML 표의 줄바꿈이 감당한다 (`catalysts.checkpoint_rows`, `render/conditions.py` 의 `.sched` 표). ④ **낡음 표시** — `stale` 노드는 첨자 ⏳ 만이 아니라 값 앞에 「⚠ 낡음 (날짜)」 를 바로 적는다 (`render/format.STALE_MARK`). ⑤ **유효세율 가드** — `1 − NP/OP` 는 지분법·금융수익 같은 영업외손익이 섞여 세율이 아니다 (10-31 NAVER 지도: 2027E NP ≈ OP 라 t = 0.4%, SOTP 세후 OP 가 부풀었다). 띠 10~35% 밖이면 버리고 기본 22% 를 쓰며 입력 출처를 `default` 로, 사유를 note 에 적는다 (`valuation.TAX_BAND / TAX_DEFAULT`); A3.2 나 시나리오 `tax_rate` 가 있으면 그것. ⑥ **파서 점검 도구** — `mindmap dart-check <종목…>` 이 종목마다 IR·유상증자·주총 공시 본문을 전부 파서에 넣어 OK · 부분(납입일 같은 핵심 항목 누락) · 날짜 못 읽음 · 본문 없음을 세고 부분·실패 건을 접수번호·DART 링크와 함께 보인다 (`dart.check_items`); 부분·실패가 하나라도 있으면 종료 코드 1. 적중률은 과거 공시로 재므로 기본 365일.

**10-31 보강 5 (네 번째 검토 뒤 — A6 가 아니라 Claim 수치 → A1 드라이버 단계).** 실제 10-31 지도에서 넷을 확인했다. ① **돈의 방향** — 하나증권 「NAVER 가 18.7조원의 지급보증을 제공하며 … 네이버클라우드가 연간 **지급할** GPUaaS 이용료가 연 3조원」 의 3조원이 AI 데이터센터 매출 드라이버(A1.5) 의 컨센이 됐다. 요금·수수료류(fee·이용료·수수료) 는 이름이 아니라 문장의 돈의 방향(지급·부담·임차 ↔ 수취·매출·수익) 으로 cost/revenue 를 정하고(`cause.flow_direction`), 못 정하면 「뜻 미정」 으로 어느 자리에도 안 들어간다. 보증·약정·계약 총액은 `commitment` — 뜻은 알지만 값이 아니다. 비용은 **모든** 변수와 양방향으로 서로 대신 못 하고(전엔 이익·매출·마진만), 정성으로 설계한 KPI(ai_dc · 원가) 는 꼭 맞는 변수 셀만 받는다. ② **지난 기간 차단** — 2025 마케팅 비용 1.92조원이 2026-10 의 컨센·내 추정이 됐다(2026E 2.53조원이 있었는데도 열 이름 오름차순). `kstock/units.py: is_past_period` 로 전망 셀에서 지난 기간(연도는 지난 해, 분기·반기는 끝난 뒤 45일) 을 거른다; 지난 수치는 current 설명과 가정 메모에 「확정 실적(공시) 확인 전이라 값으로 안 씀」 으로만 남고 실적으로 승격하지 않는다. ③ **단위 한 곳** — 제안 라벨 「하나증권 77,599조원」 (억원 원천에 노드 단위를 붙임). `kstock/units.py: fmt_amount / convert / to_eok` 하나로 모았고 `meta.draft.drivers[*].by_broker` 는 노드 단위로 환산해 `by_broker_unit` 과 함께 둔다 → 「컨센 7.76조원 (하나증권 7.76조원)」. ④ **가정 메모 확장** — 값으로 안 쓴 수치는 전부 드라이버 질문의 「가정 메모(수치 아님)」 와 `meta.draft.skipped_cells` 에 `reason`(incompatible · unclassified · past_period · commitment · relative) · 사유 · Claim 문장과 함께 남는다. ⑤ **A6.2 분석 불가** — 7일 넘게 낡은 시세면 추세·거래량 값을 비우고 「분석 불가 — 시세가 09-21 (40일 전) 에서 멈춤 · 당시 계산(참고): 추세 상승 …」 으로 적는다; 당시 값은 `meta.draft.flow_stale` 에만 (현재 판단에 안 들어간다). 실제 Claim 문장(c3·c17·c18) 으로 회귀 시험 `tests/test_cause_guard.py: TestFlowDirectionAndPeriod`.

**10-31 보강 6 (삼성전자 005930 생성본 검토 뒤 — 재무 표 정합성 층).** 삼성전자는 NAVER 에서 안 드러난 것을 드러냈다: 2027E 매출 471,980조원 · EPS 30,722,739원 · PER 0.0156배 · FCF 컨센 = 특별배당 30조 · 주주환원 이벤트에 AI 인프라 규칙 · 「27년 1월」 이 12-31 · 파운드리 손익 −0조원. 실제 PDF 4건으로 재현해 전부 고쳤다 (`tests/test_samsung_review.py`). ① **단위 교정** (`reports/digest.py: calibrate_units`) — 단위를 「읽은」 증권사도 틀릴 수 있다 (유안타 표가 ×1000: 단위 토큰을 근처 다른 문장에서 집었다). 실적(A) 열의 매출·영업이익은 증권사가 달라도 같아야 하므로 전 증권사 중앙값과 10^k 어긋나면 10^-k 로 보정하고, 그래도 억원 기준 범위(1억~10,000조) 밖이면 범위 안으로. ② **순이익 행 가드** — `extract.ROW_LABELS` 의 순이익 패턴이 「순이익률」 을 받지 않는다 (신한 115 = 11.5% × 10 이 순이익이 됐다); 그래도 들어온 것은 순이익/영업이익이 2~300% 밖이면 뺀다 (`_drop_implausible_np`). ③ **분기 표 교정** — 분기/연간 비율의 증권사별 중앙값이 10^-k × (3~60%) 면 분기 값을 10^k 보정하고(`_calibrate_quarters`; 조원 한 자리 소수 표를 십억원으로 읽은 것), 남는 것은 뺀다 → 3Q26A 영업이익 0.11조 → 107.4조. ④ **사업부 표 교정** — 가장 큰 사업부 매출이 같은 증권사 전사 매출의 5~100% 가 되게 10^k (`calibrate_segments`) → 파운드리 3Q26P 영업손익 −19,000억(−1.9조). 노드 표시는 두 값이 1,000억 미만이면 억원 (`−0.0조원` 방지, `units.fmt_amount` 도 0.1조 미만은 억원). ⑤ **증권사 간 이상값** — 같은 셀에서 셋 이상이면 중앙값의 1/4~4배 밖을 뺀다, 둘뿐이면 평균하지 않고 경고(누가 틀린지 모른다). ⑥ **정규화 EPS 교차 검증** (`eps_std`) — 보정 배수(`factor_of`) 를 쓰고, 그 증권사가 적은 EPS 와 ±50% 넘게 어긋나면(적은 EPS 가 없으면 다른 증권사 중앙값의 1/4~4배 밖이면) 정규화 쪽을 뺀다. A3.1.3 EPS 컨센은 A3.3 과 **같은 정규화 EPS** 를 쓴다 (`draft._align_result_eps`; 라벨 「정규화」, 리포트 EPS 평균은 설명에). 목표가/EPS 역산 PER 가 1~300배 밖이면 PER 를 비우고 미계산으로 (`valuation.PER_SANE`) — 「PER 0.02배로 목표가 재현」 을 막는다. ⑦ **FCF ↔ 주주환원 분리** — 변수 축 `payout`(배당·DPS·자사주·소각·환원) 을 `fcf`(현금흐름) 와 갈라 서로 대신 못 하게 (`cause._INCOMPATIBLE`); 특별배당 30조·발표 110조는 FCF 드라이버의 가정 메모로. ⑧ **이벤트 주제별 템플릿** (`catalysts.TOPIC_TEMPLATES`) — 회사 계획·마일스톤의 사전 확인·상방·하방·판단 규칙을 주제(주주환원 · LTA · HBM · 파운드리 · AI 인프라 · 증설 · 출시 · 일반) 로 고르고, 코드가 채운 템플릿인데 주제가 다르면 바꿔 끼운다 (사람이 쓴 규칙은 그대로). 주주환원 이벤트는 A9.1.3 에도 잇는다. ⑨ **시점** — 「27년 1월」「'27년 1월 말」「2027-01」 을 연월로 읽는다 (`parse_when`); 증거 붙이기는 두 기간이 다르면 날짜가 가까워도 안 붙이고(4Q26 전망 ≠ 3Q26 보고서), 「2027-01 ⊂ 2027Q1」 처럼 포함되면 붙인다 (`units.periods_overlap`); 같은 주제의 리포트 전망 이벤트는 기간이 겹치는 회사 계획 이벤트에 합쳐지고 문장은 증거로 남는다 (`same_event`). 재생성 결과: 2027E 매출 1,135조(980~1,414) · EPS 84,921(정규화) · PER 5.65 · 4Q26E OP 134조 · 파운드리 −1.9조 · 주주환원 이벤트 2027-01-31 + 증거 4건.

**10-31 보강 7 (두산에너빌리티 034020 — 리포트 본문·설명회 Q&A·프로젝트 표에서 촉매를 뽑는 층 A).** 두산은 사업이 많아 촉매가 「문장 상위 3개」 로는 안 잡힌다 (유진투자증권 설명회 Q&A 25항목 · 신한투자증권 프로젝트 표). 리포트 2건 본문을 회귀 자료로 넣고 (`tests/fixtures/reports/doosan/`, `tests/test_doosan_events.py`) 검토문의 핵심 5개 — ① 연간 수주 목표 13.3조(잔여 3.3조) ② X-energy 연내 후속 계약 ③ 미국 AP1000 기자재 발주(연말~내년 초 · 선행조건) ④ 가스터빈 슬롯 본계약 전환 2027·2028 ⑤ 중형 가스터빈 2028 하반기 출하 — 가 맞는 시점·주제·단계로 나오는 것을 기준으로 했다. ① **Q&A 추출** (`reports/extract.py: extract_qa`) — 「Q1. 질문 / - 답 / - 답」 블록을 항목으로 (쪽 번호 포함); 답은 회사의 말이라 「회사 계획 (리포트 인용)」. ② **시점의 역할** (`catalysts.parse_when`) — 한 문장의 여러 시점을 사건 시점(계약·발주·결정·FID) 과 **납기·운전 시점**(출하·운전·가동·건설 목표·슬롯 예약분) 으로 가른다: 「2030년 슬롯 예약 물량의 본계약 전환은 2027년」 → 사건 2027 · 납기 2030(`Event.delivery_period`, 표시는 「납기·운전 시점 … (사건 시점이 아니다)」); 「i-SMR 2035년 운전 목표 · 2028년 이전 발주」 → 사건 2028 · 납기 2035; 납기만 있는 문장(「2031년 출하분까지 예약 접수」) 은 사건이 아니다. 「연말~내년 초」 는 올해, 「10월 30일 3Q26 확정실적」 처럼 이웃한 토큰은 역할을 이어받는다. ③ **문장 쪼개기** (`generate/event_text.py: split_candidates`) — 금액 항목이 둘 이상(「SMR 1조원(…), 신재생 7,000억원(…), 복합 EPC 5,000억원(…)」) 이면 항목마다, 시점 든 절이 둘 이상이면 절마다 하나의 사건. ④ **사업·단계** — 사업명 사전(`catalysts.PROGRAMS`: X-energy · NuScale · i-SMR · 폴란드/미국 AP1000 · V.C. Summer · Kozloduy · 가스터빈 슬롯 · 중형 가스터빈 …) 과 계약 단계 사전(`STAGES`: 불확실 → 협의 중 → 조건 합의 → 체결 대기 → 체결) 을 이벤트에 적는다 (`Event.program` · `Event.stage`); 선행조건(「부지 확정 · 국회 승인 절차 필요」) 과 시점 없는 Q&A 답(X-energy 전열관 조기 발주) 은 같은 사업 이벤트의 사전 확인으로 (`attach_untimed_qa`). ⑤ **프로젝트 표** (`project_lines`) — 「미국 / AP1000 / Fleet Project 10기」 처럼 셀로 쪼개진 표의 진척 줄(FID · COL · EPC 계약 · 건설 목표) 에 사업명을 앞세워 문장으로; 대외 프로젝트의 「예상」 은 회사 계획이 아니라 리포트 전망. ⑥ **수주 목표 점검** (`annotate_order_target`) — 연간 목표·잔여와 연내 후보(SMR 1 + 해상풍력 0.7 + 복합화력 0.5) 를 더해 「후보 밖 미확인 1.1조원 — 협의 금액은 확정 수주가 아니다」 를 목표 이벤트의 사전 확인에 쓴다. ⑦ **같은 사건 판정** (`catalysts.same_event`) — 기간 사건끼리는 끝 날짜(12-31) 가 같은 게 우연이라 **주체(사업명 또는 본문 드라이버)·주제·기간 겹침** 으로만 본다; 주체가 다르면(X-energy vs 폴란드, SMR vs 해상풍력) 다른 사건, 주체를 모르는 쪽(Claim) 은 일반 낱말(수주·목표·계약·시점…) 을 뺀 고유 낱말이 겹쳐야 하나다 — 전엔 「수주 목표」 낱말로 연말 사건 7개가 하나가 됐다. Claim 이벤트에도 사업명을 적는다 (`mapping.py`). ⑧ **상한** — A6.1 은 16개(`MAX_EVENTS`; 중요도 H · 확실성 · 가까운 날짜 순), 생략한 것은 `meta.draft.a6_1_dropped` 와 메모 「A6.1 상한 … 생략 N건」 에 남긴다; 새 제품 첫 출하(시제품·상용화·해외 진출·양산) 는 금액이 없어도 H. 결과(리포트 2건, claims off): A6.1 16건 — 2026 연말 수주 7건(13.3조 목표 · SMR · 해상풍력 · 복합화력 · X-energy · 폴란드 · 미국 AP1000) · 가스터빈 증설 결정 · 4Q26 실적 · 본계약 전환 2027/2028(납기 2030/2031) · 미국 AP1000 2027 · Kozloduy 2027~2030 · V.C. Summer FID 2028H1 · 중형 가스터빈 2028H2 · NuScale 10월 체결(완료). 안 한 것: 이미지 PDF(OCR) · LLM 사건 추출(층 B — 규칙이 놓치는 문장을 LLM 이 같은 Event 스키마로 제안하고 규칙이 검증) · 4Q26 영업이익 부문/연결 범위 구분.


**10-31 보강 8 (두산에너빌리티 — Claim 합치기 확인 · 이미지 PDF OCR · 층 B LLM 사건 추출).** ① **실제 Claim 저장본으로 재생성** — `data/claims/hk_97300.json`(신한) · `hk_97311.json`(유진) 을 `--claims cached` 로 합쳐 돌렸다 (촉매 역할 발췌가 `tests/fixtures/claims/doosan/`, `tests/test_doosan_events.py: TestClaimMerge`). 층 A 이전의 사용자 지도(10-31) 는 A6.1 6건 — 「가스터빈은 2031년 출하분까지 예약」 이 2031 사건, 본계약 전환이 2030 사건, SMR 1조가 폴란드 이벤트에 합쳐진 것 — 이었다. 이제 신한 Claim 「폴란드 … 12월까지 EPC 계약 목표」 는 본문의 폴란드 이벤트에(사업명 기준), 유진 전망 Claim 「내년 발주 기대」 는 미국 AP1000 2027 이벤트의 증거로 합쳐지고, **납기만 있는 Claim** (「2031년 출하분까지 예약·협상 진행 중」) 은 `mapping.py` 가 사건으로 만들지 않는다 (메모 「납기만 있는 Claim 은 사건이 아니라 뺌」; `event_text.PLAN_WORDS` 를 같이 쓴다). Claim 이벤트에도 `delivery_period` 를 적는다. ② **이미지 PDF OCR** (`reports/ocr.py`) — company_97320.pdf 는 12쪽에 글자 156자(차트 축 라벨) 였다. `loader.pdf_text` 는 글자가 200자 미만이거나 **한글이 쪽당 20자 미만**이면 이미지 PDF 로 보고, tesseract + `kor.traineddata` + pdftoppm(poppler)/pypdfium2 가 있으면 OCR 로 읽어 `extractor="ocr"` 와 경고(「숫자·표·고유명사 오독 가능」) 를 남기고, 없으면 「이미지 PDF — OCR 못 함 (이유); 설치: …」 를 경고에 적는다. 환경 변수 `KSTOCK_OCR=0`(끔) · `KSTOCK_OCR_LANGS`(기본 kor+eng) · `KSTOCK_OCR_DPI`(220). 시험은 영어 데이터로 왕복(`tests/test_ocr.py`; 한국어 데이터가 없는 환경에선 건너뜀). ③ **층 B — LLM 사건 추출** (`generate/event_llm.py`, `draft_from_reports(llm_events="auto")`, CLI `--llm-events auto|on|off`) — `--llm` 이 있으면 리포트마다 한 번, 본문 전체가 아니라 **시점 표현이 든 줄(+이웃)·Q&A 블록** 만(`candidate_passages`; 표 행은 뺀다, 유진 10.5k → 4.2k자) 보내고 사건을 `{quote, what, when_text, when_role, delivery_text, program, stage, speaker, preconditions, amount}` 로 받는다. 받는 조건은 전부 규칙이다: 인용이 원문에 있어야 하고(`claims.locate_quote`; 20자 미만은 버림), **시점은 인용 문장을 `parse_when` 이 읽은 것**이 기준(LLM 의 when_text 는 인용에 시점이 없고 인용 근처 — 같은 Q&A 의 질문 — 에 실제로 있을 때만), 납기만 있는 문장·지난 일(체결·완료 제외) 은 버리고, 사업명·단계는 `PROGRAMS`/`STAGES` 에 있는 것만 (사전 밖 이름은 메모 「사전 밖 사업명」 으로 남겨 사전을 키울 후보), 선행조건은 본문에 있는 줄만, 쪽은 인용 위치로 센다. 등급은 화자로(회사 → 「회사 계획 (리포트 인용)」, 증권사 → 「리포트 전망」). 받은 사건은 `merge_into_map` 으로 층 A 와 `same_event`(주체·주제·기간) 로 합쳐져 **같은 사건이면 층 A 의 ID 가 남고 LLM 문장은 증거로** 붙는다; 메모 「A6.1 LLM: 유진투자증권: LLM 제안 8건 → 받음 3 / 버림 납기만 있음 1 · 인용 없음 1 · 지난 일 1 …」. 가짜 LLM 으로 검증 규칙과 합치기를 시험했다 (`tests/test_event_llm.py`); 실제 Claude 호출은 `--llm` 과 키가 있는 사용자 환경에서. 전체 시험 361개 통과.


A6.2 수급/기술은 리서치 DB 시세가 7일 넘게 낡으면 `stale` 로 표시하고 질문 칸에 ⚠ 를 적는다 — 낡은 시세로 「추세 상승」 이라 말하지 않는다. 외국인·기관 순매수와 공매도·신용잔고는 리서치 DB 에 자료가 없어 여전히 빈 칸이다.

## 8. 마인드맵 HTML

기본 렌더러(`render/native.py`) 는 외부 라이브러리 없이 바닐라 JS 로 레이아웃을 계산한다. 1단계 가지를 좌/우로 나눠 중심 노드가 가운데 오고, 방사형 · 오른쪽 한 방향으로 바꿀 수 있다. 노드 클릭으로 접고 펼치고(기본 2단계, 접힌 가지는 `+N`), 드래그·휠로 이동·확대, `f` 로 화면 맞춤, ☀/☾ 테마. markmap 이 필요하면 `--format markmap`.

- **복사** — 노드 글자는 마우스로 긁어 복사, `⧉` 는 그 줄을 클립보드로. 우클릭 메뉴: 한 줄 · 상세 · 이 가지 전체(Markdown) · 노드 id. `Ctrl+C` 한 줄, `Ctrl+Shift+C` 상세, 툴바 「전체 복사」 는 맵 전체.
- **편집** — 더블클릭(또는 `✎`, `F2`) 으로 오른쪽 편집 폼. 드라이버·시나리오·규칙·이벤트 노드는 그 구조의 필드(관측값 · 띠 · 경계 · 역할 · 민감도 입력 · 목표가·확률 · 조건식·행동 …) 가 나온다. 「자식 노드 추가」(`Insert`), 「이 노드 삭제」(`Delete` 두 번). 「적용」 은 화면에만(`Ctrl+Z` 되돌리기), **「저장」** 이 맵에 넣는다.
- **저장 경로** — `mindmap edit <맵>` 이나 콘솔 「고른 맵 HTML 편집…」 로 연 페이지는 로컬 편집 서버(127.0.0.1, 토큰 주소) 로 「저장」 이 맵 JSON 에 바로 들어간다(원본은 `maps/versions/`, `meta.version` 증가, A8.2 로그). `file://` 로 연 정적 HTML 은 「편집 파일 내려받기」 → `mindmap apply-edits <파일>`.
- **규칙** — 편집은 복사본에 먼저 적용해 검증을 통과해야 한다(편집 전보다 오류가 늘면 거절; 초안에 원래 있던 미입력 오류는 막지 않는다). 값이 바뀐 노드는 history 에 이전 값을 남기고 `user` 태그를 얻어 변경안이 자동 적용하지 않는다. 페이지를 연 뒤 맵이 달라졌으면 저장을 거절한다(새로고침).
- **제안값** — 초안이 비워 두는 사람 몫 칸 가운데 리포트가 근거를 주는 것(A1 내 추정 = 컨센, A2.2 소수 의견, A5 조건부 리스크, A7.2 진입 가격대) 은 편집 폼에 "제안: … [채우기]" 로 보인다. 받아들이면 history 에 "리포트 제안 수락" 이 남는다. `mindmap suggest` · `eval-suggestions`(수락률 · 수락 뒤 수정률 = 앵커링 신호). 콘솔의 「리포트로 자동 생성…」 은 「제안값 미리 채우기」 가 기본으로 켜져 있어(끄면 「채우기」 로만) 값 칸에 미리 들어간다; CLI 는 `draft --prefill-suggestions`.
- **툴바** — 「밸류 계산기」(∑) 와 **「성립 조건」** 패널(표 다섯 + 신호등 + 판단 상태 — 보류 · 재검토 · ⚪ 과반 · 제안 대기 · Claim 게이트). 패널 HTML 은 서버가 만들어 내려받기 모드에서도 보인다.

## 9. Python API 로 한 바퀴

```python
from datetime import date
from kstock.templates import new_map_skeleton
from kstock.models import Driver, Measurement, Position, MarketState, Source
from kstock.engine import evaluate_new_map, snapshot_to_holding, update_market, update_driver, \
    set_thesis_state, evaluate_holding, record_decision, rollup
from kstock.engine.decision import compute_v7_all

a = new_map_skeleton("000000", "가온로보틱스", "growth", date(2026, 10, 19))
a["A0"].set("… 한 문장 논제 …")
a["A0.4"].set(58000, source=Source(name="KRX"))
a["A1.1"].driver = Driver(current=Measurement(value=8.1), my_estimate=Measurement(value=13.0), next_check=date(2026, 11, 14))
# … A1.2, A1.3, A4.2~4(Scenario), A5.4.1(Rule, fact 기준), A7.5 …
ev = evaluate_new_map(a)              # A4.5 ER, A4.6 RR, A7.1 suggested, rr_min 게이트
compute_v7_all(a)                     # 성립 조건 · 필요 경로 · 증거 계획 · 규칙 평가 · 플래그

b = snapshot_to_holding(a, Position(entry_date=date(2026, 10, 19), entry_price=58000, quantity=862, w_target=0.05))
update_market(b, MarketState(as_of=date(2027, 1, 15), price=76000, eps_fwd=3392, band_percentile=82, w_now=0.072))
update_driver(b, "B2.1.1", 9.0, as_of=date(2027, 1, 15))
set_thesis_state(b, "유효", today=date(2027, 1, 15))
res = evaluate_holding(b)             # 🟡 + 매트릭스 trim + 주가 분해
record_decision(b, "trim", today=date(2027, 1, 15), reason="목표 비중 초과")
c = rollup([b], cash=80_000_000)
```

## 10. 구조

```
src/kstock/
  models/      노드 스키마 · 하위 스키마(Driver · Scenario · Rule · Event · Observation · RequiredPath …) · 맵 컨테이너 · 프로파일 · migrate(스키마 1.0 → 2.0 노드 번호)
               · claims.py (EvidenceSpan · Claim · ClaimSet · Issue · Change)
  templates/   노드 정의 테이블(질문 포함) → 스켈레톤 생성기
  engine/
    calc.py · rules.py · status.py · matrix.py · validate.py · lifecycle.py · rollup.py   템플릿 §3~§7·§10 (계산 · 조건식 DSL · 상태등 · 매트릭스 · 검증 · 운영 루프 · C 롤업)
    sensitivity.py   원인 변수 민감도 (템플릿 · 입력 출처 · 재무 스냅숏 · 회계 기준 전파)
    valuation.py · scenario_model.py · attractiveness.py   평가 모델 · 가정 묶음 시나리오 · 확률가중 매력도·신뢰도 (설계 v6)
    path.py · evidence.py · revision.py · decision.py   성립 조건·필요 경로 · 관측값·신호등·증거 계획 · 규칙 엔진·플래그·알림 · 묶음 실행 (설계 v7)
  reports/     loader(PDF/TXT/MD/HTML/DOCX) · extract(표지 · 추정치 표 · 발행주식수) · segments(사업부 표) · digest(집계 · 회계 기준 정규화)
               · dbcheck(리서치 DB 교차 확인) · claims(Claim 추출) · validate_claims(3단 검증) · claim_stats(품질 통계·비교·게이트)
  generate/    kpi_catalog(섹터 원인 KPI · 드라이버 사전) · cause(원인 변수 셀 · A1 선택) · rules · scenarios · issues · mapping · check
               · changes(변경안) · suggest(제안값) · edits(HTML 편집 적용) · evals · prompts · llm_fill(Claude · 로컬 OpenAI 호환) · draft(파이프라인)
               · batch(일괄 실행) · knowledge(지식 내보내기)
  render/      native(기본 HTML · 편집 · 계산기) · conditions(성립 조건 패널) · markmap · mermaid · server(로컬 편집 서버)
  collect/     수집기 인터페이스 · CSV 소스 · 이벤트 분류기 · adapters/(naver_quote · research_db · DART·KRX·KIS 골격)
  llm/         노드 채움(MockFiller · ClaudeFiller) · 모델 선택 · 프롬프트
  env.py · store.py · symbols.py · ops.py · console.py · console_text.py · demo.py · cli.py
tools/         console.py 실행기 · check_api_key.py (표준 라이브러리만)
tests/         unittest 256건 (pytest 로도 실행) — 픽스처 tests/fixtures/reports · 골든 tests/golden/{extract,drivers,issues,valuation,holdout}
docs/          mindmap-template.md(템플릿 v1.0) · ARCHITECTURE.md · report-draft-design-v2~v7.md(설계 이력) · auto-generation-plan.md
examples/      데모 출력(가온로보틱스 A/B/C) · samsung_005930/ · claims_experiment/
data/          reports/ · claims/ · issues/ · changes/ · maps/ · out/ · ref/  (개인 데이터, git 제외)
```

## 11. 설계 결정 (코드에 박힌 것)

| 원칙 | 구현 |
|---|---|
| 매입가는 판단 입력이 아니다 | `B0.5` 는 `meta_only=True`. computed/judgment 노드가 참조하면 `validate` 오류 |
| 대응은 세 입력만 | `engine.matrix.decide(thesis, valuation, weight)`. 다른 결정은 `record_decision(..., reason=…)` 에 `override=True` 로 남는다 |
| 스냅숏 불변 | `snapshot_to_holding` 이 B1.1~B1.4 해시를 저장, 수정 시 검증 오류. 논제가 바뀌면 B2.3 = `변질` |
| 선별은 코드 규칙만 | `compute_light`·`light_for` 는 코드 규칙. LLM 은 노드를 채울 뿐 상태등·매트릭스·신호등·밸류 숫자에 관여하지 않는다 |
| 비중 밴드 | `min(dev_abs 3%p, dev_rel 30% × 목표)`. 스냅숏 시 B7 에 상한 규칙이 자동 생성된다 |
| BUY 게이트 | 손익비 < rr_min, 또는 신규 BUY 보류·재검토 플래그면 `A7.5 = BUY` 를 `WATCH` 로 강제 |
| 비중 제안 vs 확정 | `A7.1 = {"suggested": 계산값, "confirmed": 사람 확정, "reason"}` |
| 사람 몫은 비워 둔다 | 확률 · 내 추정 · Kill · 역할·경계 확정 · 제안 승인은 자동으로 채우지 않는다. 초안의 빈칸은 미확정이지 오류가 아니다 |
| 증권사 가정을 섞지 않는다 | 교차 조합 없음 · 목표가 분포로 시나리오를 만들지 않음 · 역산은 조건부("내재" 금지) |

임계값은 `kstock.models.Settings` 와 `PROFILES` 에서 바꿀 수 있다. 무엇을 어디서 고치는지는 `docs/ARCHITECTURE.md` 의 표에 있다.

## 12. 테스트 · 린트

```bash
pytest                 # 또는: python -m unittest discover -s tests -p "test_*.py"   (256건)
ruff check src tests
```

테스트는 네트워크와 LLM 을 쓰지 않는다(`KSTOCK_OFFLINE`, `tests/helpers.py::NO_LLM_ENV`). 개발 PC 의 형제 폴더 리서치 DB 는 "DB 없음" 을 보는 테스트에서 `KSTOCK_RESEARCH_DB=off` 로 끈다. GitHub Actions 워크플로는 `docs/github-workflow-ci.yml`.

## 13. 설계 문서 이력

| 버전 | 날짜 | 요지 |
|---|---|---|
| 템플릿 v1.0 | 10-19 | A/B/C 맵 구조 · 노드 스키마 · 상태등 · 매트릭스 · 계산식 · 라이프사이클 · 검증 (`docs/mindmap-template.md`) |
| v3 | 10-21 | 리포트 → Claim(인용 필수 · 3단 검증) → 쟁점 → 맵 · 변경안 · 평가 |
| v4 | 10-22 | 사실·전망·계획 구분 · 기계 검증 vs 사람 승인 · 쟁점 기준 명제 · 셀 단위 집계 · 목표가 ≠ 시나리오 · Kill 은 후보까지 · 규칙 모드 제한 · 평가 분리(holdout) |
| v5 | 10-24 | 핵심 드라이버 = 원인 변수(결과 지표는 A3.1) · 사업부 표 추출 · 민감도 |
| v6 | 10-24 | 평가 모델(PER · 정상화 · SOTP) · 시나리오 = 가정 묶음 · 확률가중 매력도 · 신뢰도 · 회계 기준 정규화 · 역산의 조건부 표현 · Claim 품질 게이트 |
| **v7** | 10-27~28 | 투자 의사결정 마인드맵 — 가정 표 · 역산 표 · 날짜별 필요 경로 · 관측값 데이터 계약 · 신호등 · 판단 수정 규칙 · 판단 상태 전이 · 운영 CLI. T1~T10 구현 완료(§12.3~12.5) |
| 템플릿 v2.0 | 10-28 | 노드 번호 개편 — A3 밸류에이션 · A4 시나리오 분리, 옛 A4~A8 → A5~A9 (대응표는 템플릿 §12). v2~v6 설계 문서의 노드 번호는 당시(1.0) 기준이다 |
| A6 보강 | 10-30 | 촉매 & 타임라인 — 세 출처(DART 공시 본문 · 과거 공시 주기 · Claim 기간) 합치기 · 이벤트에 사전 확인/상방·하방/판단 규칙/사건 뒤 칸 · A8.1 은 next_check · A6.2 낡음 표시 (§7.1) |
| A6 보강 2 | 10-31 | 정정공시 셀 파싱 · affects 연결 엔진 · 전망 Claim 은 증거 · event_id 로 번호 고정 · 상대 기간 · 비용 드라이버 op 차단 (§7.1) |
| A6 보강 3 | 10-31 | 비율·평가 파라미터 차단(할인율→ASP) · evidence 칸과 실적 이벤트 엄격 매칭 · 용량 마일스톤+project · 새 초안 번호순 · A8.1 본문 · 예정/완료 표시 (§7.1) |
| A6 보강 4 | 10-31 | 「회사 계획 (리포트 인용)」 등급 · 마일스톤 판단 규칙·A3.3/A4.1 연결 · ai_dc 관측 지표 · A8.1 원문 보존 · ⚠ 낡음 · 유효세율 가드 · `dart-check` 파서 점검 (§7.1) |
| A1 수치 보강 5 | 10-31 | 돈의 방향(지급 이용료 = 비용) · 뜻 미정·보증 금액은 값 아님 · 지난 기간 열 차단 · 단위 한 곳(`kstock/units.py`) · 가정 메모 reason · A6.2 낡은 시세는 분석 불가 (§7.1) |
| 재무 정합성 6 | 10-31 | 삼성전자: 증권사 단위 교정(실적 열 10^k) · 순이익률 행 차단 · 분기·사업부 표 교정 · 이상값 제외 · 정규화 EPS 교차 검증·A3.1↔A3.3 통일 · PER 상식 범위 · FCF↔주주환원 분리 · 이벤트 주제별 템플릿 · 「27년 1월」 (§7.1) |
| A6 문장 추출 7 | 10-31 | 두산에너빌리티: 설명회 Q&A 추출 · 사건 시점 ↔ 납기·운전 시점 분리 · 금액 항목/절 단위 쪼개기 · 사업명·계약 단계 사전 · 프로젝트 표 줄 · 수주 목표 점검(후보 밖 미확인) · 같은 사건은 주체·주제·기간으로 · A6.1 상한 16 + 생략 기록 (§7.1) |
| A6 보강 8 | 10-31 | 두산에너빌리티: 실제 Claim 저장본과 본문 이벤트 합치기 확인(사업명 기준 · 납기만 있는 Claim 제외) · 이미지 PDF OCR 폴백(`reports/ocr.py`, tesseract kor) · 층 B LLM 사건 추출(`event_llm.py`, 인용·시점·사업명 규칙 검증, `--llm-events`) (§7.1) |

## 14. 다음 단계

1. 관측값 수집기 — DRAMeXchange·트렌드포스·공시 같은 출처에서 데이터 계약 JSON 을 만들어 `observe --from-file` 로 넣는 어댑터(지금은 계약과 CLI 까지).
2. Spark 에이전트 스킬 — `batch → alerts → observe` 순서로 돌리고 `export-knowledge` 결과를 memory 로 쓰는 절차 문서.
3. 사업부 표가 없는 리포트(두산 등) 를 위한 민감도 입력 보조 — `sensitivity_inputs` 를 콘솔에서 채우는 폼.
4. 수집기 어댑터 실제 구현 — `collect/adapters/` 의 DART 공시 · KRX/KIS 시세·잔고 · 컨센서스.
5. 복기 통계 — C6.2 결정 유형별 6/12개월 적중률 (현재는 로그 수집만).
6. 웹 뷰 — 🟢🟡🔴 보드 + 종목 맵 전환 UI.
