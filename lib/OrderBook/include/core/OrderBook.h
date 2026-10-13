#pragma once

#include <boost/optional.hpp>
#include <list>
#include <unordered_map>
#include <map>

#include "orders/Order.h"
#include "entries/OrderBookEntry.h"
#include "publisher/MarketDataPublisher.h"
#include "securities/Security.h"
#include "publisher/MDAdapter.h"

class OrderBookSpread {
public:
    OrderBookSpread(const std::optional<long> bid, const std::optional<long> ask)
        : bid_(bid), ask_(ask)
    {
    }

    [[nodiscard]] std::optional<long> Spread() const
    {
        if (bid_.has_value() && ask_.has_value()) {
            return ask_.value() - bid_.value();
        }
        return {};
    }

private:
    const std::optional<long> bid_;
    const std::optional<long> ask_;
};

template<typename T>
concept MDPublisher = std::is_base_of_v<mdfeed::MarketDataPublisherBase<T>, T>;

template<MDPublisher MarketDataPublisher>
class OrderBook {
private:
    Security instrument_;
    long matchedQuantity_;
    mdfeed::MDAdapter<MarketDataPublisher> md_adapter_;

    // sorted maps
    // limits could also be implemented as an array with pointers to the best bid and ask limit.
    // or a buy and a sell limit array for fast lookup, as most used limits will be near the centre (price wise) (so near the edge of a tree)
    std::map<long, std::shared_ptr<Limit>, std::less<> > askLimits_;
    std::map<long, std::shared_ptr<Limit>, std::greater<> > bidLimits_;

    // dictionary
    // could switch this for an array, with order_id as the index (as we can re start order ids for each day of trading).
    // This would also allow us to pre-allocate the storage fif we have enough memory to further improve performance. (std::vector)
    std::unordered_map<long, std::shared_ptr<OrderBookEntry> > orders_;
    // could add a map price -> limit to enable efficient finding of orders @ price.

    template<typename Sort>
    void AddOrder(Order order, long price, std::map<long, std::shared_ptr<Limit>, Sort>& limitLevels,
        std::unordered_map<long, std::shared_ptr<OrderBookEntry> >& internalOrderBook);

    bool RemoveOrder(long orderId, const std::shared_ptr<OrderBookEntry>& obe);

public:
    OrderBook(Security instrument, mdfeed::MDAdapter<MarketDataPublisher> mdAdapter);

    [[nodiscard]] size_t Count() const;

    [[nodiscard]] bool ContainsOrder(long orderId) const;

    [[nodiscard]] OrderBookSpread GetSpread() const;

    std::optional<std::shared_ptr<Limit> > GetBestBidLimit();

    std::optional<std::shared_ptr<Limit> > GetBestAskLimit();

    [[nodiscard]] std::optional<long> GetBestBidPrice() const;

    [[nodiscard]] std::optional<long> GetBestAskPrice() const;

    void PlaceMarketBuyOrder(uint32_t quantity);

    void PlaceMarketSellOrder(uint32_t quantity);

    void AddOrder(const Order& order);

    void AmendOrder(long orderId, const Order& order);

    void RemoveOrder(long orderId);

    [[nodiscard]] std::list<OrderBookEntry> GetAskOrders() const;

    [[nodiscard]] std::list<OrderBookEntry> GetBidOrders() const;

    [[nodiscard]] std::map<long, uint32_t> GetBidQuantities() const;

    [[nodiscard]] std::map<long, uint32_t> GetAskQuantities() const;

    [[nodiscard]] std::list<OrderStruct> GetOrders() const;

    [[nodiscard]] long GetOrdersMatched() const
    {
        return matchedQuantity_;
    }

    template<typename LimitMap>
    uint32_t TryMatch(Order& incomingOrder, long price, LimitMap& opposingLimits);
};
