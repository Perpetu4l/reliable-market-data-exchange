#pragma once

#include "reliability/ReliabilityTypes.h"

#include <deque>
#include <optional>
#include <vector>

namespace mdfeed::reliability {

class RecoveryHistory {
public:
    explicit RecoveryHistory(size_t maximum_events = 65536);
    void append(MarketDataEvent event);
    [[nodiscard]] std::optional<std::vector<MarketDataEvent>> get_range(const MissingRange& range) const;
    [[nodiscard]] bool contains(const MissingRange& range) const;
    [[nodiscard]] size_t capacity() const { return maximum_events_; }
    [[nodiscard]] size_t size() const { return events_.size(); }

private:
    size_t maximum_events_;
    std::deque<MarketDataEvent> events_;
};

} // namespace mdfeed::reliability
