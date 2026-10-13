#pragma once

#include "messages/Messages.h"

namespace mdfeed {
template<typename PublisherT>
class MDAdapter {
public:
    MDAdapter(uint32_t instrument_id, PublisherT& publisher)
        : publisher_(publisher), instrument_id_(instrument_id)
    {
    }

    void notify_price_level_change(const uint64_t price, const uint64_t new_quantity, const uint64_t oldQuantity,
        const bool isBid)
    {
        Side side = isBid ? Side::BUY : Side::SELL;
        if (oldQuantity == 0 && new_quantity > 0) {
            publisher_.publish_price_level_update(instrument_id_, price, new_quantity, side, UpdateAction::NEW);
        }
        else if (oldQuantity > 0 && new_quantity > 0) {
            publisher_.publish_price_level_update(instrument_id_, price, new_quantity, side, UpdateAction::CHANGE);
        }
        else if (oldQuantity > 0 && new_quantity == 0) {
            publisher_.publish_price_level_delete(instrument_id_, price, side);
        }
    }

    void notify_trade(const uint64_t trade_id, const uint64_t price, const uint64_t quantity, const bool buyerAggressed)
    {
        Side aggressor_side = buyerAggressed ? Side::BUY : Side::SELL;
        publisher_.publish_trade(instrument_id_, trade_id, price, quantity, aggressor_side);
    }

    void notify_book_clear(const uint32_t reason_code = 0)
    {
        publisher_.publish_book_clear(instrument_id_, reason_code);
    }

private:
    PublisherT& publisher_;
    uint32_t instrument_id_;
};
}
