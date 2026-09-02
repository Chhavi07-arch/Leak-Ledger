"""Guard for INC-002.

The generator must produce byte-identical output across SEPARATE PROCESSES.
Running it twice inside one process would not catch the original defect: within
a single process, set iteration order is stable, so a same-process test passes
while the real bug (per-process string hash randomisation) survives. These tests
therefore shell out.
"""
import hashlib
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FILES = ["gateway_payments.csv", "bank_statement.csv", "erp_invoices.csv", "ground_truth.json"]


def _generate_and_hash():
    subprocess.run([sys.executable, str(ROOT / "data" / "generate.py")],
                   check=True, capture_output=True, cwd=ROOT)
    return {
        f: hashlib.sha256((ROOT / "data" / "generated" / f).read_bytes()).hexdigest()
        for f in FILES
    }


class TestGeneratorDeterminism(unittest.TestCase):
    def test_identical_across_separate_processes(self):
        a = _generate_and_hash()
        b = _generate_and_hash()
        for f in FILES:
            self.assertEqual(a[f], b[f], f"{f} differs across processes — INC-002 regression")

    def test_no_unordered_set_iteration_in_generator(self):
        """Static guard: the specific construct that caused INC-002.

        Catches the defect at review time rather than only when hashes diverge.
        """
        import re
        pattern = re.compile(r"for\s+\w+\s+in\s*\{")   # iterating a set literal/comprehension
        for path in (ROOT / "src" / "leakledger" / "generate").glob("*.py"):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.strip().startswith("#"):
                    continue
                self.assertIsNone(
                    pattern.search(line),
                    f"{path.name}:{i} iterates a set directly; wrap in sorted() — INC-002",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
