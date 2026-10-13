#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <unordered_map>
#include <vector>

namespace mdfeed::reliability {

struct LatencySummary {
    uint64_t p50_ns{0};
    uint64_t p95_ns{0};
    uint64_t p99_ns{0};
    uint64_t p999_ns{0};
    uint64_t max_ns{0};
};

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
