"""OrderBook package with native C++ pybind11 bindings and automatic pure-Python fallback."""

from __future__ import annotations

from collections import OrderedDict
import itertools
from typing import Dict, Optional

try:
    from ._mdfeed_receiver import *  # type: ignore[import-not-found]
except ImportError:
    pass

try:
    from ._orderentry_client import *  # type: ignore[import-not-found]
except ImportError:
    pass

try:
    from ._orderbook import *  # type: ignore[import-not-found]
    NATIVE_BINDINGS_LOADED = True
except ImportError:
    NATIVE_BINDINGS_LOADED = False

    _order_id_counter = itertools.count(1)

    class Security:
        """Pure-Python fallback for C++ Security when pybind11 extension is not built for active Python."""

        def __init__(self, name: str, ticker: str, security_id: int) -> None:
            self._name = str(name)
            self._ticker = str(ticker)
            self._security_id = int(security_id)

        def get_security_id(self) -> int:
            return self._security_id

    class OrderCore:
        """Pure-Python fallback for C++ OrderCore."""

        def __init__(self, *args) -> None:
            if len(args) == 2:
                username, security_id = args
                self._order_id = next(_order_id_counter)
                self._username = str(username)
                self._security_id = int(security_id)
            elif len(args) == 3:
                order_id, username, security_id = args
                self._order_id = int(order_id)
                self._username = str(username)
                self._security_id = int(security_id)
            else:
                raise TypeError("OrderCore expects (username, security_id) or (order_id, username, security_id)")

        def order_id(self) -> int:
            return self._order_id

        def username(self) -> str:
            return self._username

        def security_id(self) -> int:
            return self._security_id

    class Order:
        """Pure-Python fallback for C++ Order."""

        def __init__(self, order_core: OrderCore, price: int, quantity: int, is_buy: bool) -> None:
            self._core = order_core
            self._price = int(price)
            self._initial_quantity = int(quantity)
            self._current_quantity = int(quantity)
            self._is_buy = bool(is_buy)

        def price(self) -> int:
            return self._price

        def initial_quantity(self) -> int:
            return self._initial_quantity

        def current_quantity(self) -> int:
            return self._current_quantity

        def is_buy(self) -> bool:
            return self._is_buy

        def order_id(self) -> int:
            return self._core.order_id()

        def username(self) -> str:
            return self._core.username()

        def security_id(self) -> int:
            return self._core.security_id()

    class OrderBookSpread:
        """Pure-Python fallback for C++ OrderBookSpread."""

        def __init__(self, best_bid: Optional[int], best_ask: Optional[int]) -> None:
            self._best_bid = best_bid
            self._best_ask = best_ask

        def spread(self) -> Optional[int]:
            if self._best_bid is not None and self._best_ask is not None:
                return self._best_ask - self._best_bid
            return None

    class OrderBook:
        """Pure-Python price-time-priority limit order book matching C++ OrderBook API."""

        def __init__(self, security: Security) -> None:
            self._security = security
            self._orders: "OrderedDict[int, Order]" = OrderedDict()
            self._orders_matched: int = 0

        def count(self) -> int:
            return len(self._orders)

        def contains_order(self, order_id: int) -> bool:
            return int(order_id) in self._orders

        def get_best_bid_price(self) -> Optional[int]:
            bids = [o.price() for o in self._orders.values() if o.is_buy() and o.current_quantity() > 0]
            return max(bids) if bids else None

        def get_best_ask_price(self) -> Optional[int]:
            asks = [o.price() for o in self._orders.values() if not o.is_buy() and o.current_quantity() > 0]
            return min(asks) if asks else None

        def get_spread(self) -> OrderBookSpread:
            return OrderBookSpread(self.get_best_bid_price(), self.get_best_ask_price())

        def _match(self) -> None:
            while True:
                best_bid = self.get_best_bid_price()
                best_ask = self.get_best_ask_price()
                if best_bid is None or best_ask is None or best_bid < best_ask:
                    break
                bid_order = next(
                    o for o in self._orders.values()
                    if o.is_buy() and o.current_quantity() > 0 and o.price() == best_bid
                )
                ask_order = next(
                    o for o in self._orders.values()
                    if not o.is_buy() and o.current_quantity() > 0 and o.price() == best_ask
                )
                matched = min(bid_order._current_quantity, ask_order._current_quantity)
                bid_order._current_quantity -= matched
                ask_order._current_quantity -= matched
                self._orders_matched += matched
                if bid_order._current_quantity <= 0:
                    self._orders.pop(bid_order.order_id(), None)
                if ask_order._current_quantity <= 0:
                    self._orders.pop(ask_order.order_id(), None)

        def add_order(self, order: Order) -> None:
            if order.current_quantity() <= 0:
                return
            self._orders[order.order_id()] = order
            self._match()

        def amend_order(self, order_id: int, new_order: Order) -> None:
            oid = int(order_id)
            if oid in self._orders:
                self._orders.pop(oid, None)
            self.add_order(new_order)

        def remove_order(self, order_id: int) -> None:
            self._orders.pop(int(order_id), None)

        def place_market_buy_order(self, quantity: int) -> None:
            remaining = int(quantity)
            while remaining > 0:
                best_ask = self.get_best_ask_price()
                if best_ask is None:
                    break
                ask_order = next(
                    o for o in self._orders.values()
                    if not o.is_buy() and o.current_quantity() > 0 and o.price() == best_ask
                )
                fill = min(remaining, ask_order._current_quantity)
                ask_order._current_quantity -= fill
                remaining -= fill
                self._orders_matched += fill
                if ask_order._current_quantity <= 0:
                    self._orders.pop(ask_order.order_id(), None)

        def place_market_sell_order(self, quantity: int) -> None:
            remaining = int(quantity)
            while remaining > 0:
                best_bid = self.get_best_bid_price()
                if best_bid is None:
                    break
                bid_order = next(
                    o for o in self._orders.values()
                    if o.is_buy() and o.current_quantity() > 0 and o.price() == best_bid
                )
                fill = min(remaining, bid_order._current_quantity)
                bid_order._current_quantity -= fill
                remaining -= fill
                self._orders_matched += fill
                if bid_order._current_quantity <= 0:
                    self._orders.pop(bid_order.order_id(), None)

        def get_bid_quantities(self) -> Dict[int, int]:
            out: Dict[int, int] = {}
            for o in self._orders.values():
                if o.is_buy() and o.current_quantity() > 0:
                    out[o.price()] = out.get(o.price(), 0) + o.current_quantity()
            return dict(sorted(out.items()))

        def get_ask_quantities(self) -> Dict[int, int]:
            out: Dict[int, int] = {}
            for o in self._orders.values():
                if not o.is_buy() and o.current_quantity() > 0:
                    out[o.price()] = out.get(o.price(), 0) + o.current_quantity()
            return dict(sorted(out.items()))

        def get_orders_matched(self) -> int:
            return self._orders_matched

    def create_order(username: str, security_id: int, price: int, quantity: int, is_buy: bool) -> Order:
        core = OrderCore(username, security_id)
        return Order(core, price, quantity, is_buy)
