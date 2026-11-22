# Stage 2 — Integrated Offline Acceptance

This checkpoint connects the existing Stage 1 loader, configurable packing, local buffer checks, and optional HWH inspection through one command. It does not implement an overlay loader or a DMA sender.

## Run

From the actual Git repository root, using the existing environment in the parent directory:

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s software/pynq -v
..\.venv\Scripts\python.exe software\pynq\run_offline_checks.py --dataset software\pynq\output\smoke_1000 --config software\pynq\packing_example.local.json --output-bytes 16 --output software\pynq\output\offline_acceptance_1000
```

For an optional HWH metadata check, add --hwh with the actual team export. To demonstrate the parser locally, explicitly use --hwh software\pynq\fixtures\dma_example.synthetic.hwh. This fixture is synthetic and cannot establish real board configuration. --dma requires --hwh and selects an exact full IP path.

The 16-byte output capacity is an arbitrary local allocation test value, not a correction encoding. The existing local packing profile is not an approved hardware protocol. There is no PYNQ or DMA backend option in this runner.

If the source dataset is missing, generate it with the existing Stage 1 command. If the output directory already exists, use a new name. No additional dependencies are required. On a different machine, replace the interpreter path with the project's installed Python environment.

## Acceptance bundle

| File | Content |
| --- | --- |
| payload.bin | Packed round-by-round raw syndrome bytes with the example profile |
| layout.json | Source hashes, exact packing configuration, sizes and index formulas |
| offline_report.json | Input integrity, local buffer copy/initialization/release and exported payload hash checks; optional HWH inventory; outstanding hardware requirements |

The report is written last. A failed write may leave an incomplete directory; choose a new directory to rerun. Existing output directories are not overwritten.

For a newly generated distance-3, three-round, 1,000-shot dataset, expect 3,000 input bytes. Regenerate legacy detector-only datasets before using the raw profile. See README_Stage2.md for prediction validation.

HWH metadata issues remain visible in the nested report. A successful runner exit means that offline software checks completed; it does not mean that a supplied HWH is complete or approved. Hardware readiness flags remain false.

## Remaining Stage 2 dependency

The next step is an actual board adapter. Before implementation and board acceptance, obtain the matching bitstream/HWH, available PYNQ environment, exact DMA configuration, confirmed input semantics and mapping, frame/TLAST/TKEEP rules, output encoding and lengths, and shot/result pairing.

The offline checks do not establish physically contiguous DDR allocation, cache coherency, actual transfer completion, timeout recovery, or decoder correctness. Stage 3 remains out of scope.

See README_Stage2.md, README_BufferManagement.md, and README_OverlayInspection.md for the component contracts.

## Recorded local acceptance — November 17, 2026

All 41 project tests passed, including four new integration tests. The real existing 1,000-shot Stage 1 dataset completed the integrated offline CLI run and exported 4,000 payload bytes to output/offline_acceptance_1000. No HWH was supplied for that run; all hardware-readiness flags remain false. No physical DDR allocation or DMA transfer was performed.
