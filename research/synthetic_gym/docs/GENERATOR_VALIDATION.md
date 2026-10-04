# Synthetic market generator `gen-v1`: design, assumptions, validation, and known realism gaps

Regenerate every number below with `python scripts/validate_generator.py` (fresh engineering worlds `ENG-FROZEN`, indices 600-619 for each of six
families = 120 worlds; seeds disjoint from the 12 training, 6 validation and 6 sealed worlds). Source data: `docs/generator_validation_report.json`.

## 1. What is calibrated from history and what is assumed

| Layer | Basis | Status |
|---|---|---|
| Daily return **shape**: joint (8-asset) residual vectors, overnight/intraday split, high/low extension, log-volume deviation | Block resampling of the development lane (2007-02-07..2018-12-31, dataset `ds_7e16896c…`, 2,996 sessions), standardised by a GARCH(1,1)-style filter (a=0.09, b=0.90 chosen by quasi-likelihood grid) | **Calibrated** (verified historical) |
| Regime-dependent behaviour | Residuals resampled from regime-matched pools (SPY trailing-21d vol percentile + 20% drawdown rule); pools are disjoint quarter-blocks assigned to train / validation / sealed (23 / 12 / 12 blocks) | **Calibrated** |
| Dividend months, yields, frequencies | Per-symbol yearly templates from 2008-2018 `dividend_amount` events | **Calibrated** (bond-fund yields scale with the world's rate level: assumption) |
| Unconditional volatility, mean return | Moment-matched constants (`variance_scale`, `drift_bias_correction_annual`) estimated on engineering worlds only | **Calibrated** to history / to declared priors |
| Rolling-window extremes, daily-move caps, volatility ceiling | 1.15x / 1.5x the development lane's own extremes | **Calibrated bounds** |
| Event mechanisms (energy/diesel, fertilizer/crop, inflation/yields, credit/liquidity, technology valuation, growth) and every asset response (betas, sign-flip probabilities, lags, variants, family event menus) | `gym/factors.py` | **ASSUMPTION** (no history in the development lane can estimate them) |
| Positive stock/bond co-movement | Random intervals in which bond residuals are sign-flipped | **ASSUMPTION** (the development lane's median SPY-TLT 60-day correlation is -0.44; it contains no sustained inflationary regime) |
| Half-spreads | Fixed per-symbol base bps x liquidity stress | **ASSUMPTION** (dataset has no quotes) |
| Current macro conditioning of initial states | none | The three named economic sources could not be verified (HTTP 403 bot protection); nothing was taken from them. See `sources/SOURCE_MANIFEST.json` |

## 2. Generator iteration history (chronological, including failures)

1. **Variance explosion.** The first draft multiplied volatility by the regime multiplier *and* fed the multiplied return back into the variance filter; effective persistence in stress exceeded 1 and prices went to 1e286. Fixed by keeping the filter on the base scale.
2. **Systematic drift bias.** Events and residual means moved realised mean returns by -11 pp (GLD) to +10 pp (DBC) a year relative to the drawn drift. Recentred event overlays, demeaned residuals within pool x regime, then moment-matched the remainder on an engineering ensemble (120 worlds), verified on 120 fresh ones.
3. **Uncapped jumps / fat tails** (a Student-t jump reached 24% in a day). Capped; asset-specific regime-volatility exponents (bonds, gold, commodities muted).
4. **Second variance blow-up** found in a *training* world (SPY -11%, -20%, -28%, -42%, -59% on consecutive days, rolling vol 329%). Cause: crisis-pool residuals have E[z^2] > 1, so persistence a+b·E[z^2] > 1 for long stretches. My earlier plausibility test missed it because a very wide synthetic band contains almost any historical value. Fixes: residuals normalised to unit variance within each pool x regime; the volatility state clamped to the historical range of the filter; any single day's noise capped at 1.5x the worst historical day; total volatility (state x regime) capped at the historical ceiling; regime multipliers softened.
5. **Equity stress double-counted** (synthetic equity vol 13-22% above history). Per-asset variance scales moment-matched in two passes; verified within 5% on fresh worlds.
6. **Crash speed.** A world still produced SPY -71%, EEM -85%, VNQ -96% in 70 sessions (the passive control showed a 12% drawdown at 18% gross). History's fastest comparable move is about -45%. Added a **path-speed guard**: rolling 63- and 252-session cumulative returns may not leave 1.15x the development lane's extremes. Also a weak symmetric price anchor beyond a factor of ~2.5, and a transient (not permanent) event-to-regime stress driver.
7. Tail acceptance criteria (section 4) were **written down after item 4 exposed the problem and before items 5-6 were tuned**; I stopped at four tuning iterations against them and report what remained.

Accounting was verified correct throughout: the engine's equity reconciled to an independent rebuild from the fill log; the bad worlds were generator behaviour, not engine errors.

## 3. Plausibility against the calibration set (87 statistics, 120 fresh worlds)

86 / 87 pass (rule: the historical value lies inside the synthetic [P05, P95] band, or the synthetic median is within 35% relative / 0.25 absolute of it).
* **Fails: EEM skewness** (history +0.22, synthetic median -0.46, P95 about 0.0). History's EEM skew is positive because of sharp 2008-09 rebound days; the synthetic worlds are more negatively skewed. This is conservative for a long-only study but is a gap.
* Volatility: synthetic/historical median ratio 0.955-0.987 for every asset. Mean-return offsets from the priors: -2.1 to -0.05 pp a year (VNQ -2.1 is the largest).
* Cross-asset correlations: all 28 pairs pass. Stock/bond: 105 / 120 worlds contain a stretch of positive 60-day SPY-TLT correlation (> +0.15) and all 120 contain a negative one, so the correlation sign changes within worlds, although history alone could never show the positive stretches.

## 4. Path-severity criteria (declared before the later tuning), 11 checks, 10 pass

| Check | Value | Band | |
|---|---|---|---|
| SPY worst day, median | -0.090 | [-0.156, -0.073] | ok |
| share of days with rolling-20d SPY vol > 40% / > 60% | 5.0% / 1.1% | [1.9%, 11%] / [0.5%, 6.4%] | ok |
| SPY 5-year max drawdown, median | **-0.341** | [-0.33, -0.15] | **miss by 0.011** |
| ... 10th-percentile world | -0.546 | >= -0.65 | ok |
| ... worst world | -0.72 | >= -0.80 | ok |
| worst-asset trough / start: median, P05, min | 0.513, 0.281, 0.198 | >= 0.30, 0.15, 0.10 | ok |
| max rolling-20d SPY vol: median, P95 | 0.615, 0.954 | <= 0.90, <= 1.40 | ok |

The development lane's own numbers: SPY 5y max drawdowns -55%, -51%, -22%, -19%, -19%, -13%, -13% (median -19%); share of days with rolling vol > 40% = 3.7%; max rolling-20d vol 0.94.
The one miss is small but real: the median synthetic world is slightly more drawdown-prone than the declared band, because every world is calibrated to the *whole* lane (which contains 2008) and so carries a crisis-like tail. Anyone reading a result must remember the worlds skew harsh.

## 5. Behavioural consistency of the reference strategies (18% gross overlay)

| Strategy | Max drawdown on 6 historical dev-lane windows | Synthetic median (60 worlds) | Turnover/yr: history | Turnover/yr: synthetic median [P05, P95] |
|---|---|---|---|---|
| champion | -3.8% to -6.4% | -6.1% | 0.93 to 1.21 | 1.03 [0.66, 1.32] |
| Sam | -2.6% to -4.2% | -4.1% | 0.95 to 1.18 | 1.26 [1.01, 1.56] |
| D-primary-rank3 | -3.1% to -4.9% | -4.2% | 0.93 to 1.15 | 1.25 [1.00, 1.55] |
| passive18 | -1.7% to -6.2% | -2.9% | 0.09 to 0.14 | 0.13 [0.10, 0.16] |

## 6. Known realism gaps (read before relying on any synthetic result)

* **Daily-return tails are thinner than history.** Excess kurtosis: SPY 7.8 vs 13.6, EEM 5.0 vs 15.0, GLD 3.3 vs 6.9, VNQ 7.3 vs 14.5. The stabilising constraints trade tail weight for plausible *paths*; history's kurtosis is dominated by 2008-09.
* **Skew is more negative** than history for equities (SPY -0.60 vs -0.13).
* **All event relationships are assumptions.** A strategy cannot be said to exploit a real macro relationship by winning here.
* **Hard guards are blunt instruments** (daily cap, path-speed guard, price anchor); they bound extremes but also truncate legitimate rare events.
* **The worlds share one simulator**; validation and sealed-test worlds use different severity ranges and regime-transition noise but the same machinery.
* Ex-dividend handling embeds the distribution in the opening gap; ETF distribution timing is templated from 2008-2018.
* Daily bars cannot establish auction fills, order-book depth or intraday ordering; execution stress is modelled by overlays, and ordering-sensitive failures are tested with the mock-broker fixtures instead.
