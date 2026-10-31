"""텍스트 픽스처 → PDF 픽스처 (Chromium/Playwright 로 인쇄). 테스트가 PDF 추출 경로를 타게 하려고 한 번 만들어 둔다.

    python tests/fixtures/reports/make_pdfs.py

Playwright + Chromium 이 있어야 한다 (없으면 그냥 .txt 픽스처만 쓴다). 2단 조판 흉내: 본문 일부를 두 칸으로 놓는다.
"""

from __future__ import annotations

import html
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

CSS = """
body { font-family: 'Noto Sans CJK KR', 'Noto Sans CJK SC', 'Malgun Gothic', sans-serif; font-size: 10.5pt; margin: 28px; }
pre { white-space: pre-wrap; font-family: inherit; margin: 0; }
.cols { display: flex; gap: 18px; } .cols pre { flex: 1; }
h1 { font-size: 15pt; margin: 4px 0 10px; }
"""


def to_html(text: str) -> str:
    lines = text.split("\n")
    head, body = lines[:9], lines[9:]
    # 본문 가운데 일부를 2단으로 — 추출기가 섞인 줄을 견디는지 보기 위해
    mid = len(body) // 2
    left, right = body[:mid], body[mid:]
    return (f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>"
            f"<pre>{html.escape(chr(10).join(head))}</pre>"
            f"<div class='cols'><pre>{html.escape(chr(10).join(left))}</pre><pre>{html.escape(chr(10).join(right))}</pre></div>"
            f"</body></html>")


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright 가 없습니다 — PDF 픽스처를 만들지 않습니다")
        return 1
    txts = sorted(HERE.glob("*.txt"))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for t in txts:
            page.set_content(to_html(t.read_text(encoding="utf-8")))
            out = t.with_suffix(".pdf")
            page.pdf(path=str(out), format="A5", print_background=True)
            print(f"{t.name} → {out.name} ({out.stat().st_size:,} bytes)")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
