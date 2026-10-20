"""`.env` 읽기 — 값은 환경변수로만 가고, 셸 환경변수가 우선하며, 로그에는 키 이름만 남는다."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import D0  # noqa: F401  (sys.path 설정)
from kstock import env as envmod
from kstock.env import describe, find_dotenv, load_dotenv, parse_dotenv

SAMPLE = """# 주석
export DART_API_KEY=abc123
ANTHROPIC_API_KEY = sk-test-000   # 뒤 주석
KSTOCK_LLM_MODEL="claude-sonnet-4-5"
QUOTED='a # b'
EMPTY=
BROKEN LINE
"""


class TestDotenv(unittest.TestCase):
    def setUp(self):
        envmod._tried = False
        envmod._loaded_from = None
        envmod._loaded_keys = []

    tearDown = setUp

    def test_parse(self):
        d = parse_dotenv(SAMPLE)
        self.assertEqual(d["DART_API_KEY"], "abc123")
        self.assertEqual(d["ANTHROPIC_API_KEY"], "sk-test-000")
        self.assertEqual(d["KSTOCK_LLM_MODEL"], "claude-sonnet-4-5")
        self.assertEqual(d["QUOTED"], "a # b")
        self.assertEqual(d["EMPTY"], "")
        self.assertNotIn("BROKEN", d)

    def test_load_prefers_shell_env_and_hides_values(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / ".env"
            p.write_text(SAMPLE, encoding="utf-8")
            with mock.patch.dict(os.environ, {"KSTOCK_ENV": str(p), "DART_API_KEY": "from-shell"}, clear=False):
                for k in ("ANTHROPIC_API_KEY", "KSTOCK_LLM_MODEL", "QUOTED", "EMPTY"):
                    os.environ.pop(k, None)
                self.assertEqual(find_dotenv(), p)
                keys = load_dotenv()
                self.assertIn("ANTHROPIC_API_KEY", keys)
                self.assertNotIn("DART_API_KEY", keys)                     # 셸 값이 우선
                self.assertEqual(os.environ["DART_API_KEY"], "from-shell")
                self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "sk-test-000")
                line = describe("ANTHROPIC_API_KEY")
                self.assertIn(".env 에서 읽음", line)
                self.assertNotIn("sk-test", line)                          # 값은 절대 안 보인다
                self.assertEqual(load_dotenv(), keys)                      # 두 번째는 캐시
                from kstock.generate.llm_fill import has_key, llm_status

                self.assertTrue(has_key())
                self.assertNotIn("sk-test", llm_status())
                for k in keys:
                    os.environ.pop(k, None)

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, {"KSTOCK_ENV": str(Path(t) / "none.env")}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            with mock.patch.object(envmod, "ROOT", Path(t)), mock.patch("pathlib.Path.cwd", return_value=Path(t)):
                self.assertIsNone(find_dotenv())
                self.assertEqual(load_dotenv(), [])
                self.assertIn("없음", describe("ANTHROPIC_API_KEY"))


if __name__ == "__main__":
    unittest.main()
