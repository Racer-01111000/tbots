#!/bin/sh
# Builds the immutable recurring-lifecycle release (code only; no data, no state) and, optionally, the full-suite tarball.
# usage: build_release.sh REPO_DIR COMMIT OUT_RELEASE_TAR [OUT_SUITE_TAR]
set -eu
REPO="$1"; COMMIT="$2"; OUT="$3"; SUITE="${4:-}"
cd "$REPO"
D=experiments/sam_dev_staging_20261001
git archive --format=tar "$COMMIT" \
  scripts evolution/protocol/frozen_champion_s5d_champion_00ac1019646747ad88b7eac1955dc1067f598f53e77cce01c07de7603dab2672.json \
  $D/kim_recurring.py $D/kim_recurring_config.json $D/kim_recurring_ops \
  $D/kim_paper_pilot.py $D/kim_paper_pilot_config.json \
  $D/pilot_identity.py $D/pilot_data.py $D/session_guards.py $D/session_calendar.py $D/peak_equity.py $D/atomic_io.py \
  $D/kim_once_per_session_v2.py $D/pilot_config.json $D/pilot_config.py $D/selected_development_bot.json \
  $D/alpaca_adapter/alpaca_adapter.py $D/alpaca_adapter/alpaca_paper_broker.py $D/alpaca_adapter/runtime_credential_loader.py \
  $D/kim_order_simulation_tests/kim_order_logic.py $D/kim_order_simulation_tests/broker_reconciliation.py $D/kim_order_simulation_tests/order_limits.py \
  $D/SAM_MANIFEST_20261001.json $D/test_kim_recurring_20261010.py $D/fake_alpaca_recurring.py $D/fake_alpaca_pilot.py \
  | gzip -n -9 > "$OUT"
sha256sum "$OUT"
if [ -n "$SUITE" ]; then
  git archive --format=tar "$COMMIT" scripts/lib scripts evolution/protocol $D | gzip -n -9 > "$SUITE"
  sha256sum "$SUITE"
fi
