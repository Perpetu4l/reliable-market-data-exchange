#pragma once

#include <cstring>
#include <deque>
#include <mutex>

namespace orderentry {
    struct OrderBuffer {
        static constexpr size_t MAX_MESSAGE_SIZE = 256;
        alignas(8) char data[MAX_MESSAGE_SIZE];
        size_t length;
        int client_fd;
        uint64_t client_id;

        OrderBuffer(): length(0), client_fd(-1), client_id(0) {}

        template<typename T> OrderBuffer(const T& message, int fd)
            : length(sizeof(T)), client_fd(fd), client_id(0)
        {
            static_assert(sizeof(T) <= MAX_MESSAGE_SIZE,
                          "Message too large for buffer");
            std::memcpy(data, &message, sizeof(T));
        }

        template<typename T> T* as()
        {
            static_assert(sizeof(T) <= MAX_MESSAGE_SIZE,
                          "Message too large for buffer");
            return reinterpret_cast<T*>(data);
        }

        template<typename T> const T* as() const
        {
            static_assert(sizeof(T) <= MAX_MESSAGE_SIZE,
                          "Message too large for buffer");
            return reinterpret_cast<const T*>(data);
        }
    };

    // The original SPSC queue was unsafe here: every connected TCP client has
    // its own receiver thread, so there are multiple producers.  This bounded
    // queue preserves the existing push/pop interface while making that
    // boundary safe for simultaneous independent clients.  The market-data
    // publisher retains its separate lock-free SPSC ring buffer.
    class OrderRingBuffer {
    public:
        explicit OrderRingBuffer(const size_t capacity) : capacity_(capacity) {}

        bool push(const OrderBuffer& value)
        {
            std::lock_guard lock(mutex_);
            if (queue_.size() >= capacity_) return false;
            queue_.push_back(value);
            return true;
        }

        bool pop(OrderBuffer& value)
        {
            std::lock_guard lock(mutex_);
            if (queue_.empty()) return false;
            value = queue_.front();
            queue_.pop_front();
            return true;
        }

        [[nodiscard]] size_t size() const
        {
            std::lock_guard lock(mutex_);
            return queue_.size();
        }

    private:
        size_t capacity_;
        mutable std::mutex mutex_;
        std::deque<OrderBuffer> queue_;
    };
} // namespace orderentry
