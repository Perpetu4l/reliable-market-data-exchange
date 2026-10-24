#pragma once

#include "reliability/ReliabilityTypes.h"

#include <cstdint>
#include <optional>
#include <set>

namespace mdfeed::reliability {

enum class SequenceDisposition { ACCEPTED, DUPLICATE, GAP_DETECTED };

struct SequenceObservation {
    SequenceDisposition disposition{SequenceDisposition::ACCEPTED};
    std::optional<MissingRange> missing_range;
};

// Tracks the next contiguous sequence. Events beyond a gap are remembered so
// their second feed copy is recognised as a duplicate while recovery is pending.
class SequenceTracker {
public:
    [[nodiscard]] SequenceObservation on_event(const MarketDataEvent& event);
    // HEARTBEAT carries the last sequence published, but consumes no sequence
    // itself. It exposes missing head/tail ranges when no later data event is
    // available to reveal them.
    [[nodiscard]] SequenceObservation on_heartbeat(uint64_t last_published_sequence);
    [[nodiscard]] bool has_gap() const { return last_gap_.has_value(); }
    [[nodiscard]] std::optional<MissingRange> get_missing_range() const { return last_gap_; }
    [[nodiscard]] uint64_t expected_sequence() const { return expected_sequence_; }
    [[nodiscard]] uint64_t duplicate_count() const { return duplicate_count_; }
    // Market-data sequences are global and begin at one. Starting from the
    // first datagram silently loses a dropped head-of-stream event.
    void reset(uint64_t next_expected = 1);
    void resynchronise(uint64_t next_expected);

private:
    uint64_t expected_sequence_{1};
    uint64_t duplicate_count_{0};
    std::optional<MissingRange> last_gap_;
    std::set<uint64_t> out_of_order_sequences_;
};

} // namespace mdfeed::reliability
