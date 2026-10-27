#pragma once

#include "MulticastPublisher.h"
#include "utils/RingBuffer.h"
#include "utils/PublisherConfig.h"
#include "messages/Messages.h"
#include "reliability/FaultInjector.h"
#include <thread>
#include <atomic>
#include <functional>

namespace mdfeed {
class MulticastPublisherThread {
public:
    using PublishedMessageHandler = std::function<void(const MessageBuffer&)>;
    explicit MulticastPublisherThread(const std::shared_ptr<MDRingBuffer>& ring_buffer,
        PublisherConfig config = PublisherConfig{}, PublishedMessageHandler handler = {});
    ~MulticastPublisherThread();

    MulticastPublisherThread(const MulticastPublisherThread&) = delete;
    MulticastPublisherThread& operator=(const MulticastPublisherThread&) = delete;

    bool start();
    void stop();
    bool is_running() const { return running_.load(); }

    struct Stats {
        uint64_t messages_sent = 0;
        uint64_t send_failures = 0;
        uint64_t ring_buffer_empty_count = 0;
        uint64_t feed_a_sent = 0;
        uint64_t feed_b_sent = 0;
        uint64_t feed_a_app_dropped = 0;
        uint64_t feed_b_app_dropped = 0;
    };

    Stats get_stats() const { return stats_; }

private:
    void publisher_loop();
    bool send_to_feeds(const void* data, size_t length);

    std::shared_ptr<MDRingBuffer> ring_buffer_;
    PublisherConfig config_;
    MulticastPublisher multicast_publisher_;
    MulticastPublisher feed_b_publisher_;
    std::atomic<bool> running_;
    std::thread publisher_thread_;
    Stats stats_;
    uint64_t last_published_sequence_{0};
    reliability::FaultInjector feed_a_faults_;
    reliability::FaultInjector feed_b_faults_;
    reliability::FaultInjector correlated_faults_;
    PublishedMessageHandler published_message_handler_;
};
}
