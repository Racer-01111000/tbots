# ARMED — Kim four-session PAPER pilot (sanitized: no account details, no market-data bytes)

GO `TBOTS_KIM_PAPER_ACTIVATION_FIXED_DATES_20261006` (Rick). Paper only: `https://paper-api.alpaca.markets`, pinned account, live host rejected independent of any switch.

* **Deployed SHA:** `b4a7a8ccd13c81bfae03f8dfc57c9fc3220292c2` at `/opt/tbots-kim-paper/releases/b4a7a8cc…` (`current`); rollback target = inert `eff6207…`. Release tarball sha256 `ac2bbaa0eb980b70dbbb2b1ff42080e0f451de248a5f91ef749457cbdaa71841`.
* **Sealed config sha256 (= submission seal):** `6eb2c972c0ca16de5200e63921972ac80db72cb92e60b3325bcb7a65b2883512`. Genome `gen_0307d23c…0155` (Kim); feed `sip` (frozen).
* **Verified dates (frozen write-once from Alpaca's calendar):** 2026-10-06, 07, 08, 09 — each 09:30–16:00 ET (13:30Z–20:00Z, UTC−4) = Hanoi 20:30–03:00. Fourth close 2026-10-09 20:00Z = 03:00 Hanoi Oct 10.
* **Schedule (America/New_York, systemd on the instance, independent of HOST):** preflight 09:15 · open 09:30 (window 09:30–09:35, after Alpaca's clock confirms open) · monitor every 5 min 09:35–16:05 · expiry 2026-10-09 16:10 ET · HOLD enforcement 2026-10-09 16:55 ET (= 03:55 Hanoi; bound is close+60 min).
* **Next trigger:** preflight 2026-10-06T13:15:00Z (20:15 Hanoi); first submission window 13:30–13:35Z (20:30–20:35 Hanoi).
* **Risk terms:** 8% halt from the persisted pilot-start equity peak; 18% per-asset / 18% gross caps counting every holding incl. the protected baseline share; 4 normal + 8 liquidation orders per session; long-only; DAY; extended hours off; only pilot-owned shares are ever sold.
* **Tests:** 306/306 on HOST (Python 3.13) and 306/306 on the instance's `venv313` (Python 3.13.5); installed file hashes equal the committed ones. Gate truth table on the instance: plain shell False; flag only False; flag + wrong seal False; exact unit environment True.
* **Installed units (sha256 prefix):**
  * 7c4bea2671f8  hardstop.sh
  * 4907eb620998  hold.sh
  * ef5369d936fe  tbots-kim-paper-expiry.service
  * 88668acdfa9f  tbots-kim-paper-expiry.timer
  * 63675ca756c8  tbots-kim-paper-hardstop.service
  * bd4494b13683  tbots-kim-paper-hold.service
  * bdd90fffaaef  tbots-kim-paper-hold.timer
  * a13eed080891  tbots-kim-paper-monitor.service
  * dd8752b63db0  tbots-kim-paper-monitor.timer
  * 010e741e0a2d  tbots-kim-paper-open.service
  * f9780ad937ae  tbots-kim-paper-open.timer
  * 067c2c17a225  tbots-kim-paper-preflight.service
  * c2e14f58a70b  tbots-kim-paper-preflight.timer
* **State at arming:** five timers enabled+active, 0 failed units, no competing paper trigger (earlier Kim shadow timers disabled), V5 research/guard timers untouched, no STOP/HOLD sentinel, production campaign + anchor + ledger + peak initialized once.
* **Report conventions:** an order is described as submitted / accepted / partially filled / filled strictly from broker evidence in per-session receipts; acceptance and simulation are never called fills. Per-session and final HOLD receipts are written on the instance (`/var/lib/tbots-kim-paper/receipts`) and collected privately.
