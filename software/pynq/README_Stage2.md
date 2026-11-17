# Stage 2 — Python Data Streaming

Status: Stage 2.1 offline data preparation, Stage 2.2 buffer ownership logic, and Stage 2.3a offline HWH inspection are locally tested. A lazy PYNQ allocation adapter is implemented but untested on hardware. The overlay loader and DMA sender are not implemented. No physical DDR allocation, overlay loading, or FPGA DMA transfer has been performed in this environment.

## Architecture and evidence

The user supplied the following design-document summary on November 17, 2026: RFSoC 4x2, ARM processing system, Embedded Linux / PYNQ, DDR4 contiguous buffers, AXI DMA, and AXI4-Stream. These are target architecture choices, not evidence that the IP or board environment is ready. The original design documents were not independently read during this checkpoint.

The local Stage 1 commit is `b18b243`. The GitHub main tree read during this checkpoint is `7c63d96`, containing only the initial simulation script and editor settings. Stage 1 is not assumed to be pushed. Its eight existing tests were rerun and passed. Neither inspected tree contains RTL, a bitstream, an hwh file, or a detailed streaming protocol; teammates may have additional work elsewhere.

Hao owns PYNQ buffers, Python streaming, and validation integration. Jiahui owns simulation, datasets, reference decoding, and LUT truth tables. Varun owns the PL decoding pipeline. Sean owns architecture and integration. Chris owns hardware performance analysis.

## Input semantics must be confirmed

Stage 1 produces Stim detector events, with 24 detector bits per complete shot for the default distance-3, three-round circuit. It also saves one logical observable flip label per shot. The label stays on the host and must not be sent as decoder input.

The proposed PL Defect Generator computes `D_t = S_t XOR S_(t-1)`. Its raw syndrome inputs are not automatically equivalent to Stim detector events, which already encode detection parities and include circuit boundary checks. Do not reshape 24 detector bits into three eight-bit syndrome rounds, invert them into assumed raw syndromes, or apply another temporal XOR without an agreed semantic mapping.

If PL accepts detector events, the team must define their mapping and any Defect Generator bypass. If PL requires raw syndrome rounds, Jiahui must supply those rounds, the stabilizer mapping, initial reference, boundary handling, and a compatible circuit definition. This checkpoint does not generate a replacement simulation or decoder.

## Implementation checkpoints

| Checkpoint | Work | Dependencies and acceptance limits |
| --- | --- | --- |
| 2.1 Offline preparation — implemented here | Verify Stage 1 files, configure column mapping and packing, export bytes and layout, test round/shot ordering with synthetic inputs | Runs locally; every profile is explicitly local-test-only |
| 2.2 Buffer management — locally implemented | Explicit local/PYNQ allocators; input/output ownership, copying, synchronization, and release | Local tests only; physical allocation requires PYNQ hardware; output capacity must be explicitly supplied |
| 2.3a Offline Overlay inspection — implemented | Inventory AXI DMA parameters and identify missing metadata from HWH XML | Tested on synthetic XML only; does not load/program hardware or approve a protocol |
| 2.3b Overlay and DMA adapter — pending | Load the overlay, select the DMA IP, integrate control and transfer completion | Requires matching bit/hwh files, IP configuration, confirmed input/output protocol and board environment |
| 2.4 Board transfer acceptance | Check accepted input count, output length, order, integrity, and error recovery | Requires RFSoC hardware and a working overlay; does not calculate decoder accuracy |

An ordinary C-contiguous NumPy array is not a physically contiguous DDR allocation. The new board allocation adapter uses `pynq.allocate` only when explicitly selected; local tests do not call PYNQ. Synchronization through `sync_to_device` / `sync_from_device` must match buffer coherency and driver behavior. Buffers must not be released while DMA can still access them. See [README_BufferManagement.md](README_BufferManagement.md) for the Stage 2.2 API, local smoke test, and board allocation-only procedure.

See [README_OverlayInspection.md](README_OverlayInspection.md) for Stage 2.3a metadata inspection, a clearly labeled synthetic demonstration, and commands to inspect the actual team HWH export when available.

## Parameters still required from Sean / Varun

| Parameter | Required decision |
| --- | --- |
| Input semantics | Raw syndrome rounds, Stim detector events, or another representation; supported distance and rounds |
| Mapping | Stim detector/stabilizer indices to PL input positions, timing, and boundaries |
| Stream encoding | AXI4-Stream TDATA width, word/byte/bit order, padding, and TKEEP rules |
| Framing | Shot/round/batch boundaries; TLAST policy; whether one DMA transaction contains one or multiple shots |
| Flow control | FIFO capacity, ready/valid behavior, reset/start controls, and any software-visible status |
| Output | Correction encoding, output width and length, and output validity/status fields |
| Pairing | Shot IDs or a strict ordering guarantee; handling of missing or duplicate results |
| DMA configuration | IP name, simple/SG mode, DRE/alignment, maximum transfer size, and channel settings |
| Artifacts | Vivado bitstream, matching hwh, HLS interface declarations, and integration notes |
| Environment | Available RFSoC access, PYNQ image/version, allocation support, and board readiness |

Packing word width is not an inferred AXIS width. A batch transfer does not automatically generate TLAST for every shot. Receive-side TLAST and actual received bytes must match the agreed transaction and DMA configuration. TVALID/TREADY are hardware handshake signals, not signals toggled cycle by cycle by Python.

If PL returns physical correction masks, the team must define their relationship to logical observables before Stage 3 can compare predictions with truth. Host transfer time includes communication overhead and must not be reported as PL decoder latency; coordinate hardware timing with Chris.

## Stage 2.1 module

Files added:

- `prepare_stream_data.py`: input verification, explicit packing configuration, pure packing functions, and CLI export.
- `test_prepare_stream_data.py`: offline acceptance tests.
- `packing_example.local.json`: a demonstration profile, not an approved hardware protocol.

The Stage 1 generator and team simulation script are unchanged. No additional dependencies are required.

### Input verification

`load_stage1(dataset)` checks the schema, positive dimensions, the four Stage 1 file hashes, and detector row count, exact width, bit values, and LF termination. It returns detector inputs and source hashes. Observable labels are checked for file integrity but are not included in the returned input array or exported payload.

`prepare_stage1(dataset, config)` combines loading and packing. It refuses a raw-syndrome configuration because Stage 1 does not contain raw rounds. It never silently changes input semantics.

### Packing contract

`PackingConfig` requires all fields explicitly:

| Field | Meaning |
| --- | --- |
| `input_semantics` | `detector_events` or `raw_syndrome_rounds` |
| `word_bits` | Storage word size: 8, 16, 32, or 64; not a confirmed AXIS width |
| `bit_order` | `lsb-first` or `msb-first` within each packing word |
| `byte_order` | `little` or `big` within each packing word |
| `column_order` | Complete permutation: packing position k takes source column `column_order[k]` |
| `record_order` | `shot-major`; raw 3D input also supports `round-major` |
| `profile_status` | Must be `local-test-only` at this checkpoint |

`pack_records(bits, config)` accepts detector arrays shaped `(shots, detectors)`, or caller-supplied raw arrays shaped `(shots, rounds, bits_per_round)`. The raw-array API is tested only with synthetic transport patterns; there is no raw QEC dataset loader or raw syndrome generation in this checkpoint.

Every record starts a new word. Unused word bits are zero. For packed bit k, the word index is `k // word_bits`. The bit position is `k % word_bits` for lsb-first, or `word_bits - 1 - (k % word_bits)` for msb-first. Words are serialized in increasing word-index order using the configured byte order.

Shot-major stores all rounds of shot 0, then all rounds of shot 1. Round-major stores round 0 of all shots, then round 1 of all shots. Detector arrays remain whole-shot records and reject round-major organization.

The result is a one-dimensional ordinary NumPy `uint8` array containing the exact memory byte sequence. It is C-contiguous and suitable as a future copy source for a PYNQ buffer. It is not a PYNQ buffer and has no DMA address.

### Exported artifacts

- `payload.bin`: packed input bytes only; no labels, headers, shot IDs, or TLAST markers.
- `layout.json`: source hashes, config, payload hash and length, record sizes, padding, and index-to-byte-offset formulas. It explicitly records `hardware_ready=false`; AXIS width, DMA transfer boundaries, and output encoding remain unspecified.

Metadata is written after the payload. An interrupted export may leave an incomplete directory. Existing output paths are refused; choose a new directory when rerunning.

## Run locally

From the actual Git repository root, use the existing environment in its parent directory:

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s software/pynq -v
..\.venv\Scripts\python.exe software\pynq\prepare_stream_data.py --dataset software\pynq\output\smoke_1000 --config software\pynq\packing_example.local.json --output software\pynq\output\packed_smoke_1000
```

If the Stage 1 input directory is absent, create it first:

```powershell
..\.venv\Scripts\python.exe software\pynq\generate_test_data.py --shots 1000 --seed 42 --output software\pynq\output\smoke_1000
```

On a new machine, create a Python environment and install `software/pynq/requirements.txt`. Replace the interpreter path above with that environment's interpreter. The offline module itself imports NumPy, not PYNQ. Board Python/NumPy compatibility must be checked separately; the local pinned environment is not a validated RFSoC image.

The demonstration profile uses identity mapping, 32-bit storage words, lsb-first, little-endian bytes, and whole-shot records. Each 24-bit record has eight padding bits, so 1,000 shots produce 4,000 bytes. These choices are local test values and have not been approved for PL.

Expected CLI output:

```text
Prepared 1000 shots, 4000 bytes.
LOCAL TEST ONLY: no physical DDR allocation, overlay loading or DMA transfer.
```

Tests verify known byte sequences, all supported word sizes, cross-word boundaries, bit/byte ordering, source column permutations, zero padding, synthetic shot/round order, source integrity, semantic rejection, and CLI export without overwrite. An independent unpacker uses Python `int.from_bytes` to recover and compare source bits, rather than only checking array shapes.

Passing tests validates offline data preparation only. It does not establish physical DDR allocation, DMA transfer, or decoder correctness. Stage 3 has not started.

### Recorded local acceptance — November 17, 2026

- All 18 tests passed: eight existing Stage 1 tests and ten new preparation tests.
- The existing 1,000-shot dataset exported successfully to `output/packed_smoke_1000`.
- The local 32-bit demonstration profile produced exactly 4,000 bytes.
- Reading back the exported payload and independently unpacking it recovered all 1,000 by 24 source detector bits exactly.
- The exported layout records `hardware_ready=false`. No board test was performed.

## Board acceptance after interface confirmation

1. Use agreed bit positions and a PL testbench or debug echo to confirm the received input vectors.
2. Check all-zero, walking-one, and consecutive-shot patterns for order and boundaries. These transport patterns are not necessarily physically realizable QEC syndromes and must not be used as decoder-accuracy data.
3. Transfer a fixed Stage 1 dataset only if its detector semantics match the PL input. Otherwise use the team's confirmed raw-round dataset.
4. Require a matching result or explicit error record for every accepted shot; check transfer lengths, output pairing, reset, timeout, and recovery behavior.

## Team coordination message

Hi Sean and Varun, offline input packing is ready for local testing. The current Stage 1 dataset contains 24 Stim detector events per complete shot, not confirmed raw syndrome rounds. The logical label stays on the host.

Could you share the bitstream, matching hwh, DMA configuration, and PL input/output interface? In particular, we need the expected input semantics, bit mapping, AXIS width, shot/round framing, TLAST policy, output encoding, and result-ordering rules. If the Defect Generator requires raw syndrome rounds, I will coordinate that dataset format with Jiahui before connecting the sender.

## Schedule and references

The user supplied original 2026 milestone dates: memory allocation November 7; LUT/accumulator November 18; Python DMA and IP integration November 20; alpha prototype November 26. These dates are plans, not completion evidence.

Official API references; the installed board version still needs confirmation:

- https://pynq.readthedocs.io/en/v2.7.0/pynq_libraries/allocate.html
- https://pynq.readthedocs.io/en/v3.1/pynq_libraries/dma.html

See [README_OfflineAcceptance.md](README_OfflineAcceptance.md) for the integrated offline acceptance command. It connects existing preparation, local buffer checks and optional HWH inspection without creating FPGA results or accessing hardware.
