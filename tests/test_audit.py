import unittest, sys, pathlib, tempfile, json, shutil
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from leakledger.audit import AuditLog, AuditError, evidence_hash
from leakledger.manifest import RunManifest, file_sha256


class TestAppendOnly(unittest.TestCase):
    """Reversibility is what licenses an unreviewed auto-apply. It depends on
    this log never losing or rewriting an entry."""

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.path = self.dir / "audit.jsonl"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_entries_persist_in_order(self):
        log = AuditLog(self.path, run_id="r1")
        for i in range(5):
            log.append(stage="cascade", record_id=f"p{i}", decision="AUTO_APPLY", tier="T1")
        seqs = [e.seq for e in log.read_all()]
        self.assertEqual(seqs, [1, 2, 3, 4, 5])

    def test_reopening_appends_never_truncates(self):
        """A second run must not erase the first run's history."""
        a = AuditLog(self.path, run_id="r1")
        a.append(stage="ingest", record_id="p1", decision="ACCEPT")
        b = AuditLog(self.path, run_id="r2")
        b.append(stage="ingest", record_id="p2", decision="ACCEPT")
        entries = list(b.read_all())
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].run_id, "r1")
        self.assertEqual(entries[1].run_id, "r2")

    def test_sequence_continues_across_reopen(self):
        a = AuditLog(self.path, run_id="r1")
        a.append(stage="s", record_id="p1", decision="D")
        b = AuditLog(self.path, run_id="r2")
        e = b.append(stage="s", record_id="p2", decision="D")
        self.assertEqual(e.seq, 2)

    def test_corrupt_line_is_detected_not_ignored(self):
        self.path.write_text('{"seq": 1}\nNOT JSON\n', encoding="utf-8")
        with self.assertRaises(AuditError):
            AuditLog(self.path, run_id="r2")

    def test_every_entry_carries_decision_and_evidence_hash(self):
        log = AuditLog(self.path, run_id="r1")
        log.append(stage="cascade", record_id="p1", decision="EXCEPTION",
                   reason_code="AMBIGUOUS_SUBSET", evidence={"candidates": 2})
        e = list(log.read_all())[0]
        self.assertEqual(e.decision, "EXCEPTION")
        self.assertEqual(e.reason_code, "AMBIGUOUS_SUBSET")
        self.assertEqual(len(e.evidence_sha256), 64)

    def test_detail_payload_round_trips(self):
        log = AuditLog(self.path, run_id="r1")
        log.append(stage="leakage", record_id="p1", decision="FINDING",
                   reason_code="FEE_OVERCHARGE", delta_paise=1750)
        self.assertEqual(list(log.read_all())[0].detail["delta_paise"], 1750)

    def test_file_is_valid_jsonl(self):
        log = AuditLog(self.path, run_id="r1")
        log.append(stage="s", record_id="p1", decision="D")
        log.append(stage="s", record_id="p2", decision="D")
        for line in self.path.read_text(encoding="utf-8").strip().split("\n"):
            json.loads(line)


class TestEvidenceHash(unittest.TestCase):
    def test_key_order_does_not_change_hash(self):
        self.assertEqual(evidence_hash({"a": 1, "b": 2}), evidence_hash({"b": 2, "a": 1}))

    def test_different_content_changes_hash(self):
        self.assertNotEqual(evidence_hash({"a": 1}), evidence_hash({"a": 2}))

    def test_stable_across_calls(self):
        self.assertEqual(len({evidence_hash({"x": [1, 2, 3]}) for _ in range(20)}), 1)


class TestRunManifest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_records_config_hash(self):
        cfg = pathlib.Path(__file__).resolve().parents[1] / "config" / "fee_schedule.v1.json"
        m = RunManifest.start(config_paths=[cfg])
        self.assertEqual(m.config_hashes[str(cfg)], file_sha256(cfg))

    def test_manifest_written_and_reloadable(self):
        cfg = pathlib.Path(__file__).resolve().parents[1] / "config" / "fee_schedule.v1.json"
        m = RunManifest.start(config_paths=[cfg])
        m.note("phase 01 smoke")
        out = m.write(self.dir / "manifest.json")
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["run_id"], m.run_id)
        self.assertIn("phase 01 smoke", data["notes"])

    def test_run_ids_are_unique(self):
        self.assertNotEqual(RunManifest.start().run_id, RunManifest.start().run_id)

    def test_run_id_can_be_pinned_for_determinism_runs(self):
        self.assertEqual(RunManifest.start(run_id="fixed").run_id, "fixed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
