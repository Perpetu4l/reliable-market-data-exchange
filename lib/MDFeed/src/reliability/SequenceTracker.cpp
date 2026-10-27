#include "reliability/SequenceTracker.h"

namespace mdfeed::reliability {

SequenceObservation SequenceTracker::on_event(const MarketDataEvent& event)
{
    const auto sequence = event.header.sequence_number;
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
        ++gap_count_;
        return {SequenceDisposition::GAP_DETECTED, last_gap_};
    }
    out_of_order_sequences_.erase(sequence);
    expected_sequence_ = sequence + 1;
    if (last_gap_ && expected_sequence_ > last_gap_->end_sequence) last_gap_.reset();
    return {};
}

SequenceObservation SequenceTracker::on_heartbeat(const uint64_t last_published_sequence)
{
    if (last_published_sequence < expected_sequence_) return {};

    // If a later packet is already buffered, only the range before that packet
    // is missing. Otherwise heartbeat establishes that the current tail is
    // missing through the publisher's reported final sequence.
    const auto later = out_of_order_sequences_.lower_bound(expected_sequence_);
    const auto end = later == out_of_order_sequences_.end()
        ? last_published_sequence
        : *later - 1;
    if (end < expected_sequence_) return {};
    const MissingRange gap{expected_sequence_, end};
    if (!last_gap_ || last_gap_->start_sequence != gap.start_sequence
        || last_gap_->end_sequence != gap.end_sequence) {
        ++gap_count_;
    }
    last_gap_ = gap;
    return {SequenceDisposition::GAP_DETECTED, last_gap_};
}

void SequenceTracker::reset(const uint64_t next_expected)
{
    expected_sequence_ = next_expected;
    duplicate_count_ = 0;
    gap_count_ = 0;
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
