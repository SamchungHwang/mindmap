# 리포트 → A 맵 초안 예시

`*.txt` / `*.pdf` 는 **가상의 증권사 리포트 3건**(하나·KB·키움 스타일, 숫자는 지어낸 것)이다. 실제 리포트의 표지·추정치 표
모양을 흉내 내 추출기를 시험하려고 만들었다 (`tests/fixtures/reports/` 와 같은 파일, PDF 는 `make_pdfs.py` 로 인쇄).

```powershell
mindmap draft --reports examples\reports_draft\*.pdf --date 2026-10-20 -o examples\reports_draft\A_005930_draft.json --html
```

- `A_005930_draft.json` / `.html` — 규칙 채움만으로 만든 초안 (LLM 없음). 판단 WATCH, 채운 노드 ✎(draft), 봐야 할 곳 ⚑(warn).
- `draft_summary.txt` — 콘솔에 찍히는 요약 (리포트 목록·컨센서스·추정치 표·드라이버·시나리오·검증·깃발·생성 메모).

진짜 리포트로 하려면 같은 종목의 PDF 들을 골라 콘솔 「A 맵 채우기 → 리포트로 자동 생성…」 을 누르면 된다.
