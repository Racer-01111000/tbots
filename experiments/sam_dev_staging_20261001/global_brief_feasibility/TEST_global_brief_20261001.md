# TEST — Global Pre-Open Brief (feasibility only, not a production edition)
Generated 2026-10-01T09:41:26Z · Pilot: KIM_BOUNDED_SHADOW_20261001

## Asia (completed/current sessions)
- **Nikkei 225**: 68,956.72 (+3.91% vs prior close) — fresh close, Oct 1 JST
- **Hang Seng**: 24,613.27 (-0.89% vs prior close) — **stale: Sep 30 close carried over, Hong Kong National Day holiday Oct 1**
- **CSI 300** (mainland, Shanghai): 4,357.62 (+0.29% vs prior close) — **stale: Sep 30 close carried over, China Golden Week holiday**
- **KOSPI**: 6,971.35 (-0.66% vs prior close) — fresh close, Oct 1 KST
- **S&P/ASX 200**: 8,614.40 (-1.01% vs prior close) — fresh close, Oct 1 AEST

## Europe (live, still trading — not a closing result)
- FTSE 100: 10,445.18 (-2.20% vs prior close), live
- DAX Performance Index: 25,015.43 (-0.99% vs prior close), live
- Euro Stoxx 50: 6,211.35 (-1.45% vs prior close), live

## US overnight futures
- S&P 500 futures (ES): 7,733.75 (-0.17% vs prior settle)
- Nasdaq futures (NQ): 30,893.00 (+1.07% vs prior settle)

## Commodities / FX
- WTI Crude (CL): $92.18 (-0.45%)
- Brent Crude (BZ): $100.32 (-4.71%)
- Gold (GC): $4,189.00 (+0.49%)
- ICE US Dollar Index, spot (DXY): 101.798 (+0.59%)

## News (EIA/USDA only — not comprehensive geopolitical coverage)
338 items captured, 0 new since last poll (acceptable — source has no new
releases right now). Most recent: EIA "Today in Energy" items on EV
electricity use, Henry Hub gas prices, crude/gas production — energy
releases only, no agriculture items in this window.

## Test checks
| Check | Result |
|---|---|
| Tick progression (10 min) | PASS — 34 IEX ticks logged in the last 10 minutes, advancing every minute |
| Delayed SIP, completed session | PASS — 200, 5 real daily bars returned for SPY (sample: 2026-09-30 close $762.63) |
| Global instrument coverage | PASS — 14/14 reachable via free Yahoo chart API |
| News poll/dedup | PASS — stable count, correct "no new items" handling |

**Overall: PASS.** All sources free/already-entitled; no new credentials,
no paid plan. Source for every index/future: Yahoo Finance public chart
API (unauthenticated). Source for SIP bars: existing Alpaca entitlement
via the private runtime loader. No Alpaca headers were sent to Yahoo/EIA/
USDA. Orders untouched, Kim's logic untouched, tonight's expiry/cleanup
unchanged.

