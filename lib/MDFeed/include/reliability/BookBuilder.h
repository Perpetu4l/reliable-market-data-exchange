#pragma once

#include "reliability/ReliabilityTypes.h"
#include "reliability/Snapshot.h"

#include <cstdint>
#include <map>
#include <mutex>
#include <string>
#include <vector>

namespace mdfeed::reliability {

// Rebuilds price-level state solely from published market-data messages.
// MDAdapter emits NEW/CHANGE with the *absolute* aggregated level quantity,
// while DELETE removes the level; TRADE does not alter the level map because
// the accompanying price-level change already represents that state change.
class BookBuilder {
public:
    [[nodiscard]] bool apply(const MarketDataEvent& event);
    [[nodiscard]] std::vector<SnapshotPriceLevel> levels(uint32_t instrument_id) const;
    [[nodiscard]] std::vector<SnapshotPriceLevel> all_levels() const;
    void apply_snapshot(const OrderBookSnapshot& snapshot);
    [[nodiscard]] uint64_t last_sequence() const;
    // Canonical SHA-256 digest of all levels. This is a state comparison aid,
    // not an authenticated signature.
    [[nodiscard]] std::string digest() const;
    void clear();

private:
    using PriceLevels = std::map<std::pair<uint8_t, uint64_t>, uint64_t>;
    std::map<uint32_t, PriceLevels> books_;
    uint64_t last_sequence_{0};
    mutable std::mutex mutex_;
};

} // namespace mdfeed::reliability
