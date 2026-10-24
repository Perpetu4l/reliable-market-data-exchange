#include "reliability/BookBuilder.h"

#include <cstring>

namespace mdfeed::reliability {
namespace {
template <typename T>
const T* payload_as(const MarketDataEvent& event)
{
    if (event.payload.size() != sizeof(T)) return nullptr;
    return reinterpret_cast<const T*>(event.payload.data());
}
}

bool BookBuilder::apply(const MarketDataEvent& event)
{
    const auto type = static_cast<MessageType>(event.header.message_type);
    const auto instrument = event.header.instrument_id;
    switch (type) {
    case MessageType::PRICE_LEVEL_UPDATE: {
        const auto* message = payload_as<PriceLevelUpdateMessage>(event);
        if (!message) return false;
        books_[instrument][{static_cast<uint8_t>(message->side), message->price}] = message->quantity;
        break;
    }
    case MessageType::PRICE_LEVEL_DELETE: {
        const auto* message = payload_as<PriceLevelDeleteMessage>(event);
        if (!message) return false;
        books_[instrument].erase({static_cast<uint8_t>(message->side), message->price});
        break;
    }
    case MessageType::BOOK_CLEAR:
        if (!payload_as<BookClearMessage>(event)) return false;
        books_[instrument].clear();
        break;
    case MessageType::TRADE:
        if (!payload_as<TradeMessage>(event)) return false;
        break;
    default:
        return false;
    }
    last_sequence_ = event.header.sequence_number;
    return true;
}

std::vector<SnapshotPriceLevel> BookBuilder::levels(const uint32_t instrument_id) const
{
    std::vector<SnapshotPriceLevel> result;
    if (const auto found = books_.find(instrument_id); found != books_.end()) {
        result.reserve(found->second.size());
        for (const auto& [key, quantity] : found->second) {
            result.push_back({key.second, quantity, static_cast<Side>(key.first)});
        }
    }
    return result;
}

void BookBuilder::clear()
{
    books_.clear();
    last_sequence_ = 0;
}

} // namespace mdfeed::reliability
