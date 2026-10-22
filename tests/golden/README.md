# 정답표 (설계 v3 P0)

- `extract/<report_id>.json` — 리포트 1건의 수치·제목 정답 + 필수 주장(`required_claims`/`required_risks`) + 후보 검토표(`candidate_review`).
  `mindmap golden-draft --reports …` 가 추출기 결과로 초안(`verified: false`) 을 만든다. 사람이 PDF 와 대조해 고치고 `verified: true` 로 바꾼다
  (true 인 파일은 초안 생성이 덮지 않는다). PDF 자체는 저장소 밖(`data/reports/…`) — `file` 에 경로만.
- `issues/<ticker>.json` — 종목별 쟁점 정답 (v4 V9): `required_issues` 행마다 `driver_id`·`display`(공통 견해/핵심 이견/독립 주장/미해결 쟁점)·
  `brokers_disagree`. `mindmap eval-issues` 가 테마 재현·분류 일치·**이견 누락**(가장 비싼 실수, 목표 0) 을 낸다. IssueSet 은 `data/issues/<ticker>.json`.
- `holdout/` — 최종 검증용. 프롬프트·사전 튜닝에 쓰지 않는다 (README 참고). `extract/` 는 개발·회귀용.
- `mindmap eval-extract [--verified-only] [--misses]` — 필드별 정확도. 가격류는 정확히, 추정치 표는 0.5% 허용.

주장 정답의 모양:
```json
{"id": "rc1", "proposition": "HBM4 공급 확대가 2027년 메모리 영업이익 증가를 견인한다", "roles": ["thesis"], "direction": "positive",
 "quote": "원문에서 그대로 복사한 40자 이상"}
```
`candidate_review` 는 AI 가 뽑은 Claim 이 실제로 원문의 지지를 받는지 사람이 표기한다 — 근거 불일치율의 분모. 행에 `proposition` 을 함께 적으면
claim_id 가 달라도(다른 추출기·프롬프트) 명제로 맞춘다. `eval-claims` 는 여기서 **잘못된 Claim 자동 승인률**(기계 verified 인데 사람은 '지지 안 됨') 도 낸다.
