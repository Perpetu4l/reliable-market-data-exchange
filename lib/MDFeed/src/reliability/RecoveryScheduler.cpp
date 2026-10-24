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

BlockingRecoveryScheduler::BlockingRecoveryScheduler(const SchedulingPolicy policy) : scheduler_(policy) {}

void BlockingRecoveryScheduler::enqueue(RecoveryRequest request)
{
    {
        std::lock_guard lock(mutex_);
        if (stopping_) return;
        scheduler_.enqueue(std::move(request));
        ++pending_count_;
    }
    ready_.notify_one();
}

std::optional<ScheduledRecovery> BlockingRecoveryScheduler::wait_next()
{
    std::unique_lock lock(mutex_);
    ready_.wait(lock, [this] { return stopping_ || pending_count_ != 0; });
    if (pending_count_ == 0) return std::nullopt;
    auto result = scheduler_.next();
    if (result) --pending_count_;
    return result;
}

void BlockingRecoveryScheduler::stop()
{
    {
        std::lock_guard lock(mutex_);
        stopping_ = true;
    }
    ready_.notify_all();
}

size_t BlockingRecoveryScheduler::pending_count() const
{
    std::lock_guard lock(mutex_);
    return pending_count_;
}

void BlockingRecoveryScheduler::record_completed(const ScheduledRecovery& recovery, const uint64_t messages_recovered)
{
    std::lock_guard lock(mutex_);
    scheduler_.record_completed(recovery, messages_recovered);
}

RecoverySchedulerMetrics BlockingRecoveryScheduler::metrics() const
{
    std::lock_guard lock(mutex_);
    return scheduler_.metrics();
}

} // namespace mdfeed::reliability
