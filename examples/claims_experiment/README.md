# P2.5 최소 실험 — 삼성전자 리포트 3건 (설계 v3)

판단할 질문 하나: **키워드 분류보다 증권사들의 투자 논리를 더 정확하고 유용하게 비교하는가?**

- `samsung_claims_answers.json` — 사람이 LLM 역할로 쓴 Claim 답안 (리포트별 10~11개, 인용은 원문 그대로). 실제 Claude 추출과 비교하는 기준이자 정답표 초안.
- `run_experiment.py` — 답안을 실제 파이프라인(인용 위치 찾기 → 인용·숫자·의미 3단 검증) 에 통과시켜 `data/claims/hk_9658{8,97},hk_96634.json` 로 저장하고,
  `tests/golden/extract/*.json` 의 필수 주장·리스크·후보 검토표를 채운다.
- `make_summary.py` → `samsung_experiment.html` — 비교표 · 쟁점 표(테마→메커니즘, 증권사별 ↑↓·–) · 리포트별 Claim(페이지·인용) · A 맵 초안 요약 · A2.1 쟁점 노드.
- `A_005930_v3_draft.json / .html` — Claim/Issue 를 매핑한 v3 초안 (시나리오·확률·내 추정·Kill 은 공란 = 미확정).

실제 Claude 로 같은 것을 만들려면 (Windows, `.env` 키 동작 확인 뒤):

```powershell
mindmap claims --reports data\reports\삼성전자\*.pdf --ticker 005930 --show --out data\claims_llm
mindmap issues --claims data\claims_llm --ticker 005930
mindmap draft --reports data\reports\삼성전자\*.pdf --ticker 005930 --llm --date 2026-10-21 -o maps\A_005930_llm.json --html
```
