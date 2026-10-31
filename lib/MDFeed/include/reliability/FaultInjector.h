#pragma once

#include "reliability/ReliabilityTypes.h"

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <mutex>
#include <random>
#include <set>
#include <string>
#include <vector>

namespace mdfeed::reliability {

struct FaultConfig {
    double packet_loss_rate{0.0};
    double duplication_rate{0.0};
    double reorder_rate{0.0};
    std::chrono::milliseconds delay{0}; // generic impairment only; not a fairness mechanism
    uint64_t random_seed{0};
    size_t reorder_depth{1};
};

class FaultInjector {
public:
    explicit FaultInjector(FaultConfig config = {});
    [[nodiscard]] bool should_drop();
    [[nodiscard]] bool should_duplicate();
    [[nodiscard]] bool should_reorder();
    // Sequence-keyed decisions are stable irrespective of packet arrival
    // timing. Live subscribers supply one injector per feed (distinct
    // seeds for independent loss; the same seed for correlated loss).
    [[nodiscard]] bool drops(const MarketDataEvent& event) const;
    [[nodiscard]] bool duplicates(const MarketDataEvent& event) const;
    [[nodiscard]] bool reorders(const MarketDataEvent& event) const;
    [[nodiscard]] std::vector<MarketDataEvent> apply(const MarketDataEvent& event);
    [[nodiscard]] const FaultConfig& config() const { return config_; }
private:
    FaultConfig config_;
    std::mt19937_64 random_;
    std::uniform_real_distribution<double> distribution_{0.0, 1.0};
    [[nodiscard]] bool roll(double rate);
    [[nodiscard]] bool sequence_roll(uint64_t sequence, uint64_t stream_tag, double rate) const;
};

// Stateful receive-side impairment stage implementing deterministic per-sequence
// drop, duplication, reorder hold-back (depth K), and delay hold-back.
class ImpairmentStage {
public:
    explicit ImpairmentStage(FaultConfig config = {});

    // Processes an incoming event at `now_ns` and returns any events ready for
    // immediate delivery at `now_ns`.
    [[nodiscard]] std::vector<MarketDataEvent> on_receive(const MarketDataEvent& event, uint64_t now_ns = 0);

    // Releases any delayed events whose release timestamp is <= `now_ns`.
    [[nodiscard]] std::vector<MarketDataEvent> poll_ready(uint64_t now_ns);

    // Releases all remaining reordered and delayed events (for shutdown/drain).
    [[nodiscard]] std::vector<MarketDataEvent> flush();

    [[nodiscard]] uint64_t dropped_count() const;
    [[nodiscard]] uint64_t duplicated_count() const;
    [[nodiscard]] uint64_t reordered_count() const;
    [[nodiscard]] uint64_t delayed_count() const;
    [[nodiscard]] std::vector<uint64_t> dropped_sequences(uint64_t max_sequence = UINT64_MAX) const;
    [[nodiscard]] std::string dropped_sequences_hash(uint64_t max_sequence = UINT64_MAX) const;
    [[nodiscard]] const FaultInjector& injector() const { return injector_; }

private:
    struct HeldReorder {
        MarketDataEvent event;
        size_t remaining_depth{0};
    };
    struct DelayedEvent {
        uint64_t release_ns{0};
        MarketDataEvent event;
    };

    void enqueue_for_delay_locked(MarketDataEvent event, uint64_t now_ns);
    std::vector<MarketDataEvent> collect_ready_locked(uint64_t now_ns);

    FaultConfig config_;
    FaultInjector injector_;
    mutable std::mutex mutex_;
    std::deque<HeldReorder> reorder_buffer_;
    std::deque<DelayedEvent> delay_queue_;
    std::set<uint64_t> dropped_sequences_;
    uint64_t dropped_count_{0};
    uint64_t duplicated_count_{0};
    uint64_t reordered_count_{0};
    uint64_t delayed_count_{0};
};

} // namespace mdfeed::reliability
