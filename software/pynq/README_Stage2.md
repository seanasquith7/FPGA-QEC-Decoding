# Distance-3 data preparation and validation

Activate the project Python environment with NumPy and Stim. Run these commands from the repository root with fresh output paths.

```powershell
cd software/pynq
python generate_test_data.py --output output/raw_1000 --shots 1000 --rounds 3 --distance 3 --seed 42
python run_offline_checks.py --dataset output/raw_1000 --config packing_example.local.json --output-bytes 16 --output output/raw_acceptance
python -m unittest discover -v
```

## Dataset and packing

The generator samples the complete measurement record once. The distance-3 adapter follows the chronological MR instructions in the expanded rotated-memory-Z circuit. Each round measures eight distinct stabilizer ancillas in the same order. The generator records their qubit IDs and absolute, zero-based measurement indices in metadata.json. NumPy stores raw_syndrome.npy as a Boolean array with axes (shot, round, stabilizer). Final data-qubit measurements are excluded from this array and retained during conversion to detector events and logical flips.

Stim's measurement-to-detector converter derives detectors.01 and observables.01 from the same measurements. Row i in both files corresponds to raw_syndrome[i]. The software reference decoder can use the detector file and detector error model. Truth stays on the host. Other distances retain the detector/observable export. Regenerate older detector-only datasets to obtain paired raw measurements.

The local example packs one byte per round. Bit k holds raw column k, or the explicitly selected column_order[k]. For three rounds, shot i occupies offsets 3*i through 3*i+2. The 1,000-shot example produces 3,000 bytes. The layout records the mapping and payload hash. The underlying packer also supports round-major order and explicit legacy detector profiles. The old 32-bit detector profile is suitable for reproducing older offline experiments.

## Automatic validator

Use normalized, single-observable predictions from either decoder or both. Each JSON file contains the following fields. The dataset identity is the lowercase SHA-256 of the exact metadata.json bytes (also recorded as layout.source.metadata_sha256). Supply one prediction for every shot. IDs are zero-based and rows may arrive in any order.

```json
{
  "decoder": "fpga",
  "source": "synthetic",
  "semantics": "logical_flip",
  "dataset_sha256": "<dataset metadata SHA-256>",
  "predictions": [{"shot_id": 0, "logical_flip": 1}]
}
```

Choose decoder software or fpga. Use source software for reference results, hardware for board results, or synthetic for fixtures. The test helper synthetic_fixture builds deliberately reordered records with known error IDs for reproducible checks. Physical corrections require an agreed mapping to a logical flip before import. The validator rejects unknown semantics, invalid bits, foreign dataset IDs and missing, duplicated or out-of-range shot IDs.

```powershell
python validate_predictions.py --dataset output/raw_1000 --software output/software.json --fpga output/fpga.json --output output/validation_report.json
```

Each decoder's error rate is its number of mismatches with Stim truth divided by shots. Agreement counts matching FPGA and software predictions independently of truth. Reports retain the declared source for each decoder. With a single decoder, the agreement field is null.

## Board integration

The PL Defect Generator consumes successive raw syndromes. Confirm the stabilizer-to-bit mapping, initial round reference, shot reset, boundary handling, AXI width, framing, TLAST/TKEEP, output encoding and shot association with Sean and Varun. The offline byte layout does not program any of these settings. Board validation requires the matching overlay and actual received data.

See [buffer management](README_BufferManagement.md), [overlay inspection](README_OverlayInspection.md) and [offline acceptance](README_OfflineAcceptance.md) for the component procedures.
