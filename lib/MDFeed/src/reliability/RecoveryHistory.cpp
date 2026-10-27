#include "reliability/RecoveryHistory.h"

namespace mdfeed::reliability {

RecoveryHistory::RecoveryHistory(const size_t maximum_events) : maximum_events_(maximum_events) {}

void RecoveryHistory::append(MarketDataEvent event)
{
    if (maximum_events_ == 0) return;
    std::lock_guard lock(mutex_);
    events_.push_back(std::move(event));
    while (events_.size() > maximum_events_) events_.pop_front();
}

bool RecoveryHistory::contains(const MissingRange& range) const
{
    std::lock_guard lock(mutex_);
    if (!range.valid() || events_.empty()) return false;
    return events_.front().header.sequence_number <= range.start_sequence
        && events_.back().header.sequence_number >= range.end_sequence;
}

std::optional<std::vector<MarketDataEvent>> RecoveryHistory::get_range(const MissingRange& range) const
{
    std::lock_guard lock(mutex_);
    if (!range.valid() || events_.empty()
        || events_.front().header.sequence_number > range.start_sequence
        || events_.back().header.sequence_number < range.end_sequence) return std::nullopt;
    std::vector<MarketDataEvent> result;
    for (const auto& event : events_) {
        const auto sequence = event.header.sequence_number;
        if (sequence >= range.start_sequence && sequence <= range.end_sequence) result.push_back(event);
    }
    // A range can be outside the retention window, or internally incomplete.
    if (result.size() != range.end_sequence - range.start_sequence + 1) return std::nullopt;
    return result;
}

size_t RecoveryHistory::size() const
{
    std::lock_guard lock(mutex_);
    return events_.size();
}

std::optional<uint64_t> RecoveryHistory::minimum_sequence() const
{
    std::lock_guard lock(mutex_);
    if (events_.empty()) return std::nullopt;
    return events_.front().header.sequence_number;
}

std::optional<uint64_t> RecoveryHistory::latest_sequence() const
{
    std::lock_guard lock(mutex_);
    if (events_.empty()) return std::nullopt;
    return events_.back().header.sequence_number;
}

} // namespace mdfeed::reliability
