#include "reliability/RecoveryScheduler.h"

#include <algorithm>

namespace mdfeed::reliability {
namespace {
uint64_t to_ns(const std::chrono::steady_clock::time_point tp)
{
    return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
        tp.time_since_epoch()).count());
}
} // namespace

RecoveryScheduler::RecoveryScheduler(const SchedulingPolicy policy) : policy_(policy) {}

size_t RecoveryScheduler::queued_count() const
{
    if (policy_ == SchedulingPolicy::FCFS) return fcfs_queue_.size();
    size_t total = 0;
    for (const auto& [_, q] : per_subscriber_) total += q.size();
    return total;
}

void RecoveryScheduler::enqueue(RecoveryRequest request, const size_t active_workers)
{
    ++metrics_.recovery_request_count;
    const auto now = std::chrono::steady_clock::now();
    const size_t depth = queued_count() + active_workers + 1;
    const uint64_t enq_ns = to_ns(now);
    last_enqueue_ns_ = std::max(last_enqueue_ns_, enq_ns);
    if (depth >= 2 && !first_saturated_enqueue_ns_) {
        first_saturated_enqueue_ns_ = enq_ns;
    }

    ScheduledRecovery scheduled{std::move(request), now, depth};
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

void RecoveryScheduler::record_completed(const ScheduledRecovery& recovery,
    const uint64_t messages_recovered,
    const uint64_t queue_wait_ns,
    std::string status,
    const uint64_t started_ns,
    const uint64_t finished_ns)
{
    metrics_.messages_recovered += messages_recovered;
    metrics_.total_queue_wait_ns += queue_wait_ns;
    metrics_.max_queue_wait_ns = std::max(metrics_.max_queue_wait_ns, queue_wait_ns);
    ++metrics_.per_subscriber_service_distribution[recovery.request.subscriber_id];
    metrics_.per_subscriber_queue_waits_ns[recovery.request.subscriber_id].push_back(queue_wait_ns);

    const uint64_t enq_ns = to_ns(recovery.enqueued_at);
    if (first_saturated_enqueue_ns_ && enq_ns >= *first_saturated_enqueue_ns_ && enq_ns <= last_enqueue_ns_) {
        ++metrics_.saturated_window_service_distribution[recovery.request.subscriber_id];
    }

    metrics_.request_log.push_back({
        recovery.request.subscriber_id,
        recovery.request.missing_range.start_sequence,
        recovery.request.missing_range.end_sequence,
        messages_recovered,
        std::move(status),
        policy_ == SchedulingPolicy::ROUND_ROBIN ? "round-robin" : "fcfs",
        enq_ns,
        started_ns != 0 ? started_ns : enq_ns + queue_wait_ns,
        finished_ns != 0 ? finished_ns : enq_ns + queue_wait_ns,
        recovery.queue_depth_at_enqueue,
        queue_wait_ns,
    });
}

BlockingRecoveryScheduler::BlockingRecoveryScheduler(const SchedulingPolicy policy) : scheduler_(policy) {}

void BlockingRecoveryScheduler::enqueue(RecoveryRequest request)
{
    {
        std::lock_guard lock(mutex_);
        if (stopping_) return;
        scheduler_.enqueue(std::move(request), active_workers_);
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
    if (result) {
        --pending_count_;
        ++active_workers_;
    }
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

size_t BlockingRecoveryScheduler::active_and_pending_count() const
{
    std::lock_guard lock(mutex_);
    return pending_count_ + active_workers_;
}

void BlockingRecoveryScheduler::record_completed(const ScheduledRecovery& recovery,
    const uint64_t messages_recovered,
    const uint64_t queue_wait_ns,
    std::string status,
    const uint64_t started_ns,
    const uint64_t finished_ns)
{
    std::lock_guard lock(mutex_);
    if (active_workers_ > 0) --active_workers_;
    scheduler_.record_completed(recovery, messages_recovered, queue_wait_ns, std::move(status), started_ns, finished_ns);
}

RecoverySchedulerMetrics BlockingRecoveryScheduler::metrics() const
{
    std::lock_guard lock(mutex_);
    return scheduler_.metrics();
}

} // namespace mdfeed::reliability
