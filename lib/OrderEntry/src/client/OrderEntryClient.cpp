#include "client/OrderEntryClient.h"
#include <arpa/inet.h>
#include <cerrno>
#include <cstring>
#include <iostream>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

namespace orderentry {
OrderEntryClient::OrderEntryClient()
    : socket_fd_(-1), sequence_number_(1)
{
}

OrderEntryClient::~OrderEntryClient()
{
    disconnect();
}

bool OrderEntryClient::connect(const std::string_view server_address, uint16_t port)
{
    if (is_connected()) return true;

    socket_fd_ = socket(AF_INET, SOCK_STREAM, 0);
    if (socket_fd_ < 0) {
        std::cerr << "Failed to create socket" << std::endl;
        return false;
    }

    sockaddr_in server_addr{};
    server_addr.sin_family = AF_INET;
    server_addr.sin_port = htons(port);

    if (inet_pton(AF_INET, server_address.data(), &server_addr.sin_addr) <= 0) {
        std::cerr << "Invalid address: " << server_address << std::endl;
        close(socket_fd_);
        socket_fd_ = -1;
        return false;
    }

    if (::connect(socket_fd_, reinterpret_cast<sockaddr*>(&server_addr), sizeof(server_addr)) < 0) {
        std::cerr << "Connection failed to " << server_address << ":" << port << std::endl;
        close(socket_fd_);
        socket_fd_ = -1;
        return false;
    }

    std::cout << "Connected to " << server_address << ":" << port << std::endl;
    return true;
}

void OrderEntryClient::disconnect()
{
    if (socket_fd_ >= 0) {
        shutdown(socket_fd_, SHUT_RDWR);
        close(socket_fd_);
        socket_fd_ = -1;
        receive_buffer_.clear();
        std::cout << "Disconnected from server" << std::endl;
    }
}

bool OrderEntryClient::send_new_order(const uint64_t client_order_id, const std::string_view symbol, const Side side,
    const uint64_t price, const uint64_t quantity)
{
    if (!is_connected()) return false;

    NewOrderMessage msg{};
    message_utils::init_header(msg, MessageType::NEW_ORDER, sequence_number_++, 1);
    msg.client_order_id = client_order_id;
    msg.price = price;
    msg.quantity = quantity;
    msg.side = side;
    msg.order_type = OrderType::LIMIT;
    msg.time_in_force = TimeInForce::DAY;

    const size_t copy_len = std::min(symbol.length(), sizeof(msg.symbol));
    std::memcpy(msg.symbol, symbol.data(), copy_len);
    if (copy_len < sizeof(msg.symbol)) {
        std::memset(msg.symbol + copy_len, 0, sizeof(msg.symbol) - copy_len);
    }
    std::memset(msg.reserved, 0, sizeof(msg.reserved));
    return send_message(&msg, sizeof(msg));
}

bool OrderEntryClient::send_cancel_order(const uint64_t client_order_id, const uint64_t original_order_id,
    const std::string_view symbol)
{
    if (!is_connected()) return false;

    CancelOrderMessage msg{};
    message_utils::init_header(msg, MessageType::CANCEL_ORDER, sequence_number_++, 1);
    msg.client_order_id = client_order_id;
    msg.original_client_order_id = original_order_id;

    size_t copy_len = std::min(symbol.length(), sizeof(msg.symbol));
    std::memcpy(msg.symbol, symbol.data(), copy_len);
    if (copy_len < sizeof(msg.symbol)) {
        std::memset(msg.symbol + copy_len, 0,
            sizeof(msg.symbol) - copy_len);
    }

    std::memset(msg.reserved, 0, sizeof(msg.reserved));
    return send_message(&msg, sizeof(msg));
}

bool OrderEntryClient::send_client_hello(const uint64_t client_id, const std::string_view client_name)
{
    if (!is_connected()) return false;

    ClientHelloMessage message{};
    message_utils::init_header(message, MessageType::CLIENT_HELLO, sequence_number_++, 0);
    message.client_id = client_id;
    const auto copy_len = std::min(client_name.size(), sizeof(message.client_name));
    std::memcpy(message.client_name, client_name.data(), copy_len);
    return send_message(&message, sizeof(message));
}

bool OrderEntryClient::send_message(const void* data, const size_t length)
{
    if (!is_connected()) return false;

    const auto* bytes = static_cast<const uint8_t*>(data);
    size_t offset = 0;
    while (offset < length) {
        const auto sent = send(socket_fd_, bytes + offset, length - offset, MSG_NOSIGNAL);
        if (sent <= 0) {
            if (sent < 0 && errno == EINTR) continue;
            std::cerr << "Failed to send message" << std::endl;
            disconnect();
            return false;
        }
        offset += static_cast<size_t>(sent);
    }
    return true;
}

bool OrderEntryClient::extract_message(ReceivedMessage& message)
{
    if (receive_buffer_.size() < sizeof(MessageHeader)) return false;
    MessageHeader header{};
    std::memcpy(&header, receive_buffer_.data(), sizeof(header));
    const auto type = static_cast<MessageType>(header.message_type);
    if (!message_utils::is_valid_message_length(type, header.message_length)) {
        std::cerr << "Received invalid order-entry frame" << std::endl;
        disconnect();
        return false;
    }
    if (receive_buffer_.size() < header.message_length) return false;

    message.type = type;
    message.sequence_number = header.sequence_number;
    message.timestamp_ns = header.timestamp_ns;
    message.bytes.assign(receive_buffer_.begin(), receive_buffer_.begin() + header.message_length);
    receive_buffer_.erase(receive_buffer_.begin(), receive_buffer_.begin() + header.message_length);
    return true;
}

bool OrderEntryClient::receive_message(ReceivedMessage& message, const int timeout_ms)
{
    if (!is_connected()) return false;
    if (extract_message(message)) return true;

    pollfd descriptor{socket_fd_, POLLIN, 0};
    const int poll_result = poll(&descriptor, 1, timeout_ms);
    if (poll_result <= 0) return false;
    if ((descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)) != 0) {
        disconnect();
        return false;
    }

    uint8_t buffer[4096];
    const auto bytes_read = recv(socket_fd_, buffer, sizeof(buffer), 0);
    if (bytes_read <= 0) {
        disconnect();
        return false;
    }
    receive_buffer_.insert(receive_buffer_.end(), buffer, buffer + bytes_read);
    return extract_message(message);
}
} // namespace orderentry
