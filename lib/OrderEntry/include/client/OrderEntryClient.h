#pragma once

#include "messages/OrderMessages.h"
#include <cstdint>
#include <string>
#include <vector>

namespace orderentry {
    class OrderEntryClient {
    public:
        struct ReceivedMessage {
            MessageType type{MessageType::ORDER_REJECT};
            uint64_t sequence_number{0};
            uint64_t timestamp_ns{0};
            std::vector<uint8_t> bytes;
        };

        OrderEntryClient();
        ~OrderEntryClient();

        bool connect(std::string_view server_address, uint16_t port);
        void disconnect();
        [[nodiscard]] bool is_connected() const { return socket_fd_ >= 0; }

        bool send_new_order(uint64_t client_order_id, std::string_view symbol,
            Side side, uint64_t price, uint64_t quantity);

        bool send_cancel_order(uint64_t client_order_id,
            uint64_t original_order_id,
            std::string_view symbol);

        // The workload client uses this optional handshake to make its
        // identity explicit. Interactive legacy clients may still connect and
        // submit orders without it.
        bool send_client_hello(uint64_t client_id, std::string_view client_name);

        // Returns one complete application frame, not one arbitrary recv().
        // timeout_ms == 0 performs a non-blocking poll.
        bool receive_message(ReceivedMessage& message, int timeout_ms = 0);

    private:
        bool send_message(const void* data, size_t length);
        bool extract_message(ReceivedMessage& message);

        int socket_fd_;
        uint64_t sequence_number_;
        std::vector<uint8_t> receive_buffer_;
    };
} // namespace orderentry
