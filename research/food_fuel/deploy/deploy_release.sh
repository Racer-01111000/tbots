#!/bin/bash
# Idempotent pinned-revision deploy (run as root on the research instance). usage: deploy_release.sh <full_sha> <archive.tar> <archive_sha256>
# Verifies the archive hash, unpacks to /opt/tbots-foodfuel/releases/<sha>, records the previous release for rollback, flips `current`. Never touches
# /var/lib/tbots-foodfuel state, the paper runner, broker credentials, NODE or KESTREL.
set -euo pipefail
SHA="$1"; ARC="$2"; WANT="$3"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "bad sha"; exit 2; }
GOT=$(sha256sum "$ARC" | cut -d' ' -f1); [ "$GOT" = "$WANT" ] || { echo "archive hash mismatch $GOT"; exit 3; }
id tbotsff >/dev/null 2>&1 || useradd --system --no-create-home --shell /sbin/nologin tbotsff
install -d -o root -g root -m 0755 /opt/tbots-foodfuel/releases
install -d -o tbotsff -g tbotsff -m 0750 /var/lib/tbots-foodfuel
REL=/opt/tbots-foodfuel/releases/$SHA
if [ ! -d "$REL" ]; then
  mkdir -p "$REL.tmp" && tar -xf "$ARC" -C "$REL.tmp" && echo "$SHA" > "$REL.tmp/DEPLOYED_SHA" && mv "$REL.tmp" "$REL" && chown -R root:root "$REL"
fi
# research dependencies come from the root-owned, hash-pinned v1 export (data, frozen schemas, reference genomes)
for d in data evolution experiments scripts; do ln -sfn /opt/tbots-gym/src/$d "$REL/$d"; done
PREV=""; [ -L /opt/tbots-foodfuel/current ] && PREV=$(readlink -f /opt/tbots-foodfuel/current)
[ -n "$PREV" ] && [ "$PREV" != "$REL" ] && echo "$PREV" > /opt/tbots-foodfuel/ROLLBACK_TO
ln -sfn "$REL" /opt/tbots-foodfuel/current.new && mv -Tf /opt/tbots-foodfuel/current.new /opt/tbots-foodfuel/current
echo "current -> $(readlink -f /opt/tbots-foodfuel/current); rollback target: $(cat /opt/tbots-foodfuel/ROLLBACK_TO 2>/dev/null || echo none)"
