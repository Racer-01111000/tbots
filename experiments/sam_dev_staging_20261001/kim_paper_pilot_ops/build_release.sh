#!/bin/sh
# Builds the immutable release tarball from a committed revision (git archive: code only, no data, no state, no replay dirs).
# usage: build_release.sh REPO_DIR COMMIT OUT_TAR
set -eu
REPO="$1"; COMMIT="$2"; OUT="$3"
cd "$REPO"
git archive --format=tar "$COMMIT" \
  scripts evolution/protocol/frozen_champion_s5d_champion_00ac1019646747ad88b7eac1955dc1067f598f53e77cce01c07de7603dab2672.json \
  experiments/sam_dev_staging_20261001/kim_paper_pilot.py experiments/sam_dev_staging_20261001/kim_paper_pilot_config.json \
  experiments/sam_dev_staging_20261001/pilot_identity.py experiments/sam_dev_staging_20261001/pilot_data.py \
  experiments/sam_dev_staging_20261001/session_guards.py experiments/sam_dev_staging_20261001/session_calendar.py \
  experiments/sam_dev_staging_20261001/peak_equity.py experiments/sam_dev_staging_20261001/atomic_io.py \
  experiments/sam_dev_staging_20261001/kim_once_per_session_v2.py experiments/sam_dev_staging_20261001/pilot_config.json \
  experiments/sam_dev_staging_20261001/pilot_config.py experiments/sam_dev_staging_20261001/selected_development_bot.json \
  experiments/sam_dev_staging_20261001/alpaca_adapter/alpaca_adapter.py experiments/sam_dev_staging_20261001/alpaca_adapter/alpaca_paper_broker.py \
  experiments/sam_dev_staging_20261001/alpaca_adapter/runtime_credential_loader.py \
  experiments/sam_dev_staging_20261001/kim_order_simulation_tests/kim_order_logic.py \
  experiments/sam_dev_staging_20261001/kim_order_simulation_tests/broker_reconciliation.py \
  experiments/sam_dev_staging_20261001/kim_order_simulation_tests/order_limits.py \
  experiments/sam_dev_staging_20261001/SAM_MANIFEST_20261001.json experiments/sam_dev_staging_20261001/test_kim_paper_pilot_20261006.py experiments/sam_dev_staging_20261001/fake_alpaca_pilot.py \
  | gzip -n -9 > "$OUT"
sha256sum "$OUT"
