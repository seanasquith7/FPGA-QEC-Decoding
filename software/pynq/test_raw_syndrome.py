"""Round/shot integrity checks for the distance-3 raw syndrome export."""

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from generate_test_data import generate_dataset
from prepare_stream_data import PackingConfig, prepare_stage1


class RawSyndromeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dataset = Path(directory.name) / "data"
        self.meta = generate_dataset(self.dataset, shots=29, rounds=3, noise=0)
        profile = Path(__file__).with_name("packing_example.local.json")
        self.config = PackingConfig.from_dict(json.loads(profile.read_text()))

    def test_round_bytes_match_raw_readout_across_shots(self):
        raw = np.load(self.dataset / "raw_syndrome.npy")
        self.assertEqual(raw.shape, (29, 3, 8))
        self.assertTrue(raw.any())
        np.testing.assert_array_equal(raw[:, 1:], raw[:, :-1])
        self.assertEqual(self.meta["raw_syndrome"]["stabilizer_qubit_ids"],
                         [2, 9, 11, 13, 14, 16, 18, 25])
        self.assertEqual(self.meta["raw_syndrome"]["measurement_indices"],
                         np.arange(24).reshape(3, 8).tolist())
        payload, layout = prepare_stage1(self.dataset, self.config)
        expected = np.packbits(raw, axis=2, bitorder="little").ravel()
        np.testing.assert_array_equal(payload, expected)
        self.assertEqual(layout["bytes_per_record"], 1)

    def test_corrupt_raw_file(self):
        with (self.dataset / "raw_syndrome.npy").open("ab") as stream:
            stream.write(b"x")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            prepare_stage1(self.dataset, self.config)

    def test_wrong_mapping(self):
        self.meta["raw_syndrome"]["stabilizer_qubit_ids"].reverse()
        (self.dataset / "metadata.json").write_text(json.dumps(self.meta))
        with self.assertRaisesRegex(ValueError, "mapping"):
            prepare_stage1(self.dataset, self.config)
