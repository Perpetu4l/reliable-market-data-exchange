#pragma once

#include "reliability/RecoveryProtocol.h"
#include "reliability/RecoveryScheduler.h"

#include <atomic>
#include <chrono>
#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <thread>
#include <unordered_map>
#include <vector>

namespace mdfeed::reliability {

class RecoveryTcpServer {
public:
    struct Config {
        uint16_t port{9090};
        SchedulingPolicy scheduling_policy{SchedulingPolicy::FCFS};
        size_t worker_count{1};
        uint64_t service_delay_us_per_request{0};
        uint64_t service_delay_us_per_event{0};
        uint64_t subscriber_idle_timeout_s{10};
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
    [[nodiscard]] size_t queue_depth() const { return scheduler_.active_and_pending_count(); }
    [[nodiscard]] size_t active_subscriber_count();

private:
    struct Connection;
    struct PendingRequest;
    struct SubscriberSession {
        std::string address;
        std::chrono::steady_clock::time_point last_seen;
    };

    void accept_loop();
    void client_loop(std::shared_ptr<Connection> connection);
    void worker_loop();
    bool enqueue_request(std::shared_ptr<Connection> connection, RecoveryRequest request);
    void record_malformed();
    void touch_subscriber(const std::string& subscriber_id, const std::string& peer_addr);
    void disconnect_subscriber(const std::string& subscriber_id, const char* reason);
    void reap_idle_subscribers_locked(std::chrono::steady_clock::time_point now);

    Config config_;
    std::shared_ptr<RecoveryServer> recovery_service_;
    BlockingRecoveryScheduler scheduler_;
    std::atomic<bool> running_{false};
    std::atomic<uint64_t> next_request_id_{1};
    int listen_fd_{-1};
    uint16_t bound_port_{0};
    std::thread accept_thread_;
    std::vector<std::thread> worker_threads_;
    mutable std::mutex connections_mutex_;
    std::vector<std::shared_ptr<Connection>> connections_;
    std::vector<std::thread> client_threads_;
    mutable std::mutex pending_mutex_;
    std::unordered_map<uint64_t, PendingRequest> pending_requests_;
    mutable std::mutex stats_mutex_;
    Stats stats_;
    mutable std::mutex subscribers_mutex_;
    std::unordered_map<std::string, SubscriberSession> active_subscribers_;
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
    [[nodiscard]] std::optional<RecoveryStatusInfo> request_status(
        std::string& error, std::string_view subscriber_id = {}) const;
    bool send_bye(std::string_view subscriber_id, std::string& error) const;

private:
    Endpoint endpoint_;
};

} // namespace mdfeed::reliability
