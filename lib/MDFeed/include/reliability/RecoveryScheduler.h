#pragma once

#include "reliability/RecoveryProtocol.h"

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace mdfeed::reliability {

enum class SchedulingPolicy { FCFS, ROUND_ROBIN };

struct ScheduledRecovery {
    RecoveryRequest request;
    std::chrono::steady_clock::time_point enqueued_at{std::chrono::steady_clock::now()};
    size_t queue_depth_at_enqueue{1};
};

struct RecoveryRequestLogEntry {
    std::string subscriber_id;
    uint64_t range_start{0};
    uint64_t range_end{0};
    uint64_t n_events{0};
    std::string status;
    std::string policy;
    uint64_t enqueued_ns{0};
    uint64_t started_ns{0};
    uint64_t finished_ns{0};
    size_t queue_depth_at_enqueue{0};
    uint64_t queue_wait_ns{0};
};

struct RecoverySchedulerMetrics {
    uint64_t recovery_request_count{0};
    uint64_t messages_recovered{0};
    uint64_t total_queue_wait_ns{0};
    uint64_t max_queue_wait_ns{0};
    std::unordered_map<std::string, uint64_t> per_subscriber_service_distribution;
    std::unordered_map<std::string, uint64_t> saturated_window_service_distribution;
    std::unordered_map<std::string, std::vector<uint64_t>> per_subscriber_queue_waits_ns;
    std::vector<RecoveryRequestLogEntry> request_log;
};

class RecoveryScheduler {
public:
    explicit RecoveryScheduler(SchedulingPolicy policy = SchedulingPolicy::FCFS);
    void enqueue(RecoveryRequest request, size_t active_workers = 0);
    [[nodiscard]] std::optional<ScheduledRecovery> next();
    void record_completed(const ScheduledRecovery& recovery, uint64_t messages_recovered,
        uint64_t queue_wait_ns = 0,
        std::string status = "OK",
        uint64_t started_ns = 0,
        uint64_t finished_ns = 0);
    [[nodiscard]] const RecoverySchedulerMetrics& metrics() const { return metrics_; }
    [[nodiscard]] SchedulingPolicy policy() const { return policy_; }
    [[nodiscard]] size_t queued_count() const;

private:
    SchedulingPolicy policy_;
    std::deque<ScheduledRecovery> fcfs_queue_;
    std::unordered_map<std::string, std::deque<ScheduledRecovery>> per_subscriber_;
    std::deque<std::string> round_robin_order_;
    RecoverySchedulerMetrics metrics_;
    std::optional<uint64_t> first_saturated_enqueue_ns_;
    uint64_t last_enqueue_ns_{0};
};

class BlockingRecoveryScheduler {
public:
    explicit BlockingRecoveryScheduler(SchedulingPolicy policy = SchedulingPolicy::FCFS);
    void enqueue(RecoveryRequest request);
    [[nodiscard]] std::optional<ScheduledRecovery> wait_next();
    void stop();
    [[nodiscard]] size_t pending_count() const;
    [[nodiscard]] size_t active_and_pending_count() const;
    void record_completed(const ScheduledRecovery& recovery, uint64_t messages_recovered,
        uint64_t queue_wait_ns = 0,
        std::string status = "OK",
        uint64_t started_ns = 0,
        uint64_t finished_ns = 0);
    [[nodiscard]] RecoverySchedulerMetrics metrics() const;

private:
    mutable std::mutex mutex_;
    std::condition_variable ready_;
    RecoveryScheduler scheduler_;
    bool stopping_{false};
    size_t pending_count_{0};
    size_t active_workers_{0};
};

} // namespace mdfeed::reliability
