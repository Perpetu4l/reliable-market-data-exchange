#include "publisher/MulticastPublisherThread.h"
#include <iostream>
#include <chrono>
#include <utility>

namespace mdfeed {
MulticastPublisherThread::MulticastPublisherThread(const std::shared_ptr<MDRingBuffer>& ring_buffer,
    PublisherConfig config)
    : ring_buffer_(ring_buffer), config_(std::move(config)), running_(false)
{
}

MulticastPublisherThread::~MulticastPublisherThread()
{
    stop();
}

bool MulticastPublisherThread::start()
{
    if (running_.load()) {
        return true;
    }

    if (!ring_buffer_) {
        std::cerr << "No ring buffer provided to MulticastPublisherThread!" << std::endl;
        return false;
    }
    if (!multicast_publisher_.initialize(config_.multicast_ip, config_.multicast_port, config_.interface_ip)) {
        std::cerr << "Failed to initialize multicast publisher!" << std::endl;
        return false;
    }
    if (config_.dual_feed && !feed_b_publisher_.initialize(
        config_.feed_b_multicast_ip, config_.feed_b_multicast_port, config_.interface_ip)) {
        std::cerr << "Failed to initialize Feed B multicast publisher!" << std::endl;
        multicast_publisher_.close();
        return false;
    }

    running_.store(true);
    stats_ = Stats{};
    publisher_thread_ = std::thread(&MulticastPublisherThread::publisher_loop, this);

    std::cout << "MulticastPublisherThread started - publishing to " << config_.multicast_ip << ":" << config_.
            multicast_port << std::endl;
    if (config_.dual_feed) {
        std::cout << "Feed B enabled - publishing to " << config_.feed_b_multicast_ip << ':'
                  << config_.feed_b_multicast_port << std::endl;
    }
    return true;
}

void MulticastPublisherThread::stop()
{
    if (!running_.load()) {
        return;
    }

    std::cout << "Stopping MulticastPublisherThread..." << std::endl;
    running_.store(false);

    if (publisher_thread_.joinable()) {
        publisher_thread_.join();
    }
    multicast_publisher_.close();
    feed_b_publisher_.close();
    std::cout << "MulticastPublisherThread stopped. Messages sent: " << stats_.messages_sent << ", Failures: " << stats_
            .send_failures << std::endl;
}

void MulticastPublisherThread::publisher_loop()
{
    MessageBuffer message;
    auto last_stats_time = std::chrono::steady_clock::now();
    auto last_heartbeat_time = last_stats_time;
    std::cout << "Publisher thread started" << std::endl;
    while (running_.load(std::memory_order_relaxed)) {
        if (ring_buffer_->pop(message)) {
            if (send_to_feeds(message.data, message.length)) {
                stats_.messages_sent++;
                if (message.length >= sizeof(MessageHeader)) {
                    const auto* header = reinterpret_cast<const MessageHeader*>(message.data);
                    if (static_cast<MessageType>(header->message_type) != MessageType::HEARTBEAT) {
                        last_published_sequence_ = header->sequence_number;
                    }
                }

                auto now = std::chrono::steady_clock::now();
                if (std::chrono::duration_cast<std::chrono::seconds>(now - last_stats_time).count() >= 5) {
                    std::cout << "Publisher stats - Sent: " << stats_.messages_sent
                            << ", Failures: " << stats_.send_failures
                            << ", Empty polls: " << stats_.ring_buffer_empty_count << std::endl;
                    last_stats_time = now;
                }
            }
            else {
                stats_.send_failures++;
                std::cerr << "Failed to send multicast message" << std::endl;
            }
        }
        else {
            stats_.ring_buffer_empty_count++;
            const auto now = std::chrono::steady_clock::now();
            if (last_published_sequence_ != 0 && now - last_heartbeat_time >= config_.heartbeat_interval) {
                HeartbeatMessage heartbeat{};
                message_utils::init_header(heartbeat, MessageType::HEARTBEAT, last_published_sequence_, 0);
                if (!send_to_feeds(&heartbeat, sizeof(heartbeat))) ++stats_.send_failures;
                last_heartbeat_time = now;
            }

            if (config_.use_busy_wait) {
                auto start = std::chrono::high_resolution_clock::now();
                while (std::chrono::high_resolution_clock::now() - start < config_.spin_duration) {
                }
            }
            else {
                std::this_thread::yield();
            }
        }
    }

    std::cout << "Publisher thread stopped." << std::endl;
}

bool MulticastPublisherThread::send_to_feeds(const void* data, const size_t length)
{
    const bool a_sent = multicast_publisher_.send(data, length);
    const bool b_sent = !config_.dual_feed || feed_b_publisher_.send(data, length);
    return a_sent && b_sent;
}
}
