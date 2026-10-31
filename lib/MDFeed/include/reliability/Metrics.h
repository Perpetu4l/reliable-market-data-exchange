#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <unordered_map>
#include <vector>

namespace mdfeed::reliability {

enum class MissingEventClass : uint8_t {
    OTHER_FEED = 0,
    TCP_RECOVERY = 1,
    SNAPSHOT = 2,
};

[[nodiscard]] inline const char* missing_event_class_name(const MissingEventClass cls)
{
    switch (cls) {
    case MissingEventClass::OTHER_FEED: return "other_feed";
    case MissingEventClass::TCP_RECOVERY: return "tcp_recovery";
    case MissingEventClass::SNAPSHOT: return "snapshot";
    }
    return "unknown";
}

struct MissingEventSample {
    uint64_t sequence_number{0};
    MissingEventClass event_class{MissingEventClass::TCP_RECOVERY};
    uint64_t gap_detected_ns{0};
    uint64_t delivered_ns{0};
    uint64_t latency_ns{0};
};

struct LatencySummary {
    uint64_t p50_ns{0};
    uint64_t p95_ns{0};
    uint64_t p99_ns{0};
    uint64_t p999_ns{0};
    uint64_t max_ns{0};
    uint64_t mean_ns{0};
    uint64_t count{0};
    bool insufficient_samples{true}; // true when count < 100 (for p99)
    bool insufficient_p999{true};    // true when count < 1000 (for p99.9)
};

// Nearest-rank percentile summary over `values`.
[[nodiscard]] LatencySummary compute_latency_summary(std::vector<uint64_t> values);

struct SystemMetrics {
    double throughput_events_per_second{0.0};
    uint64_t packets_sent{0};
    uint64_t packets_lost{0};
    uint64_t duplicate_count{0};
    uint64_t gap_count{0};
    uint64_t recovery_request_count{0};
    uint64_t recovered_events{0};
    uint64_t snapshot_recoveries{0};
    uint64_t unrecoverable_gaps{0};
    LatencySummary recovery_latency;
    std::unordered_map<std::string, uint64_t> fairness_service_distribution;
    double redundancy_effectiveness{0.0};
};

class MetricsCollector {
public:
    void record_recovery_latency(uint64_t nanoseconds);
    [[nodiscard]] SystemMetrics snapshot() const;
    [[nodiscard]] const std::vector<uint64_t>& samples() const { return recovery_latencies_; }
    SystemMetrics& mutable_metrics() { return metrics_; } // experiment harness integration seam
private:
    SystemMetrics metrics_;
    std::vector<uint64_t> recovery_latencies_;
};

class MetricsExporter {
public:
    [[nodiscard]] bool export_csv(const SystemMetrics& metrics, const std::filesystem::path& path) const;
};

} // namespace mdfeed::reliability
