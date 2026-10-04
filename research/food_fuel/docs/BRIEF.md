# Pre-market brief generator

`python scripts/run_brief.py ASOF_DATE 0915|0929 OUT_DIR [CACHE_DIR] [--no-fetch]` - read-only (public GETs only), order-free, idempotent.

* Skips weekends/NYSE holidays (rule-based table for 2026-27, labelled not exchange-verified) and writes a `NO_BRIEF_*` stub instead.
* An edition file (`food_fuel_brief_<date>_<edition>.json/.md`) is created with `O_EXCL` and never rewritten. The 0929 edition adds `new_since_0915` and `moved_since_0915` (quotes that moved >= 0.2 pp).
* Contents: what changed in food and fuel from the latest WASDE, Crop Progress, US Drought Monitor and EIA weekly data (value, level, class observation/forecast, source, publication time, reference period, retrieval time, units); tested support from the frozen evidence map; mechanism, competing explanations, transmission horizon, invalidation; Asian closes / European intraday / US futures / commodities-FX context with per-quote session labels (fresh, live or stale N days, cause unverified); proxy and coverage warnings.
* Reuses the existing global-brief contract (instrument sections, staleness labelling, 09:15/09:29 ET edition keys). A test asserts the instrument list equals `kim_shadow_tick.GLOBAL_INSTRUMENTS`; this module imports nothing from the paper runner and contains no order or credential code (also tested).
* ETFs, equities and continuous futures are shown as proxies; none is a spot commodity price or opening-auction data.
* Staged scheduling: `scheduling_staged/*.timer|*.service` (Mon-Fri 09:15 and 09:29 America/New_York, sandboxed, `tbotsgym` user). They are NOT installed or enabled; `systemd-analyze verify` is clean. Activation needs a separate decision.
* Sample outputs: `sample_brief/` (manual run for the Monday 2026-10-05 session executed on Sunday 2026-10-04, so every quote is labelled stale from the 2026-10-02 close; the 10-04 stub shows the holiday/weekend gate; the second 0915 invocation returned EXISTS).
