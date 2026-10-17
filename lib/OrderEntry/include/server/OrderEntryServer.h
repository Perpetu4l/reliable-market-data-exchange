#pragma once

#include "utils/OrderBuffer.h"
#include "utils/ServerConfig.h"
#include <atomic>
#include <cstdint>
#include <mutex>
#include <memory>
#include <unordered_set>
#include <thread>
#include <vector>

namespace orderentry {
class OrderEntryServer {
public:
    explicit OrderEntryServer(uint16_t port = 8080);
    ~OrderEntryServer();

    bool start();
    void stop();
    bool is_running() const { return running_.load(); }

    OrderRingBuffer* get_order_buffer() const
    {
        return order_buffer_.get();
    }

    struct Stats {
        uint64_t orders_received = 0;
        uint64_t connections = 0;
        uint64_t active_connections = 0;
        uint64_t malformed_messages = 0;
        uint64_t queue_overflows = 0;
    };

    Stats get_stats() const;

private:
    void server_loop();
    void handle_client(int client_fd);
    void send_welcome(int client_fd, uint64_t client_id);
    void record_disconnect(int client_fd);

    uint16_t port_;
    int server_fd_;
    std::unique_ptr<OrderRingBuffer> order_buffer_;
    std::atomic<bool> running_;
    std::thread server_thread_;
    mutable std::mutex stats_mutex_;
    Stats stats_;
    std::mutex clients_mutex_;
    std::unordered_set<int> client_fds_;
    std::vector<std::thread> client_threads_;
};
} // namespace orderentry
