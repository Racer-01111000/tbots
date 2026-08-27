"""S4.8: simulated accounting. Integer cents throughout so 'identical
final equity' between two runs is an exact comparison, never an
epsilon one.

Execution convention (frozen before any performance was inspected):
  - Indicators/decisions are computed from a session's ADJUSTED close
    (S4.2) -- correct for return/momentum math, since it removes
    artificial jumps from dividends/splits.
  - Target share COUNTS are sized using that same session's RAW close
    (the last actually-tradeable price known at decision time), then
    fixed as an integer share count.
  - The actual FILL happens at the NEXT session's RAW open, with
    slippage and commission applied -- never the same session's close
    the decision was made from. A decision on day T can only ever be
    filled on or after day T+1.
  - Daily equity/drawdown marks use each session's RAW close.
  - Commission: 5 bps of trade notional. Slippage: 5 bps adverse
    (buys fill above open, sells fill below), both conservative
    assumptions for liquid ETFs. Fees are deducted from cash on top of
    the slipped fill price, never netted invisibly into it.
  - Realized P/L and cost basis are rounded to the nearest cent at each
    fill; small residual rounding (at most a few cents over the life of
    an episode) is an accepted simplification, not a defect.
"""
import math

COMMISSION_BPS = 5
SLIPPAGE_BPS = 5


def buy_fill_price_cents(open_price_cents: int) -> int:
    return round(open_price_cents * (1 + SLIPPAGE_BPS / 10_000))


def sell_fill_price_cents(open_price_cents: int) -> int:
    return round(open_price_cents * (1 - SLIPPAGE_BPS / 10_000))


def commission_cents(notional_cents: int) -> int:
    return round(abs(notional_cents) * COMMISSION_BPS / 10_000)


class Portfolio:
    def __init__(self, starting_cash_cents: int):
        self.starting_cash_cents = starting_cash_cents
        self.cash_cents = starting_cash_cents
        self.positions: dict[str, dict] = {}  # symbol -> {"shares": int, "cost_basis_cents": int}
        self.peak_equity_cents = starting_cash_cents
        self.realized_pl_cents = 0
        self.halted = False
        self.total_commission_cents = 0
        self.total_traded_notional_cents = 0
        self.fill_count = 0

    def shares_of(self, symbol: str) -> int:
        return self.positions.get(symbol, {}).get("shares", 0)

    def equity_cents(self, mark_prices_cents: dict) -> int:
        val = self.cash_cents
        for symbol, pos in self.positions.items():
            val += pos["shares"] * mark_prices_cents[symbol]
        return val

    def update_peak_and_drawdown(self, equity_cents: int) -> float:
        if equity_cents > self.peak_equity_cents:
            self.peak_equity_cents = equity_cents
        return (equity_cents / self.peak_equity_cents) - 1.0

    def apply_fill(self, symbol: str, side: str, shares: int, open_price_cents: int) -> dict:
        if side == "buy":
            fill_price = buy_fill_price_cents(open_price_cents)
            notional = shares * fill_price
            fee = commission_cents(notional)
            self.cash_cents -= (notional + fee)
            pos = self.positions.setdefault(symbol, {"shares": 0, "cost_basis_cents": 0})
            pos["shares"] += shares
            pos["cost_basis_cents"] += notional
        elif side == "sell":
            fill_price = sell_fill_price_cents(open_price_cents)
            notional = shares * fill_price
            fee = commission_cents(notional)
            pos = self.positions[symbol]
            avg_cost = pos["cost_basis_cents"] / pos["shares"] if pos["shares"] else 0.0
            realized = round((fill_price - avg_cost) * shares) - fee
            self.realized_pl_cents += realized
            self.cash_cents += (notional - fee)
            pos["shares"] -= shares
            pos["cost_basis_cents"] -= round(avg_cost * shares)
            if pos["shares"] <= 0:
                del self.positions[symbol]
        else:
            raise ValueError(f"unknown side: {side}")

        slippage = abs(fill_price - open_price_cents) * shares
        self.total_commission_cents += fee
        self.total_traded_notional_cents += abs(notional)
        self.fill_count += 1
        return {"symbol": symbol, "side": side, "shares": shares,
                "fill_price_cents": fill_price, "commission_cents": fee,
                "slippage_cents": slippage, "notional_cents": notional}


def compute_orders(target_weights: dict, universe: list, equity_cents: int,
                    sizing_prices_cents: dict, current_shares: dict) -> list[dict]:
    """S4.5/S4.6: target_weights covers only symbols the strategy wants
    a position in; every OTHER symbol currently held is implicitly
    targeted at 0 (full liquidation), never left open by omission.
    No leverage, no shorting: target_shares is always >= 0 (long only),
    floor-rounded to whole shares."""
    full_target = {s: target_weights.get(s, 0.0) for s in set(universe) | set(current_shares)}
    orders = []
    for symbol in sorted(full_target):
        target_dollar_cents = full_target[symbol] * equity_cents
        price = sizing_prices_cents.get(symbol)
        target_shares = 0 if price is None or price <= 0 else math.floor(target_dollar_cents / price)
        target_shares = max(0, target_shares)  # long-only: never negative
        delta = target_shares - current_shares.get(symbol, 0)
        if delta > 0:
            orders.append({"symbol": symbol, "side": "buy", "shares": delta})
        elif delta < 0:
            orders.append({"symbol": symbol, "side": "sell", "shares": -delta})
    return orders
