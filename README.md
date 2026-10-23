# kstock-mindmap — 주식투자 마인드맵 생성·대응 엔진

신규 투자 맵(A), 보유 종목 맵(B), 포트폴리오 롤업(C)을 **하나의 노드 스키마**로 생성·갱신·복기하는 Python 패키지.
기준 문서는 [`docs/mindmap-template.md`](docs/mindmap-template.md) (템플릿 v1.0) 이고, 코드의 모듈·함수 이름은 그 문서의 섹션 번호를 따른다.

핵심 원칙 다섯 가지 — 논제 중심 · 매입가 제거 · 숫자화와 사전 약속 · 선별(🟢🟡🔴) · 분리와 참조 — 가 그대로 코드의 제약이 된다.
예를 들어 평가손익(`B0.5`)은 `meta_only` 노드라서 어떤 판단 노드가 참조하면 검증이 실패하고, 대응 결정은 (논제 상태 × 밸류 상태 × 비중 상태) 세 입력만 받는 공통 매트릭스가 기본값을 낸다.

## 설치

기존 conda 환경 `ksa` 를 그대로 쓴다 (Python 3.10 이상이면 된다. 3.10 은 `StrEnum` 호환 shim 으로 지원).

```powershell
conda activate ksa
cd C:\kstock\mindmap
python --version                 # 3.10+ 확인
pip install -e ".[dev,reports]"  # pydantic + pytest + ruff + pypdf (리포트 PDF 읽기)  (editable 설치 → 소스 수정이 바로 반영)
# 선택: pip install -e ".[llm]"  # anthropic SDK (노드 채움 · 리포트 초안의 LLM 그룹 채움)
#       pip install -e ".[data]" # httpx, pandas (수집기 구현 시)
```

설치하면 콘솔 명령 `mindmap` 이 생긴다 (`pyproject.toml` 의 `[project.scripts]`). 배포 이름은 `kstock-mindmap`,
import 이름은 `kstock` 이다. 설치 없이 쓰려면 `python -m kstock.cli …` 에 `PYTHONPATH=src` 를 더한다.

API 키는 프로젝트 루트의 **`.env`** 에 둔다 (`copy .env.example .env` 뒤 채움; `.env` 는 git 에서 제외). `mindmap`, 콘솔,
`ClaudeFiller` 가 시작할 때 `kstock.env.load_dotenv()` 로 읽는다 — **셸 환경변수가 이미 있으면 그것이 우선**이고(예전 키가 Windows 사용자 환경변수에 남아 있으면 `.env` 를 고쳐도 반영되지 않는다 — `describe()`·`llm-check` 가 이 충돌을 알려 준다), 값은 어디에도
찍히지 않는다(로그·화면에는 "ANTHROPIC_API_KEY: .env 에서 읽음 (경로)" 처럼 출처만). 다른 위치의 파일을 쓰려면 환경변수
`KSTOCK_ENV=경로`.

```dotenv
ANTHROPIC_API_KEY=sk-ant-usr-…    # 리포트 초안의 LLM 그룹 채움, kstock.llm.ClaudeFiller (platform.claude.com → API 키 에서 발급; 레거시 sk-ant-api03-… 도 됨)
ANTHROPIC_WORKSPACE_ID=wrkspc_…    # 키 범위가 '조직'(워크스페이스 미지정)일 때만 — Console → 워크스페이스 → ID. Default 범위 키면 불필요
KSTOCK_LLM_MODEL=claude-sonnet-5-5 # 선택 — 비우면 계정의 모델 목록에서 최신 Sonnet 을 자동으로 고른다 (kstock.llm.models)
KSTOCK_LLM_THINKING=off            # 선택 — 기본 off: Claude 5 계열은 사고(thinking) 가 기본으로 켜져 사고 토큰이 max_tokens 를 먹고 텍스트가 빈다 → 끈다. adaptive 면 모델 기본
KSTOCK_PREFER_DOTENV=1             # 선택 — 이 파일의 키가 셸(Windows 사용자/시스템) 환경변수를 덮는다. 예전 키가 환경변수에 남아 .env 를 자꾸 가릴 때
```

셸 환경변수가 `.env` 를 가리면 `llm-check`·`describe()` 가 **어디서 온 변수인지**(Windows 사용자 / 시스템 / 이 세션 — 프로필·conda) 와 지우는 명령을 함께 보여 준다.
`conda activate ksa` 가 매번 넣는 변수라면 `conda env config vars list -n ksa` 로 보고 `conda env config vars unset ANTHROPIC_API_KEY -n ksa`.

Claude 5 계열(Sonnet 5.5 등) 과 anthropic SDK 1.x 에서 달라진 것은 코드가 알아서 맞춘다 — SDK 가 `temperature` 인자를 뺐으면 빼고 다시 부르고,
사고 끄기 값은 모델이 받는 것(`between_tools` → `disabled` → 없음) 을 차례로 시도해 기억하며, 응답이 `max_tokens` 로 잘리면 예산을 4배로 늘려
한 번 더 묻는다. 그래도 텍스트가 비면 "모델이 텍스트를 주지 않았다 (stop_reason=…, 블록=…)" 로 원인을 보여 준다.

키가 실제로 동작하는지는 **`mindmap llm-check`** 로 본다 — 키 출처·계열(`sk-ant-usr-` 사용자 연결 키 / `sk-ant-api03-` 레거시)·길이,
인증(모델 목록), 설정한 모델이 목록에 있는지, 아주 작은 호출 1회(토큰 수십 개). 키 값은 어디에도 찍히지 않는다. 콘솔 「작업 → LLM 연결 확인」,
텍스트 콘솔 A 메뉴 `L` 도 같다. 패키지 없이 돌리는 독립 스크립트는 **`python tools/check_api_key.py`** (표준 라이브러리만; anthropic 이 설치돼 있으면
SDK 경로도 함께 확인).

## 빠른 시작

```powershell
mindmap demo -o out\demo                 # 가상 종목 '가온로보틱스' 로 A → B → C 전체 흐름 + HTML
#   out\demo\gaon_A.html  신규 투자 맵
#   out\demo\gaon_B.html  보유 종목 맵 (🟡, 매트릭스 trim)
#   out\demo\gaon_B_summary.html  🟢 종목용 요약 뷰
#   out\demo\gaon_C.html  포트폴리오 롤업

mindmap new --ticker 005930 --name 삼성전자 --type growth    # A 맵 스켈레톤 (질문만 들어 있음)
mindmap validate maps\A_005930.json                          # §3.3 완료 조건 · §10 검증
mindmap eval maps\A_005930.json                              # 기대수익률 · 손익비 · 제안 비중 · rr_min 게이트
mindmap snapshot maps\A_005930.json --entry-price 58000 --qty 100 -o maps\B_005930.json   # 매수 → B 맵
mindmap market maps\B_005930.json --price 76000 --w-now 0.072 --eps-fwd 3392 --band 82 --total-assets 1e9
mindmap status maps\B_005930.json                            # 상태등 + 매트릭스 기본 대응 + 주가 분해
mindmap render maps\B_005930.json -o out\B.html              # 양쪽 균형 HTML (--layout radial|right, --format markmap|md|mermaid)
mindmap edit maps\A_005930.json                              # HTML 을 로컬 편집 서버로 열기 — 노드 고치기·추가·삭제가 맵 JSON 에 바로 저장 (맵 id 도 됨)
mindmap apply-edits A_005930.edits.json --map maps\A_005930.json   # file:// 로 연 HTML 이 내려준 편집 파일 반영
mindmap rollup maps\B_*.json --cash 80000000 -o maps\C.json  # C 포트폴리오
mindmap draft --reports r1.pdf r2.pdf r3.pdf --db data\db\kstock.sqlite --html   # 증권사 리포트 여러 건 → A 맵 초안
```

맵은 JSON 파일 하나다. 에디터로 직접 채워도 되고(노드마다 `question` 이 적혀 있다), `kstock.llm.fill_map` 으로 LLM 이 채우게 할 수도 있다.

## 콘솔 (GUI)

관심종목을 올리고, 맵을 만들고, 보유 종목의 상태등을 보는 일상 작업은 콘솔에서 한다. 리서치 콘솔과 같은
tkinter 창이고 외부 의존성이 없다.

```powershell
python tools\console.py                                   # = mindmap console
python tools\console.py --db data\db\kstock.sqlite --no-browser
mindmap console --text                                    # 번호 메뉴 텍스트 콘솔 (같은 기능)
```

```
┌ KSTOCK  마인드맵 콘솔                                                      준비됨 ┐
│ 관심종목 N · A 맵 N · B 맵 N (🟢🟡🔴) · 가장 가까운 확인일 · C 맵 날짜        판단 보조 도구입니다 │
├ 자료와 실행 ───────────────────────────────────────────────────────────────────┤
│ DB  data\db\kstock.sqlite                                              [다시 읽기] │
│ 관심종목  종목코드 [ ] 종목명 [ ] 시장 [KR] 섹터 [ ] 유형 [growth] 메모 [ ]  [추가] [추가 + A 맵] │
│ 보기  [전체 ▾]  N종목 · 주의 n                     [C 롤업] [모든 맵 렌더링] [모든 맵 검증] ▭ │
├ 관심종목과 맵 ─ 종목 · 이름 · 시장 · 섹터 · 유형 · A 맵 · B 맵 · 논제 · 비중 · 결정 · 다음 확인 · 메모 ┤
│   (🟡 🔴 줄만 색이 칠해진다 — 색은 "주의가 필요한가" 하나만 말한다)                       │
├ 자세히 ── [개요] [A 맵 채우기] [B 맵 운영] [마인드맵] [포트폴리오] [실행 로그] ──────────────┤
│ A 맵 채우기: 논제 · 드라이버 5슬롯 · 컨센서스 vs 뷰 · 시나리오 3행 · 리스크 · 무효화 조건 빌더 · 실행 계획  │
│             [현재가 갱신] [리포트로 자동 생성…] [A 맵 생성] [저장·검증] [평가] [HTML] [매수 체결 → B 맵…] │
│ B 맵 운영:   시장 상태 [갱신] · 드라이버 표 [값 갱신…] [사실 로그…] · 논제 판정 [저장]              │
│             평가 → 상태등·매트릭스·주가 분해 → 대응·사유·규모 + 편향 체크 6 [결정 기록]  [종료·복기…] │
└ 고른 줄 요약                                [고른 맵 HTML 열기] [실행 로그 보기] [로그 지우기] [닫기] ┘
```

관심종목은 **종목코드나 종목명 하나만** 적고 Enter(또는 [찾기])를 누르면 나머지가 채워진다 (`5930` → `005930 삼성전자`,
`하이닉스` → `000660 SK하이닉스`). 이름이 여러 종목에 걸리면(`삼성`) 시가총액 순 후보 중에서 고른다. 종목 목록은
`data/ref/symbols.csv` 스냅숏 → 리서치 DB(`C:\kstock\research\data\db\kstock.sqlite` 의 `prices`, 약 2,800종목; 설정
`research_db` 또는 환경변수 `KSTOCK_RESEARCH_DB` 로 바꿀 수 있다) → 이미 등록한 관심종목 순으로 합쳐 읽고, 「작업 →
종목 목록 갱신」이 리서치 DB 를 CSV 로 다시 떠 놓는다. 둘 다 없으면 코드와 이름을 함께 적으면 된다.

흐름은 표에서 종목을 고르면 아래 탭이 그 종목으로 바뀐다. 관심종목 추가 → 「추가 + A 맵」 → A 탭에서 채우고
「저장·검증」·「평가」 → 손익비 게이트를 통과하면 「매수 체결 → B 맵…」 → B 탭에서 시장 상태를 넣고 「평가」 →
매트릭스가 낸 기본 대응을 보고 「결정 기록」. 무효화 조건은 드라이버·연산자·임계값을 고르면 조건식이
만들어진다. 매트릭스와 다른 결정은 사유 없이는 기록되지 않는다.

화면 코드(`console.py`)는 위젯을 놓고 값을 모을 뿐이고, 폼 값을 맵으로 옮기는 일은 `ops.py` 가 한다. 그래서
tkinter 가 없는 환경에서도 `ops` 는 통째로 테스트되고, GUI 는 가짜 tkinter 위에서 스모크 테스트된다
(`tests/test_gui_smoke.py`).

저장 위치는 `data/` 아래다 (개인 데이터라 git 에서 제외됨).

| 경로 | 내용 |
|---|---|
| `data/db/kstock.sqlite` | 관심종목(`watchlist`) · 맵 인덱스(`maps`) · 설정(`settings`: 현금, 총자산) |
| `data/maps/*.json` | 맵 본문 (진실 원본; 인덱스는 「작업 → 맵 인덱스 재구성」 으로 다시 만들 수 있다) |
| `data/maps/versions/*.v<N>.json` | 변경안 적용·HTML 편집 저장 직전의 원본 (버전마다 1개) |
| `data/out/*.html` | 렌더링 결과 |
| `data/ref/symbols.csv` | 종목 목록 스냅숏 (코드·이름·시장·섹터·시총) — 「작업 → 종목 목록 갱신」 |

## 리포트로 A 맵 자동 생성 (초안) — 설계 v3

같은 종목의 증권사 리포트를 여러 건 주면 A 맵 초안을 만든다. 콘솔에서는 「A 맵 채우기」 탭의 **[리포트로 자동 생성…]**
(또는 작업 메뉴), CLI 에서는 `mindmap draft`, 텍스트 콘솔에서는 「A 맵 → 8」. 설계 문서: `docs/report-draft-design-v3.md`.

```powershell
mindmap draft --reports data\reports\삼성전자\*.pdf --db data\db\kstock.sqlite --html      # 규칙 + (키가 있으면) Claim 추출
mindmap draft --reports *.pdf --ticker 005930 --llm                                      # Claim 추출·의미 검증에 LLM 사용
mindmap claims --reports *.pdf --show        # Claim 만 뽑아 data/claims/ 에 (검토 대기함: mindmap claims-review <report_id>; ⚑ 확인 권고 포함)
mindmap issues --claims data/claims --ticker 005930 --llm   # 쟁점 표 (--llm: 기준 명제 판정, ◐ 부분 동의)
mindmap eval-issues                           # 쟁점 정답표 대비 이견 누락 (v4)
mindmap issues --ticker 005930               # 증권사 간 쟁점 표 (공통 견해·핵심 이견·독립 주장·미해결)
mindmap changes --map A:005930:2026-10-21 --reports new.pdf   # 기존 맵 + 새 리포트 → 변경안 (승인한 것만 적용)
mindmap golden-draft --reports *.pdf · mindmap eval-extract · mindmap eval-claims        # 정답표와 평가
```

파일은 PDF(텍스트가 있는 것) · TXT · MD · HTML · DOCX. PDF 는 `pypdf` → `pdfminer.six` → `pdfplumber` → `pdftotext` 가운데 설치된 것을 쓴다.

```
리포트 n건 ─▶ 읽기(loader, 페이지 보존) ─▶ 사실 추출(extract) ─▶ 수치 집계(digest: 증권사별 최신 1건·45일·basis·분산)
          ─▶ Claim 추출(claims: LLM, 명제 1개씩, 인용 필수) ─▶ 3단 검증(validate_claims: 인용·숫자·의미) ─▶ 검토 대기함
          ─▶ 쟁점 통합(issues: 테마→메커니즘, 증권사별 ↑↓·–) ─▶ 규칙 채움(rules) + 매핑(mapping) ─▶ 교차검증 ─▶ 엔진
```

| 어디서 | 무엇을 | 들어가는 노드 |
|---|---|---|
| 리포트 표지·표 (규칙) | 증권사·날짜·제목·의견·목표주가(직전)·현재주가·52주·추정치 표(+회계 기준) | A0.4(작성 시점 현재가), A1.1~3 표 드라이버(current=최근 실적, consensus=증권사별 최신 1건 평균, **내 추정=빈칸**), A3.6.3 리비전, **A3.6.4 목표가 분포**(min/중앙/max/분산·상승여력), A8.1.4 |
| Claim (LLM, verified 만) | 명제·역할(논거/리스크/촉매/밸류/주주환원/사업/변경 사유)·driver·메커니즘·방향·조건·시점·숫자·인용(페이지) | A0 논제 초안(증권사 종합), A1 빈 슬롯(테마), **A2.1.x 쟁점**(공통 견해·핵심 이견·독립 주장·미해결, 증권사별 ↑↓·–), A4.1~3 리스크(확률·영향은 빈칸), A4.4 Kill(조건부 리스크에 수치가 있을 때만 제안), A5.1 촉매(날짜 있는 것), A3.6.3 리비전 사유, **A8.4.x 리포트별 주장** |
| 시나리오 | 리포트가 Bull/Base/Bear 를 명시하면 그대로, 산식(PER×EPS 등) 이 읽히면 **그 리포트 안의 값만으로** 재계산한 후보 | A3.1~3 은 기본 **공란**(pending). 콘솔 「시나리오 후보 넣기」 로 증권사 한 곳의 후보를 고른다. 확률은 자동으로 넣지 않는다 — 「예시 확률 입력(30/45/25, 미검증)」 은 사용자 행위로 기록 |

지키는 것: 서로 다른 증권사의 가정을 섞지 않는다(min(EPS)×min(PER) 같은 교차 조합 없음), 목표가 분포로 시나리오를 만들지 않는다,
확률·내 추정·Kill·A2.2 내 뷰·A2.3 은 사람이 쓴다(초안은 비워 두고 **미확정** 으로 센다 — 오류가 아니다), 판단(A6.5)은 항상 WATCH,
모든 자동 노드는 `claim_id`·페이지로 원문까지 되짚는다(콘솔 「노드 출처 보기」). 증권사 컨센서스는 "현재 주가에 내재된 기대" 가 아니라
"리포트에서 관측된 것" 으로 표기한다(A2.1 값은 비우고 자식 쟁점에만).

Claim 검증: ① 인용이 원문에 그대로 있는가(코드) ② 숫자가 인용·표에 있는가(코드, 억/십억/조·만원 환산) ③ 인용이 명제를 지지하는가(LLM 배치 판정 →
supported/contradicted/insufficient). 셋 다 통과해야 `verified`, 아니면 **검토 대기함**(콘솔 「Claim 검토 대기함…」, `mindmap claims-review`) 에서 사람이 승인/반려.
LLM 이 없으면 키워드 문장 분류로 대체하고 `quality: rules`(저품질) 로 표시한다. 프롬프트 c2(2026-10-21): 인용은 1~3개(`quotes`), 명제의 숫자·사실은 전부 인용 안에,
한 명제 한 방향, 추정치 변경·분기 전망치는 필수. 명제가 말하지 않는데 quant 에 끼어 온 수치(표의 정밀값, 목표가) 는 불일치가 아니라 **제외**하고 note 에 남긴다 —
검증 규칙이 바뀐 뒤에는 `mindmap claims-review <report_id> --recheck` 로 LLM 호출 없이 코드 검증(①②) 만 다시 하고 상태를 재확정할 수 있다.
`eval-claims --misses` 는 못 찾은 필수 주장을 `[미추출]` / `[검토 대기]` 로 나눠 보여 준다 (검토 대기는 뽑혔지만 검증을 못 넘은 것 — 승인하면 재현율에 들어간다).

수치 집계 규칙: 증권사별 **최신 1건** 만 집계(이전 건은 리비전 이력), 기준일보다 **45일** 넘게 오래된 리포트는 집계 제외(참고), 순이익은 지배/전체 기준이 섞이면 다수 기준만,
최신 실적 발표일(리서치 DB `disclosures` 또는 분기 말 +8일 추정) 이전 리포트는 "실적 전" 표시, 파일명의 한경 리포트 번호로 리서치 DB `reports` 와 교차 확인(증권사·날짜·제목·TP 불일치 깃발).

새 리포트가 들어오면 「새 리포트 → 변경안」: 기존 맵은 그대로 두고 변경 후보(추정치 변화·새 쟁점·상태 변화·TP 리비전·새 리스크·새 촉매)만 보여 준다.
승인한 것만 적용하고 원본은 `data/maps/versions/<map_id>.v<N>.json` 에 보존, `meta.version` 증가, A7.2 업데이트 로그에 기록. 사용자가 손댄 노드는 자동 적용 대상에서 뺀다.

현재가는 리포트에 적힌 날짜의 값이 아니라 **맵을 만드는 시점**의 값을 쓴다: `collect/adapters/naver_quote.py` 가 네이버 금융
모바일 API 에서 현재가·체결시각을 받고, 실패하면 리서치 DB 종가, 그것도 없으면 리포트의 현재주가로 내려간다. 콘솔 대화상자의 「현재가를 작성 시점 기준으로
조회」(기본 켬), CLI `--no-fetch`, 환경변수 `KSTOCK_OFFLINE=1`. 작성일보다 3일 넘게 오래되면 ⚑ "갱신 필요"; 기존 맵은 「현재가 갱신」.

정답표(`tests/golden/`): `mindmap golden-draft` 가 추출기 결과로 초안(`verified: false`) 을 만들고 사람이 고친다. `eval-extract` 는 필드별 정확도,
`eval-claims` 는 필수 주장 재현율과 근거 불일치율. 최소 실험(삼성전자 3건, 사람이 LLM 역할로 쓴 Claim 답안) 은 `examples/claims_experiment/`.

리서치 DB(`C:\kstock\research\data\db\kstock.sqlite`) 가 있으면 현재가(리포트보다 최신일 때)·52주·추세·거래량 배수·최근 90일 DB 목표가·실적 공시일·리포트 교차 확인을 보탠다. 없어도 리포트만으로 끝까지 간다. 섹터는 관심종목 값 > 본문 단어 추정

### 설계 v4 (2026-10-22) — 검토의견 3 반영, `docs/report-draft-design-v4.md`

실측(삼성전자 3건, Claude Sonnet 5.5) 뒤 "잘못된 투자정보가 생기거나 검증·업데이트에 구조적 오류가 날 수 있는 것" 열 가지를 고쳤다. 전부 기본값이 있어 c1/c2 저장본이 그대로 읽힌다.

- **사실·전망·계획 구분 (V1)** — Claim 에 `asserted_by`(analyst/company/market/authority) · `certainty`(realized/announced/planned/expected/conditional) · `as_of`. 프롬프트 c3. 노드·표·인용에 접두어 `[실적]` `[기업 발표]` `[기업 계획]` `[증권사 전망]` `[조건부]`.
- **기계 검증 vs 사람 승인 (V2)** — `verified_by: machine|human`. 자동 통과해도 **표에 없는** 전사·미래 판단 지표 수치, 증권사 간 15% 넘는 전사 수치 충돌(단위 환산, 사업부·목표가 제외), 조건부 리스크, "애널리스트가 사실로 단정", 인용 밖 본문 수치는 `review_required` ⚑ 로 검토 대기함에 함께 뜬다(초안에는 쓴다). '근거 불충분' 판정을 승인할 때는 이유(`--note`) 필수. 사람이 정한 것은 `--recheck` 가 되돌리지 않는다. `claims-review --recheck --llm` 은 검토 대기 건의 의미 판정만 다시 묻는다(숫자는 코드가 인용 → 표 → 본문 순으로 대조).
- **쟁점의 기준 명제 (V3)** — 테마마다 `reference_proposition` 하나를 두고, LLM 이 있으면 증권사별로 그 명제에 agree/disagree/**partial**(◐ 부분 동의)/unrelated 를 판정한다(`mindmap issues --llm`, `draft` 는 기본 켬, `--no-issue-check` 로 끔). 판정 근거는 표의 `≡` 줄. 리포트를 끝까지 못 읽은 증권사는 침묵(–) 대신 **안 읽음(?)**.
- **셀 단위 집계 (V4)** — 증권사별 대표 문서는 최신 1건이지만, 거기 없는 (지표, 기간) 셀은 같은 증권사 이전 리포트(45일 안) 로 **보강**하고 표에 `*`. 열 라벨(…A/…P/…E) 이 다르면 다른 셀. "조정 영업이익" 은 `op_adj` 로 분리. EPS 주식 수·환율 기준은 읽을 수 없어 각주로만.
- **증권사 목표가 ≠ 시나리오 (V5)** — 후보 `origin`(broker_case/broker_recalc/broker_tp). 리포트가 그 이름으로 제시한 케이스가 아닌 것을 Bear/Base/Bull 에 넣으면 `[사용자 재해석]` 으로 기록(history · `meta.draft.scenario_origin`).
- **Kill 은 후보까지만 (V6)** — 조건부 리스크 Claim 은 `meta.draft.kill_candidates` 로만. 콘솔 「Kill 후보 확정…」/텍스트 `K` 에서 **관측 지표·갱신 주기·위반 시 행동** 세 칸을 채워야 Rule 이 된다(`origin=user_confirmed`). 키워드 문장으로 뽑은 리스크도 값이 아니라 질문의 후보로만.
- **변경안 정합성 (V7)** — `input_snapshot_id`(입력 리포트 집합), 변경별 `affects`(함께 바뀌어야 하는 노드 ↔), 적용은 **복사본에 먼저 → 검증 오류면 중단**, 버전·변경안 파일은 임시 파일 → rename, 부분 승인은 A7.2 에 ⚑.
- **규칙 모드 제한 (V8)** — LLM 없이 만든(키워드 분류) Claim 으로는 A0 논제·A2.1 쟁점·A3.6.3·A4 리스크·A5 촉매를 **만들지 않는다**(숫자 노드만). 요약 머리에 "⚠ 규칙 모드".
- **평가 분리 (V9)** — `tests/golden/holdout`(최종 검증, 튜닝에 안 씀) / `extract`(개발). `eval-claims` 가 **잘못된 Claim 자동 승인률** 을, 새 `mindmap eval-issues` 가 종목별 쟁점 정답(`tests/golden/issues/<ticker>.json`) 대비 테마 재현·분류 일치·**이견 누락** 을 낸다.
- **문서 (V10)** — v3 의 P2.5 를 실제 순서로 정정.
- **생성 방식 (부록, 2026-10-22)** — 콘솔 「리포트로 자동 생성…」 의 기본은 **새로 만들기**: 그 종목의 기존 A 맵 **초안**(meta.draft 가 있는 것; B 맵이 참조하거나 사용자가 처음부터 만든 맵은 유지) 을 `data/maps/_archive/` 로, 이 리포트들의 Claim 저장본을 `data/claims/_archive/<시각>/` 로 옮기고(사람이 승인/반려한 Claim 수를 로그에) 전부 다시 뽑는다. **기존에 더하기** 를 고르면 기존 맵은 그대로 두고 새 리포트는 「변경안」으로 간다(저장본 재사용). CLI `draft --db … --mode replace|incremental`, 텍스트 콘솔은 "새로 만들까요?" 질문.

## Python API 로 한 바퀴

```python
from datetime import date
from kstock.templates import new_map_skeleton
from kstock.models import Driver, Measurement, Scenario, Rule, Position, MarketState, Source
from kstock.engine import evaluate_new_map, snapshot_to_holding, update_market, update_driver, \
    set_thesis_state, evaluate_holding, record_decision, rollup

a = new_map_skeleton("000000", "가온로보틱스", "growth", date(2026, 10, 19))
a["A0"].set("… 한 문장 논제 …")
a["A0.3"].set("M"); a["A0.4"].set(58000, source=Source(name="KRX"))
a["A1.1"].driver = Driver(current=Measurement(value=8.1), my_estimate=Measurement(value=13.0), next_check=date(2026, 11, 14))
# … A1.2, A1.3, A3.1~3(Scenario), A4.4.1(Rule, fact 기준), A6.5 …
ev = evaluate_new_map(a)              # A3.4 ER, A3.5 RR, A6.1 suggested, rr_min 게이트

b = snapshot_to_holding(a, Position(entry_date=date(2026, 10, 19), entry_price=58000, quantity=862, w_target=0.05))
update_market(b, MarketState(as_of=date(2027, 1, 15), price=76000, eps_fwd=3392, band_percentile=82, w_now=0.072))
update_driver(b, "B2.1.1", 9.0, as_of=date(2027, 1, 15))
set_thesis_state(b, "유효", today=date(2027, 1, 15))
res = evaluate_holding(b)             # 🟡 + 매트릭스 trim + 주가 분해(멀티플 주도)
record_decision(b, "trim", today=date(2027, 1, 15), reason="목표 비중 초과")
c = rollup([b], cash=80_000_000)
```

## 구조

```
src/kstock/
  models/      §2 공통 노드 스키마 (Node) · 하위 스키마 (Driver, Scenario, Rule, Event, DecisionLog) · 맵 컨테이너 · §8 프로파일
               · claims.py 설계 v3 데이터 계약 (EvidenceSpan · Claim · ClaimSet · Issue · IssueSet · Change · ChangeSet)
  templates/   §3.2 · §4.2 노드 정의 테이블 (질문 포함) → 스켈레톤 생성기
  engine/
    calc.py        §3.4 · §4.4 계산식 (기대수익률, 손익비, 비중 제안, 주가 분해, 비중/밸류 상태)
    rules.py       If-Then 조건식 DSL (eval 없음) 과 평가기
    status.py      §4.3 상태등 🟢🟡🔴
    matrix.py      §4.5 대응 결정 매트릭스
    validate.py    §3.3 완료 조건 · §10 검증 규칙 (meta_only 의존 금지, 스냅숏 불변 …)
    lifecycle.py   §7 A 평가 → 스냅숏 → B 운영 루프 → 결정 로그 → 종료/복기, 논제 복원 위자드
    rollup.py      §5 C 포트폴리오 롤업
  render/      native (기본 — 중심에서 양쪽 균형/방사형/오른쪽, 의존성 없음) · markmap (오른쪽 한 방향, CDN) · mermaid (문서 삽입용)
  reports/     리포트 파일 → 텍스트(loader: PDF/TXT/MD/HTML/DOCX, 페이지 경계 유지) → 사실(extract: 의견·목표가·현재가·추정치 표·기준(지배/연결)·문장)
               → 종목 합산(digest: 증권사별 최신 1건 · 45일 · 실적 전 표시 · spread) · dbcheck(리서치 DB 교차 확인)
               · claims(리포트 → Claim: 청크 → LLM/규칙 추출 → 인용 위치 확인) · validate_claims(3단계 검증: 인용 존재 · 숫자 일치 · 의미 지지)
  generate/    리포트 → A 맵 초안(설계 v3): kpi_catalog(섹터 KPI · 드라이버 사전) · rules(규칙 채움, 사람 몫은 pending) · scenarios(리포트 제시 케이스만)
               · issues(Claim → 테마/메커니즘 쟁점, 공통 견해/핵심 이견) · mapping(쟁점·Claim → A0/A1/A2.1/A3.6/A4/A5/A8.4) · check(교차검증)
               · changes(새 리포트 → 변경안 → 승인 반영 · 버전) · evals(골든 초안 · 추출/Claim 평가) · prompts+llm_fill(LLM 호출, 로컬 OpenAI 호환 포함) · draft(파이프라인)
  collect/     수집기 인터페이스 · CSV 오프라인 소스 · 규칙 기반 이벤트 분류기 · adapters/ (naver_quote 작성 시점 현재가, research_db 읽기 전용, DART·KRX·KIS·컨센서스 골격)
  llm/         노드 채움 (MockFiller / ClaudeFiller — 모델 자동 선택 models.py, 워크스페이스 헤더) · LLM 이벤트 분류기 · 프롬프트
  env.py       프로젝트 루트 .env 읽기 (python-dotenv 없이; 셸 환경변수 우선, 값은 로그에 남기지 않음)
  store.py     SQLite(관심종목·맵 인덱스·설정) + JSON 맵 저장소
  symbols.py   종목 찾기 — 코드↔이름 (symbols.csv · 리서치 DB · 관심종목)
  ops.py       화면이 부르는 조작 계층 — 폼 dict ↔ 맵, 평가·스냅숏·B 운영·롤업·렌더 (UI 비의존)
  console.py   GUI 콘솔 (tkinter, 리서치 콘솔과 같은 구조)
  console_text.py  번호 메뉴 텍스트 콘솔 (`mindmap console --text`)
  demo.py      §9 예시 인스턴스
  cli.py       mindmap 명령 (콘솔 스크립트; draft · claims · claims-review · issues · changes · golden-draft · eval-extract · eval-claims · llm-check · render …)
tools/          console.py 콘솔 실행기 · check_api_key.py ANTHROPIC_API_KEY 독립 확인 스크립트 (표준 라이브러리만)
tests/         unittest 164건 (pytest 로도 실행) — GUI 는 가짜 tkinter 로 스모크, 리포트 픽스처는 tests/fixtures/reports,
               골든 초안은 tests/golden/extract (verified:false 는 사람이 확인 전)
docs/          템플릿 v1.0 · 아키텍처 메모 · 리포트→마인드맵 과정(report-to-mindmap.md) · 자동 생성 계획 · 개선 검토(report-draft-improvements.md) · 설계 v2/v3/v4(report-draft-design-v2.md, -v3.md, -v4.md)
examples/      데모 출력 (JSON · HTML) · claims_experiment/ 삼성전자 3건 Claim 실험 (사람이 쓴 Claim → 쟁점 → 맵, 요약 HTML)
data/          reports/ 원본 리포트 · claims/ ClaimSet(리포트별) · issues/ IssueSet(종목별) · changes/ 변경안 · maps/versions/ 맵 버전
```

## 마인드맵 HTML — 중심에서 사방으로

기본 렌더러(`render/native.py`)는 외부 라이브러리 없이 바닐라 JS 로 레이아웃을 계산한다. 1단계 가지를 **좌/우로 나눠**
(큰 가지부터 세로 길이가 짧은 쪽에 배정) 중심 노드가 가운데 오고, 자식은 부모 바깥쪽으로 세로로 쌓이므로 상·하·좌·우로
고르게 퍼진다. HTML 안의 선택 상자로 **방사형**(각도를 박스 크기에 비례해 나누고 겹치지 않을 만큼 반지름을 키움)과
**오른쪽 한 방향**(markmap 식)으로 바꿀 수 있다. 노드 클릭으로 접고 펼치고(기본 2단계까지 펼침, 접힌 가지는 `+N`),
드래그·휠로 이동·확대, `f` 로 화면 맞춤, ☀/☾ 테마. markmap 이 필요하면 `--format markmap`.

**노드 복사 (cut & paste)** — 노드 글자는 마우스로 긁어 복사할 수 있고, 노드에 마우스를 올리면 나오는 `⧉` 가 그 줄을
클립보드로 보낸다. **우클릭 메뉴**: 한 줄 · 상세(값·질문·출처·기준일·상태·태그·연결) · 이 가지 전체(Markdown 목록) ·
노드 id · 상세 보기(복사용 글상자). 노드를 고른 뒤 `Ctrl+C` 는 한 줄, `Ctrl+Shift+C` 는 상세, 툴바 「전체 복사」는 맵 전체를
Markdown 으로.

**노드 편집** — 노드를 **더블클릭**(또는 마우스를 올리면 나오는 `✎`, 우클릭 「편집…」, `F2`) 하면 오른쪽에 편집 폼이 열린다.
라벨·값·질문·출처·기준일·다음 확인일·상태·확신도·태그·연결을 고칠 수 있고, 드라이버·시나리오·규칙·이벤트 노드는 값 대신
그 구조의 필드(현재/컨센/내 추정 값, 목표가·확률, 조건식·행동, 날짜·사실…) 가 나온다 (기준일·출처·근거는 「세부 필드」 안).
우클릭 「자식 노드 추가…」(`Insert`) 로 새 노드를, 「이 노드 삭제」(`Delete`, 두 번 누름) 로 가지째 지운다. 「적용」 은 화면에만
반영되고(✎/＋ 표시, 왼쪽 위 「고침 n건」 막대 — `Ctrl+Z` 되돌리기), **「저장」** 이 맵에 넣는다.

- `mindmap edit <맵 id|맵.json>` 이나 콘솔 「보기 → 고른 맵 HTML 편집…」 로 연 페이지는 **로컬 편집 서버**(127.0.0.1, 토큰 있는 주소)
  를 통해 「저장」 이 맵 JSON 에 바로 들어간다 — 적용 전 원본을 `maps/versions/` 에 보존하고 `meta.version` 을 올리며, A7.2
  업데이트 로그에 한 줄 남기고 HTML 을 다시 쓴다. 콘솔은 저장을 감지해 맵을 다시 읽는다.
- `file://` 로 연 정적 HTML 에서는 「편집 파일 내려받기」 가 `<맵>.edits.json` 을 주고, `mindmap apply-edits <파일>` 또는 콘솔
  「편집 파일 반영…」 이 같은 방식으로 맵에 넣는다.
- 규칙: 편집은 먼저 복사본에 적용해 pydantic 검증과 맵 검증을 통과해야 한다 — **편집 전보다 검증 오류가 늘면 거절**(막대에 ⚠ 로 미리
  보인다; 초안에 원래 있던 미입력 오류는 저장을 막지 않는다). 값이 바뀐 노드는 이전 값을 history 에 남기고(출처 "사용자 편집 (HTML)"),
  `user` 태그를 얻고 `draft`·`pending`·⚑ 가 떨어진다 — 변경안(changes) 은 그 노드를 자동 적용하지 않는다. 루트와 「기본 정보」 묶음은
  지울 수 없고, 새 노드 id 는 부모 아래 다음 번호(`A4.6`, 루트 아래는 `A9`, 기본 정보 아래는 `A0.5`) 다. 페이지를 연 뒤 맵이 달라졌으면
  (base_hash) 저장을 거절하니 새로고침 뒤 다시 고친다.

2026-10-23 이전에 만든 HTML 은 다시 렌더링해야(`mindmap render …` 또는 콘솔 「마인드맵 HTML 열기」) 복사·편집 기능이 들어간다.

## 설계 결정 (코드에 박힌 것)

| 원칙 | 구현 |
|---|---|
| 매입가는 판단 입력이 아니다 | `B0.5` 는 `meta_only=True`. computed/judgment 노드의 `links`/`formula` 가 참조하면 `validate` 오류 |
| 대응은 세 입력만 | `engine.matrix.decide(thesis, valuation, weight)`. 다른 결정은 `record_decision(..., reason=…)` 에 `override=True` 로 남는다 |
| 스냅숏 불변 | `snapshot_to_holding` 이 B1.1~B1.4 해시를 저장, 수정 시 검증 오류. 논제가 바뀌면 B2.3 = `변질` |
| 선별 | `compute_light` 는 코드 규칙만 쓴다. LLM 은 노드를 채울 뿐 상태등·매트릭스에 관여하지 않는다 |
| 비중 밴드 | `min(dev_abs 3%p, dev_rel 30% × 목표)` — 둘 중 먼저 걸리는 쪽. 스냅숏 시 B7 에 상한 규칙이 자동 생성된다 |
| rr_min 게이트 | 손익비가 프로파일 최소치 미만이면 `A6.5 = BUY` 를 `WATCH` 로 강제 |
| 비중 제안 vs 확정 | `A6.1 = {"suggested": 계산값, "confirmed": 사람 확정, "reason"}` |

임계값은 모두 `kstock.models.Settings` 와 `PROFILES` 에서 바꿀 수 있다.

## 테스트 · 린트

```bash
pytest                 # 또는: python -m unittest discover -s tests -p "test_*.py"
ruff check src tests
```

GitHub Actions 용 워크플로는 `docs/github-workflow-ci.yml` 에 있다. 저장소를 만들면 `.github/workflows/ci.yml` 로 복사한다
(원격 도구가 `.github/` 아래에는 쓸 수 없어 docs 에 두었다).

## 실제 예시 — 삼성전자 (2026-10-20)

`examples/samsung_005930/build.py` 가 공개 자료(리서치 DB·공시·기사, 전부 `Source` 에 출처)로 채운 A 맵을 만든다.
드라이버는 분기 영업이익·HBM 점유율·2027 HBM4 판가, 시나리오는 Bear 17만/Base 36만/Bull 52만(현재가 21.8만),
기대수익률 +57%·손익비 2.96. 「내 추정」은 초안이고 판단은 WATCH 다 — 숫자를 고치고 판단하는 것은 논제의 주인이다.
자동 생성기가 만들어 내야 할 결과물의 모양이기도 하다 (`docs/auto-generation-plan.md`).

## 다음 단계 (템플릿 §11)

1. 수집기 어댑터 실제 구현 — `collect/adapters/` 의 `NotImplementedError` 자리 (DART 공시, KRX/KIS 시세·잔고, 컨센서스). `.env.example` 참고.
2. 자동 초안 고도화 — 리서치 DB 의 리포트 PDF(`pdf_url`) 를 받아 종목코드 하나로 초안 만들기(계획 M1), 골든 맵(삼성전자) 대비 수정률 측정(M6), 공시 원문 → 드라이버 값 추출.
3. 운영 루프 스케줄러 — `next_check` 와 B7 규칙을 주기적으로 평가해 🟡🔴 알림 (cron / Windows 작업 스케줄러 / 에이전트).
4. 저장소 — 맵 본문도 SQLite 로 옮기고 `history`, `decision_log` 를 질의·통계 (지금은 관심종목·인덱스만 SQLite).
5. 복기 통계 — `C6.2` 결정 유형별 6/12개월 적중률 (현재는 로그 수집만).
6. 웹 뷰 — markmap HTML 을 넘어 🟢🟡🔴 보드 + 종목 맵 전환 UI.
