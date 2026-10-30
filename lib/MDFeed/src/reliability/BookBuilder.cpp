#include "reliability/BookBuilder.h"
#include "reliability/Sha256.h"

#include <cstring>

namespace mdfeed::reliability {
namespace {
void append_u32(std::string& output, const uint32_t value)
{
    for (int shift = 24; shift >= 0; shift -= 8) output.push_back(static_cast<char>(value >> shift));
}
void append_u64(std::string& output, const uint64_t value)
{
    for (int shift = 56; shift >= 0; shift -= 8) output.push_back(static_cast<char>(value >> shift));
}
template <typename T>
const T* payload_as(const MarketDataEvent& event)
{
    if (event.payload.size() != sizeof(T)) return nullptr;
    return reinterpret_cast<const T*>(event.payload.data());
}
}

bool BookBuilder::apply(const MarketDataEvent& event)
{
    std::lock_guard lock(mutex_);
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
    std::lock_guard lock(mutex_);
    std::vector<SnapshotPriceLevel> result;
    if (const auto found = books_.find(instrument_id); found != books_.end()) {
        result.reserve(found->second.size());
        for (const auto& [key, quantity] : found->second) {
            result.push_back({key.second, quantity, static_cast<Side>(key.first), instrument_id});
        }
    }
    return result;
}

std::vector<SnapshotPriceLevel> BookBuilder::all_levels() const
{
    std::lock_guard lock(mutex_);
    std::vector<SnapshotPriceLevel> result;
    for (const auto& [instrument, levels] : books_) {
        for (const auto& [key, quantity] : levels) {
            result.push_back({key.second, quantity, static_cast<Side>(key.first), instrument});
        }
    }
    return result;
}

void BookBuilder::apply_snapshot(const OrderBookSnapshot& snapshot)
{
    std::lock_guard lock(mutex_);
    books_.clear();
    for (const auto& level : snapshot.book_state) {
        const auto instrument = level.instrument_id == 0 ? snapshot.instrument_id : level.instrument_id;
        if (instrument == 0) continue;
        books_[instrument][{static_cast<uint8_t>(level.side), level.price}] = level.quantity;
    }
    last_sequence_ = snapshot.sequence_number;
}

uint64_t BookBuilder::last_sequence() const
{
    std::lock_guard lock(mutex_);
    return last_sequence_;
}

std::string BookBuilder::digest() const
{
    std::lock_guard lock(mutex_);
    std::string material;
    // std::map supplies the required instrument, side (BUY then SELL), and
    // price order independent of insertion order.
    for (const auto& [instrument, levels] : books_) {
        for (const auto& [key, quantity] : levels) {
            append_u32(material, instrument);
            material.push_back(static_cast<char>(key.first));
            append_u64(material, key.second);
            append_u64(material, quantity);
        }
    }
    return Sha256::hex_digest(material);
}

void BookBuilder::clear()
{
    std::lock_guard lock(mutex_);
    books_.clear();
    last_sequence_ = 0;
}

} // namespace mdfeed::reliability
