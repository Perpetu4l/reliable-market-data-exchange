#pragma once

#include "reliability/RecoveryProtocol.h"

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <optional>
#include <string>
#include <unordered_map>

namespace mdfeed::reliability {

enum class SchedulingPolicy { FCFS, ROUND_ROBIN };

struct ScheduledRecovery {
    RecoveryRequest request;
    std::chrono::steady_clock::time_point enqueued_at{std::chrono::steady_clock::now()};
};

struct RecoverySchedulerMetrics {
    uint64_t recovery_request_count{0};
    uint64_t messages_recovered{0};
    std::unordered_map<std::string, uint64_t> per_subscriber_service_distribution;
};

// A policy seam for future shared TCP recovery-server scheduling.
class RecoveryScheduler {
public:
    explicit RecoveryScheduler(SchedulingPolicy policy = SchedulingPolicy::FCFS);
    void enqueue(RecoveryRequest request);
    [[nodiscard]] std::optional<ScheduledRecovery> next();
    void record_completed(const ScheduledRecovery& recovery, uint64_t messages_recovered);
    [[nodiscard]] const RecoverySchedulerMetrics& metrics() const { return metrics_; }
private:
    SchedulingPolicy policy_;
    std::deque<ScheduledRecovery> fcfs_queue_;
    std::unordered_map<std::string, std::deque<ScheduledRecovery>> per_subscriber_;
    std::deque<std::string> round_robin_order_;
    RecoverySchedulerMetrics metrics_;
};

// Blocking ownership wrapper for a live recovery acceptor/worker setup. The
// policy implementation remains independently testable above; this wrapper
// supplies synchronisation and shutdown semantics without making the policy
// class itself depend on sockets or threads.
class BlockingRecoveryScheduler {
public:
    explicit BlockingRecoveryScheduler(SchedulingPolicy policy = SchedulingPolicy::FCFS);
    void enqueue(RecoveryRequest request);
    [[nodiscard]] std::optional<ScheduledRecovery> wait_next();
    void stop();
    [[nodiscard]] size_t pending_count() const;
    void record_completed(const ScheduledRecovery& recovery, uint64_t messages_recovered);
    [[nodiscard]] RecoverySchedulerMetrics metrics() const;

private:
    mutable std::mutex mutex_;
    std::condition_variable ready_;
    RecoveryScheduler scheduler_;
    bool stopping_{false};
    size_t pending_count_{0};
};

} // namespace mdfeed::reliability
