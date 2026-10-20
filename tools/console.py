"""kstock 콘솔을 띄운다.

    python tools/console.py
    python tools/console.py --db data/db/kstock.sqlite

이 파일은 실행기다. 화면은 `src/kstock/console.py` 에 있다. 여기에 논리를 두면
`pip install` 로 깔았을 때 따라오지 않는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kstock.console import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
