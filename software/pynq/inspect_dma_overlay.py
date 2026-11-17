"""Read AXI DMA configuration from HWH XML without importing PYNQ or touching PL."""

import argparse
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET


PARAMETERS = {
    "mm2s_enabled": ("c_include_mm2s", "flag"),
    "s2mm_enabled": ("c_include_s2mm", "flag"),
    "scatter_gather": ("c_include_sg", "flag"),
    "micro_dma": ("c_micro_dma", "flag"),
    "mm2s_dre": ("c_include_mm2s_dre", "flag"),
    "s2mm_dre": ("c_include_s2mm_dre", "flag"),
    "mm2s_memory_width_bits": ("c_m_axi_mm2s_data_width", "width"),
    "s2mm_memory_width_bits": ("c_m_axi_s2mm_data_width", "width"),
    "mm2s_stream_width_bits": ("c_m_axis_mm2s_tdata_width", "width"),
    "s2mm_stream_width_bits": ("c_s_axis_s2mm_tdata_width", "width"),
    "length_register_bits": ("c_sg_length_width", "positive"),
}


def _describe_module(module):
    name = (module.get("FULLNAME") or module.get("INSTANCE") or "").strip("/")
    if not name:
        raise ValueError("AXI DMA module has no FULLNAME or INSTANCE.")
    raw = {}
    for parameter in module.findall("./PARAMETERS/*"):
        key = parameter.get("NAME", "").lower()
        value = parameter.get("VALUE")
        if not key:
            continue
        if key in raw and raw[key] != value:
            raise ValueError(f"Conflicting parameter values in {name}: {key}")
        raw[key] = value
    fields, issues = {}, []
    for field, (key, kind) in PARAMETERS.items():
        fields[field] = None
        value = raw.get(key)
        if value is None:
            issues.append(f"Missing {key}; no default assumed.")
            continue
        try:
            number = int(value, 16 if value.lower().startswith("0x") else 10)
            if kind == "flag" and number not in (0, 1):
                raise ValueError()
            if kind != "flag" and (number <= 0 or (kind == "width" and number % 8)):
                raise ValueError()
        except (ValueError, TypeError):
            issues.append(f"Invalid {key}: {value!r}; value left unknown.")
            continue
        fields[field] = bool(number) if kind == "flag" else number
    for field in ("mm2s_enabled", "s2mm_enabled"):
        if fields[field] is False:
            issues.append(f"{field} is false; this IP alone cannot provide both directions.")
    if fields["scatter_gather"] is True and fields["micro_dma"] is True:
        issues.append("Micro DMA and scatter-gather are both enabled; check driver support.")
    if fields["scatter_gather"] is None or fields["micro_dma"] is None:
        mode = "unknown"
    elif fields["scatter_gather"] and fields["micro_dma"]:
        mode = "conflicting"
    elif fields["scatter_gather"]:
        mode = "scatter-gather"
    elif fields["micro_dma"]:
        mode = "micro"
    else:
        mode = "simple"
    return {"name": name, "vlnv": module.get("VLNV"), "dma_mode": mode,
            "fields": fields, "raw_parameters": raw, "metadata_issues": issues}


def inspect_hwh(path, *, dma_name=None):
    """Inventory modules. Metadata cannot prove routing, protocol, or board readiness."""
    path = Path(path)
    data = path.read_bytes()
    root = ET.fromstring(data)
    # Vivado exports can use an XML namespace; match elements by local name.
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    modules = []
    for module in root.iter("MODULE"):
        vlnv = module.get("VLNV", "").split(":")
        if module.get("MODTYPE") == "axi_dma" or (len(vlnv) == 4 and vlnv[2] == "axi_dma"):
            modules.append(_describe_module(module))
    if not modules:
        raise ValueError("No AXI DMA modules found in HWH XML.")
    names = [module["name"] for module in modules]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate full AXI DMA paths; selection would be ambiguous.")
    selected = None
    if dma_name is not None:
        selected = dma_name.strip("/")
        if selected not in names:
            raise ValueError(f"Unknown DMA full path: {dma_name}. Available: {', '.join(names)}")
    elif len(modules) == 1:
        selected = names[0]
    return {
        "schema_version": 1,
        "inspection": "offline HWH metadata only",
        "hardware_ready": False,
        "overlay_loaded": False,
        "dma_executed": False,
        "source": {"filename": path.name, "sha256": hashlib.sha256(data).hexdigest(),
                   "marked_synthetic": root.get("TEST_FIXTURE") == "synthetic"},
        "dma_modules": modules,
        "selected_dma": selected,
        "selection_required": selected is None,
        "still_required": [
            "Matching bitstream and confirmation that bit/hwh came from the same build",
            "Installed PYNQ version, driver binding, board access, and allocation target",
            "PS addressability, clocks, reset, interrupts, and actual stream connections",
            "Raw syndrome versus detector-event input semantics and exact source-bit mapping",
            "Shot/round/batch framing and TLAST/TKEEP behavior",
            "Correction output encoding, actual byte length, and result pairing",
            "DMA size/alignment constraints for the selected mode and installed driver",
            "Hardware completion, timeout, and recovery procedure",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hwh", type=Path, required=True)
    parser.add_argument("--dma", help="Exact full IP path, including any hierarchy.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = inspect_hwh(args.hwh, dma_name=args.dma)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents accidental replacement of a prior report.
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    except (OSError, ValueError, ET.ParseError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(f"Found {len(report['dma_modules'])} AXI DMA IP(s).")
    print(f"Selected DMA: {report['selected_dma'] or 'not selected; use --dma'}")
    if report["source"]["marked_synthetic"]:
        print("SYNTHETIC FIXTURE: this is not a real hardware export.")
    print("OFFLINE INSPECTION ONLY: no overlay loading or DMA transfer.")
    print(f"Saved report: {args.output}")


if __name__ == "__main__":
    main()
