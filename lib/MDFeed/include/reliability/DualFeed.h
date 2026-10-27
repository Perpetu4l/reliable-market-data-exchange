#pragma once

#include "reliability/SequenceTracker.h"
#include "reliability/FaultInjector.h"

#include <functional>
#include <map>
#include <memory>
#include <mutex>

namespace mdfeed::reliability {

class MarketDataFeed {
public:
    virtual ~MarketDataFeed() = default;
    [[nodiscard]] virtual FeedId id() const = 0;
    virtual void publish(MarketDataEvent event) = 0;
};

// Adapter targets for the existing multicast publisher; socket work is intentionally TODO.
class FeedA final : public MarketDataFeed {
public:
    explicit FeedA(std::function<void(MarketDataEvent)> sink = {});
    [[nodiscard]] FeedId id() const override { return FeedId::A; }
    void publish(MarketDataEvent event) override;
private:
    std::function<void(MarketDataEvent)> sink_;
};

class FeedB final : public MarketDataFeed {
public:
    explicit FeedB(std::function<void(MarketDataEvent)> sink = {});
    [[nodiscard]] FeedId id() const override { return FeedId::B; }
    void publish(MarketDataEvent event) override;
private:
    std::function<void(MarketDataEvent)> sink_;
};

struct FeedDeliveryStats {
    uint64_t publish_attempts{0};
    uint64_t dropped_packets{0};
    uint64_t delivered_packets{0};
    uint64_t duplicate_packets{0};
};

// In-process faultable feed for demonstrations and controlled experiments.
// It deliberately does not simulate sockets, delay queues, or packet reordering.
class FaultInjectingFeed final : public MarketDataFeed {
public:
    FaultInjectingFeed(FeedId id, FaultConfig config, std::function<void(MarketDataEvent)> sink = {});
    [[nodiscard]] FeedId id() const override { return id_; }
    void publish(MarketDataEvent event) override;
    [[nodiscard]] const FeedDeliveryStats& stats() const { return stats_; }
    // Result of the most recent publish attempt. This makes experiment metrics
    // reflect the same sequence event, rather than comparing aggregate counts.
    [[nodiscard]] bool last_publish_was_dropped() const { return last_publish_was_dropped_; }
private:
    FeedId id_;
    FaultInjector injector_;
    std::function<void(MarketDataEvent)> sink_;
    FeedDeliveryStats stats_;
    bool last_publish_was_dropped_{false};
};

class DualFeedPublisher {
public:
    DualFeedPublisher(std::shared_ptr<MarketDataFeed> feed_a, std::shared_ptr<MarketDataFeed> feed_b);
    void publish(const MarketDataEvent& event) const;
private:
    std::shared_ptr<MarketDataFeed> feed_a_;
    std::shared_ptr<MarketDataFeed> feed_b_;
};

class DualFeedSubscriber {
public:
    using EventHandler = std::function<void(const MarketDataEvent&)>;
    using GapHandler = std::function<void(MissingRange)>;

    explicit DualFeedSubscriber(EventHandler event_handler = {}, GapHandler gap_handler = {});
    void on_packet(FeedId feed_id, MarketDataEvent packet);
    void on_heartbeat(uint64_t last_published_sequence);
    // Recovery responses enter through the same ordering path as feed events.
    void on_recovered_event(MarketDataEvent event);
    // Call after a validated snapshot has been applied to the local book state.
    void resynchronise_after_snapshot(uint64_t checkpoint_sequence);
    [[nodiscard]] const SequenceTracker& sequence_tracker() const { return sequence_tracker_; }
    [[nodiscard]] uint64_t expected_sequence() const;
    [[nodiscard]] size_t pending_event_count() const;

private:
    void accept_and_deliver(const MarketDataEvent& event);
    void drain_pending_events();

    SequenceTracker sequence_tracker_;
    // Feed A and Feed B callbacks may run on separate UDP receiver threads.
    // A recursive mutex also permits the synchronous recovery callback to
    // inject recovered events through the normal ordering path.
    mutable std::recursive_mutex mutex_;
    EventHandler event_handler_;
    GapHandler gap_handler_;
    std::map<uint64_t, MarketDataEvent> pending_events_;
};

} // namespace mdfeed::reliability
