#include "reliability/SequenceTracker.h"

namespace mdfeed::reliability {

SequenceObservation SequenceTracker::on_event(const MarketDataEvent& event)
{
    const auto sequence = event.header.sequence_number;
    if (expected_sequence_ == 0) {
        expected_sequence_ = sequence + 1;
        return {};
    }
    if (sequence < expected_sequence_) {
        ++duplicate_count_;
        return {SequenceDisposition::DUPLICATE, std::nullopt};
    }
    if (sequence > expected_sequence_) {
        const auto [_, inserted] = out_of_order_sequences_.insert(sequence);
        if (!inserted) {
            ++duplicate_count_;
            return {SequenceDisposition::DUPLICATE, std::nullopt};
        }
        last_gap_ = MissingRange{expected_sequence_, sequence - 1};
        return {SequenceDisposition::GAP_DETECTED, last_gap_};
    }
    out_of_order_sequences_.erase(sequence);
    expected_sequence_ = sequence + 1;
    return {};
}

void SequenceTracker::reset(const uint64_t next_expected)
{
    expected_sequence_ = next_expected;
    duplicate_count_ = 0;
    last_gap_.reset();
    out_of_order_sequences_.clear();
}

void SequenceTracker::resynchronise(const uint64_t next_expected)
{
    expected_sequence_ = next_expected;
    last_gap_.reset();
    out_of_order_sequences_.clear();
}

} // namespace mdfeed::reliability
