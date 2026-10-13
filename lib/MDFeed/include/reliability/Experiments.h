#pragma once

#include "reliability/FaultInjector.h"
#include "reliability/Snapshot.h"

#include <chrono>
#include <cstdint>

namespace mdfeed::reliability {

enum class RedundancyMode { SINGLE_FEED, DUAL_FEED_INDEPENDENT_LOSS, DUAL_FEED_CORRELATED_LOSS };

struct RedundancyExperimentConfig {
    RedundancyMode mode{RedundancyMode::SINGLE_FEED};
    FaultConfig feed_a_faults{};
    FaultConfig feed_b_faults{};
};

struct RedundancyExperimentMetrics {
    uint64_t packets_sent{0};
    uint64_t packets_lost{0};
    uint64_t gaps_detected{0};
    uint64_t gaps_avoided_by_second_feed{0};
    uint64_t recovery_requests{0};
    std::chrono::nanoseconds recovery_latency{};
};

class RedundancyExperiment {
public:
    explicit RedundancyExperiment(RedundancyExperimentConfig config = {});
    void record_packet_sent();
    void record_packet_lost();
    void record_gap(bool avoided_by_second_feed);
    void record_recovery(std::chrono::nanoseconds latency);
    [[nodiscard]] const RedundancyExperimentConfig& config() const { return config_; }
    [[nodiscard]] const RedundancyExperimentMetrics& metrics() const { return metrics_; }
private:
    RedundancyExperimentConfig config_;
    RedundancyExperimentMetrics metrics_;
};

struct SnapshotExperimentConfig {
    uint64_t snapshot_interval{0};
    size_t retention_count{0};
    double loss_rate{0.0};
    std::chrono::seconds test_duration{0};
};

struct SnapshotExperimentMetrics {
    uint64_t snapshot_count{0};
    uint64_t snapshot_overhead_bytes{0};
    uint64_t recovery_requests{0};
    uint64_t snapshot_recoveries{0};
    uint64_t unrecoverable_gaps{0};
    std::chrono::nanoseconds recovery_latency{};
};

} // namespace mdfeed::reliability
