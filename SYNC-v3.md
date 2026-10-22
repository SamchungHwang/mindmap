# 설계 v3 (v0.3.0) 동기화 안내 — 2026-10-21

PC(`C:\kstock\mindmap`) 와 클라우드 작업본의 연결이 끊겨 변경분을 zip 으로 보낸다.
이 zip 은 **프로젝트 트리 전체**(소스·테스트·문서·예시·Claim 실험 데이터) 이고, 다음은 **들어 있지 않다**:
`.env`(비밀) · `data/reports/*.pdf`(원본 리포트, PC 에 이미 있음) · `data/db` · `data/maps` · `data/out` · 캐시(`__pycache__`, `.ruff_cache`).
즉 PC 의 개인 데이터는 건드리지 않고 코드·문서만 덮어쓴다.

## 적용 (PowerShell, conda `ksa`)

```powershell
cd C:\kstock\mindmap
# 1) 혹시 모를 로컬 수정 보존
Compress-Archive -Path src,tests,tools,docs,examples,README.md,pyproject.toml -DestinationPath ..\mindmap_backup_$(Get-Date -Format yyyyMMdd_HHmm).zip
# 2) 덮어쓰기 (같은 경로의 파일은 교체, 없는 파일은 추가 — 삭제는 하지 않음)
Expand-Archive -Path $HOME\Downloads\kstock-mindmap-v3.zip -DestinationPath C:\kstock\mindmap -Force
# 3) 패키지 메타 갱신 + 테스트
pip install -e . --no-deps
python -m unittest discover -s tests -q     # 137 tests OK 기대
ruff check src tests tools examples          # (ruff 가 있으면)
```

`Expand-Archive` 는 zip 안 최상위가 바로 `src/ tests/ …` 라 `C:\kstock\mindmap` 에 그대로 풀린다.
`SHA256SUMS.txt` 로 확인하려면:

```powershell
Get-Content SHA256SUMS.txt | ForEach-Object {
  $h, $p = $_ -split '\s+', 2
  $actual = (Get-FileHash -Algorithm SHA256 $p).Hash.ToLower()
  if ($actual -ne $h) { "MISMATCH $p" }
}
```

(출력이 없으면 전부 일치.)

## 삭제해야 할 파일

없음. v3 는 파일을 더하거나 바꾸기만 했다.

## 적용 뒤 바로 해 볼 것

```powershell
# LLM 연결 (이미 ✓ 확인됨)
mindmap llm-check

# 1. 삼성전자 3건에서 Claude 로 Claim 추출 (+3단 검증) — 결과를 별도 폴더에
mindmap claims --reports data\reports\삼성전자\*.pdf --ticker 005930 --show --out data\claims_llm

# 2. 사람이 쓴 Claim 31건(examples\claims_experiment) 과 비교 — 재현율 · 숫자 불일치율
mindmap eval-claims --claims data\claims_llm

# 3. 쟁점 표 (추출한 ClaimSet 폴더에서) / 초안 전체 (JSON + HTML)
mindmap issues --claims data\claims_llm --ticker 005930
mindmap draft  --reports data\reports\삼성전자\*.pdf --ticker 005930 --name 삼성전자 --llm -o data\out\A_005930_v3.json --html

# 4. 골든 초안 13건 확인 → 맞으면 "verified": true 로 바꾼다
mindmap eval-extract --golden tests\golden\extract
```

GUI 콘솔(`python tools\console.py`) 에서는 「리포트로 자동 생성…」 뒤 A 탭의 「시나리오 후보…」「쟁점」, 작업 메뉴의
「검토 대기함」「출처 열기」「변경안」이 v3 기능이다. 자세한 것은 README 의 "리포트로 A 맵 자동 생성 (초안) — 설계 v3" 절.
