#include "server/OrderEntryServer.h"
#include "messages/OrderMessages.h"
#include <algorithm>
#include <cerrno>
#include <cstring>
#include <iostream>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

namespace orderentry {
OrderEntryServer::OrderEntryServer(const uint16_t port)
    : port_(port), server_fd_(-1), order_buffer_(std::make_unique<OrderRingBuffer>(65536)), running_(false)
{
}

OrderEntryServer::~OrderEntryServer()
{
    stop();
}

bool OrderEntryServer::start()
{
    if (running_.load()) return true;

    server_fd_ = socket(AF_INET, SOCK_STREAM, 0);
    if (server_fd_ < 0) {
        std::cerr << "Failed to create socket" << std::endl;
        return false;
    }

    const int opt = 1;
    setsockopt(server_fd_, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = INADDR_ANY;
    address.sin_port = htons(port_);

    if (bind(server_fd_, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0) {
        std::cerr << "Bind failed on port " << port_ << ": " << std::strerror(errno) << std::endl;
        close(server_fd_);
        return false;
    }

    if (listen(server_fd_, 10) < 0) {
        std::cerr << "Listen failed" << std::endl;
        close(server_fd_);
        return false;
    }

    running_.store(true);
    server_thread_ = std::thread(&OrderEntryServer::server_loop, this);

    std::cout << "OrderEntry server started on 0.0.0.0:" << port_
              << " (all IPv4 interfaces)" << std::endl;
    return true;
}

void OrderEntryServer::stop()
{
    if (!running_.load()) return;

    running_.store(false);

    if (server_fd_ >= 0) {
        shutdown(server_fd_, SHUT_RDWR);
        close(server_fd_);
        server_fd_ = -1;
    }

    if (server_thread_.joinable()) { server_thread_.join(); }

    {
        std::lock_guard lock(clients_mutex_);
        for (const auto client_fd : client_fds_) {
            shutdown(client_fd, SHUT_RDWR);
        }
    }
    for (auto& thread : client_threads_) {
        if (thread.joinable()) thread.join();
    }
    client_threads_.clear();

    std::cout << "OrderEntry server stopped" << std::endl;
}

void OrderEntryServer::server_loop()
{
    while (running_.load()) {
        sockaddr_in client_address{};
        socklen_t client_len = sizeof(client_address);

        int client_fd = accept(server_fd_, reinterpret_cast<struct sockaddr*>(&client_address), &client_len);
        if (client_fd < 0) {
            if (running_.load()) {
                std::cerr << "Accept failed" << std::endl;
            }
            continue;
        }

        {
            std::lock_guard lock(stats_mutex_);
            ++stats_.connections;
            ++stats_.active_connections;
        }
        {
            std::lock_guard lock(clients_mutex_);
            client_fds_.insert(client_fd);
        }
        std::cout << "New client connected (fd=" << client_fd << ")" << std::endl;

        client_threads_.emplace_back(&OrderEntryServer::handle_client, this, client_fd);
    }
}

void OrderEntryServer::handle_client(int client_fd)
{
    std::vector<uint8_t> stream_buffer;
    stream_buffer.reserve(4096);
    uint64_t client_id = 0;
    uint8_t buffer[4096];

    while (running_.load()) {
        const auto bytes_read = recv(client_fd, buffer, sizeof(buffer), 0);

        if (bytes_read <= 0) {
            break; // Client disconnected
        }
        stream_buffer.insert(stream_buffer.end(), buffer, buffer + bytes_read);

        size_t offset = 0;
        while (offset + sizeof(MessageHeader) <= stream_buffer.size()) {
            MessageHeader header{};
            std::memcpy(&header, stream_buffer.data() + offset, sizeof(header));
            const auto type = static_cast<MessageType>(header.message_type);
            if (!message_utils::is_valid_message_length(type, header.message_length)
                || header.message_length > OrderBuffer::MAX_MESSAGE_SIZE) {
                {
                    std::lock_guard lock(stats_mutex_);
                    ++stats_.malformed_messages;
                }
                std::cerr << "Closing client " << client_fd << " after invalid frame" << std::endl;
                offset = stream_buffer.size();
                shutdown(client_fd, SHUT_RDWR);
                break;
            }
            if (offset + header.message_length <= stream_buffer.size()) {
                if (type == MessageType::CLIENT_HELLO) {
                    ClientHelloMessage hello{};
                    std::memcpy(&hello, stream_buffer.data() + offset, sizeof(hello));
                    client_id = hello.client_id;
                    send_welcome(client_fd, client_id);
                    std::cout << "Client " << client_id << " identified on fd=" << client_fd << std::endl;
                    offset += header.message_length;
                    continue;
                }
                OrderBuffer order_buf;
                if (header.message_length <= OrderBuffer::MAX_MESSAGE_SIZE) {
                    std::memcpy(order_buf.data, stream_buffer.data() + offset, header.message_length);
                    order_buf.length = header.message_length;
                    order_buf.client_fd = client_fd;
                    order_buf.client_id = client_id;

                    if (order_buffer_->push(order_buf)) {
                        {
                            std::lock_guard lock(stats_mutex_);
                            ++stats_.orders_received;
                        }

                        if (type == MessageType::NEW_ORDER) {
                            NewOrderMessage order{};
                            std::memcpy(&order, stream_buffer.data() + offset, sizeof(order));
                            std::cout << "Received order: id=" << order.client_order_id << ", symbol=" <<
                                    std::string(order.symbol, strnlen(order.symbol, sizeof(order.symbol))) << ", side=" << (order.side == Side::BUY
                                            ? "BUY"
                                            : "SELL")
                                    << ", price=$" << static_cast<double>(order.price) / 100 << ", qty=" << order.quantity << std::endl;
                        }
                    }
                    else {
                        std::lock_guard lock(stats_mutex_);
                        ++stats_.queue_overflows;
                    }
                }
                offset += header.message_length;
            }
            else {
                break; // preserve incomplete application frame for the next recv()
            }
        }
        if (offset > 0) stream_buffer.erase(stream_buffer.begin(), stream_buffer.begin() + offset);
    }
    record_disconnect(client_fd);
}

void OrderEntryServer::send_welcome(const int client_fd, const uint64_t client_id)
{
    ServerWelcomeMessage welcome{};
    message_utils::init_header(welcome, MessageType::SERVER_WELCOME, 0, 0);
    welcome.client_id = client_id;
    welcome.connection_id = static_cast<uint32_t>(client_fd);
    constexpr std::string_view server_name{"exchange"};
    std::memcpy(welcome.server_name, server_name.data(), server_name.size());
    const auto* bytes = reinterpret_cast<const uint8_t*>(&welcome);
    size_t offset = 0;
    while (offset < sizeof(welcome)) {
        const auto sent = send(client_fd, bytes + offset, sizeof(welcome) - offset, MSG_NOSIGNAL);
        if (sent <= 0) return;
        offset += static_cast<size_t>(sent);
    }
}

void OrderEntryServer::record_disconnect(const int client_fd)
{
    {
        std::lock_guard lock(clients_mutex_);
        client_fds_.erase(client_fd);
    }
    {
        std::lock_guard lock(stats_mutex_);
        if (stats_.active_connections > 0) --stats_.active_connections;
    }
    close(client_fd);
    std::cout << "Client disconnected (fd=" << client_fd << ")" << std::endl;
}

OrderEntryServer::Stats OrderEntryServer::get_stats() const
{
    std::lock_guard lock(stats_mutex_);
    return stats_;
}
} // namespace orderentry
