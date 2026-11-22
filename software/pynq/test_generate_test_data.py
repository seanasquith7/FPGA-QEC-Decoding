"""Run with: python -m unittest discover -s software/pynq -v."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import stim

from generate_test_data import generate_dataset, load_experiment


def read_bits(path, width):
    return stim.read_shot_data_file(path=str(path), format="01", num_detectors=width)


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_original_circuit_pairing_and_file_roundtrip(self):
        out = self.root / "paired"
        info = generate_dataset(out, shots=257, seed=7)
        circuit = load_experiment()["circuit"]
        self.assertEqual(stim.Circuit.from_file(str(out / "circuit.stim")), circuit)
        measurements = circuit.compile_sampler(seed=7).sample(257)
        expected_d, expected_o = circuit.compile_m2d_converter().convert(
            measurements=measurements, separate_observables=True)
        raw = np.load(out / "raw_syndrome.npy")
        np.testing.assert_array_equal(raw, measurements[:, :24].reshape(257, 3, 8))
        d = read_bits(out / "detectors.01", circuit.num_detectors)
        o = read_bits(out / "observables.01", circuit.num_observables)
        np.testing.assert_array_equal(d, expected_d)
        np.testing.assert_array_equal(o, expected_o)
        self.assertEqual(d.shape, (257, 24))
        self.assertEqual(o.shape, (257, 1))
        self.assertEqual(json.loads((out / "metadata.json").read_text()), info)
        for filename, digest in info["files_sha256"].items():
            self.assertEqual(hashlib.sha256((out / filename).read_bytes()).hexdigest(), digest)
        # Decimal text serialization can slightly round binary probabilities.
        saved_model = stim.DetectorErrorModel.from_file(str(out / "detector_error_model.dem"))
        self.assertTrue(saved_model.approx_equals(
            circuit.detector_error_model(decompose_errors=True), atol=1e-15))
        raw = (out / "detectors.01").read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertTrue(all(len(row) == 24 and set(row) <= {48, 49}
                            for row in raw.splitlines()))

    def test_noiseless_circuit_has_no_events_or_flips(self):
        out = self.root / "noiseless"
        info = generate_dataset(out, shots=100, noise=0)
        self.assertFalse(read_bits(out / "detectors.01", info["num_detectors"]).any())
        self.assertFalse(read_bits(out / "observables.01", info["num_observables"]).any())

    def test_known_logical_flip_is_saved_as_truth(self):
        # An undetectable forced flip: the detector is zero, but logical truth is one.
        experiment = load_experiment()
        experiment["circuit"] = stim.Circuit(
            "R 0 1\nREPEAT 3 {\nMR 2 3 4 5 6 7 8 9\nTICK\n}\nX_ERROR(1) 0\nM 0 1\nDETECTOR rec[-1]\nOBSERVABLE_INCLUDE(0) rec[-2]")
        with patch("generate_test_data.load_experiment", return_value=experiment):
            out = self.root / "known"
            generate_dataset(out, shots=8)
        self.assertFalse(read_bits(out / "detectors.01", 1).any())
        self.assertTrue(read_bits(out / "observables.01", 1).all())

    def test_seed_repeats_on_this_environment(self):
        for name in ("a", "b"):
            generate_dataset(self.root / name, shots=100, seed=123)
        for filename in ("detectors.01", "observables.01"):
            self.assertEqual((self.root / "a" / filename).read_bytes(),
                             (self.root / "b" / filename).read_bytes())

    def test_parameter_overrides_and_one_shot(self):
        out = self.root / "override"
        info = generate_dataset(out, shots=1, distance=5, rounds=2, noise=0)
        circuit = stim.Circuit.from_file(str(out / "circuit.stim"))
        self.assertEqual(info["num_detectors"], circuit.num_detectors)
        self.assertEqual(read_bits(out / "detectors.01", circuit.num_detectors).shape,
                         (1, circuit.num_detectors))

    def test_invalid_parameters_do_not_create_output(self):
        for kwargs in ({"shots": 0}, {"distance": 1}, {"rounds": 0},
                       {"noise": -0.1}, {"noise": 1.1}, {"noise": float("nan")},
                       {"seed": -1}, {"seed": 2**64}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                generate_dataset(self.root / "invalid", **kwargs)
        self.assertFalse((self.root / "invalid").exists())

    def test_existing_directory_is_not_overwritten(self):
        out = self.root / "existing"
        out.mkdir()
        sentinel = out / "keep.txt"
        sentinel.write_text("keep me")
        with self.assertRaises(FileExistsError):
            generate_dataset(out, shots=1)
        self.assertEqual(sentinel.read_text(), "keep me")
        self.assertEqual(list(out.iterdir()), [sentinel])

    def test_command_line(self):
        script = Path(__file__).with_name("generate_test_data.py")
        command = [sys.executable, str(script), "--output", str(self.root / "cli"),
                   "--shots", "10", "--seed", "42"]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Saved 10 paired shots", result.stdout)
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)


if __name__ == "__main__":
    unittest.main()
