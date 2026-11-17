"""Run the existing Stage 2 offline components together; never access hardware."""

import argparse
import hashlib
import json
from pathlib import Path
from xml.etree.ElementTree import ParseError

import numpy as np

from dma_buffers import allocate_local, prepare_buffers
from inspect_dma_overlay import inspect_hwh
from prepare_stream_data import PackingConfig, prepare_stage1


def run_offline_checks(dataset, config, output, *, output_nbytes, hwh=None, dma_name=None):
    """Export a local acceptance bundle, not FPGA results or decoder metrics."""
    output = Path(output)
    if output.exists():
        raise FileExistsError("Output already exists; choose a new directory.")
    if dma_name is not None and hwh is None:
        raise ValueError("--dma requires --hwh.")
    payload, layout = prepare_stage1(dataset, config)
    inspection = inspect_hwh(hwh, dma_name=dma_name) if hwh is not None else None

    # The offline runner deliberately has no selectable PYNQ or DMA backend.
    with prepare_buffers(
        payload, output_nbytes=output_nbytes, allocator=allocate_local,
    ) as buffers:
        tx, rx = buffers.tx, buffers.rx
        np.testing.assert_array_equal(np.asarray(tx), payload)
        if not np.all(rx == 0):
            raise ValueError("Local output initialization failed.")
    if not tx.released or not rx.released:
        raise RuntimeError("Local buffer release failed.")

    report = {
        "schema_version": 1,
        "scope": "Stage 2 offline integration acceptance only",
        "hardware_ready": False,
        "physical_ddr_allocated": False,
        "overlay_loaded": False,
        "dma_executed": False,
        "fpga_output_available": False,
        "shots": layout["shots"],
        "input_semantics": config.input_semantics,
        "payload_nbytes": payload.nbytes,
        "payload_sha256": layout["payload_sha256"],
        "local_output_capacity_bytes": output_nbytes,
        "checks": {
            "stage1_integrity": "passed",
            "packing_config_validation": "passed",
            "local_input_copy": "passed",
            "local_output_zero_initialization": "passed",
            "local_buffer_release": "passed",
        },
        "hwh_inspection": inspection,
        "pending": [
            "Real matching bit/hwh, available board and confirmed PYNQ/DMA configuration",
            "Confirmed input semantics, mapping, framing and TLAST/TKEEP rules",
            "Correction encoding, output length and result-to-shot pairing",
            "Board DDR allocation, DMA completion and recovery acceptance",
        ],
        "limitations": [
            "Output capacity is an explicit local test value, not a correction-format assumption.",
            "No receive result, reference decoding, accuracy or hardware timing is produced.",
            "HWH inspection, if requested, is metadata only and does not prove hardware readiness.",
        ],
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "payload.bin").write_bytes(payload.tobytes())
    (output / "layout.json").write_text(json.dumps(layout, indent=2) + "\n", encoding="utf-8")
    if hashlib.sha256((output / "payload.bin").read_bytes()).hexdigest() != report["payload_sha256"]:
        raise ValueError("Exported payload hash mismatch.")
    report["checks"]["exported_payload_sha256"] = "passed"
    # This report is written last; an interrupted export may be incomplete.
    (output / "offline_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--output-bytes", type=int, required=True,
                        help="Explicit local test capacity, not an FPGA output format.")
    parser.add_argument("--hwh", type=Path)
    parser.add_argument("--dma")
    args = parser.parse_args()
    try:
        config = PackingConfig.from_dict(json.loads(args.config.read_text(encoding="utf-8")))
        report = run_offline_checks(
            args.dataset, config, args.output, output_nbytes=args.output_bytes,
            hwh=args.hwh, dma_name=args.dma)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, AssertionError, ParseError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(f"Offline checks passed: {report['shots']} shots, {report['payload_nbytes']} input bytes.")
    print(f"HWH inspection: {'included (metadata only)' if report['hwh_inspection'] else 'not provided'}")
    print("No physical DDR allocation, overlay loading, DMA transfer or FPGA output.")
    print(f"Saved acceptance bundle: {args.output}")


if __name__ == "__main__":
    main()
