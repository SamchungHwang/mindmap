# holdout — 최종 검증용 정답표 (설계 v4 V9)

여기 든 리포트·정답표는 **프롬프트·드라이버 사전·임계값을 고칠 때 보지 않는다.** `tests/golden/extract`(개발·회귀) 로 고친 뒤
마지막에 한 번 `mindmap eval-extract --golden tests/golden/holdout` · `mindmap eval-claims --golden tests/golden/holdout --claims <폴더>` 로 잰다.

- 새 종목 리포트가 들어오면 **먼저 여기에** `mindmap golden-draft --reports … --out tests/golden/holdout` 로 초안을 만들고 사람이 확인한다.
- 개발 중 holdout 결과를 보고 코드를 고쳤다면 그 파일은 `extract/` 로 옮긴다 — 더는 holdout 이 아니다.
- 모양은 `extract/` 와 같다 (`report_id`, `facts`, `estimates`, `required_claims`, `required_risks`, `candidate_review`, `verified`).

현재 후보: NAVER(035420) · 두산에너빌리티(034020) 리포트 — `data/reports/` 에 PDF 만 있고 정답표는 아직 없다.
