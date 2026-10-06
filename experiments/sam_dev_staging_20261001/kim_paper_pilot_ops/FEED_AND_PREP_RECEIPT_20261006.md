# Kim four-session paper pilot — preparation receipt (sanitized; no market-data bytes, no account details)

GO `TBOTS_KIM_REPAIR_AND_FOUR_SESSION_PAPER_PILOT_20261006`. Paper only, pinned account, `https://paper-api.alpaca.markets` only.

## Identity (fail-closed, Kim only)
Genome `gen_0307d23c13fd796db749e78c86947c04ac7de020b3e4c6f02ea1f95dc10e0155` verified four ways (independent canonical sha256, `lib.ids`, selected-bot record, sealed config) and against the frozen champion file sha256 `82a0e1b1…ccdaad21`. Decision-function sources pinned: `control_agent.py 898e5403…`, `indicators.py f95c59d1…`, `risk.py 526d2f79…`, `execution.py c99ec955…` (control_agent/indicators/risk unchanged since commit e00af4a, 2026-09-01, before the champion freeze; execution.py last changed f38da91, 2026-09-25, dividend repair). Sam, lineage D and any other id are refused by id and by mismatch; tests prove Kim cannot be replaced.

## Feed decision: Alpaca consolidated SIP (frozen, no automatic switching)
The existing account returns SIP historical daily bars (HTTP 200, no subscription purchase). Provider Alpaca; venue coverage consolidated SIP (IEX is a single-venue print and is NOT used); close = Alpaca's daily-bar close; signals use `adjustment=all`, sizing/reconciliation use `adjustment=raw` of the same completed session. Yahoo (the training/ranking source, consolidated `adjclose`) is not authorized as a forward feed, and exact equivalence is NOT claimed.
Measured on 392 common sessions 2025-02-03..2026-08-25 (nothing later examined):

| comparison | adjusted daily-return difference | notes |
|---|---|---|
| SIP vs Yahoo | std 0.00–1.42 bps, max 0.00–4.38 bps per symbol (worst: DBC) | SIP raw close vs Yahoo close: median 0.0 bps, max 0.58 bps (TLT) |
| IEX vs Yahoo | std 1.7–8.2 bps, max 5.5–116.7 bps (SPY) | rejected |
| SIP vs IEX | mirrors IEX vs Yahoo | |

Kim's frozen decision on the 97 evaluable days (>=296 bars) selected the same asset on SIP, IEX and Yahoo (97/97; the overlap is short, so this is weak evidence). Adjustment differences remain possible after future corporate actions (Alpaca re-adjusts history at retrieval; Yahoo's vintage was 2026-08-26).

## Data boundary (pilot_data.py)
Requires >=296 observations, strictly increasing dates, no duplicates, finite positive closes, contiguity over the exchange calendar for all eight symbols, the last bar = the latest fully completed session, nothing dated later, and a raw sizing close for that session. Any violation abstains (no order, no sale). Tests: gap, duplicate, future-dated, out-of-order, NaN, negative, short warm-up, missing sizing bar.

## Opening path
Decision from the latest completed session before the opening date (Oct 6 opening -> inputs through Oct 5); actual retrieval/compute/submission times recorded, never backdated; Alpaca clock must report open inside 09:30-09:35 ET; whole-share DAY market orders, `extended_hours:false`.

## Verification
* Local (HOST, Python 3.13): full relevant suite 305 passed (247 pre-existing + 58 new pilot tests); extracted-release run of the pilot tests: 58 passed.
* EC2 (`venv313` Python 3.13.5, exact deployed interpreter): full suite 305 passed.
* Mutation checks (each turned tests red): account-pin check removed, baseline-sell cap removed, same-day close used, halt ignoring pilot ownership, lock removed, cancel skipped at halt, anchor reset by init, sizing from adjusted close, expiry not cancelling, normal order cap removed.
* A real-decision-function test exposed one integration bug (the 1-share baseline counted toward the 18% gross cap and refused a clean target); fixed and pinned by a regression test.
* Validate-only invocation on EC2 (scratch state, real calendar/account/positions/orders/SIP-data/clock reads, submission disabled): identity ok, account pin ok, baseline holdings as expected, no unexplained orders, history valid for all eight symbols, decision prepared from data through 2026-10-05 (feed `sip`).
* Units rendered from the campaign and checked with `systemd-analyze verify` and `systemd-analyze calendar` on the instance (nothing installed).

## Risk terms in the sealed config
8% drawdown halt from the persisted pilot-start equity peak; 18% per-asset / 18% gross target caps; 4 normal orders per session; separate 8 liquidation orders per session; long-only; DAY orders; 10% cash reserve; pilot-owned shares only are ever sold; the pre-existing 1-share baseline is never sold.
