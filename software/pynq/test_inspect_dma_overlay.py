"""Synthetic XML tests only; no real Vivado overlay is available."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET

from inspect_dma_overlay import inspect_hwh


FIXTURE = Path(__file__).parent / "fixtures" / "dma_example.synthetic.hwh"


class OverlayInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.hwh"
        self.root = ET.parse(FIXTURE).getroot()

    def save(self):
        ET.ElementTree(self.root).write(self.path, encoding="utf-8")
        return self.path

    def parameter(self, name):
        return self.root.find(f".//PARAMETER[@NAME='{name}']")

    def test_fixture_fields_and_memory_stream_distinction(self):
        report = inspect_hwh(FIXTURE)
        dma = report["dma_modules"][0]
        self.assertEqual(report["selected_dma"], "test_hierarchy/axi_dma_0")
        self.assertEqual(dma["dma_mode"], "simple")
        self.assertEqual(dma["fields"]["mm2s_memory_width_bits"], 64)
        self.assertEqual(dma["fields"]["mm2s_stream_width_bits"], 32)
        self.assertFalse(dma["fields"]["mm2s_dre"])
        self.assertEqual(dma["metadata_issues"], [])
        self.assertFalse(report["hardware_ready"])
        self.assertFalse(report["overlay_loaded"])
        self.assertFalse(report["dma_executed"])
        self.assertTrue(report["source"]["marked_synthetic"])
        self.assertEqual(report["source"]["sha256"], hashlib.sha256(FIXTURE.read_bytes()).hexdigest())

    def test_missing_parameters_stay_unknown(self):
        parent = self.root.find(".//PARAMETERS")
        parent.remove(self.parameter("C_INCLUDE_SG"))
        parent.remove(self.parameter("C_INCLUDE_MM2S"))
        dma = inspect_hwh(self.save())["dma_modules"][0]
        self.assertIsNone(dma["fields"]["scatter_gather"])
        self.assertIsNone(dma["fields"]["mm2s_enabled"])
        self.assertEqual(dma["dma_mode"], "unknown")
        self.assertEqual(len(dma["metadata_issues"]), 2)

    def test_invalid_values_and_hex(self):
        self.parameter("C_INCLUDE_MM2S").set("VALUE", "2")
        self.parameter("C_M_AXIS_MM2S_TDATA_WIDTH").set("VALUE", "not-an-integer")
        self.parameter("C_M_AXI_MM2S_DATA_WIDTH").set("VALUE", "0x40")
        dma = inspect_hwh(self.save())["dma_modules"][0]
        self.assertIsNone(dma["fields"]["mm2s_enabled"])
        self.assertIsNone(dma["fields"]["mm2s_stream_width_bits"])
        self.assertEqual(dma["fields"]["mm2s_memory_width_bits"], 64)

    def test_modes_and_disabled_channel(self):
        self.parameter("C_INCLUDE_SG").set("VALUE", "1")
        self.parameter("C_INCLUDE_S2MM").set("VALUE", "0")
        dma = inspect_hwh(self.save())["dma_modules"][0]
        self.assertEqual(dma["dma_mode"], "scatter-gather")
        self.assertTrue(any("cannot provide both" in issue for issue in dma["metadata_issues"]))
        self.parameter("C_MICRO_DMA").set("VALUE", "1")
        self.assertEqual(inspect_hwh(self.save())["dma_modules"][0]["dma_mode"], "conflicting")
        self.parameter("C_INCLUDE_SG").set("VALUE", "0")
        self.assertEqual(inspect_hwh(self.save())["dma_modules"][0]["dma_mode"], "micro")

    def test_multiple_ips_require_explicit_full_path(self):
        second = ET.fromstring(ET.tostring(self.root.find(".//MODULE")))
        second.set("FULLNAME", "/other/axi_dma_0")
        self.root.find("MODULES").append(second)
        path = self.save()
        self.assertIsNone(inspect_hwh(path)["selected_dma"])
        self.assertTrue(inspect_hwh(path)["selection_required"])
        self.assertEqual(inspect_hwh(path, dma_name="/other/axi_dma_0")["selected_dma"], "other/axi_dma_0")
        with self.assertRaises(ValueError):
            inspect_hwh(path, dma_name="axi_dma_0")

    def test_namespaces_and_lowercase_parameter_names(self):
        for element in self.root.iter():
            if element.tag == "PARAMETER":
                element.set("NAME", element.get("NAME").lower())
            element.tag = "{urn:synthetic-test}" + element.tag
        report = inspect_hwh(self.save())
        self.assertEqual(report["dma_modules"][0]["fields"]["mm2s_stream_width_bits"], 32)

    def test_duplicate_paths_and_conflicting_parameters_rejected(self):
        parent = self.root.find(".//PARAMETERS")
        ET.SubElement(parent, "PARAMETER", NAME="c_include_mm2s", VALUE="0")
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            inspect_hwh(self.save())
        parent.remove(parent[-1])
        self.root.find("MODULES").append(ET.fromstring(ET.tostring(self.root.find(".//MODULE"))))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            inspect_hwh(self.save())

    def test_no_dma_and_malformed_xml(self):
        self.path.write_text("<SYSTEM />")
        with self.assertRaisesRegex(ValueError, "No AXI DMA"):
            inspect_hwh(self.path)
        self.path.write_text("<SYSTEM>")
        with self.assertRaises(ET.ParseError):
            inspect_hwh(self.path)

    def test_cli_report_and_exclusive_output(self):
        output = Path(self.temp.name) / "report.json"
        command = [sys.executable, str(Path(__file__).with_name("inspect_dma_overlay.py")),
                   "--hwh", str(FIXTURE), "--output", str(output)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SYNTHETIC FIXTURE", result.stdout)
        original = output.read_bytes()
        self.assertFalse(json.loads(original)["hardware_ready"])
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
