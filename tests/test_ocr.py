"""이미지(스캔) PDF 의 OCR 폴백 (2026-10-31) — 두산에너빌리티 company_97320.pdf 는 12쪽에 글자 156자(차트 축 라벨) 뿐이었다.

tesseract 한국어 데이터는 시험 환경에 없을 수 있어, 영어 데이터가 있으면 영어 스캔 PDF 로 왕복을 확인하고 없으면 건너뛴다.
도구가 없을 때 「왜 못 읽었나」 를 경고에 남기는 것은 어디서나 확인한다.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kstock.reports.loader import MIN_CHARS, load_report, pdf_text
from kstock.reports.ocr import looks_scanned, ocr_available

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    Image = None  # type: ignore

LINES = [
    "Doosan Enerbility 3Q26 review. Operating profit 261.2 billion won, up 90.6 percent year on year.",
    "Annual order target 13.3 trillion won; remaining orders of 3.3 trillion won are scheduled this year.",
    "SMR contract of about 1 trillion won is pending signature after agreement on key terms.",
    "Gas turbine slot reservations run through 2031 deliveries; conversion to firm contracts is expected in 2027 and 2028.",
    "Medium gas turbine prototype testing continues with first shipment targeted for the second half of 2028.",
]


def _scan_pdf(path: Path, pages: int = 2) -> None:
    """글자가 그림으로만 든 PDF — 텍스트 추출기는 0자를 돌려준다."""
    w, h = 1654, 2339
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    imgs = []
    for p in range(pages):
        img = Image.new("RGB", (w, h), "white")
        d = ImageDraw.Draw(img)
        y = 200
        for ln in LINES if p == 0 else ["Second page: appendix charts only."]:
            for part in (ln[i:i + 60] for i in range(0, len(ln), 60)):
                d.text((120, y), part, fill="black", font=font)
                y += 60
            y += 30
        imgs.append(img)
    imgs[0].save(path, "PDF", resolution=200.0, save_all=True, append_images=imgs[1:])


class TestScannedDetection(unittest.TestCase):
    def test_looks_scanned_by_hangul_density(self):
        self.assertTrue(looks_scanned("260 KOSPI 210 160 110 60 25.10 26.2 × × × (원) 200,000 150,000", 12))   # 차트 라벨만 (97320)
        self.assertTrue(looks_scanned("", 1))
        self.assertFalse(looks_scanned("두산에너빌리티 3분기 연결 영업이익은 2,612억원으로 컨센서스를 상회했다. " * 3, 1))

    def test_disabled_by_env_gives_reason(self):
        with patch.dict(os.environ, {"KSTOCK_OCR": "0"}):
            ok, why = ocr_available()
        self.assertEqual((ok, why), (False, "KSTOCK_OCR=0"))

    @unittest.skipIf(Image is None, "PIL 없음")
    def test_scanned_pdf_without_ocr_keeps_reason_in_warnings(self):
        with tempfile.TemporaryDirectory() as t, patch.dict(os.environ, {"KSTOCK_OCR": "0"}):
            p = Path(t) / "scan.pdf"
            _scan_pdf(p, pages=1)
            doc = load_report(p)
        self.assertTrue(doc.is_empty)
        self.assertEqual(doc.extractor, "none")
        self.assertTrue(any("이미지 PDF — OCR 못 함 (KSTOCK_OCR=0)" in w and "tesseract" in w for w in doc.warnings), doc.warnings)
        self.assertTrue(any("스캔 PDF 이거나 비어 있다" in w for w in doc.warnings))


@unittest.skipIf(Image is None, "PIL 없음")
class TestOcrRoundTrip(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, {"KSTOCK_OCR_LANGS": "eng", "KSTOCK_OCR": "1"}):
            ok, why = ocr_available("eng")
        if not ok:
            self.skipTest(f"OCR 못 함: {why}")

    def test_english_scan_is_read_by_ocr(self):
        with tempfile.TemporaryDirectory() as t, patch.dict(os.environ, {"KSTOCK_OCR_LANGS": "eng", "KSTOCK_OCR": "1"}):
            p = Path(t) / "scan.pdf"
            _scan_pdf(p, pages=2)
            text, pages, extractor, warnings = pdf_text(p)
        self.assertEqual((extractor, pages), ("ocr", 2))
        self.assertGreaterEqual(len(text.strip()), MIN_CHARS)
        self.assertIn("13.3 trillion", text)
        self.assertIn("\f", text)                                                   # 쪽 경계는 다른 추출기와 같은 꼴
        self.assertTrue(any(w.startswith("OCR(tesseract eng) 로 읽음") for w in warnings), warnings)

    def test_ocr_can_be_turned_off_per_call(self):
        with tempfile.TemporaryDirectory() as t, patch.dict(os.environ, {"KSTOCK_OCR_LANGS": "eng", "KSTOCK_OCR": "1"}):
            p = Path(t) / "scan.pdf"
            _scan_pdf(p, pages=1)
            text, _pages, extractor, _w = pdf_text(p, ocr=False)
        self.assertEqual((text, extractor), ("", "none"))


if __name__ == "__main__":
    unittest.main()
