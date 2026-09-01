# Historical preservation migration

This record documents a bounded, byte-identical preservation copy.  These are
the original accepted historical artifacts; they were not reacquired,
renormalized, regenerated, or reinterpreted.

- Authority: Rick, `GO — RESTORE ACCEPTED HISTORICAL EVIDENCE INTO TBOTS`
- Source host: `NODE`
- Source repository: `/opt/evolutionary-markets`
- Source HEAD: `74acb3666b046dd363c2b4b502ae0d0a2a1806c1`
- Destination repository: `Racer-01111000/tbots`
- Destination starting HEAD: `e00af4a3aadb670621cf078ca4305808ff844ee4`
- Migration relation: NODE preservation source -> byte-identical tbots copy

For every row below, the full source path is
`/opt/evolutionary-markets/<relative-path>` and the destination path is the
same relative path in `tbots`.  Every entry is a regular file.  `mtime` is the
source and destination Unix epoch after timestamp-preserving copy.

## Accepted source dataset

Associated dataset revision:
`ds_7e16896c873671fe86ac416b24a0ce74502249a8a0fc33603e0f1935e5fab131`.

| Relative path | Bytes | mtime | Source SHA-256 = destination SHA-256 |
|---|---:|---:|---|
| `data/raw/DBC.json` | 567298 | 1787729870 | `1bf03aed426257a6c194861ca35561804a12fcb448d31b09bc4295bce41f4f29` |
| `data/raw/EEM.json` | 649121 | 1787729767 | `de47bb5901b00943fca162fc91c7795d308294dfad036d7a22499e6c52ca3468` |
| `data/raw/EFA.json` | 682767 | 1787729717 | `2a820955845963e1744e829e5a039ab3bb3a4c1a869f1f8113b9f1adc48673b7` |
| `data/raw/GLD.json` | 598192 | 1787729854 | `386c4cf3fe4e2d100b9b7e25d9ca149b6e426a15db50004512c5c9cc5164bfd4` |
| `data/raw/IEF.json` | 664876 | 1787729793 | `db161596808b3edb1a037307173bdfc53a788a9ea79bf464ecc32a23fc82dcbb` |
| `data/raw/SPY.json` | 854360 | 1787729694 | `83463686deab3cb7b391140d8b108ff2fe19dd18eba57303bc8af12fe7fa6f6e` |
| `data/raw/TLT.json` | 670029 | 1787729818 | `b175fe83d2e999ee71f146451e2a1188418406d7a4484cd4c354a74b1f659325` |
| `data/raw/VNQ.json` | 590530 | 1787729903 | `36cdcdf9bef8ace2a348cd9ee341207a9272e17344658131cf3a0ed5574de8f5` |
| `data/normalized/DBC.csv` | 1036574 | 1787730468 | `a815b74c6ecaf1807b6e6b8dd283c95c736707a242ab5ac048205e0f97001334` |
| `data/normalized/EEM.csv` | 1182219 | 1787730467 | `09eb69da68774ec1e2ab83af87a0c1636fc276b54d1f809e8205bc8ad6c556cb` |
| `data/normalized/EFA.csv` | 1252844 | 1787730467 | `df886ea25a63b6048b3fcacbcd56c46375e3ff589143212322acee22df9d8884` |
| `data/normalized/GLD.csv` | 1095444 | 1787730468 | `3d1159fa54d38d7cf27f026cf2041baca367d70c4d50b16611280fcd63342b8d` |
| `data/normalized/IEF.csv` | 1209833 | 1787730467 | `024b21295ed53eabcc18f99da6045b54665419f0edf6032d7bf3b95996cf9f4e` |
| `data/normalized/SPY.csv` | 1622089 | 1787730466 | `c2853530b644bdcc9c9e4f5aa59c54d3cbaae7b1bd9d13d75f10c4a61af53734` |
| `data/normalized/TLT.csv` | 1215023 | 1787730467 | `0721bb9ac7e19f9583f47aba88345dc3adaa26cb7f31102fa18447bd14f082f2` |
| `data/normalized/VNQ.csv` | 1089437 | 1787730468 | `0d83875ad240152936f0f90a88ee16bde83d340751ee011670ccec732b5a15f0` |

The retained `data/raw/manifest.json` and
`data/normalized/manifest_ds_7e16896c873671fe86ac416b24a0ce74502249a8a0fc33603e0f1935e5fab131.json`
were already tracked at the destination starting HEAD.  Their destination
working blobs equal their NODE/HEAD blobs, and all recorded raw and normalized
SHA-256 values match the copied files.

## Accepted S5A DEVELOPMENT bundle

Associated bundle revision:
`s5adev_98e2f764f466b90ee2bbc2532b75188bfc4fd20b4a13523f94bce65e6a1f193a`.
Associated manifest identity:
`s5a_dev_manifest_9b10b502bb2fb83b5bf833bc0983e4f421afdbff78fd7f1093130ce88bffc2a3`.

All paths below are under
`data/development_bundles/s5adev_98e2f764f466b90ee2bbc2532b75188bfc4fd20b4a13523f94bce65e6a1f193a/`.

| Relative filename | Bytes | mtime | Source SHA-256 = destination SHA-256 |
|---|---:|---:|---|
| `DBC.csv` | 358463 | 1787821172 | `83f9eab5a91f274dd9735e4f9eb1a7070d8d7a43c0e329709f8ed9abe3020da7` |
| `EEM.csv` | 375215 | 1787821172 | `68cbdbb352de216905c3e1ebb739c653da9f19177ef4a1b384a9ca18cda29c44` |
| `EFA.csv` | 368904 | 1787821172 | `189ab0910f911f60867ce25483c981878d6811d3772f6eab07bc658754d7afe4` |
| `GLD.csv` | 370806 | 1787821172 | `0972e1a8780f80f736cea746ce45a86e3f38f7339dfd3399d3842bfa8b3541cd` |
| `IEF.csv` | 370703 | 1787821172 | `5be5f331055389e1bf0c8f3e26b6fa9e8951c070c7adb7f0b6e8ee1f1ecf9f19` |
| `SPY.csv` | 379540 | 1787821172 | `2c28982c84e531d9bd95a52339efc8bdb6e39a3b91d76e46abbc4fcb738bd380` |
| `TLT.csv` | 374272 | 1787821172 | `3aecd092fa32379ea576cb004df7365e2d730789f1af356da651b22f0beab52e` |
| `VNQ.csv` | 363687 | 1787821172 | `50ea7a74e6da86d1370a9a6089333ce13e15bef413b8ab8ba65c8b7a8a1f9ee3` |
| `manifest_s5adev_98e2f764f466b90ee2bbc2532b75188bfc4fd20b4a13523f94bce65e6a1f193a.json` | 7441 | 1787821172 | `98c566cd9a15d297841a9e4a4284daab8759135be621071a12225bb360c3361b` |

The bundle revision and manifest identity recompute exactly from the copied
manifest and artifacts.

## Accepted S5C result and champion chain

| Relative path | Bytes | mtime | Source SHA-256 = destination SHA-256 |
|---|---:|---:|---|
| `reports/s5c_championship_08325c0_result.json` | 16959 | 1787903022 | `974b911afcd8eed6d981ddf4179eac3244a8eed68f881f5deb529684ae5346c0` |

- S5C run: `s5c_c4f43cb6bad29918631189eadd456b1cd1ee8149437b55c432221ac4f18bde1d`
- S5C deterministic digest: `ecada3c0a7430515fe01e262d08d7c69454364b141826137d14d659fca50835e`
- Frozen champion genome: `gen_0307d23c13fd796db749e78c86947c04ac7de020b3e4c6f02ea1f95dc10e0155`
- Frozen champion identity: `s5d_champion_00ac1019646747ad88b7eac1955dc1067f598f53e77cce01c07de7603dab2672`

The result SHA-256 equals the value pinned by the S5D preparation lock and
frozen champion artifact.  This migration did not execute or alter the
champion.

## Incomplete reacquisition draft disposition

Before removal, `scripts/canonical_historical_data.py` was 11833 bytes, had
mtime `2026-09-01 17:33:02.307965070 +0700`, and SHA-256
`3bb1464bcd1f16df21d15af76ec7e68801c5e86016de2c83cb4eeedfb3dd75b6`.
It was never accepted as the canonical source and was removed from the repair
worktree.  No Yahoo reacquisition output was committed.

## Explicit exclusions

No equity curves, databases, tainted or nonaccepted S5A artifacts, forensic
artifacts, superseded reproductions, or unrelated historical outputs were
copied.

## Verification

- Source/destination comparison: 26 of 26 files match in byte size, mtime, and
  SHA-256; zero mismatches.
- Canonical loader: 8 accepted normalized assets authenticated as dataset
  `ds_7e16896c873671fe86ac416b24a0ce74502249a8a0fc33603e0f1935e5fab131`.
- DEVELOPMENT loader: 8 accepted assets authenticated as bundle
  `s5adev_98e2f764f466b90ee2bbc2532b75188bfc4fd20b4a13523f94bce65e6a1f193a`.
- Champion-chain authentication: passed without champion execution.
- Proof-loader isolation: a file marked `TEST_FIXTURE_ONLY` is rejected even
  if its content hash is substituted into a temporary manifest.
- Runtime/test dependency scan: no reference to `/opt/evolutionary-markets/`
  exists under `scripts/` or `tests/`.
- Complete suite: `python3 -m unittest discover -s tests -v` ran 279 tests in
  9.580 seconds; result `OK`.

Historical proof executions, hidden-outcome reveals, evolutionary runs,
champion executions, mutations/retraining, and broker/live/paper/real activity
were all zero during this migration.
