#pragma once

#include "reliability/SequenceTracker.h"
#include "reliability/FaultInjector.h"
#include "reliability/Metrics.h"

#include <chrono>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace mdfeed::reliability {

class MarketDataFeed {
public:
    virtual ~MarketDataFeed() = default;
    [[nodiscard]] virtual FeedId id() const = 0;
    virtual void publish(MarketDataEvent event) = 0;
};

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
class FaultInjectingFeed final : public MarketDataFeed {
public:
    FaultInjectingFeed(FeedId id, FaultConfig config, std::function<void(MarketDataEvent)> sink = {});
    [[nodiscard]] FeedId id() const override { return id_; }
    void publish(MarketDataEvent event) override;
    [[nodiscard]] const FeedDeliveryStats& stats() const { return stats_; }
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

struct RedundancyBitsetSummary {
    uint64_t potential_single_feed_gaps{0};
    uint64_t gaps_avoided_by_second_feed{0};
    std::optional<double> redundancy_effectiveness; // nullopt when potential_single_feed_gaps == 0 ("n/a")
};

struct AccountingBitsetSummary {
    uint64_t events_expected{0};
    uint64_t delivered{0};
    uint64_t delivered_via_feed_a{0};
    uint64_t delivered_via_feed_b{0};
    uint64_t delivered_via_recovery{0};
    uint64_t events_covered_by_snapshot{0};
    uint64_t permanently_lost{0};
    bool accounting_valid{true};
};

class DualFeedSubscriber {
public:
    using EventHandler = std::function<void(const MarketDataEvent&)>;
    using GapHandler = std::function<void(MissingRange)>;
    using ClockFn = std::function<uint64_t()>;

    static uint64_t default_clock_ns()
    {
        return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count());
    }

    explicit DualFeedSubscriber(EventHandler event_handler = {}, GapHandler gap_handler = {},
        ClockFn clock_fn = default_clock_ns);

    void on_packet(FeedId feed_id, MarketDataEvent packet);
    void on_heartbeat(uint64_t last_published_sequence);
    // Recovery responses enter through the same ordering path as feed events.
    void on_recovered_event(MarketDataEvent event);
    // Marks a missing sequence range as having been requested over TCP recovery.
    void mark_tcp_requested(MissingRange range);
    // Call after a validated snapshot has been applied to the local book state.
    void resynchronise_after_snapshot(uint64_t checkpoint_sequence);

    [[nodiscard]] const SequenceTracker& sequence_tracker() const { return sequence_tracker_; }
    [[nodiscard]] uint64_t expected_sequence() const;
    [[nodiscard]] uint64_t last_delivered_sequence() const;
    [[nodiscard]] size_t pending_event_count() const;

    // Normative counters and latency/bitset summaries.
    [[nodiscard]] uint64_t gap_detections() const;
    [[nodiscard]] uint64_t events_in_gaps() const;
    [[nodiscard]] uint64_t delivered_via_feed_a() const;
    [[nodiscard]] uint64_t delivered_via_feed_b() const;
    [[nodiscard]] uint64_t delivered_via_recovery() const;
    [[nodiscard]] uint64_t events_covered_by_snapshot() const;

    [[nodiscard]] RedundancyBitsetSummary compute_redundancy(uint64_t max_sequence) const;
    [[nodiscard]] AccountingBitsetSummary compute_accounting(uint64_t expected_sequence_cut) const;
    [[nodiscard]] std::vector<MissingEventSample> latency_samples() const;
    [[nodiscard]] LatencySummary latency_summary(MissingEventClass cls) const;

private:
    void record_gap_detected_locked(MissingRange range);
    void accept_and_deliver(const MarketDataEvent& event);
    void drain_pending_events();
    void ensure_bitset_capacity_locked(uint64_t sequence);

    SequenceTracker sequence_tracker_;
    mutable std::recursive_mutex mutex_;
    EventHandler event_handler_;
    GapHandler gap_handler_;
    ClockFn clock_fn_;
    std::map<uint64_t, MarketDataEvent> pending_events_;
    uint64_t last_heartbeat_sequence_{0};
    uint64_t last_delivered_sequence_{0};

    // Bitsets indexed by sequence number (1-based).
    std::vector<bool> arrived_a_;
    std::vector<bool> arrived_b_;
    std::vector<bool> delivered_a_;
    std::vector<bool> delivered_b_;
    std::vector<bool> delivered_recovery_;
    std::vector<bool> snapshot_covered_;
    std::vector<bool> tcp_requested_;

    uint64_t delivered_via_feed_a_{0};
    uint64_t delivered_via_feed_b_{0};
    uint64_t delivered_via_recovery_{0};
    uint64_t events_covered_by_snapshot_{0};
    uint64_t gap_detections_{0};
    uint64_t events_in_gaps_{0};

    std::unordered_map<uint64_t, uint64_t> gap_detected_ns_;
    std::unordered_set<uint64_t> latency_recorded_sequences_;
    std::vector<MissingEventSample> latency_samples_;
};

} // namespace mdfeed::reliability
