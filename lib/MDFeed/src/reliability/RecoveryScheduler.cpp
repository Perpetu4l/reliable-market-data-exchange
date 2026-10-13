#include "reliability/RecoveryScheduler.h"

namespace mdfeed::reliability {

RecoveryScheduler::RecoveryScheduler(const SchedulingPolicy policy) : policy_(policy) {}

void RecoveryScheduler::enqueue(RecoveryRequest request)
{
    ++metrics_.recovery_request_count;
    ScheduledRecovery scheduled{std::move(request)};
    if (policy_ == SchedulingPolicy::FCFS) {
        fcfs_queue_.push_back(std::move(scheduled));
        return;
    }
    auto& subscriber_queue = per_subscriber_[scheduled.request.subscriber_id];
    if (subscriber_queue.empty()) round_robin_order_.push_back(scheduled.request.subscriber_id);
    subscriber_queue.push_back(std::move(scheduled));
}

std::optional<ScheduledRecovery> RecoveryScheduler::next()
{
    if (policy_ == SchedulingPolicy::FCFS) {
        if (fcfs_queue_.empty()) return std::nullopt;
        auto next_request = std::move(fcfs_queue_.front());
        fcfs_queue_.pop_front();
        return next_request;
    }
    if (round_robin_order_.empty()) return std::nullopt;
    const auto subscriber_id = round_robin_order_.front();
    round_robin_order_.pop_front();
    auto& subscriber_queue = per_subscriber_.at(subscriber_id);
    auto next_request = std::move(subscriber_queue.front());
    subscriber_queue.pop_front();
    if (!subscriber_queue.empty()) round_robin_order_.push_back(subscriber_id);
    return next_request;
}

void RecoveryScheduler::record_completed(const ScheduledRecovery& recovery, const uint64_t messages_recovered)
{
    metrics_.messages_recovered += messages_recovered;
    ++metrics_.per_subscriber_service_distribution[recovery.request.subscriber_id];
}

} // namespace mdfeed::reliability
