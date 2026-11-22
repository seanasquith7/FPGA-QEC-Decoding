"""Offline acceptance tests for packing and Stage 1 input integrity."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from generate_test_data import generate_dataset
from prepare_stream_data import PackingConfig, load_stage1, pack_records, prepare_stage1


def profile(width, **overrides):
    return PackingConfig.from_dict({
        "input_semantics": "detector_events", "word_bits": 32,
        "bit_order": "lsb-first", "byte_order": "little",
        "column_order": list(range(width)), "record_order": "shot-major",
        "profile_status": "local-test-only", **overrides,
    })


def independent_unpack(payload, layout):
    """Decode using Python integers instead of the NumPy packing algorithm."""
    config = layout["config"]
    word_bytes = config["word_bits"] // 8
    record_bytes = layout["bytes_per_record"]
    rows = []
    raw = payload.tobytes()
    for offset in range(0, len(raw), record_bytes):
        row = [0] * layout["bits_per_record"]
        for column, source_column in enumerate(config["column_order"]):
            word_offset = offset + (column // config["word_bits"]) * word_bytes
            word = int.from_bytes(raw[word_offset:word_offset + word_bytes], config["byte_order"])
            position = column % config["word_bits"]
            if config["bit_order"] == "msb-first":
                position = config["word_bits"] - 1 - position
            row[source_column] = (word >> position) & 1
        rows.append(row)
    return np.array(rows, dtype=np.uint8)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_known_bytes_bit_and_byte_order(self):
        bits = np.array([[1, 0, 1], [0, 1, 0]], dtype=np.uint8)
        for bit_order, byte_order, expected in [
            ("lsb-first", "little", b"\x05\x00\x02\x00"),
            ("lsb-first", "big", b"\x00\x05\x00\x02"),
            ("msb-first", "little", b"\x00\xa0\x00\x40"),
            ("msb-first", "big", b"\xa0\x00\x40\x00"),
        ]:
            with self.subTest(bit_order=bit_order, byte_order=byte_order):
                payload, _ = pack_records(bits, profile(3, word_bits=16,
                    bit_order=bit_order, byte_order=byte_order))
                self.assertEqual(payload.tobytes(), expected)

    def test_all_word_sizes_and_cross_word_boundaries(self):
        rng = np.random.default_rng(4)
        for word_bits in (8, 16, 32, 64):
            for width in (1, 24, word_bits, word_bits + 1):
                bits = rng.integers(0, 2, size=(7, width), dtype=np.uint8)
                for bit_order in ("lsb-first", "msb-first"):
                    for byte_order in ("little", "big"):
                        with self.subTest(word_bits=word_bits, width=width,
                                          bit_order=bit_order, byte_order=byte_order):
                            payload, layout = pack_records(bits, profile(width,
                                word_bits=word_bits, bit_order=bit_order,
                                byte_order=byte_order, column_order=list(reversed(range(width)))))
                            np.testing.assert_array_equal(independent_unpack(payload, layout), bits)
                            self.assertEqual(payload.dtype, np.uint8)
                            self.assertTrue(payload.flags.c_contiguous)
                            self.assertFalse(layout["hardware_ready"])

    def test_zero_padding_is_per_record(self):
        payload, layout = pack_records(np.ones((2, 24), dtype=np.uint8), profile(24))
        self.assertEqual(payload.tobytes(), b"\xff\xff\xff\x00" * 2)
        self.assertEqual(layout["padding_bits_per_record"], 8)
        self.assertEqual(layout["bytes_per_record"], 4)

    def test_raw_round_ordering_with_synthetic_inputs(self):
        # These are synthetic transport patterns, not generated QEC syndromes.
        bits = np.array([[[1, 0], [0, 1]], [[1, 1], [0, 0]]], dtype=np.uint8)
        for order, expected in [("shot-major", b"\x01\x02\x03\x00"),
                                ("round-major", b"\x01\x03\x02\x00")]:
            payload, layout = pack_records(bits, profile(2, word_bits=8,
                input_semantics="raw_syndrome_rounds", record_order=order))
            self.assertEqual(payload.tobytes(), expected)
            arranged = bits if order == "shot-major" else bits.transpose(1, 0, 2)
            np.testing.assert_array_equal(independent_unpack(payload, layout), arranged.reshape(4, 2))

    def test_semantics_cannot_be_silently_changed(self):
        dataset = self.root / "legacy"
        generate_dataset(dataset, shots=2)
        path = dataset / "metadata.json"
        metadata = json.loads(path.read_text())
        del metadata["raw_syndrome"]
        path.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, "not raw syndrome"):
            prepare_stage1(dataset, profile(24, input_semantics="raw_syndrome_rounds"))
        with self.assertRaises(ValueError):
            pack_records(np.zeros((2, 24), dtype=np.uint8), profile(24, record_order="round-major"))
        with self.assertRaises(ValueError):
            pack_records(np.zeros((2, 3, 8), dtype=np.uint8), profile(8))

    def test_bad_bits_and_config_rejected(self):
        for bits in (np.array([[2]]), np.array([[-1]]), np.array([[0.5]]), np.zeros((0, 1))):
            with self.subTest(bits=bits), self.assertRaises(ValueError):
                pack_records(bits, profile(1))
        for changes in ({"word_bits": 24}, {"word_bits": True},
                        {"column_order": [0, 0]}, {"column_order": [1, 2]},
                        {"bit_order": "unknown"}, {"byte_order": "native"},
                        {"profile_status": "confirmed"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                pack_records(np.zeros((1, 2), dtype=np.uint8), profile(2, **changes))
        with self.assertRaises(ValueError):
            PackingConfig.from_dict({"word_bits": 32})

    def test_dataset_roundtrip_and_truth_is_not_exported(self):
        dataset = self.root / "dataset"
        generate_dataset(dataset, shots=37)
        bits, _ = load_stage1(dataset)
        payload, layout = prepare_stage1(dataset, profile(24))
        np.testing.assert_array_equal(independent_unpack(payload, layout), bits)
        self.assertEqual(payload.nbytes, 37 * 4)
        self.assertNotIn("observables", json.dumps(layout))
        self.assertEqual(layout["source"]["input_semantics"], "detector_events")

    def test_corruption_and_missing_hash_rejected(self):
        dataset = self.root / "dataset"
        generate_dataset(dataset, shots=2)
        path = dataset / "detectors.01"
        path.write_bytes(b"x" + path.read_bytes()[1:])
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            load_stage1(dataset)

    def test_invalid_rows_rejected_even_with_updated_hash(self):
        dataset = self.root / "dataset"
        generate_dataset(dataset, shots=2)
        for raw in (b"0\n", b"x" * 24 + b"\n" + b"0" * 24 + b"\n",
                    b"0" * 24 + b"\r\n" + b"0" * 24 + b"\r\n"):
            (dataset / "detectors.01").write_bytes(raw)
            metadata_path = dataset / "metadata.json"
            meta = json.loads(metadata_path.read_text())
            meta["files_sha256"]["detectors.01"] = hashlib.sha256(raw).hexdigest()
            metadata_path.write_text(json.dumps(meta))
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                load_stage1(dataset)

    def test_cli_export_and_overwrite_refusal(self):
        dataset = self.root / "dataset"
        generate_dataset(dataset, shots=10)
        here = Path(__file__).resolve().parent
        out = self.root / "packed"
        command = [sys.executable, str(here / "prepare_stream_data.py"),
                   "--dataset", str(dataset), "--config", str(here / "packing_example.local.json"),
                   "--output", str(out)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LOCAL TEST ONLY", result.stdout)
        self.assertEqual({p.name for p in out.iterdir()}, {"payload.bin", "layout.json"})
        layout = json.loads((out / "layout.json").read_text())
        original = (out / "payload.bin").read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(), layout["payload_sha256"])
        self.assertEqual(layout["payload_nbytes"], 30)
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((out / "payload.bin").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
