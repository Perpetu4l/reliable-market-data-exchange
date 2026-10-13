#include "reliability/Metrics.h"

#include <algorithm>
#include <fstream>

namespace mdfeed::reliability {
namespace {
uint64_t percentile(std::vector<uint64_t> values, const double fraction)
{
    if (values.empty()) return 0;
    std::sort(values.begin(), values.end());
    const auto index = static_cast<size_t>(fraction * static_cast<double>(values.size() - 1));
    return values[index];
}
}
void MetricsCollector::record_recovery_latency(const uint64_t nanoseconds) { recovery_latencies_.push_back(nanoseconds); }
SystemMetrics MetricsCollector::snapshot() const
{
    auto result = metrics_;
    result.recovery_latency = {percentile(recovery_latencies_, .50), percentile(recovery_latencies_, .95),
        percentile(recovery_latencies_, .99), percentile(recovery_latencies_, .999), percentile(recovery_latencies_, 1.0)};
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
