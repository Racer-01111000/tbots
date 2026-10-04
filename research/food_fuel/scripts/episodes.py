"""Descriptive case studies (discovery-grade, all years, NOT validation). Contemporaneous WASDE vintages vs futures path."""
import sys, json, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ffmod.prices import load
W = json.load(open("derived/wasde_vintage.json"))["rows"]
EP = [("2007-08 food stress", "wheat", "ZW", "2007-06-01", "2008-06-30"), ("2007-08 food stress", "soybeans", "ZS", "2007-06-01", "2008-06-30"),
      ("2012 drought", "corn", "ZC", "2012-05-01", "2012-10-31"), ("2013-14 corn glut (failed-shortage follow-through)", "corn", "ZC", "2013-05-01", "2014-03-31"),
      ("2016-17 quiet/ample", "corn", "ZC", "2016-05-01", "2017-03-31"), ("2020 demand collapse and recovery", "corn", "ZC", "2020-02-01", "2020-12-31"),
      ("2022 energy/fertilizer shock", "wheat", "ZW", "2022-01-01", "2022-07-31"), ("2022 energy/fertilizer shock", "corn", "ZC", "2022-01-01", "2022-07-31")]
lines = ["# Episode case studies (descriptive; discovery-grade; original WASDE vintages)", "", "Futures are continuous front-month (roll gaps possible). Price = close on release day. STU = ending stocks/total use of newest marketing year as published that day.", ""]
for name, com, ins, a, b in EP:
    s = load(ins); rows = [r for r in W if r["commodity"] == com and a <= r["release_date"] <= b]
    lines += [f"## {name} - {com} ({ins})", "", "| release | mkt yr | STU | yield | price close | 20d fwd ret |", "|---|---|---|---|---|---|"]
    for r in rows:
        i = s.first_on_or_after(r["release_date"]); fw = s.ret(i, i + 20) if i is not None else None
        lines.append(f"| {r['release_date']} | {r['marketing_year']} | {r['stocks_to_use']:.3f} | {r['yield']} | {s.close[i]:.2f} | {'' if fw is None else f'{fw*100:+.1f}%'} |")
    lines.append("")
for name, ins, a, b in (("2020 oil demand collapse", "CL", "2020-02-14", "2020-06-30"), ("2022 energy shock", "HO", "2022-01-03", "2022-06-30"), ("2022 fertilizer equities", "CF", "2022-01-03", "2022-06-30")):
    s = load(ins); i, j = s.first_on_or_after(a), s.first_on_or_after(b)
    seg = s.close[i:j + 1]; lo, hi = min(seg), max(seg)
    lines += [f"## {name} ({ins}) price range {a}..{b}", "", f"start {seg[0]:.2f}, low {lo:.2f} on {s.dates[i+seg.index(lo)]}, high {hi:.2f} on {s.dates[i+seg.index(hi)]}, end {seg[-1]:.2f}", ""]
open("docs/EPISODES.md", "w").write("\n".join(lines))
print(len(lines))
