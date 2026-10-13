#include "reliability/Experiments.h"

namespace mdfeed::reliability {

RedundancyExperiment::RedundancyExperiment(RedundancyExperimentConfig config) : config_(std::move(config)) {}
void RedundancyExperiment::record_packet_sent() { ++metrics_.packets_sent; }
void RedundancyExperiment::record_packet_lost() { ++metrics_.packets_lost; }
void RedundancyExperiment::record_gap(const bool avoided_by_second_feed)
{
    ++metrics_.gaps_detected;
    if (avoided_by_second_feed) ++metrics_.gaps_avoided_by_second_feed;
}
void RedundancyExperiment::record_recovery(const std::chrono::nanoseconds latency)
{
    ++metrics_.recovery_requests;
    metrics_.recovery_latency += latency;
}

} // namespace mdfeed::reliability
