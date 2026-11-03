import random
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

import orderbook


@dataclass
class Trade:
    price: int
    quantity: int
    timestamp: float
    side: str = "BUY"


class MarketSimulator:
    """In-process C++ OrderBook market-flow generator with realistic bounded price dynamics."""

    ANCHOR_PRICE_CENTS = 500_00  # $500.00 anchor
    MIN_PRICE_CENTS = 50_00      # $50.00 hard floor (never negative)
    MAX_PRICE_CENTS = 2000_00    # $2,000.00 hard ceiling

    def __init__(self, cpp_orderbook):
        self.orderbook = cpp_orderbook
        self.simulation_thread: Optional[threading.Thread] = None
        self.generator_ = random.Random(42)
        self.running = False
        self.last_ask = 501_00
        self.last_bid = 499_00

        self.trades: List[Trade] = []
        self.base_username = "simulator"

    def start_simulation(self):
        if self.running:
            return
        self.running = True
        self.simulation_thread = threading.Thread(target=self._simulation_loop, daemon=True)
        self.simulation_thread.start()

    def stop_simulation(self):
        self.running = False
        if self.simulation_thread:
            self.simulation_thread.join(timeout=1.0)

    def add_buy_pressure(self):
        pressure_thread = threading.Thread(target=self._apply_buy_pressure, daemon=True)
        pressure_thread.start()

    def add_sell_pressure(self):
        pressure_thread = threading.Thread(target=self._apply_sell_pressure, daemon=True)
        pressure_thread.start()

    def _clamp_price(self, price_cents: float) -> int:
        return int(max(self.MIN_PRICE_CENTS, min(self.MAX_PRICE_CENTS, round(price_cents))))

    def _track_trade(self, price: int, quantity: int, side: str = "BUY"):
        now = time.time()
        self.trades.append(Trade(self._clamp_price(price), int(quantity), now, side))
        if len(self.trades) > 1200:
            self.trades = self.trades[-800:]

    def _replenish_depth_around(self, center_cents: int, security_id: int = 1):
        """Ensure both bid and ask sides maintain multi-level resting liquidity."""
        center = self._clamp_price(center_cents)
        bid_map = self.orderbook.get_bid_quantities()
        ask_map = self.orderbook.get_ask_quantities()

        if len(bid_map) < 6:
            top_bid = self.orderbook.get_best_bid_price() or (center - 50)
            top_bid = self._clamp_price(top_bid)
            for lvl in range(1, 9):
                p = self._clamp_price(top_bid - lvl * 15)
                qty = max(40, int(self.generator_.normalvariate(180, 45)))
                self.orderbook.add_order(orderbook.create_order("mm_bid", security_id, p, qty, True))
            self.last_bid = top_bid

        if len(ask_map) < 6:
            top_ask = self.orderbook.get_best_ask_price() or (center + 50)
            top_ask = self._clamp_price(max(top_ask, (self.orderbook.get_best_bid_price() or center) + 20))
            for lvl in range(1, 9):
                p = self._clamp_price(top_ask + lvl * 15)
                qty = max(40, int(self.generator_.normalvariate(180, 45)))
                self.orderbook.add_order(orderbook.create_order("mm_ask", security_id, p, qty, False))
            self.last_ask = top_ask

    def _apply_sell_pressure(self):
        security_id = 1
        username = "sell_pressure"
        for _ in range(120):
            if not self.running:
                break
            time.sleep(0.004)
            qty = max(20, int(self.generator_.normalvariate(95, 20)))
            old_matched = self.orderbook.get_orders_matched()
            self.orderbook.place_market_sell_order(qty)
            new_matched = self.orderbook.get_orders_matched()

            best_bid = self.orderbook.get_best_bid_price() or max(self.MIN_PRICE_CENTS + 100, self.last_bid - 15)
            best_ask = self.orderbook.get_best_ask_price() or (best_bid + 40)
            best_bid = self._clamp_price(best_bid)
            best_ask = self._clamp_price(max(best_bid + 15, best_ask))

            if new_matched > old_matched:
                self._track_trade(best_bid, new_matched - old_matched, "SELL")

            ask_p = self._clamp_price(best_bid + self.generator_.randint(10, 35))
            bid_p = self._clamp_price(best_bid - self.generator_.randint(10, 45))
            self.orderbook.add_order(orderbook.create_order(username, security_id, ask_p, qty, False))
            self.orderbook.add_order(orderbook.create_order(username, security_id, bid_p, int(qty * 0.8), True))
            self.last_bid = bid_p
            self.last_ask = ask_p
            self._replenish_depth_around((best_bid + best_ask) // 2, security_id)

    def _apply_buy_pressure(self):
        security_id = 1
        username = "buy_pressure"
        for _ in range(120):
            if not self.running:
                break
            time.sleep(0.004)
            qty = max(20, int(self.generator_.normalvariate(95, 20)))
            old_matched = self.orderbook.get_orders_matched()
            self.orderbook.place_market_buy_order(qty)
            new_matched = self.orderbook.get_orders_matched()

            best_ask = self.orderbook.get_best_ask_price() or min(self.MAX_PRICE_CENTS - 100, self.last_ask + 15)
            best_bid = self.orderbook.get_best_bid_price() or (best_ask - 40)
            best_ask = self._clamp_price(best_ask)
            best_bid = self._clamp_price(min(best_ask - 15, best_bid))

            if new_matched > old_matched:
                self._track_trade(best_ask, new_matched - old_matched, "BUY")

            bid_p = self._clamp_price(best_ask - self.generator_.randint(10, 35))
            ask_p = self._clamp_price(best_ask + self.generator_.randint(10, 45))
            self.orderbook.add_order(orderbook.create_order(username, security_id, bid_p, qty, True))
            self.orderbook.add_order(orderbook.create_order(username, security_id, ask_p, int(qty * 0.8), False))
            self.last_bid = bid_p
            self.last_ask = ask_p
            self._replenish_depth_around((best_bid + best_ask) // 2, security_id)

    def run_simulation(self):
        security_id = 1
        username = "simulation"

        for lvl in range(1, 12):
            bid_p = self.ANCHOR_PRICE_CENTS - lvl * 15
            ask_p = self.ANCHOR_PRICE_CENTS + lvl * 15
            qty = 180 + (12 - lvl) * 25
            self.orderbook.add_order(orderbook.create_order(username, security_id, bid_p, qty, True))
            self.orderbook.add_order(orderbook.create_order(username, security_id, ask_p, qty, False))

        self.last_bid = self.ANCHOR_PRICE_CENTS - 15
        self.last_ask = self.ANCHOR_PRICE_CENTS + 15

        while self.running:
            time.sleep(0.015)

            best_bid = self.orderbook.get_best_bid_price()
            best_ask = self.orderbook.get_best_ask_price()
            if best_bid is None or best_ask is None or best_bid >= best_ask:
                self._replenish_depth_around(self.ANCHOR_PRICE_CENTS, security_id)
                best_bid = self.orderbook.get_best_bid_price() or self.last_bid
                best_ask = self.orderbook.get_best_ask_price() or self.last_ask

            best_bid = self._clamp_price(best_bid)
            best_ask = self._clamp_price(max(best_bid + 10, best_ask))
            mid_price = (best_bid + best_ask) / 2.0

            drift_bias = (self.ANCHOR_PRICE_CENTS - mid_price) * 0.04

            for _ in range(2):
                old_matched = self.orderbook.get_orders_matched()
                qty = max(15, int(self.generator_.normalvariate(85, 25)))
                r = self.generator_.random()

                if r < 0.32:
                    is_buy = self.generator_.random() < (0.52 if drift_bias >= 0 else 0.48)
                    if is_buy:
                        self.orderbook.place_market_buy_order(qty // 2)
                        exec_p = best_ask
                        side_str = "BUY"
                    else:
                        self.orderbook.place_market_sell_order(qty // 2)
                        exec_p = best_bid
                        side_str = "SELL"
                    new_matched = self.orderbook.get_orders_matched()
                    if new_matched > old_matched:
                        self._track_trade(exec_p, new_matched - old_matched, side_str)
                elif r < 0.66:
                    offset = self.generator_.normalvariate(-25 + drift_bias, 35)
                    p = self._clamp_price(mid_price + offset)
                    self.orderbook.add_order(orderbook.create_order(username, security_id, p, qty, True))
                    self.last_bid = p
                    new_matched = self.orderbook.get_orders_matched()
                    if new_matched > old_matched:
                        self._track_trade(p, new_matched - old_matched, "BUY")
                else:
                    offset = self.generator_.normalvariate(25 + drift_bias, 35)
                    p = self._clamp_price(mid_price + offset)
                    self.orderbook.add_order(orderbook.create_order(username, security_id, p, qty, False))
                    self.last_ask = p
                    new_matched = self.orderbook.get_orders_matched()
                    if new_matched > old_matched:
                        self._track_trade(p, new_matched - old_matched, "SELL")

            self._replenish_depth_around(int(mid_price), security_id)

    def _simulation_loop(self):
        self.run_simulation()

    def get_recent_volume(self, seconds: float = 1.0) -> int:
        current_time = time.time()
        return sum(t.quantity for t in self.trades if current_time - t.timestamp <= seconds)

    def get_recent_trades(self, limit: int = 12) -> List[Trade]:
        return list(reversed(self.trades[-limit:]))
