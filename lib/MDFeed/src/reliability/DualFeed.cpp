#include "reliability/DualFeed.h"

#include <algorithm>

namespace mdfeed::reliability {

FeedA::FeedA(std::function<void(MarketDataEvent)> sink) : sink_(std::move(sink)) {}
void FeedA::publish(MarketDataEvent event) { event.feed_id = id(); if (sink_) sink_(std::move(event)); }
FeedB::FeedB(std::function<void(MarketDataEvent)> sink) : sink_(std::move(sink)) {}
void FeedB::publish(MarketDataEvent event) { event.feed_id = id(); if (sink_) sink_(std::move(event)); }

FaultInjectingFeed::FaultInjectingFeed(const FeedId id, FaultConfig config,
    std::function<void(MarketDataEvent)> sink)
    : id_(id), injector_(std::move(config)), sink_(std::move(sink)) {}

void FaultInjectingFeed::publish(MarketDataEvent event)
{
    ++stats_.publish_attempts;
    event.feed_id = id_;
    const auto delivered = injector_.apply(event);
    if (delivered.empty()) {
        last_publish_was_dropped_ = true;
        ++stats_.dropped_packets;
        return;
    }
    last_publish_was_dropped_ = false;
    for (size_t index = 0; index < delivered.size(); ++index) {
        if (index == 0) ++stats_.delivered_packets;
        else ++stats_.duplicate_packets;
        if (sink_) sink_(delivered[index]);
    }
}

DualFeedPublisher::DualFeedPublisher(std::shared_ptr<MarketDataFeed> feed_a, std::shared_ptr<MarketDataFeed> feed_b)
    : feed_a_(std::move(feed_a)), feed_b_(std::move(feed_b)) {}

void DualFeedPublisher::publish(const MarketDataEvent& event) const
{
    if (feed_a_) feed_a_->publish(event);
    if (feed_b_) feed_b_->publish(event);
}

DualFeedSubscriber::DualFeedSubscriber(EventHandler event_handler, GapHandler gap_handler, ClockFn clock_fn)
    : event_handler_(std::move(event_handler)),
      gap_handler_(std::move(gap_handler)),
      clock_fn_(clock_fn ? std::move(clock_fn) : default_clock_ns)
{
}

void DualFeedSubscriber::ensure_bitset_capacity_locked(const uint64_t sequence)
{
    if (sequence < arrived_a_.size()) return;
    const size_t new_size = std::max<size_t>(static_cast<size_t>(sequence) + 1, arrived_a_.size() * 2 + 64);
    arrived_a_.resize(new_size, false);
    arrived_b_.resize(new_size, false);
    delivered_a_.resize(new_size, false);
    delivered_b_.resize(new_size, false);
    delivered_recovery_.resize(new_size, false);
    snapshot_covered_.resize(new_size, false);
    tcp_requested_.resize(new_size, false);
}

void DualFeedSubscriber::record_gap_detected_locked(const MissingRange range)
{
    if (!range.valid()) return;
    ++gap_detections_;
    const uint64_t now_ns = clock_fn_();
    ensure_bitset_capacity_locked(range.end_sequence);
    for (uint64_t seq = range.start_sequence; seq <= range.end_sequence; ++seq) {
        if (gap_detected_ns_.emplace(seq, now_ns).second) {
            ++events_in_gaps_;
        }
    }
}

uint64_t DualFeedSubscriber::expected_sequence() const
{
    std::lock_guard lock(mutex_);
    return sequence_tracker_.expected_sequence();
}

uint64_t DualFeedSubscriber::last_delivered_sequence() const
{
    std::lock_guard lock(mutex_);
    return last_delivered_sequence_;
}

void DualFeedSubscriber::on_packet(const FeedId feed_id, MarketDataEvent packet)
{
    std::lock_guard lock(mutex_);
    packet.feed_id = feed_id;
    packet.from_recovery = false;
    const uint64_t seq = packet.header.sequence_number;
    if (seq > 0) {
        ensure_bitset_capacity_locked(seq);
        if (feed_id == FeedId::A) arrived_a_[seq] = true;
        else if (feed_id == FeedId::B) arrived_b_[seq] = true;
    }

    // A buffered later sequence has already caused its one gap report. A
    // second feed copy must be a duplicate, not a fresh gap detection.
    if (seq >= sequence_tracker_.expected_sequence()
        && pending_events_.contains(seq)) {
        sequence_tracker_.record_duplicate();
        return;
    }
    const auto result = sequence_tracker_.on_event(packet);
    if (result.disposition == SequenceDisposition::DUPLICATE) return; // first-copy-wins
    if (result.disposition == SequenceDisposition::ACCEPTED) {
        accept_and_deliver(packet);
        drain_pending_events();
        return;
    }

    // Hold the later event until the missing range is recovered. A second copy
    // on the other feed is suppressed by SequenceTracker.
    pending_events_.emplace(seq, std::move(packet));
    if (result.missing_range) {
        record_gap_detected_locked(*result.missing_range);
        if (gap_handler_) gap_handler_(*result.missing_range);
    }
}

void DualFeedSubscriber::on_heartbeat(const uint64_t last_published_sequence)
{
    std::lock_guard lock(mutex_);
    last_heartbeat_sequence_ = std::max(last_heartbeat_sequence_, last_published_sequence);
    const uint64_t before_gaps = sequence_tracker_.gap_count();
    const auto result = sequence_tracker_.on_heartbeat(last_published_sequence);
    if (result.disposition == SequenceDisposition::GAP_DETECTED && result.missing_range) {
        if (sequence_tracker_.gap_count() > before_gaps
            || !gap_detected_ns_.contains(result.missing_range->start_sequence)
            || !gap_detected_ns_.contains(result.missing_range->end_sequence)) {
            record_gap_detected_locked(*result.missing_range);
        }
        if (gap_handler_) gap_handler_(*result.missing_range);
    }
    // Also check if there is a missing tail beyond the highest buffered out-of-order packet.
    if (const auto highest_ooo = sequence_tracker_.highest_out_of_order_sequence();
        highest_ooo && last_published_sequence > *highest_ooo) {
        const MissingRange tail_gap{*highest_ooo + 1, last_published_sequence};
        if (tail_gap.valid()) {
            bool any_new = false;
            for (uint64_t seq = tail_gap.start_sequence; seq <= tail_gap.end_sequence; ++seq) {
                if (!gap_detected_ns_.contains(seq)) {
                    any_new = true;
                    break;
                }
            }
            if (any_new) {
                record_gap_detected_locked(tail_gap);
            }
            if (gap_handler_) gap_handler_(tail_gap);
        }
    }
}

void DualFeedSubscriber::mark_tcp_requested(const MissingRange range)
{
    if (!range.valid()) return;
    std::lock_guard lock(mutex_);
    ensure_bitset_capacity_locked(range.end_sequence);
    for (uint64_t seq = range.start_sequence; seq <= range.end_sequence; ++seq) {
        tcp_requested_[seq] = true;
    }
}

void DualFeedSubscriber::on_recovered_event(MarketDataEvent event)
{
    std::lock_guard lock(mutex_);
    event.from_recovery = true;
    const uint64_t seq = event.header.sequence_number;
    if (seq > 0) {
        ensure_bitset_capacity_locked(seq);
        tcp_requested_[seq] = true;
    }
    if (seq > sequence_tracker_.expected_sequence()) {
        // TCP ranges may be delivered by concurrent requests in reverse order.
        // Buffer them without telling SequenceTracker about a new UDP gap.
        const auto [_, inserted] = pending_events_.emplace(seq, std::move(event));
        if (!inserted) sequence_tracker_.record_duplicate();
        return;
    }
    const auto result = sequence_tracker_.on_event(event);
    if (result.disposition != SequenceDisposition::ACCEPTED) return;
    accept_and_deliver(event);
    drain_pending_events();
}

size_t DualFeedSubscriber::pending_event_count() const
{
    std::lock_guard lock(mutex_);
    return pending_events_.size();
}

void DualFeedSubscriber::resynchronise_after_snapshot(const uint64_t checkpoint_sequence)
{
    std::lock_guard lock(mutex_);
    ensure_bitset_capacity_locked(checkpoint_sequence);
    const uint64_t now_ns = clock_fn_();
    const uint64_t start_seq = sequence_tracker_.expected_sequence();
    for (uint64_t seq = 1; seq <= checkpoint_sequence; ++seq) {
        const bool already_delivered = delivered_a_[seq] || delivered_b_[seq] || delivered_recovery_[seq];
        if (!already_delivered && seq >= start_seq && !snapshot_covered_[seq]) {
            snapshot_covered_[seq] = true;
            ++events_covered_by_snapshot_;
        }
        if (!already_delivered && snapshot_covered_[seq]) {
            if (const auto it = gap_detected_ns_.find(seq);
                it != gap_detected_ns_.end() && latency_recorded_sequences_.insert(seq).second) {
                const uint64_t lat = now_ns >= it->second ? (now_ns - it->second) : 0;
                latency_samples_.push_back({seq, MissingEventClass::SNAPSHOT, it->second, now_ns, lat});
            }
        }
    }
    for (auto it = pending_events_.begin(); it != pending_events_.end();) {
        if (it->first <= checkpoint_sequence) it = pending_events_.erase(it);
        else ++it;
    }
    last_delivered_sequence_ = std::max(last_delivered_sequence_, checkpoint_sequence);
    sequence_tracker_.resynchronise(checkpoint_sequence + 1);
    drain_pending_events();
}

void DualFeedSubscriber::accept_and_deliver(const MarketDataEvent& event)
{
    const uint64_t seq = event.header.sequence_number;
    ensure_bitset_capacity_locked(seq);
    if (!delivered_a_[seq] && !delivered_b_[seq] && !delivered_recovery_[seq] && !snapshot_covered_[seq]) {
        if (event.from_recovery) {
            delivered_recovery_[seq] = true;
            ++delivered_via_recovery_;
        } else if (event.feed_id == FeedId::A) {
            delivered_a_[seq] = true;
            ++delivered_via_feed_a_;
        } else {
            delivered_b_[seq] = true;
            ++delivered_via_feed_b_;
        }
    }
    last_delivered_sequence_ = std::max(last_delivered_sequence_, seq);

    if (const auto it = gap_detected_ns_.find(seq);
        it != gap_detected_ns_.end() && latency_recorded_sequences_.insert(seq).second) {
        const uint64_t now_ns = clock_fn_();
        const uint64_t lat = now_ns >= it->second ? (now_ns - it->second) : 0;
        const MissingEventClass cls = (event.from_recovery || tcp_requested_[seq])
            ? MissingEventClass::TCP_RECOVERY
            : MissingEventClass::OTHER_FEED;
        latency_samples_.push_back({seq, cls, it->second, now_ns, lat});
    }

    if (event_handler_) event_handler_(event);
}

void DualFeedSubscriber::drain_pending_events()
{
    while (true) {
        const auto next = pending_events_.find(sequence_tracker_.expected_sequence());
        if (next == pending_events_.end()) break;
        const auto event = next->second;
        pending_events_.erase(next);
        const auto result = sequence_tracker_.on_event(event);
        if (result.disposition != SequenceDisposition::ACCEPTED) break;
        accept_and_deliver(event);
    }
    if (last_heartbeat_sequence_ >= sequence_tracker_.expected_sequence() && pending_events_.empty()) {
        const MissingRange tail{sequence_tracker_.expected_sequence(), last_heartbeat_sequence_};
        if (tail.valid()) {
            const auto obs = sequence_tracker_.on_heartbeat(last_heartbeat_sequence_);
            if (obs.disposition == SequenceDisposition::GAP_DETECTED && obs.missing_range) {
                bool any_new = false;
                for (uint64_t s = obs.missing_range->start_sequence; s <= obs.missing_range->end_sequence; ++s) {
                    if (!gap_detected_ns_.contains(s)) { any_new = true; break; }
                }
                if (any_new) record_gap_detected_locked(*obs.missing_range);
                if (gap_handler_) gap_handler_(*obs.missing_range);
            }
        }
    }
}

uint64_t DualFeedSubscriber::gap_detections() const
{
    std::lock_guard lock(mutex_);
    return gap_detections_;
}

uint64_t DualFeedSubscriber::events_in_gaps() const
{
    std::lock_guard lock(mutex_);
    return events_in_gaps_;
}

uint64_t DualFeedSubscriber::delivered_via_feed_a() const
{
    std::lock_guard lock(mutex_);
    return delivered_via_feed_a_;
}

uint64_t DualFeedSubscriber::delivered_via_feed_b() const
{
    std::lock_guard lock(mutex_);
    return delivered_via_feed_b_;
}

uint64_t DualFeedSubscriber::delivered_via_recovery() const
{
    std::lock_guard lock(mutex_);
    return delivered_via_recovery_;
}

uint64_t DualFeedSubscriber::events_covered_by_snapshot() const
{
    std::lock_guard lock(mutex_);
    return events_covered_by_snapshot_;
}

RedundancyBitsetSummary DualFeedSubscriber::compute_redundancy(const uint64_t max_sequence) const
{
    std::lock_guard lock(mutex_);
    RedundancyBitsetSummary summary{};
    for (uint64_t seq = 1; seq <= max_sequence; ++seq) {
        const bool a_arrived = seq < arrived_a_.size() && arrived_a_[seq];
        if (!a_arrived) {
            ++summary.potential_single_feed_gaps;
            const bool from_b_without_recovery = seq < delivered_b_.size() && delivered_b_[seq]
                && !(seq < delivered_recovery_.size() && delivered_recovery_[seq])
                && !(seq < snapshot_covered_.size() && snapshot_covered_[seq])
                && !(seq < tcp_requested_.size() && tcp_requested_[seq]);
            if (from_b_without_recovery) {
                ++summary.gaps_avoided_by_second_feed;
            }
        }
    }
    if (summary.potential_single_feed_gaps > 0) {
        summary.redundancy_effectiveness = static_cast<double>(summary.gaps_avoided_by_second_feed)
            / static_cast<double>(summary.potential_single_feed_gaps);
    }
    return summary;
}

AccountingBitsetSummary DualFeedSubscriber::compute_accounting(const uint64_t expected_sequence_cut) const
{
    std::lock_guard lock(mutex_);
    AccountingBitsetSummary summary{};
    summary.events_expected = expected_sequence_cut;
    for (uint64_t seq = 1; seq <= expected_sequence_cut; ++seq) {
        const bool via_a = seq < delivered_a_.size() && delivered_a_[seq];
        const bool via_b = seq < delivered_b_.size() && delivered_b_[seq];
        const bool via_rec = seq < delivered_recovery_.size() && delivered_recovery_[seq];
        const bool via_snap = seq < snapshot_covered_.size() && snapshot_covered_[seq];
        const int individual_count = (via_a ? 1 : 0) + (via_b ? 1 : 0) + (via_rec ? 1 : 0);
        if (individual_count > 1 || (individual_count > 0 && via_snap)) {
            summary.accounting_valid = false;
        }
        if (via_a) ++summary.delivered_via_feed_a;
        else if (via_b) ++summary.delivered_via_feed_b;
        else if (via_rec) ++summary.delivered_via_recovery;
        else if (via_snap) ++summary.events_covered_by_snapshot;
        else ++summary.permanently_lost;
    }
    summary.delivered = summary.delivered_via_feed_a + summary.delivered_via_feed_b + summary.delivered_via_recovery;
    const uint64_t total_delivered_any_seq = delivered_via_feed_a_ + delivered_via_feed_b_ + delivered_via_recovery_;
    if (total_delivered_any_seq > expected_sequence_cut) {
        summary.accounting_valid = false;
    }
    if (summary.delivered + summary.events_covered_by_snapshot + summary.permanently_lost != summary.events_expected) {
        summary.accounting_valid = false;
    }
    return summary;
}

std::vector<MissingEventSample> DualFeedSubscriber::latency_samples() const
{
    std::lock_guard lock(mutex_);
    return latency_samples_;
}

LatencySummary DualFeedSubscriber::latency_summary(const MissingEventClass cls) const
{
    std::lock_guard lock(mutex_);
    std::vector<uint64_t> values;
    for (const auto& sample : latency_samples_) {
        if (sample.event_class == cls) values.push_back(sample.latency_ns);
    }
    return compute_latency_summary(std::move(values));
}

} // namespace mdfeed::reliability
