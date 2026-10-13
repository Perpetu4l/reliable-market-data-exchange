#include "publisher/MarketDataPublisher.h"
#include <utility>

namespace mdfeed {
MarketDataPublisher::MarketDataPublisher(PublisherConfig config)
    : config_(std::move(config)), ring_buffer_(std::make_shared<MDRingBuffer>(config_.ring_buffer_size))
{
}

MarketDataPublisher::~MarketDataPublisher() = default;

bool MarketDataPublisher::publish_price_level_update(const uint32_t instrument_id, const uint64_t price,
    const uint64_t quantity, const Side side, const UpdateAction action)
{
    PriceLevelUpdateMessage msg{};
    message_utils::init_header(msg, MessageType::PRICE_LEVEL_UPDATE, sequence_number_++, instrument_id);
    msg.price = price;
    msg.quantity = quantity;
    msg.side = side;
    msg.action = action;
    std::memset(msg.reserved, 0, sizeof(msg.reserved));
    return enqueue_message(msg);
}

bool MarketDataPublisher::publish_price_level_delete(const uint32_t instrument_id, const uint64_t price,
    const Side side)
{
    PriceLevelDeleteMessage msg{};
    message_utils::init_header(msg, MessageType::PRICE_LEVEL_DELETE, sequence_number_++, instrument_id);
    msg.price = price;
    msg.side = side;
    std::memset(msg.reserved, 0, sizeof(msg.reserved));
    return enqueue_message(msg);
}

bool MarketDataPublisher::publish_trade(const uint32_t instrument_id, const uint64_t trade_id, const uint64_t price,
    const uint64_t quantity, const Side aggressor_side)
{
    TradeMessage msg{};
    message_utils::init_header(msg, MessageType::TRADE, sequence_number_++, instrument_id);
    msg.trade_id = trade_id;
    msg.price = price;
    msg.quantity = quantity;
    msg.aggressor_side = aggressor_side;
    std::memset(msg.reserved, 0, sizeof(msg.reserved));
    return enqueue_message(msg);
}

bool MarketDataPublisher::publish_book_clear(uint32_t instrument_id, uint32_t reason_code)
{
    BookClearMessage msg{};
    message_utils::init_header(msg, MessageType::BOOK_CLEAR, sequence_number_++, instrument_id);
    msg.reason_code = reason_code;
    std::memset(msg.reserved, 0, sizeof(msg.reserved));

    return enqueue_message(msg);
}

std::shared_ptr<MDRingBuffer> MarketDataPublisher::get_ring_buffer() const
{
    return ring_buffer_;
}
}
