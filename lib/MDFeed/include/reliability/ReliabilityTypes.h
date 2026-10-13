#pragma once

#include "messages/Messages.h"

#include <cstdint>
#include <string>
#include <vector>

namespace mdfeed::reliability {

// Transport-neutral identity for the two copies of the existing MDFeed message.
enum class FeedId : uint8_t { A = 1, B = 2 };

// Deliberately wraps, rather than replaces, the established binary message header.
// payload is an opaque copy of the existing binary message for future transport work.
struct MarketDataEvent {
    MessageHeader header{};
    FeedId feed_id{FeedId::A};
    std::vector<uint8_t> payload;
};

struct MissingRange {
    uint64_t start_sequence{0};
    uint64_t end_sequence{0};

    [[nodiscard]] bool valid() const { return start_sequence != 0 && start_sequence <= end_sequence; }
};

} // namespace mdfeed::reliability
