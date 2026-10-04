# Sam single-week replay: RESULTS (one run, pre-registered at commit fbf65ac)

**Not blind.** No historical week can be shown unseen for Sam (training/evaluation/selection touch every date 2007-02-07..2026-09-30). Of the five sessions, **2026-09-28..09-30 are inside the 2025-2026 ranking replays that selected him**; **2026-10-01 and 10-02 are post-selection.** This is an out-of-sample *behaviour/plumbing check on one decision*, not evidence of edge.

## Setup actually executed
Candidate code `e81ceab` (engine files hash-verified unchanged); Sam `gen_8e7a622b…` (verified); week 2026-09-28..10-02 (calendar confirmed exactly 5 sessions); $100,000; 8-ETF universe; pilot overlay gross ≤ 18%; 8% halt, caps 4/8 and 2026-11-01 expiry as test assumptions (expiry blocked **none** of the dates); decision at session close, fill at **next session's raw open**; 10 bps slippage (primary) / 5 bps (sensitivity) + 5 bps commission; marks at raw close. Alpaca v2 bars, **feed iex, 1Day, adjustment=all (decisions) / raw (fills, marks)**, bar timestamps 04:00Z = ET midnight (bar date = ET date), requested 2026-03-01..2026-10-02; 150 bars/symbol, HTTP 200, no pagination; 145 warm-up sessions (≥123 required); **no data gaps**; transfers SHA-256-verified (data file `89b328d1…1381`). One deterministic pass; nothing tuned, replaced, re-chosen or re-run.

## Strategy result (primary: 10 bps slippage)
| | Sam | Passive 18% equal-weight | Cash |
|---|---|---|---|
| Start equity | $100,000.00 | $100,000.00 | $100,000.00 |
| End equity | **$100,121.30** | $99,951.42 | $100,000.00 |
| Net return | **+0.1213%** | −0.0486% | 0 |
| Max drawdown (session-close marks) | −0.0486% | −0.0864% | 0 |
| Orders / fills | 3 / 3 | 8 / 8 | 0 |
| Costs (commission + slippage) | $25.78 ($8.64 + $17.14) | $24.91 | $0 |
| Traded notional | $17,269 | $16,687 | 0 |
| Ending exposure | 17.38% (SPY 7, EEM 89, DBC 184) | 16.65% | 0% |
| Halted / pending unfilled | no / none | no / none | – |

* **Decision:** Mon 09-28 close → risk-on regime → top 3 by 122-session return among eligible risk assets: **SPY, EEM, DBC** at 6% each (18% gross after the overlay). Bought Tue 09-29 open. **Holds: 4 sessions** (09-29..10-02); no halt, no liquidation, no second decision (next rebalance would be step 31).
* **Path:** 09-29 −0.04%, 09-30 −0.0486% (max drawdown), 10-01 back to +0.04%, 10-02 +0.12%. P&L split: seen days (09-28..09-30) **−$48.55**; post-selection days (10-01..10-02) **+$169.85**. The positive result came entirely from the two unseen sessions; the contaminated days did not flatter it.
* **vs passive:** +0.170 pp uncorrected; ≈ +0.153 pp after the dividend correction below.
* **Sensitivity (5 bps slippage):** Sam +0.1294% ($100,129.44), cost $17.64, max DD −0.0404%; passive −0.0405%; same decision, same holds.

## Limitations: execution and data (kept separate from the strategy result)
1. **Simulated fills.** Fills are the IEX *raw open* ± explicit slippage, on a feed that is a venue subset (thin volume), not the consolidated tape or the opening auction. Bars do not prove actual execution, queue priority, partial fills or a `day` order queued after the close.
2. **Dividends not credited.** IEF and TLT went ex-dividend on 2026-10-01 (adjustment ratio steps from ≈0.9965/0.9960 to 1.0). Sam holds neither (unaffected); the **passive benchmark does**, so it is understated by an estimated ≈ $16 (0.016%): ≈ −0.032% with dividends. Estimate derived from the adjustment factor, not a recorded dividend.
3. **Adjusted prices are as of retrieval (2026-10-04).** A corporate action between 10-02 and retrieval would back-adjust history; none observed in the ratios.
4. **Five daily marks only.** Max drawdown is on session closes; intraday moves are invisible.
5. **Anchoring assumption.** Step 0 = first test session (as in every earlier replay); a different real-world cadence anchor would produce zero decisions this week.
6. **Not modelled:** unresolved-order hold, kill switch, duplicate protection, broker timeouts / delayed visibility; no broker exists in a replay. Order caps and halt are applied/asserted but never bound (3 orders ≤ 4; no halt).
7. **Statistical power:** one decision and four holding days; +$121 is well inside noise. The positive sign says nothing about edge.

## Process record
Pre-registration committed 2026-10-04T11:10:44+07:00 (`fbf65ac`) before any test-week bar was retrieved; frozen file hashes re-verified by the driver immediately before the run (no change). `execute_replay.py` is a thin driver written after the freeze (parameters read from the pre-registration). Offline harness validation used old data only (Sam's overlay arm equals the accepted evaluator exactly at 5 and 10 bps). Retrieval was read-only GETs on the research instance over SSM (stdin script, nothing written there). No orders, no broker writes, submission constant untouched, no deployment/timers/EC2 changes. Not pushed.
