import os
import unittest

from tests.fresh_eyes.helpers import tmpdir
from fe_common import FEError, read_json, run, write_json


class CommonTest(unittest.TestCase):
    def test_write_read_json_keeps_cyrillic(self):
        with tmpdir() as d:
            p = os.path.join(d, "a", "x.json")
            write_json(p, {"t": "Импорт мест"})
            with open(p, encoding="utf-8") as f:
                self.assertIn("Импорт мест", f.read())
            self.assertEqual(read_json(p), {"t": "Импорт мест"})

    def test_run_raises_feerror_on_failure(self):
        with self.assertRaises(FEError):
            run(["python3", "-c", "import sys; sys.exit(3)"])

    def test_run_returns_stdout_bytes(self):
        self.assertEqual(run(["python3", "-c", "print('ok')"]).stdout.strip(), b"ok")


if __name__ == "__main__":
    unittest.main()
