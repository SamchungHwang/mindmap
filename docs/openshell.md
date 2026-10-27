검토해 보니 먼저 용어를 바로잡아야 합니다. **OpenShell** 은 NVIDIA 가 오픈소스로 낸 "에이전트 런타임"(샌드박스 + 정책 + 게이트웨이 + 자격증명 라우팅)이고, 모델이나 지식베이스를 직접 제공하진 않습니다. DGX Spark 에서 실제로 쓰는 묶음은 **NemoClaw** (OpenShell 위에 OpenClaw/Hermes 에이전트 하네스 + 로컬 모델을 한 번에 깔아 주는 블루프린트)이고, "open model" 은 Ollama/vLLM 으로 띄우는 Nemotron 3 Super/Nano·Qwen3.6, "knowledge 구축" 은 OpenShell 이 아니라 **하네스(OpenClaw) 의 workspace 메모리·memory_search·skills·플러그인** 으로 합니다. 이 전제로 순서를 정리했습니다. CLI 가 빠르게 바뀌고 있어(최근 문서에서 `openshell inference set/get` 이 provider profile 방식으로 대체됨) 설치 시점의 버전을 고정하고 그 버전 문서를 보는 걸 권합니다.

## 0. 준비·결정 (Spark 만지기 전)

- 역할을 정합니다. 에이전트가 할 일은 "새 리포트 폴더 감시 → `mindmap draft/changes` 실행 → 변경안·체크포인트·Kill 규칙 발동을 요약해 Telegram 으로 알림 → 사용자가 HTML 편집으로 판단" 까지이고, 판단(BUY)·확률·내 추정·A2.2/A2.3 은 지금 설계대로 사람 몫으로 남깁니다. 이 경계를 나중에 AGENTS.md 에 그대로 적게 됩니다.
- 모델을 고릅니다. Nemotron 3 Super 120B-A12B(Ollama Q4, 약 87GB, GPU 메모리 약 90GB, 응답 30~90초) 는 품질, Nemotron 3 Nano(약 27GB) 는 여유, NemoClaw 최신 기본값은 Qwen3.6-35B(vLLM NVFP4 로 2.6배 빠름) 입니다. mindmap 의 Claim 추출은 긴 리포트 + JSON 출력이라 처음엔 Super 로 품질을 보고, 상시 운영은 Nano/Qwen 으로 내리는 식이 현실적입니다.
- build.nvidia.com 에서 `NVIDIA_API_KEY` 를 받아 둡니다. 전부 로컬로 돌려도 NemoClaw CLI 게이트웨이가 키를 요구합니다.

## 1. DGX Spark OS·런타임 준비

- Ubuntu 24.04 + 최신 드라이버 확인(`nvidia-smi`), Docker 28 이상 + NVIDIA 컨테이너 런타임(`sudo nvidia-ctk runtime configure --runtime=docker`), `docker run --rm --runtime=nvidia --gpus all ubuntu nvidia-smi` 로 검증, `sudo usermod -aG docker $USER`.
- Spark 전용 필수 수정: `/etc/docker/daemon.json` 에 `"default-cgroupns-mode": "host"` 를 넣고 Docker 재시작합니다. 이게 없으면 OpenShell 게이트웨이(k3s 기반) 초기화가 `cgroup ... pids.max: no such file` 로 실패합니다.
- Node.js 22 로 올립니다(GX10 출하본은 v18 이라 설치기가 거부). 포트 8080(게이트웨이)·18789(OpenClaw UI) 가 비어 있는지, 모델 + 샌드박스 이미지(1.5GB) + 리포트 데이터 디스크(100GB 이상 여유) 를 확인합니다.

## 2. 로컬 open model 서빙

- Ollama 설치 후 반드시 systemd override 로 `OLLAMA_HOST=0.0.0.0` 을 두고 재시작합니다(샌드박스는 `host.openshell.internal:11434` 로 접근하므로 127.0.0.1 바인딩이면 못 닿음). `ollama pull nemotron-3-super:120b` (또는 `nemotron-3-nano`), `ollama run` 으로 한 번 GPU 에 올려 둡니다.
- 지식 검색용 임베딩 모델도 같은 Ollama 에 하나 받아 둡니다(OpenClaw memory_search 가 로컬 임베딩을 쓸 수 있도록). 성능이 급하면 vLLM + NVFP4 Qwen3.6-35B 를 OpenAI 호환 포트로 띄우는 2안을 준비합니다.
- 호스트에서 `curl http://0.0.0.0:11434/v1/models` 로 OpenAI 호환 응답을 확인해 둡니다. mindmap 의 `make_filler("local")` 이 이 엔드포인트를 그대로 씁니다.

## 3. OpenShell + NemoClaw 설치·온보딩

- `curl -fsSL https://www.nvidia.com/nemoclaw.sh | bash` → `sudo nemoclaw setup-spark` → `nemoclaw onboard`. 온보딩이 preflight·게이트웨이·샌드박스 빌드·추론 선택(Local Ollama)·provider 생성·OpenClaw 기동·정책 프리셋(pypi/npm) 7단계를 돌리고 마지막에 `http://127.0.0.1:18789/#token=…` URL 을 한 번만 보여 주니 저장합니다. SSH 로 할 땐 `NEMOCLAW_NON_INTERACTIVE=1` 이 안전합니다.
- 알려진 걸림돌: 설치기가 exit 243 을 내도 `nemoclaw --version` 이 되면 정상, `npm link` EACCES 는 `sudo npm link`, OpenShell 바이너리 URL 404 는 릴리스 tar.gz 로 대체(`file` 로 ELF 인지 확인), 온보딩 2단계의 k3s reconciler 로그 수백 줄은 오류가 아닙니다.
- provider 를 Ollama 로 고정합니다(버전에 따라 `openshell provider create --name ollama-local --type openai --config "OPENAI_BASE_URL=http://host.openshell.internal:11434/v1"` 또는 profile import 방식). 확인은 `nemoclaw my-assistant connect` 뒤 `openclaw agent --agent main --local -m "hello"`, 모니터링은 호스트의 `openshell term`.

## 4. mindmap 를 샌드박스에 넣고 로컬 모델로 돌리기

- 코드는 샌드박스 안에서 `git clone` + `pip install -e ".[llm,data]"`(또는 `openshell sandbox upload`) 로 넣고, `data/reports`·`data/db`·`data/maps`·`data/claims` 는 호스트 볼륨 또는 upload 로 옮깁니다. `C:\kstock\research` 는 읽기 전용 원칙대로 복사본만 가져가고 원본은 건드리지 않습니다.
- `.env` 의 키를 파일로 넣지 않습니다. 외부 API 가 꼭 필요하면 OpenShell provider/credential 로 등록해 승인된 엔드포인트에서만 치환되게 하고, 로컬 추론은 `LLM_BASE_URL=http://host.openshell.internal:11434/v1`, `LLM_MODEL=nemotron-3-super:120b` 로 `make_filler("local")` 경로를 씁니다.
- 네트워크 정책을 최소로 엽니다: `host.openshell.internal:11434`, pypi, 현재가 조회(네이버), DART/KRX 등 실제로 쓰는 호스트만. 거부된 요청은 `openshell rule get <sandbox> --status pending` → `approve/reject` 로 처리합니다(파일시스템·프로세스 규칙은 샌드박스 생성 시 고정, 네트워크는 핫리로드).
- 검증 순서: `KSTOCK_OFFLINE=1 python -m unittest discover -s tests -q`(208건) → `mindmap draft --reports … --claims llm --llm-kind local` 로 삼성전자 3건을 돌려 Claim 품질(`verified` 비율, 쟁점 수) 을 클라우드 결과와 비교 → `eval-drivers 5/5`·`test_v6` 골든 재현. 여기서 Nemotron 의 JSON 준수율이 낮으면 프롬프트(`generate/prompts.py`) 나 모델을 바꿉니다.

## 5. 에이전트로 운영 (OpenClaw workspace)

- `~/.openclaw/workspace` 의 부트스트랩 파일을 채웁니다. AGENTS.md 에 운영 규칙(판단·확률·내 추정은 쓰지 말 것, 자동 맵은 WATCH, 빈 칸은 미확정, LLM 은 민감도·밸류 숫자를 만들지 않음), TOOLS.md 에 mindmap CLI 목록(`draft` · `changes` · `valuation` · `scenario` · `implied` · `eval-drivers` · `apply-edits`) 과 경로·DB 위치, USER.md 에 당신의 투자 유형·관심 종목·알림 선호를 적습니다. 파일이 20,000자를 넘으면 잘리니 짧게 유지합니다.
- 반복 작업은 `skills/<name>/SKILL.md` 로 만듭니다. 예: "새 리포트 PDF 가 들어오면 `draft --claims llm --mode incremental` → 변경안 요약 → 승인 대기", "장 마감 뒤 현재가 갱신 → A7.1 체크포인트·Kill 규칙 발동 여부 → 한 줄 보고". HEARTBEAT.md 에 하루 한 번 돌릴 체크리스트를 두면 상시 운영이 됩니다.
- 채널은 Telegram(@BotFather 토큰을 `TELEGRAM_BOT_TOKEN` 으로 두고 `nemoclaw onboard`, 첫 메시지의 페어링 코드를 `openclaw pairing approve telegram <CODE>`) 이 가장 간단합니다. tkinter 콘솔은 샌드박스에서 못 쓰니 텍스트 콘솔과 CLI 만 쓰고, HTML 편집은 `openshell forward start 18789`(또는 편집 서버 포트) + SSH 터널로 PC 에서 엽니다.
- 자기 진화형이 필요하면 Hermes Agent 하네스(NemoClaw 지원) 도 선택지입니다. 반복 패턴을 스스로 SKILL.md 로 쓰고, `snapshot.sh`/`restore.sh` 로 skills·memory·세션을 보존하며 자격증명은 필터로 뺍니다.

## 6. Knowledge 구축

- 1층은 OpenClaw 메모리입니다. `MEMORY.md` 에 종목별 논제·결정·Kill 조건 요약(큐레이션), `memory/YYYY-MM-DD.md` 에 일일 로그가 자동으로 쌓이고, `memory_search`(sqlite-vec/LanceDB/QMD 인덱스 + 임베딩, 로컬 임베딩 가능) 가 검색합니다. 임베딩을 2단계의 Ollama 모델로 지정해 데이터가 밖으로 안 나가게 합니다.
- 2층은 mindmap 이 이미 만드는 구조화 지식입니다. ClaimSet(`data/claims`)·IssueSet·맵 JSON·`docs/report-draft-design-v*.md`·devlog 가 사실상 지식베이스이므로, 이를 종목별 Markdown(논제·드라이버·쟁점·시나리오·밸류 한 장) 으로 내보내 workspace 아래 `knowledge/` 에 두고 인덱스에 포함시킵니다. 이 export 명령(`mindmap export-knowledge`) 은 mindmap 쪽에 하나 추가해야 합니다.
- 더 큰 지식그래프가 필요하면 Basic Memory 플러그인(`openclaw plugins install @basicmemory/openclaw-basic-memory`, Markdown 지식그래프 + 하이브리드 검색 + `/ingest`·`/research` 명령) 을 붙이고, 사내형 RAG 가 필요해지면 NVIDIA AI-Q Blueprint 를 봅니다. 처음부터 AI-Q 는 과합니다.
- 지식에 넣지 말 것: `.env`·토큰·`~/.openclaw/` 내부, 리서치 원본의 저작권 자료 전문(요약·Claim 만), 그리고 사용자의 판단을 모델이 "학습" 하지 않도록 판단 필드는 읽기만 하고 제안값 수락률(`eval-suggestions`) 로 앵커링을 감시합니다.

## 7. 운영·보안·백업

- 정책 최소권한을 유지하고 `openshell term` 으로 거부 로그를 주기적으로 봅니다. 프롬프트 인젝션은 샌드박스로도 완전히 막히지 않으니 새 도구는 격리된 샌드박스에서 먼저 시험합니다.
- 상태 보존: 맵·Claim·버전 파일은 git(비밀 제외), 에이전트 상태는 NemoClaw 의 state 마이그레이션 또는 Hermes snapshot, 모델은 Ollama 캐시. PC `C:\kstock\mindmap` 과 Spark 는 git remote 로 맞추는 게 지금의 SHA256SUMS 방식보다 편합니다.
- 버전 고정: `OPENSHELL_VERSION=…` 으로 설치하고 NemoClaw·OpenClaw 버전을 기록해 두며, 업그레이드 전 `nemoclaw <name> status`·테스트 208건·삼성전자 재현(골든) 을 돌리는 체크리스트를 둡니다.

## 8. mindmap 쪽에서 미리 할 작업

- `make_filler("local")` 의 JSON 모드·타임아웃(120B 는 응답 수십 초)·컨텍스트 길이(리포트 1건 9,000자 materials) 를 Nemotron/Qwen 기준으로 점검하고, 프롬프트 버전(`CLAIM_PROMPT_VERSION`) 을 올려 저장본과 구분합니다.
- 비대화형 운영용 작은 명령 추가: 리포트 폴더 감시 → draft/changes 일괄 실행과 결과 요약(JSON/Markdown), `export-knowledge`, 체크포인트·Kill 발동 알림 텍스트. 콘솔 없이 CLI 만으로 끝나야 에이전트가 쓸 수 있습니다.
- 경로 정리: Windows 경로(`data\reports\삼성전자`) 를 Linux 경로로 통일하고, `KSTOCK_OFFLINE`·`LLM_*` 환경변수만으로 전부 설정되게 합니다.

원하시면 이 중 8번(에이전트용 CLI·export-knowledge)은 지금 코드에 바로 붙일 수 있고, 5~6번의 AGENTS.md·TOOLS.md·SKILL.md 초안도 mindmap 설계 원칙에 맞춰 써 드릴 수 있습니다.

Sources:
- [NVIDIA/OpenShell (GitHub)](https://github.com/NVIDIA/OpenShell)
- [OpenShell 문서 — Supported Agents](https://docs.nvidia.com/openshell/v0.0.116/about/supported-agents.md)
- [OpenShell 문서 — Run Your First Agent](https://docs.nvidia.com/openshell/about/run-your-first-agent.md)
- [OpenShell 문서 — Inference (latest)](https://docs.nvidia.com/openshell/latest/how-it-works/inference.md)
- [NVIDIA 블로그 — Build a More Secure, Always-On Local AI Agent with OpenClaw and NemoClaw](https://developer.nvidia.com/blog/build-a-secure-always-on-local-ai-agent-with-nvidia-nemoclaw-and-openclaw/)
- [NVIDIA 블로그 — Run Local AI Agents with Faster Models and Multi-Node Clustering on DGX Spark](https://developer.nvidia.com/blog/run-local-ai-agents-with-faster-models-and-multi-node-clustering-on-nvidia-dgx-spark/)
- [NVIDIA 블로그 — Deploy Self-Evolving Agents with a Hermes Agent and NemoClaw](https://developer.nvidia.com/blog/deploy-self-evolving-agents-for-faster-more-secure-research-with-a-hermes-agent-and-nvidia-nemoclaw/)
- [NemoClaw 문서 — Overview](https://docs.nvidia.com/nemoclaw/0.0.16/about/overview.html)
- [DGX Spark 핸즈온 — I tried running NVIDIA NemoClaw on DGX Spark (Classmethod)](https://dev.classmethod.jp/en/articles/dgx-spark-nemoclaw-openshell-handson/)
- [How to Install NemoClaw on DGX Spark (4 Undocumented Fixes) — ai-muninn](https://ai-muninn.com/en/blog/nemoclaw-install-gx10-from-scratch)
- [dgx-spark-playbooks — CLI Coding Agent and OpenShell Sandbox (DeepWiki)](https://deepwiki.com/NVIDIA/dgx-spark-playbooks/3.6-cli-coding-agent-and-openshell-sandbox)
- [OpenClaw 문서 — Agent Workspace](https://huggingface.co/spaces/darkfire514/OpenClawBot/blob/main/docs/concepts/agent-workspace.md)
- [OpenClaw Memory — how it works (CometAPI)](https://www.cometapi.com/openclaw-memory-how-it-works-why-it-matters-and-how-you-control-it/)
- [Basic Memory — OpenClaw Plugin](https://docs.basicmemory.com/raw/integrations/openclaw.md)
- [NVIDIA 보도자료 — Open Agent Development Platform (GTC 2026)](https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Ignites-the-Next-Industrial-Revolution-in-Knowledge-Work-With-Open-Agent-Development-Platform/default.aspx)
- [OpenShell Research — Documentation](https://nvidia.github.io/OpenShell-Research/documentation/)