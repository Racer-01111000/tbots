# Import provenance

This repository snapshot was derived from the tracked tree of the NODE source
repository at `/opt/evolutionary-markets`, revision
`74acb3666b046dd363c2b4b502ae0d0a2a1806c1`.

Snapshot date: 2026-09-01.

The snapshot was exported from the named revision without importing the source
repository's 22-commit history. The original repository and its history remain
the authoritative preservation copy on NODE.

The import excludes generated and untracked experiment runs, local databases,
raw provider downloads, caches, virtual environments, credentials, secrets,
logs, and machine-specific material. Small, intentionally tracked research
fixtures and frozen evaluation bundles are retained for reproducibility.

Legacy external-project isolation markers were renamed to neutral external-system
markers, and their deterministic envelope hashes were refreshed. No trading,
risk, execution, evolutionary, or evaluation behavior was intentionally changed.

## Known omission

The authorized S5A DEVELOPMENT bundle
(`data/development_bundles/s5adev_98e2f764f466b90ee2bbc2532b75188bfc4fd20b4a13523f94bce65e6a1f193a/`,
pinned in `scripts/s5a_config.py`) was excluded from this snapshot by the
`data/development_bundles/*` ignore rule. It is a frozen evaluation bundle like
the tracked qualification, championship, and final-reserve bundles and should
have been retained. Until it is copied from NODE, 7 tests that load it error
with "authorized DEVELOPMENT bundle manifest is unreadable". `.gitignore` now
allows that bundle directory so it can be committed.
