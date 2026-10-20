"""리포트 파일 → 본문 텍스트.

증권사 리포트는 대부분 PDF 다. 텍스트 추출기는 설치된 것을 앞에서부터 쓴다:
`pypdf` → `pdfminer.six` → `pdfplumber` → `pdftotext`(poppler CLI). 하나도 없으면 ReportError 로
무엇을 설치해야 하는지 알려 준다 (`pip install kstock[reports]` = pypdf).

PDF 가 아닌 것도 받는다: `.txt` `.md` (그대로), `.html` `.htm` (태그 제거), `.docx` (word/document.xml).
스캔 이미지 PDF 는 글자가 나오지 않으므로 `ReportDoc.is_empty` 가 True — 부르는 쪽이 경고한다.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import unicodedata
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

TEXT_SUFFIXES = {".txt", ".md", ".text"}
HTML_SUFFIXES = {".html", ".htm"}
DOCX_SUFFIXES = {".docx"}
PDF_SUFFIXES = {".pdf"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | HTML_SUFFIXES | DOCX_SUFFIXES | PDF_SUFFIXES

#: 이보다 글자가 적으면 "읽히지 않은" 문서로 본다 (스캔 PDF, 암호화, 빈 파일).
MIN_CHARS = 200


class ReportError(ValueError):
    """사람이 고칠 수 있는 입력 오류 — 파일이 없다, 형식을 모른다, PDF 추출기가 없다."""


@dataclass
class ReportDoc:
    path: Path
    text: str
    kind: str                 # pdf | txt | md | html | docx
    extractor: str            # pypdf | pdfminer | pdfplumber | pdftotext | text | html | docx
    pages: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return len(self.text.strip()) < MIN_CHARS

    @property
    def name(self) -> str:
        return self.path.name

    def head(self, n: int = 4000) -> str:
        return self.text[:n]


# ---------------------------------------------------------------------------- PDF
def _pdf_pypdf(path: Path) -> tuple[str, int]:
    from pypdf import PdfReader  # type: ignore

    reader = PdfReader(str(path))
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            pass
    parts = [(page.extract_text() or "") for page in reader.pages]
    return "\n\f".join(parts), len(parts)


def _pdf_pdfminer(path: Path) -> tuple[str, int]:
    from pdfminer.high_level import extract_text  # type: ignore

    text = extract_text(str(path)) or ""
    return text, max(1, text.count("\f"))


def _pdf_pdfplumber(path: Path) -> tuple[str, int]:
    import pdfplumber  # type: ignore

    parts: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n\f".join(parts), len(parts)


def _pdf_pdftotext(path: Path) -> tuple[str, int]:
    exe = shutil.which("pdftotext")
    if not exe:
        raise ImportError("pdftotext not found")
    out = subprocess.run([exe, "-enc", "UTF-8", str(path), "-"], capture_output=True, check=True, timeout=120)
    text = out.stdout.decode("utf-8", "replace")
    return text, max(1, text.count("\f"))


PDF_EXTRACTORS = (("pypdf", _pdf_pypdf), ("pdfminer", _pdf_pdfminer), ("pdfplumber", _pdf_pdfplumber),
                  ("pdftotext", _pdf_pdftotext))


def pdf_text(path: Path) -> tuple[str, int, str, list[str]]:
    """(text, pages, extractor, warnings). 추출기를 차례로 시도하고, 글자가 나온 첫 결과를 쓴다."""
    warnings: list[str] = []
    missing: list[str] = []
    for name, fn in PDF_EXTRACTORS:
        try:
            text, pages = fn(path)
        except ImportError:
            missing.append(name)
            continue
        except Exception as e:  # noqa: BLE001 — 깨진 PDF, 암호화 등: 다음 추출기로
            warnings.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
            continue
        if len(text.strip()) >= MIN_CHARS:
            return text, pages, name, warnings
        warnings.append(f"{name}: 글자 {len(text.strip())}자 — 스캔 이미지일 수 있다")
    if len(missing) == len(PDF_EXTRACTORS):
        raise ReportError("PDF 텍스트 추출기가 없습니다 — `pip install pypdf` (또는 `pip install kstock[reports]`)")
    return "", 0, "none", warnings


# ---------------------------------------------------------------------------- 그 외 형식
class _HtmlText(HTMLParser):
    SKIP = {"script", "style", "head", "noscript"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article", "td", "th"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n" if tag not in ("td", "th") else " ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n" if tag not in ("td", "th") else " ")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_text(raw: str) -> str:
    p = _HtmlText()
    p.feed(raw)
    return html.unescape("".join(p.parts))


def docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return html.unescape(xml)


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------------------- 정리
_WS_LINE = re.compile(r"[ \t ]+")


def normalize_text(text: str) -> str:
    """전각 숫자·기호를 반각으로, 줄 안의 연속 공백을 하나로. 줄 구조는 유지한다 (표 파싱에 필요)."""
    t = unicodedata.normalize("NFKC", text)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WS_LINE.sub(" ", ln).strip() for ln in t.split("\n")]
    # 빈 줄 3개 이상은 2개로
    out: list[str] = []
    blank = 0
    for ln in lines:
        if ln == "":
            blank += 1
            if blank > 2:
                continue
        else:
            blank = 0
        out.append(ln)
    return "\n".join(out)


# ---------------------------------------------------------------------------- 진입점
def load_report(path: str | Path) -> ReportDoc:
    p = Path(path)
    if not p.exists():
        raise ReportError(f"파일이 없습니다 — {p}")
    if p.is_dir():
        raise ReportError(f"폴더입니다 (파일을 지정하세요) — {p}")
    suffix = p.suffix.lower()
    warnings: list[str] = []
    pages = 0
    if suffix in PDF_SUFFIXES:
        text, pages, extractor, warnings = pdf_text(p)
        kind = "pdf"
    elif suffix in TEXT_SUFFIXES:
        text, extractor, kind = _read_text(p), "text", suffix.lstrip(".")
    elif suffix in HTML_SUFFIXES:
        text, extractor, kind = html_text(_read_text(p)), "html", "html"
    elif suffix in DOCX_SUFFIXES:
        text, extractor, kind = docx_text(p), "docx", "docx"
    else:
        raise ReportError(f"지원하지 않는 형식 {suffix!r} — PDF/TXT/MD/HTML/DOCX 만 받습니다 ({p.name})")
    doc = ReportDoc(path=p, text=normalize_text(text), kind=kind, extractor=extractor, pages=pages, warnings=warnings)
    if doc.is_empty:
        doc.warnings.append(f"{p.name}: 본문 글자가 {len(doc.text.strip())}자 — 스캔 PDF 이거나 비어 있다")
    return doc


def load_reports(paths: list[str | Path]) -> list[ReportDoc]:
    """여러 파일. 하나라도 형식 오류면 ReportError (조용히 건너뛰지 않는다 — 사용자가 고른 파일이다)."""
    if not paths:
        raise ReportError("리포트 파일을 하나 이상 지정하세요")
    return [load_report(p) for p in paths]


__all__ = ["ReportDoc", "ReportError", "load_report", "load_reports", "pdf_text", "html_text", "docx_text",
           "normalize_text", "SUPPORTED_SUFFIXES", "MIN_CHARS"]
