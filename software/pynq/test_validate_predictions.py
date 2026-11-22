"""Synthetic integration fixtures for normalized single-observable predictions."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from generate_test_data import generate_dataset
from validate_predictions import validate


def synthetic_fixture(dataset, decoder, flipped_ids):
    """Reverse the row order and inject known mismatches for offline checks."""
    dataset = Path(dataset)
    truth = (dataset / "observables.01").read_text().splitlines()
    return {
        "decoder": decoder, "source": "synthetic", "semantics": "logical_flip",
        "dataset_sha256": hashlib.sha256((dataset / "metadata.json").read_bytes()).hexdigest(),
        "predictions": [{"shot_id": i, "logical_flip": int(truth[i]) ^ int(i in flipped_ids)}
                        for i in reversed(range(len(truth)))],
    }


class ValidationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dataset = Path(temp.name) / "dataset"
        generate_dataset(self.dataset, shots=4)
        self.fpga = Path(temp.name) / "fpga.json"
        self.doc = synthetic_fixture(self.dataset, "fpga", {1})

    def check(self):
        self.fpga.write_text(json.dumps(self.doc))
        return validate(self.dataset, fpga=self.fpga)

    def test_reordered_shots_and_known_rates(self):
        # Deterministic asymmetric truth exposes validators that compare row positions.
        truth_path = self.dataset / "observables.01"
        truth_path.write_bytes(b"0\n1\n0\n1\n")
        meta_path = self.dataset / "metadata.json"
        meta = json.loads(meta_path.read_bytes())
        meta["files_sha256"]["observables.01"] = hashlib.sha256(truth_path.read_bytes()).hexdigest()
        meta_path.write_text(json.dumps(meta))
        self.doc = synthetic_fixture(self.dataset, "fpga", {1})
        result = self.check()
        self.assertIsNone(result["fpga_vs_software"])
        self.assertEqual(result["decoders"]["fpga"], {
            "source": "synthetic", "correct": 3, "errors": 1, "accuracy": .75,
            "logical_error_rate": .25})
        soft = self.fpga.with_name("software.json")
        software = synthetic_fixture(self.dataset, "software", {2, 3})
        software["predictions"].reverse()
        soft.write_text(json.dumps(software))
        result = validate(self.dataset, fpga=self.fpga, software=soft)
        self.assertEqual(result["decoders"]["software"]["errors"], 2)
        self.assertEqual(result["fpga_vs_software"],
                         {"matches": 1, "disagreements": 3, "agreement": .25})

    def test_missing_shot(self):
        self.doc["predictions"].pop()
        with self.assertRaisesRegex(ValueError, "count"):
            self.check()

    def test_duplicate_or_unknown_shot(self):
        for shot in (1, 4, -1, True):
            self.doc["predictions"][0]["shot_id"] = shot
            with self.subTest(shot=shot), self.assertRaisesRegex(ValueError, "shot ID"):
                self.check()

    def test_unknown_encoding_or_foreign_dataset(self):
        for key, value in (("semantics", "physical_mask"), ("dataset_sha256", "other"),
                           ("decoder", "software"), ("source", "unknown")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.doc = {**synthetic_fixture(self.dataset, "fpga", set()), key: value}
                self.check()

    def test_malformed_prediction(self):
        for value in (None, True, "1", 2):
            self.doc["predictions"][0]["logical_flip"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.check()

    def test_corrupt_truth(self):
        (self.dataset / "observables.01").write_bytes(b"1")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.check()

    def test_duplicate_source_field(self):
        data = json.dumps(self.doc).replace('"source": "synthetic"',
                                           '"source": "synthetic", "source": "hardware"')
        self.fpga.write_text(data)
        with self.assertRaisesRegex(ValueError, "Duplicate JSON field"):
            validate(self.dataset, fpga=self.fpga)
