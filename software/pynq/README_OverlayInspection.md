# Stage 2.3a — Offline DMA Overlay Inspection

Status: offline HWH configuration inspection only. No real bitstream or HWH export is available in the inspected local project. The included XML fixture is synthetic, not a Vivado export. No PYNQ import, overlay loading, register access, or DMA operation occurs.

## Purpose

Before implementing the board sender, inspect the AXI DMA configuration exported by Sean's integrated Vivado design. `inspect_dma_overlay.py` inventories AXI DMA modules and preserves unknown values instead of inventing defaults. It is a focused metadata reader, not a replacement for PYNQ's HWH parser or a complete Vivado design checker.

The reader recognizes MODULE elements using MODTYPE `axi_dma` or the IP name in a VLNV identifier. It reads module-level PARAMETERS, accepts upper/lowercase parameter names, and handles XML element namespaces. Hierarchical FULLNAME paths are preserved; INSTANCE is used only when FULLNAME is absent. It does not inspect connectivity, address maps, timing, clocks, reset nets, or signal behavior.

## Report fields

| Field | Meaning |
| --- | --- |
| `selected_dma` | Full DMA IP path; automatically selected only when exactly one DMA is present |
| `mm2s_enabled`, `s2mm_enabled` | Configured memory-to-stream and stream-to-memory channel enables |
| `scatter_gather`, `micro_dma` | Explicit DMA mode settings; absent settings remain unknown |
| `mm2s_dre`, `s2mm_dre` | Configured data realignment engine flags |
| `*_memory_width_bits` | AXI memory-mapped interface widths |
| `*_stream_width_bits` | AXI4-Stream data widths, kept separate from memory widths |
| `length_register_bits` | Exported length-field width; not a verified transfer-size limit |
| `raw_parameters` | Original parameter values, with normalized names |
| `metadata_issues` | Missing/invalid values, disabled channels, or conflicting modes |
| `source` | HWH filename, SHA-256, and explicit synthetic-fixture marker if present |
| `still_required` | Protocol and board facts not established by this inspection |

Malformed or missing values remain null and are listed as issues. Conflicting duplicate parameters or duplicate full DMA paths are rejected. Multiple DMA instances are reported without silently choosing one; use `--dma` with the exact full path to select a candidate.

An HWH report cannot prove that the bitstream matches, that the DMA is PS-addressable, that its streams connect to the decoder, or that TLAST is generated correctly. `hardware_ready`, `overlay_loaded`, and `dma_executed` are always false. Even a complete parameter inventory does not approve the Stage 2.1 packing profile or validate the installed PYNQ driver.

## Run a local demonstration

From the actual Git repository root:

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s software/pynq -v
..\.venv\Scripts\python.exe software\pynq\inspect_dma_overlay.py --hwh software\pynq\fixtures\dma_example.synthetic.hwh --output software\pynq\output\dma_inspection.synthetic.json
```

Expected output includes:

```text
Found 1 AXI DMA IP(s).
Selected DMA: test_hierarchy/axi_dma_0
SYNTHETIC FIXTURE: this is not a real hardware export.
OFFLINE INSPECTION ONLY: no overlay loading or DMA transfer.
```

The fixture deliberately uses 64-bit memory interfaces and 32-bit streaming interfaces to test that they are not confused. These are arbitrary test values, not RFSoC project configuration. The report is saved under the ignored output directory. Existing report files are not overwritten; choose a new output filename when rerunning.

## Inspect the actual team export when available

Replace the paths and exact IP name with values from the team:

```powershell
..\.venv\Scripts\python.exe software\pynq\inspect_dma_overlay.py --hwh path\to\actual_design.hwh --output software\pynq\output\dma_inspection.actual.json
```

If multiple instances are present, inspect `dma_modules` and rerun with an explicit selection:

```powershell
..\.venv\Scripts\python.exe software\pynq\inspect_dma_overlay.py --hwh path\to\actual_design.hwh --dma actual_hierarchy/actual_dma_name --output software\pynq\output\dma_inspection.selected.json
```

These example paths and names are placeholders. There is no fabricated real-board profile in this checkpoint.

## Remaining requirements before board sender implementation

1. Matching bit/hwh from the same integrated build, board access, PYNQ version, and confirmed DMA driver support.
2. Raw-syndrome or detector-event input contract, exact mapping, word/byte/bit order, padding, and shot/round/batch organization.
3. TLAST/TKEEP behavior and transaction boundaries; input/output stream connectivity and decoder start/reset controls.
4. Correction encoding, expected/actual output length, and result-to-shot correspondence.
5. Mode-specific DMA transfer-size and address-alignment constraints, completion checks, and timeout/recovery procedure.

Do not call the future `BufferPair.release_after_dma_stopped` merely because a software timeout elapsed. Actual hardware ownership must be resolved first.

## Local acceptance tests

Tests use only minimal synthetic XML. They cover field extraction, memory/stream width distinction, missing/invalid/hex values, channel disable and mode reporting, hierarchical selection, namespaces, conflicting metadata, malformed XML, and exclusive CLI output creation. No actual Vivado-export compatibility or hardware behavior has been tested.

Recorded local acceptance on November 17, 2026: all 37 project tests passed, including nine new inspection tests. The synthetic CLI demonstration successfully exported `output/dma_inspection.synthetic.json`, explicitly identifying its synthetic source and offline-only status.

References:

- https://github.com/Xilinx/PYNQ/blob/master/pynq/pl_server/hwh_parser.py
- https://pynq.readthedocs.io/en/v2.7.0/_modules/pynq/lib/dma.html

The installed board version must be checked separately. Missing metadata remains unknown even when a particular PYNQ version supplies its own defaults.
