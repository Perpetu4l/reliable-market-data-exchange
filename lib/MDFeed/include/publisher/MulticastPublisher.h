#pragma once
#include <string>
#include <memory>

namespace mdfeed {
class MulticastSocket;

class MulticastPublisher {
public:
    MulticastPublisher();
    explicit MulticastPublisher(std::string_view multicast_ip, uint16_t port,
        std::string_view interface_ip = "127.0.0.1");
    ~MulticastPublisher();

    MulticastPublisher(const MulticastPublisher&) = delete;
    MulticastPublisher& operator=(const MulticastPublisher&) = delete;

    bool initialize(std::string_view multicast_ip, uint16_t port, std::string_view interface_ip = "127.0.0.1");
    void close();

    bool send(const void* data, std::size_t length) const;

    template<typename T>
    bool send_message(const T& message)
    {
        return send(&message, sizeof(T));
    }

    [[nodiscard]] bool is_initialized() const { return initialized_; }

private:
    std::unique_ptr<MulticastSocket> socket_;
    bool initialized_;
};
}
