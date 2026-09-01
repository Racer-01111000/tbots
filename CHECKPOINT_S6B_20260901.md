# S6B checkpoint — 2026-09-01

Authority: Rick

## Accepted state

- Canonical working branch: `repair/self-contained-canonical-20260901`
- Last accepted/pushed HEAD: `6ac72a290e4bd55c89165ad98214e5deea63ac4f`
- NODE preservation repo: `/opt/evolutionary-markets`
- NODE preservation HEAD: `74acb3666b046dd363c2b4b502ae0d0a2a1806c1`
- Historical Blind Evolution v1: **LOCKED**
- Frozen historical champion remains immutable: `gen_0307d23c13fd796db749e78c86947c04ac7de020b3e4c6f02ea1f95dc10e0155`
- S5D champion identity: `s5d_champion_00ac1019646747ad88b7eac1955dc1067f598f53e77cce01c07de7603dab2672`

## Historical preservation

Accepted historical evidence has been restored into `tbots` byte-identically. The S6B preservation ledger records 130 preserved historical run artifacts with 0 hash mismatches. The legacy completion-lock compatibility bridge is accepted and fail-closed; it recognizes only `CURRENT_TBOTS` or the specifically proven `LEGACY_NODE_74ACB366` profile.

Historical NODE completion lock:
`s6a_completion_lock_4af53c577a4520b7cfd48f1ea3553fe2c0393273b85272ed4f43d50c187aaf83`

Current tbots completion lock:
`s6a_completion_lock_0bd22a5df27a9f48141cb50b59df92ac88925a2b65c5dea77adb07c4ba1c7e81`

Authorized compatibility transform is exactly the documented zero-valued field rename `kestrel_access: 0 -> external_system_access: 0`; the two hashes remain distinct.

## Preserved S6B state

- B primary: COMPLETE, Gen0–Gen12, `LEGACY_NODE_74ACB366`
- B reproduction: COMPLETE, Gen0–Gen12, `LEGACY_NODE_74ACB366`
- C primary: INTERRUPTED after Gen11; next missing Gen12, `LEGACY_NODE_74ACB366`
- D primary: COMPLETE, Gen0–Gen12, `LEGACY_NODE_74ACB366`
- D reproduction: INTERRUPTED after Gen6; next missing Gen7, `LEGACY_NODE_74ACB366`
- E/F/G: NO_EXISTING_RUN

Persisted runs have contiguous generations, exactly 64 indexed slots per generation, and same-lineage immediate-predecessor parent provenance. Observed 62–63 unique genome IDs in some Gen1–Gen12 populations were resolved as valid under the frozen protocol; Gen0 remains 64/64 unique.

## Last accepted engineering results

- S6B deterministic continuation surface accepted.
- Legacy identity compatibility bridge accepted.
- Historical S6B evidence preservation accepted.
- Full suite at latest reported checkpoint: `321/321 PASSED`.
- NODE remained unchanged throughout preservation work.

## HOLD / STOP

Do not resume C or D yet.
Do not start E/F/G.
Do not begin Historical Blind Evolution.
Do not execute the frozen champion.
No broker, live-feed, external paper-order, or real-money activity.

## Pending next GO

`GO — S6B DETERMINISTIC RESUME EXECUTOR — IMPLEMENTATION ONLY / NO HISTORICAL EXECUTION`

Purpose: implement and synthetically prove a fail-closed executor that can continue a verified interrupted checkpoint from only the next missing generation, with atomic persistence and crash/restart safety. It must preserve legacy provenance, reject completed/no-existing runs, and must not execute C Gen12, D reproduction Gen7+, or create E/F/G under that GO.

The resume-executor GO has been drafted but no completion receipt has yet been accepted at this checkpoint.
