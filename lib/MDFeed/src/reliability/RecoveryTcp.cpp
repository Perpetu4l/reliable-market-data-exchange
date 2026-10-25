#include "reliability/RecoveryTcp.h"
#include "reliability/RecoveryWire.h"

#include <arpa/inet.h>
#include <cerrno>
#include <cstring>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

namespace mdfeed::reliability {
namespace {
bool send_all(const int fd, const std::vector<uint8_t>& bytes)
{
    size_t offset = 0;
    while (offset < bytes.size()) {
        const auto sent = send(fd, bytes.data() + offset, bytes.size() - offset, MSG_NOSIGNAL);
        if (sent <= 0) return false;
        offset += static_cast<size_t>(sent);
    }
    return true;
}

void close_socket(const int fd)
{
    if (fd >= 0) {
        shutdown(fd, SHUT_RDWR);
        close(fd);
    }
}
} // namespace

struct RecoveryTcpServer::Connection {
    explicit Connection(const int socket_fd) : fd(socket_fd) {}
    int fd;
    std::atomic<bool> open{true};
    std::mutex write_mutex;
};

struct RecoveryTcpServer::PendingRequest {
    std::shared_ptr<Connection> connection;
};

RecoveryTcpServer::RecoveryTcpServer(Config config, std::shared_ptr<RecoveryServer> recovery_service)
    : config_(config), recovery_service_(std::move(recovery_service)), scheduler_(config_.scheduling_policy) {}

RecoveryTcpServer::~RecoveryTcpServer()
{
    stop();
}

bool RecoveryTcpServer::start(std::string& error)
{
    error.clear();
    if (running_.load()) return true;
    if (!recovery_service_) {
        error = "recovery service is unavailable";
        return false;
    }
    listen_fd_ = socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd_ < 0) {
        error = std::string("could not create recovery socket: ") + std::strerror(errno);
        return false;
    }
    const int enabled = 1;
    setsockopt(listen_fd_, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_ANY);
    address.sin_port = htons(config_.port);
    if (bind(listen_fd_, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0) {
        error = std::string("could not bind recovery port ") + std::to_string(config_.port)
            + ": " + std::strerror(errno);
        close_socket(listen_fd_);
        listen_fd_ = -1;
        return false;
    }
    if (listen(listen_fd_, 32) < 0) {
        error = std::string("could not listen for recovery clients: ") + std::strerror(errno);
        close_socket(listen_fd_);
        listen_fd_ = -1;
        return false;
    }
    sockaddr_in bound{};
    socklen_t bound_size = sizeof(bound);
    if (getsockname(listen_fd_, reinterpret_cast<sockaddr*>(&bound), &bound_size) == 0) {
        bound_port_ = ntohs(bound.sin_port);
    }
    running_.store(true);
    worker_thread_ = std::thread(&RecoveryTcpServer::worker_loop, this);
    accept_thread_ = std::thread(&RecoveryTcpServer::accept_loop, this);
    return true;
}

void RecoveryTcpServer::stop()
{
    if (!running_.exchange(false)) return;
    close_socket(listen_fd_);
    listen_fd_ = -1;
    scheduler_.stop();
    if (accept_thread_.joinable()) accept_thread_.join();
    {
        std::lock_guard lock(connections_mutex_);
        for (const auto& connection : connections_) {
            connection->open.store(false);
            close_socket(connection->fd);
        }
    }
    for (auto& thread : client_threads_) {
        if (thread.joinable()) thread.join();
    }
    client_threads_.clear();
    if (worker_thread_.joinable()) worker_thread_.join();
    {
        std::lock_guard lock(pending_mutex_);
        pending_requests_.clear();
    }
    bound_port_ = 0;
}

RecoveryTcpServer::Stats RecoveryTcpServer::stats() const
{
    std::lock_guard lock(stats_mutex_);
    return stats_;
}

RecoverySchedulerMetrics RecoveryTcpServer::scheduler_metrics() const
{
    return scheduler_.metrics();
}

void RecoveryTcpServer::accept_loop()
{
    while (running_.load()) {
        sockaddr_in peer{};
        socklen_t peer_size = sizeof(peer);
        const auto fd = accept(listen_fd_, reinterpret_cast<sockaddr*>(&peer), &peer_size);
        if (fd < 0) {
            if (running_.load()) continue;
            break;
        }
        auto connection = std::make_shared<Connection>(fd);
        {
            std::lock_guard lock(connections_mutex_);
            connections_.push_back(connection);
        }
        {
            std::lock_guard lock(stats_mutex_);
            ++stats_.connections;
        }
        client_threads_.emplace_back(&RecoveryTcpServer::client_loop, this, std::move(connection));
    }
}

void RecoveryTcpServer::client_loop(std::shared_ptr<Connection> connection)
{
    std::vector<uint8_t> stream;
    uint8_t buffer[4096];
    while (running_.load() && connection->open.load()) {
        const auto received = recv(connection->fd, buffer, sizeof(buffer), 0);
        if (received <= 0) break;
        stream.insert(stream.end(), buffer, buffer + received);
        while (true) {
            std::string error;
            const auto frame = RecoveryWireCodec::take_frame(stream, error);
            if (!error.empty()) {
                record_malformed();
                connection->open.store(false);
                break;
            }
            if (!frame) break;
            if (frame->type == RecoveryWireType::SNAPSHOT_REQUEST) {
                const auto sequence = RecoveryWireCodec::decode_snapshot_request(frame->payload, error);
                if (!sequence) {
                    record_malformed();
                    continue;
                }
                auto response = recovery_service_->send_snapshot(*sequence);
                auto payload = RecoveryWireCodec::encode_snapshot_response(response);
                if (payload.size() + 1 > RecoveryWireCodec::MAX_FRAME_BYTES) {
                    response = {RecoveryStatus::ERROR, {}, std::nullopt,
                        "snapshot response exceeds the maximum frame size"};
                    payload = RecoveryWireCodec::encode_snapshot_response(response);
                }
                const auto response_frame = RecoveryWireCodec::frame(
                    RecoveryWireType::SNAPSHOT_RESPONSE, payload);
                bool sent = false;
                {
                    std::lock_guard write_lock(connection->write_mutex);
                    if (connection->open.load()) sent = send_all(connection->fd, response_frame);
                }
                std::lock_guard stats_lock(stats_mutex_);
                if (sent) ++stats_.responses_sent;
                else ++stats_.send_failures;
                continue;
            }
            if (frame->type != RecoveryWireType::RECOVERY_REQUEST) {
                record_malformed();
                continue;
            }
            const auto request = RecoveryWireCodec::decode_request(frame->payload, error);
            if (!request) {
                record_malformed();
                continue;
            }
            if (!enqueue_request(connection, *request)) break;
        }
    }
    connection->open.store(false);
    close_socket(connection->fd);
}

bool RecoveryTcpServer::enqueue_request(std::shared_ptr<Connection> connection, RecoveryRequest request)
{
    if (!running_.load()) return false;
    request.request_id = next_request_id_.fetch_add(1);
    {
        std::lock_guard lock(pending_mutex_);
        pending_requests_.emplace(request.request_id, PendingRequest{std::move(connection)});
    }
    scheduler_.enqueue(std::move(request));
    {
        std::lock_guard lock(stats_mutex_);
        ++stats_.requests_received;
    }
    return true;
}

void RecoveryTcpServer::worker_loop()
{
    while (const auto scheduled = scheduler_.wait_next()) {
        PendingRequest pending;
        {
            std::lock_guard lock(pending_mutex_);
            const auto it = pending_requests_.find(scheduled->request.request_id);
            if (it == pending_requests_.end()) continue;
            pending = std::move(it->second);
            pending_requests_.erase(it);
        }
        auto response = recovery_service_->request_missing_data(scheduled->request);
        auto payload = RecoveryWireCodec::encode_response(response);
        if (payload.size() + 1 > RecoveryWireCodec::MAX_FRAME_BYTES) {
            response = {RecoveryStatus::ERROR, {}, std::nullopt,
                "recovery response exceeds the maximum frame size"};
            payload = RecoveryWireCodec::encode_response(response);
        }
        const auto bytes = RecoveryWireCodec::frame(RecoveryWireType::RECOVERY_RESPONSE, payload);
        bool sent = false;
        {
            std::lock_guard write_lock(pending.connection->write_mutex);
            if (pending.connection->open.load()) sent = send_all(pending.connection->fd, bytes);
        }
        scheduler_.record_completed(*scheduled,
            response.status == RecoveryStatus::OK ? response.events.size() : 0);
        std::lock_guard lock(stats_mutex_);
        if (sent) ++stats_.responses_sent;
        else ++stats_.send_failures;
    }
}

void RecoveryTcpServer::record_malformed()
{
    std::lock_guard lock(stats_mutex_);
    ++stats_.malformed_frames;
}

std::optional<RecoveryResponse> RecoveryTcpClient::request_missing_data(
    const RecoveryRequest& request, std::string& error) const
{
    error.clear();
    const auto fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        error = std::string("could not create recovery client socket: ") + std::strerror(errno);
        return std::nullopt;
    }
    timeval timeout{};
    timeout.tv_sec = static_cast<time_t>(endpoint_.timeout_ms / 1000);
    timeout.tv_usec = static_cast<suseconds_t>((endpoint_.timeout_ms % 1000) * 1000);
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(endpoint_.port);
    if (inet_pton(AF_INET, endpoint_.host.c_str(), &address.sin_addr) != 1) {
        error = "recovery endpoint must be an IPv4 numeric address";
        close_socket(fd);
        return std::nullopt;
    }
    if (connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0) {
        error = std::string("could not connect to recovery endpoint ") + endpoint_.host + ':'
            + std::to_string(endpoint_.port) + ": " + std::strerror(errno);
        close_socket(fd);
        return std::nullopt;
    }
    const auto bytes = RecoveryWireCodec::frame(RecoveryWireType::RECOVERY_REQUEST,
        RecoveryWireCodec::encode_request(request));
    if (!send_all(fd, bytes)) {
        error = std::string("could not send recovery request: ") + std::strerror(errno);
        close_socket(fd);
        return std::nullopt;
    }
    std::vector<uint8_t> stream;
    uint8_t buffer[4096];
    while (true) {
        const auto received = recv(fd, buffer, sizeof(buffer), 0);
        if (received <= 0) {
            error = std::string("recovery response was not received: ") + std::strerror(errno);
            close_socket(fd);
            return std::nullopt;
        }
        stream.insert(stream.end(), buffer, buffer + received);
        const auto frame = RecoveryWireCodec::take_frame(stream, error);
        if (!error.empty()) {
            close_socket(fd);
            return std::nullopt;
        }
        if (!frame) continue;
        if (frame->type != RecoveryWireType::RECOVERY_RESPONSE) {
            error = "unexpected recovery response frame";
            close_socket(fd);
            return std::nullopt;
        }
        const auto response = RecoveryWireCodec::decode_response(frame->payload, error);
        close_socket(fd);
        return response;
    }
}

std::optional<RecoveryResponse> RecoveryTcpClient::request_snapshot(
    const uint64_t minimum_sequence, std::string& error) const
{
    error.clear();
    const auto fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        error = std::string("could not create recovery client socket: ") + std::strerror(errno);
        return std::nullopt;
    }
    timeval timeout{};
    timeout.tv_sec = static_cast<time_t>(endpoint_.timeout_ms / 1000);
    timeout.tv_usec = static_cast<suseconds_t>((endpoint_.timeout_ms % 1000) * 1000);
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(endpoint_.port);
    if (inet_pton(AF_INET, endpoint_.host.c_str(), &address.sin_addr) != 1) {
        error = "recovery endpoint must be an IPv4 numeric address";
        close_socket(fd);
        return std::nullopt;
    }
    if (connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) < 0) {
        error = std::string("could not connect to recovery endpoint ") + endpoint_.host + ':'
            + std::to_string(endpoint_.port) + ": " + std::strerror(errno);
        close_socket(fd);
        return std::nullopt;
    }
    const auto bytes = RecoveryWireCodec::frame(RecoveryWireType::SNAPSHOT_REQUEST,
        RecoveryWireCodec::encode_snapshot_request(minimum_sequence));
    if (!send_all(fd, bytes)) {
        error = std::string("could not send snapshot request: ") + std::strerror(errno);
        close_socket(fd);
        return std::nullopt;
    }
    std::vector<uint8_t> stream;
    uint8_t buffer[4096];
    while (true) {
        const auto received = recv(fd, buffer, sizeof(buffer), 0);
        if (received <= 0) {
            error = std::string("snapshot response was not received: ") + std::strerror(errno);
            close_socket(fd);
            return std::nullopt;
        }
        stream.insert(stream.end(), buffer, buffer + received);
        const auto frame = RecoveryWireCodec::take_frame(stream, error);
        if (!error.empty()) {
            close_socket(fd);
            return std::nullopt;
        }
        if (!frame) continue;
        if (frame->type != RecoveryWireType::SNAPSHOT_RESPONSE) {
            error = "unexpected snapshot response frame";
            close_socket(fd);
            return std::nullopt;
        }
        const auto response = RecoveryWireCodec::decode_snapshot_response(frame->payload, error);
        close_socket(fd);
        return response;
    }
}

} // namespace mdfeed::reliability
