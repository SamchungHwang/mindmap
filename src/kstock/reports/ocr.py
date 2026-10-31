"""이미지(스캔) PDF → 본문 — tesseract OCR (2026-10-31, 두산에너빌리티 company_97320.pdf 가 글자 156자뿐이었다).

선택 기능이다. 설치돼 있을 때만 쓰고, 없으면 `loader.pdf_text` 가 「이미지 PDF — OCR 못 함 (이유)」 경고를 남긴다:
- `tesseract` 실행 파일 + 한국어 데이터 `kor.traineddata` (Windows: UB-Mannheim 설치본에서 Korean 선택; Linux: `tesseract-ocr-kor`)
- 페이지 → 그림: `pdftoppm`(poppler) 또는 `pypdfium2`
환경 변수: `KSTOCK_OCR=0` 끔 · `KSTOCK_OCR_LANGS` (기본 `kor+eng`) · `KSTOCK_OCR_DPI` (기본 220).

OCR 본문은 숫자·표가 틀릴 수 있어 `ReportDoc.extractor == "ocr"` 와 경고로 표시한다 — 재무 표 정합성 층(digest) 이 뒤에서 거른다.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

DEFAULT_LANGS = "kor+eng"
OCR_DPI = 220
OCR_MAX_PAGES = 24
#: 추출된 글자 가운데 한글이 쪽당 이보다 적으면 「글자가 아니라 그림」 인 PDF 로 본다 (차트 축 라벨·숫자만 잡힌 경우)
MIN_HANGUL_PER_PAGE = 20
_HANGUL = re.compile(r"[가-힣]")


def ocr_langs() -> str:
    return os.environ.get("KSTOCK_OCR_LANGS") or DEFAULT_LANGS


def looks_scanned(text: str, pages: int) -> bool:
    """글자가 거의 없거나, 있어도 한글이 쪽당 MIN_HANGUL_PER_PAGE 미만이면 이미지 PDF 다."""
    n = len(_HANGUL.findall(text or ""))
    return n < MIN_HANGUL_PER_PAGE * max(1, pages)


@lru_cache(maxsize=1)
def tesseract_langs() -> frozenset[str]:
    exe = shutil.which("tesseract")
    if not exe:
        return frozenset()
    try:
        out = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    langs = {ln.strip() for ln in (out.stdout + out.stderr).splitlines() if re.fullmatch(r"[a-z_]{3,}", ln.strip())}
    return frozenset(langs)


def _has_pdfium() -> bool:
    try:
        import pypdfium2  # type: ignore  # noqa: F401
    except ImportError:
        return False
    return True


def ocr_available(langs: str | None = None) -> tuple[bool, str]:
    """(쓸 수 있나, 못 쓰는 이유)."""
    if os.environ.get("KSTOCK_OCR", "1").strip() in ("0", "off", "false"):
        return False, "KSTOCK_OCR=0"
    if not shutil.which("tesseract"):
        return False, "tesseract 없음"
    want = [x for x in (langs or ocr_langs()).split("+") if x]
    missing = [x for x in want if x not in tesseract_langs()]
    if missing:
        return False, f"tesseract 언어 데이터 없음: {', '.join(missing)}"
    if not (shutil.which("pdftoppm") or _has_pdfium()):
        return False, "pdftoppm(poppler) 또는 pypdfium2 없음"
    return True, ""


def render_pages(path: Path, out_dir: Path, *, dpi: int = OCR_DPI, max_pages: int = OCR_MAX_PAGES) -> list[Path]:
    """PDF 쪽 → PNG 파일들 (쪽 순서). pdftoppm 이 있으면 그것, 없으면 pypdfium2."""
    exe = shutil.which("pdftoppm")
    if exe:
        subprocess.run([exe, "-r", str(dpi), "-png", "-l", str(max_pages), str(path), str(out_dir / "p")],
                       capture_output=True, check=True, timeout=600)
        return sorted(out_dir.glob("p-*.png"), key=lambda q: int(re.sub(r"\D", "", q.stem) or 0))
    import pypdfium2 as pdfium  # type: ignore

    pdf = pdfium.PdfDocument(str(path))
    out: list[Path] = []
    for i in range(min(len(pdf), max_pages)):
        img = pdf[i].render(scale=dpi / 72).to_pil()
        q = out_dir / f"p-{i + 1:03d}.png"
        img.save(q)
        out.append(q)
    return out


def ocr_image(img: Path, langs: str, *, psm: int = 3, timeout: int = 180) -> str:
    exe = shutil.which("tesseract")
    if not exe:
        raise RuntimeError("tesseract 없음")
    out = subprocess.run([exe, str(img), "stdout", "-l", langs, "--psm", str(psm)], capture_output=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError((out.stderr or b"").decode("utf-8", "replace")[:200])
    return out.stdout.decode("utf-8", "replace")


def ocr_pdf(path: str | Path, *, langs: str | None = None, dpi: int | None = None, max_pages: int = OCR_MAX_PAGES) -> tuple[str, int, list[str]]:
    """(본문, 쪽수, 경고). 쪽 사이는 '\\f' — loader 가 다른 추출기와 같은 꼴로 받는다."""
    langs = langs or ocr_langs()
    dpi = dpi or int(os.environ.get("KSTOCK_OCR_DPI") or OCR_DPI)
    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="kstock_ocr_") as t:
        imgs = render_pages(Path(path), Path(t), dpi=dpi, max_pages=max_pages)
        parts: list[str] = []
        for img in imgs:
            try:
                parts.append(ocr_image(img, langs))
            except (RuntimeError, subprocess.SubprocessError) as e:
                warnings.append(f"OCR {img.stem}: {str(e)[:80]}")
                parts.append("")
    if len(imgs) >= max_pages:
        warnings.append(f"OCR 은 앞 {max_pages}쪽까지만")
    return "\n\f".join(parts), len(parts), warnings


__all__ = ["DEFAULT_LANGS", "MIN_HANGUL_PER_PAGE", "OCR_DPI", "OCR_MAX_PAGES", "looks_scanned", "ocr_available", "ocr_langs", "ocr_pdf",
           "render_pages", "tesseract_langs"]
