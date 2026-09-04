"""Numbers spoken aloud in the video cue sheet must match the harness.

A table can update while a sentence three files away still quotes the old figure.
That drift is invisible in review and fatal on camera, so the cue sheet is held to
the same standard as the README.
"""
import pathlib, re, sys, unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))


def _lakh(v):
    w, _, f = v.partition(".")
    head, tail = w[:-3], w[-3:]
    g = []
    while len(head) > 2:
        g.insert(0, head[-2:]); head = head[:-2]
    if head:
        g.insert(0, head)
    return ",".join(g + [tail])


class CueSheet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from payload import build_payload
        cls.P = build_payload()
        cls.flat = re.sub(r"\s+", " ", (ROOT / "SUBMISSION.md").read_text(encoding="utf-8"))

    def test_confirmed_figure_is_current(self):
        self.assertIn(_lakh(self.P["value"]["confirmed"]), self.flat,
                      "cue sheet quotes a stale confirmed figure")

    def test_flagged_figure_is_current(self):
        self.assertIn(_lakh(self.P["value"]["flagged"]), self.flat,
                      "cue sheet quotes a stale flagged figure")

    def test_refused_count_is_current(self):
        self.assertIn(f'{self.P["disposition"]["exception"]} records refused', self.flat)

    def test_false_match_rate_is_current(self):
        self.assertIn(f'{self.P["false_match"]["rate"]:.4f}', self.flat)

    def test_no_stale_gross_is_quoted_as_the_headline(self):
        """The gross must never be the spoken number."""
        self.assertIn("Do not say", self.flat)


if __name__ == "__main__":
    unittest.main(verbosity=2)
