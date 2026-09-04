"""Phase 05 CI assertions.

Three guarantees the submission rests on, asserted rather than intended:

  DETERMINISM    5 runs -> 1 hash. A reconciliation engine that produces
                 different books on re-run is disqualified in finance even when
                 it is right, which is the core of the AI-judgment argument.
  IDEMPOTENCE    applying a run twice leaves the ledger unchanged. This is what
                 makes an auto-applied match reversible, and reversibility -- not
                 certainty -- is what licenses skipping review.
  FALSE MATCH    the primary metric. INC-012 showed it can regress to 6.7% while
                 every individual step looks correct, so it is pinned here: no
                 future change can raise it without this failing immediately.
"""
import csv, json, pathlib, sys, unittest
from collections import defaultdict
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "harness"))
from leakledger.clock import BusinessCalendar                                  # noqa: E402
from leakledger.feeschedule import FeeSchedule                                 # noqa: E402
from leakledger.ledger import Ledger, apply_run                                # noqa: E402
from leakledger.money import Money                                             # noqa: E402
from leakledger.schema import ingest_rows                                      # noqa: E402
from leakledger.cascade.engine import (                                        # noqa: E402
    AUTO_APPLY, EXCEPTION, REVIEW, Cascade, covered_cycles_by_matching)
from leakledger.leakage import detectors                                       # noqa: E402

DATA = ROOT / "data" / "generated"
def _load(n):
    """Read a generated CSV. Uses a context manager so the handle is closed --
    the lambda this replaced leaked one per call and filled test runs with
    ResourceWarnings."""
    with (DATA / n).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))

# Pinned ceiling. Raising this requires a deliberate edit and a reason.
MAX_FALSE_MATCH_RATE = 0.0
MIN_SCOREABLE_MATCHES = 10


def pipeline():
    fs = FeeSchedule.from_file(ROOT / "config" / "fee_schedule.v1.json")
    cal = BusinessCalendar.from_file(ROOT / "config" / "holidays_2026.json")
    gw = ingest_rows("gateway", _load("gateway_payments.csv"))
    refunds, adj = _load("gateway_refunds.csv"), _load("gateway_adjustments.csv")
    bank = _load("bank_statement.csv")
    eng = Cascade(payments=gw.records, refunds=refunds, bank=bank,
                  adjustments=adj, calendar=cal)
    casc = eng.run()
    cov = covered_cycles_by_matching(eng, bank)
    found = detectors.run_all(fs=fs, payments=eng.t0.canonical, refunds=refunds, adjustments=adj,
                              bank_rows=bank, cascade_result=casc, calendar=cal,
                              as_of=date(2026, 7, 31), covered_cycles=cov)
    return eng, bank, casc, found


def run_signature(casc, found):
    """Content hash of a run's decisions and findings. Order-independent."""
    import hashlib
    rows = sorted(
        (m.bank_txn_id, m.tier, m.disposition, m.reason_code or "",
         tuple(sorted(m.matched_ids))) for m in casc.matches)
    frows = sorted((f.leak_class, f.entity_id, f.value.paise) for f in found.findings)
    return hashlib.sha256(
        json.dumps([rows, frows], sort_keys=True, default=str).encode("utf-8")).hexdigest()


class Determinism(unittest.TestCase):
    def test_five_runs_one_hash(self):
        """PLAN.md: determinism, 5 runs -> 1 hash."""
        sigs = set()
        for _ in range(5):
            eng, bank, casc, found = pipeline()
            sigs.add(run_signature(casc, found))
        self.assertEqual(len(sigs), 1,
                         f"5 runs produced {len(sigs)} distinct outcomes; the engine is "
                         f"non-deterministic and its books cannot be relied on")

    def test_ledger_state_hash_stable_across_runs(self):
        hashes = set()
        for _ in range(3):
            eng, bank, casc, found = pipeline()
            led = Ledger()
            apply_run(led, run_id="t", cascade_result=casc, findings=found,
                      payments=eng.t0.canonical)
            hashes.add(led.state_hash())
        self.assertEqual(len(hashes), 1)


class Idempotence(unittest.TestCase):
    def test_delta_ledger_is_zero_on_second_apply(self):
        """PLAN.md: idempotency, delta ledger = 0."""
        eng, bank, casc, found = pipeline()
        led = Ledger()
        first = apply_run(led, run_id="r1", cascade_result=casc, findings=found,
                          payments=eng.t0.canonical)
        h1, n1 = led.state_hash(), len(led)
        second = apply_run(led, run_id="r2", cascade_result=casc, findings=found,
                           payments=eng.t0.canonical)
        self.assertEqual(led.state_hash(), h1, "second apply changed ledger state")
        self.assertEqual(len(led), n1, "second apply added entries")
        self.assertEqual(second["posted"], 0, "second apply posted new entries")
        self.assertGreater(first["posted"], 0, "first apply posted nothing to test")

    def test_double_entry_holds(self):
        eng, bank, casc, found = pipeline()
        led = Ledger()
        apply_run(led, run_id="r", cascade_result=casc, findings=found, payments=eng.t0.canonical)
        self.assertEqual(led.trial_balance(), Money.zero(),
                         "ledger does not balance: money was created or destroyed")


class FalseMatchRate(unittest.TestCase):
    """The primary metric, pinned. See INC-012."""

    @classmethod
    def setUpClass(cls):
        cls.eng, cls.bank, cls.casc, cls.found = pipeline()
        cls.truth = json.loads((DATA / "ground_truth.json").read_text(encoding="utf-8"))

    def _audit(self):
        by_net = defaultdict(list)
        for s in self.truth["settlements"]:
            if s["paid"]:
                by_net[s["net_paise"]].append(s)
        correct = wrong = 0
        for m in self.casc.matches:
            if m.tier != "T3" or m.disposition not in (AUTO_APPLY, REVIEW):
                continue
            b = next(x for x in self.bank if x["txn_id"] == m.bank_txn_id)
            cands = by_net.get(Money.from_rupees_str(b["amount"]).paise, [])
            if len(cands) != 1:
                continue
            if set(cands[0]["payment_ids"]) == set(m.matched_ids):
                correct += 1
            else:
                wrong += 1
        return correct, wrong

    def test_false_match_rate_at_or_below_ceiling(self):
        correct, wrong = self._audit()
        total = correct + wrong
        self.assertGreaterEqual(total, MIN_SCOREABLE_MATCHES,
                                "too few scoreable matches for the rate to mean anything; "
                                "a change that stops matching would otherwise pass silently")
        rate = wrong / total
        self.assertLessEqual(rate, MAX_FALSE_MATCH_RATE,
                             f"false-match rate regressed to {rate:.4f} "
                             f"({wrong} wrong of {total}); ceiling is {MAX_FALSE_MATCH_RATE}")

    def test_adversarial_rows_all_refused(self):
        """UTR reused with an altered amount must never be accepted."""
        adv = [m for m in self.casc.matches if m.bank_txn_id.endswith("X")]
        self.assertGreater(len(adv), 0)
        for m in adv:
            self.assertEqual(m.disposition, EXCEPTION,
                             f"{m.bank_txn_id} accepted; the exact key was trusted blindly")

    def test_ambiguous_payouts_never_reach_the_ledger(self):
        led = Ledger()
        apply_run(led, run_id="r", cascade_result=self.casc, findings=self.found,
                  payments=self.eng.t0.canonical)
        for m in self.casc.matches:
            if m.reason_code == "AMBIGUOUS_SUBSET":
                self.assertNotIn(f"match:{m.bank_txn_id}", led._entries)


class GroundTruthConsistency(unittest.TestCase):
    """INC-014: a self-contradictory fixture silently mis-scores a correct engine."""

    def test_validator_passes(self):
        import subprocess
        out = subprocess.run([sys.executable, str(ROOT / "harness" / "validate_ground_truth.py")],
                             capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(out.returncode, 0,
                         f"ground truth is internally inconsistent:\n{out.stdout}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
