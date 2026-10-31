#include "reliability/Metrics.h"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <numeric>

namespace mdfeed::reliability {
namespace {
uint64_t nearest_rank_sorted(const std::vector<uint64_t>& sorted_values, const double fraction)
{
    if (sorted_values.empty()) return 0;
    if (fraction <= 0.0) return sorted_values.front();
    if (fraction >= 1.0) return sorted_values.back();
    const auto rank = static_cast<size_t>(std::ceil(fraction * static_cast<double>(sorted_values.size())));
    const auto index = std::clamp<size_t>(rank == 0 ? 0 : rank - 1, 0, sorted_values.size() - 1);
    return sorted_values[index];
}
} // namespace

LatencySummary compute_latency_summary(std::vector<uint64_t> values)
{
    LatencySummary summary{};
    summary.count = values.size();
    summary.insufficient_samples = values.size() < 100;
    summary.insufficient_p999 = values.size() < 1000;
    if (values.empty()) return summary;
    std::sort(values.begin(), values.end());
    summary.p50_ns = nearest_rank_sorted(values, 0.50);
    summary.p95_ns = nearest_rank_sorted(values, 0.95);
    summary.p99_ns = nearest_rank_sorted(values, 0.99);
    summary.p999_ns = nearest_rank_sorted(values, 0.999);
    summary.max_ns = values.back();
    unsigned __int128 sum = 0;
    for (const auto v : values) sum += v;
    summary.mean_ns = static_cast<uint64_t>(sum / values.size());
    return summary;
}

void MetricsCollector::record_recovery_latency(const uint64_t nanoseconds)
{
    recovery_latencies_.push_back(nanoseconds);
}

SystemMetrics MetricsCollector::snapshot() const
{
    auto result = metrics_;
    result.recovery_latency = compute_latency_summary(recovery_latencies_);
    return result;
}

bool MetricsExporter::export_csv(const SystemMetrics& metrics, const std::filesystem::path& path) const
{
    std::ofstream output(path);
    if (!output) return false;
    output << "throughput_eps,packets_sent,packets_lost,duplicates,gaps,recovery_requests,recovered_events,snapshot_recoveries,unrecoverable_gaps,p50_ns,p95_ns,p99_ns,p999_ns,max_ns,redundancy_effectiveness\n";
    output << metrics.throughput_events_per_second << ',' << metrics.packets_sent << ',' << metrics.packets_lost << ','
           << metrics.duplicate_count << ',' << metrics.gap_count << ',' << metrics.recovery_request_count << ','
           << metrics.recovered_events << ',' << metrics.snapshot_recoveries << ',' << metrics.unrecoverable_gaps << ','
           << metrics.recovery_latency.p50_ns << ',' << metrics.recovery_latency.p95_ns << ',' << metrics.recovery_latency.p99_ns << ','
           << metrics.recovery_latency.p999_ns << ',' << metrics.recovery_latency.max_ns << ',' << metrics.redundancy_effectiveness << '\n';
    return true;
}
} // namespace mdfeed::reliability
