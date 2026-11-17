"""Integration tests of existing offline components; no hardware simulation."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from generate_test_data import generate_dataset
from prepare_stream_data import PackingConfig, load_stage1
from run_offline_checks import run_offline_checks
from test_prepare_stream_data import independent_unpack


HERE = Path(__file__).resolve().parent


class OfflineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = self.root / "dataset"
        generate_dataset(self.dataset, shots=17)
        self.config = PackingConfig.from_dict(json.loads(
            (HERE / "packing_example.local.json").read_text()))
        self.out = self.root / "acceptance"

    def test_full_bundle_preserves_inputs_without_fabricating_results(self):
        report = run_offline_checks(
            self.dataset, self.config, self.out, output_nbytes=16,
            hwh=HERE / "fixtures" / "dma_example.synthetic.hwh")
        self.assertEqual({p.name for p in self.out.iterdir()},
                         {"payload.bin", "layout.json", "offline_report.json"})
        data = (self.out / "payload.bin").read_bytes()
        layout = json.loads((self.out / "layout.json").read_text())
        bits, _ = load_stage1(self.dataset)
        np.testing.assert_array_equal(
            independent_unpack(np.frombuffer(data, dtype=np.uint8), layout), bits)
        self.assertEqual(len(data), 17 * 4)
        self.assertEqual(report["payload_sha256"], hashlib.sha256(data).hexdigest())
        for flag in ("hardware_ready", "physical_ddr_allocated", "overlay_loaded",
                     "dma_executed", "fpga_output_available"):
            self.assertFalse(report[flag])
        self.assertTrue(report["hwh_inspection"]["source"]["marked_synthetic"])
        self.assertEqual(json.loads((self.out / "offline_report.json").read_text()), report)
        self.assertTrue(all(value == "passed" for value in report["checks"].values()))

    def test_invalid_input_does_not_export_a_success_bundle(self):
        path = self.dataset / "detectors.01"
        path.write_bytes(b"corrupted")
        with self.assertRaises(ValueError):
            run_offline_checks(self.dataset, self.config, self.out, output_nbytes=16)
        self.assertFalse(self.out.exists())

    def test_semantics_capacity_and_selection_errors_are_not_hidden(self):
        raw_config = PackingConfig.from_dict({
            **json.loads((HERE / "packing_example.local.json").read_text()),
            "input_semantics": "raw_syndrome_rounds"})
        for config, kwargs in [
            (raw_config, {"output_nbytes": 16}),
            (self.config, {"output_nbytes": 0}),
            (self.config, {"output_nbytes": 16, "dma_name": "some_dma"}),
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                run_offline_checks(self.dataset, config, self.out, **kwargs)
        self.assertFalse(self.out.exists())

    def test_cli_without_hwh_and_overwrite_refusal(self):
        command = [sys.executable, str(HERE / "run_offline_checks.py"),
                   "--dataset", str(self.dataset),
                   "--config", str(HERE / "packing_example.local.json"),
                   "--output", str(self.out), "--output-bytes", "16"]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HWH inspection: not provided", result.stdout)
        before = (self.out / "offline_report.json").read_bytes()
        self.assertIsNone(json.loads(before)["hwh_inspection"])
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.out / "offline_report.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
