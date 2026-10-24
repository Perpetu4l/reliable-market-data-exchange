#include "reliability/DualFeed.h"

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
    // TODO: connect these adapter calls to two independently configured multicast publishers.
    if (feed_a_) feed_a_->publish(event);
    if (feed_b_) feed_b_->publish(event);
}

DualFeedSubscriber::DualFeedSubscriber(EventHandler event_handler, GapHandler gap_handler)
    : event_handler_(std::move(event_handler)), gap_handler_(std::move(gap_handler)) {}

void DualFeedSubscriber::on_packet(const FeedId feed_id, MarketDataEvent packet)
{
    packet.feed_id = feed_id;
    const auto result = sequence_tracker_.on_event(packet);
    if (result.disposition == SequenceDisposition::DUPLICATE) return; // first-copy-wins
    if (result.disposition == SequenceDisposition::ACCEPTED) {
        accept_and_deliver(packet);
        drain_pending_events();
        return;
    }

    // Hold the later event until the missing range is recovered. A second copy
    // on the other feed is suppressed by SequenceTracker.
    pending_events_.emplace(packet.header.sequence_number, std::move(packet));
    if (result.missing_range && gap_handler_) gap_handler_(*result.missing_range);
}

void DualFeedSubscriber::on_heartbeat(const uint64_t last_published_sequence)
{
    const auto result = sequence_tracker_.on_heartbeat(last_published_sequence);
    if (result.disposition == SequenceDisposition::GAP_DETECTED
        && result.missing_range && gap_handler_) {
        gap_handler_(*result.missing_range);
    }
}

void DualFeedSubscriber::on_recovered_event(MarketDataEvent event)
{
    const auto result = sequence_tracker_.on_event(event);
    if (result.disposition != SequenceDisposition::ACCEPTED) return;
    accept_and_deliver(event);
    drain_pending_events();
}

void DualFeedSubscriber::resynchronise_after_snapshot(const uint64_t checkpoint_sequence)
{
    // The caller is responsible for applying and verifying the book snapshot.
    // This class only restores sequence continuity and retains later packets.
    for (auto it = pending_events_.begin(); it != pending_events_.end();) {
        if (it->first <= checkpoint_sequence) it = pending_events_.erase(it);
        else ++it;
    }
    sequence_tracker_.resynchronise(checkpoint_sequence + 1);
    drain_pending_events();
}

void DualFeedSubscriber::accept_and_deliver(const MarketDataEvent& event)
{
    if (event_handler_) event_handler_(event);
}

void DualFeedSubscriber::drain_pending_events()
{
    while (true) {
        const auto next = pending_events_.find(sequence_tracker_.expected_sequence());
        if (next == pending_events_.end()) return;
        const auto event = next->second;
        pending_events_.erase(next);
        const auto result = sequence_tracker_.on_event(event);
        if (result.disposition != SequenceDisposition::ACCEPTED) return;
        accept_and_deliver(event);
    }
}

} // namespace mdfeed::reliability
