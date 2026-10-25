#pragma once

#include "reliability/RecoveryProtocol.h"
#include "reliability/RecoveryScheduler.h"

#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace mdfeed::reliability {

// A deliberately small TCP transport around RecoveryServer. It uses the
// bounded RecoveryWire framing and one shared scheduler worker, so FCFS and
// round-robin policies apply across connected subscribers. Authentication,
// TLS and snapshot transfer are separate future work.
class RecoveryTcpServer {
public:
    struct Config {
        uint16_t port{9090};
        SchedulingPolicy scheduling_policy{SchedulingPolicy::FCFS};
    };

    struct Stats {
        uint64_t connections{0};
        uint64_t requests_received{0};
        uint64_t responses_sent{0};
        uint64_t malformed_frames{0};
        uint64_t send_failures{0};
    };

    RecoveryTcpServer(Config config, std::shared_ptr<RecoveryServer> recovery_service);
    ~RecoveryTcpServer();
    RecoveryTcpServer(const RecoveryTcpServer&) = delete;
    RecoveryTcpServer& operator=(const RecoveryTcpServer&) = delete;

    bool start(std::string& error);
    void stop();
    [[nodiscard]] bool is_running() const { return running_.load(); }
    [[nodiscard]] uint16_t port() const { return bound_port_; }
    [[nodiscard]] Stats stats() const;
    [[nodiscard]] RecoverySchedulerMetrics scheduler_metrics() const;

private:
    struct Connection;
    struct PendingRequest;

    void accept_loop();
    void client_loop(std::shared_ptr<Connection> connection);
    void worker_loop();
    bool enqueue_request(std::shared_ptr<Connection> connection, RecoveryRequest request);
    void record_malformed();

    Config config_;
    std::shared_ptr<RecoveryServer> recovery_service_;
    BlockingRecoveryScheduler scheduler_;
    std::atomic<bool> running_{false};
    std::atomic<uint64_t> next_request_id_{1};
    int listen_fd_{-1};
    uint16_t bound_port_{0};
    std::thread accept_thread_;
    std::thread worker_thread_;
    mutable std::mutex connections_mutex_;
    std::vector<std::shared_ptr<Connection>> connections_;
    std::vector<std::thread> client_threads_;
    mutable std::mutex pending_mutex_;
    std::unordered_map<uint64_t, PendingRequest> pending_requests_;
    mutable std::mutex stats_mutex_;
    Stats stats_;
};

class RecoveryTcpClient {
public:
    struct Endpoint {
        std::string host{"127.0.0.1"};
        uint16_t port{9090};
        uint32_t timeout_ms{5000};
    };

    explicit RecoveryTcpClient(Endpoint endpoint) : endpoint_(std::move(endpoint)) {}
    [[nodiscard]] std::optional<RecoveryResponse> request_missing_data(
        const RecoveryRequest& request, std::string& error) const;
    [[nodiscard]] std::optional<RecoveryResponse> request_snapshot(
        uint64_t minimum_sequence, std::string& error) const;

private:
    Endpoint endpoint_;
};

} // namespace mdfeed::reliability
