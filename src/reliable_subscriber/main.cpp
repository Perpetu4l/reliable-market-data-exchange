#include "receiver/MulticastReceiver.h"
#include "reliability/DualFeed.h"
#include "reliability/RecoveryTcp.h"

#include <atomic>
#include <csignal>
#include <cstring>
#include <iostream>
#include <memory>
#include <string>

namespace {
using namespace mdfeed;
using namespace mdfeed::reliability;

std::atomic<bool> keep_running{true};
void on_signal(int) { keep_running.store(false); }

struct Config {
    ReceiverConfig feed_a;
    ReceiverConfig feed_b = [] {
        ReceiverConfig config;
        config.multicast_ip = "239.1.1.2";
        config.multicast_port = 10000;
        return config;
    }();
    bool dual_feed{false};
    RecoveryTcpClient::Endpoint recovery;
    std::string subscriber_id{"reliable-subscriber"};
};

void usage(const char* program)
{
    std::cout << "Usage: " << program << " [options]\n"
              << "  --feed-a-ip <group>       Feed A group (default: 239.1.1.1)\n"
              << "  --feed-a-port <port>      Feed A port (default: 9999)\n"
              << "  --dual-feed                Also join Feed B\n"
              << "  --feed-b-ip <group>       Feed B group (default: 239.1.1.2)\n"
              << "  --feed-b-port <port>      Feed B port (default: 10000)\n"
              << "  --interface <IPv4>        Local multicast interface (default: 127.0.0.1)\n"
              << "  --recovery-server <IPv4>  Exchange recovery server (default: 127.0.0.1)\n"
              << "  --recovery-port <port>    Recovery TCP port (default: 9090)\n"
              << "  --subscriber <id>         Recovery subscriber identity\n";
}

bool parse_args(int argc, char* argv[], Config& config)
{
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];
        if (argument == "--help" || argument == "-h") { usage(argv[0]); return false; }
        if (argument == "--dual-feed") { config.dual_feed = true; continue; }
        if (index + 1 >= argc) return false;
        const std::string value = argv[++index];
        if (argument == "--feed-a-ip") config.feed_a.multicast_ip = value;
        else if (argument == "--feed-a-port") config.feed_a.multicast_port = static_cast<uint16_t>(std::stoul(value));
        else if (argument == "--feed-b-ip") config.feed_b.multicast_ip = value;
        else if (argument == "--feed-b-port") config.feed_b.multicast_port = static_cast<uint16_t>(std::stoul(value));
        else if (argument == "--interface") { config.feed_a.interface_ip = value; config.feed_b.interface_ip = value; }
        else if (argument == "--recovery-server") config.recovery.host = value;
        else if (argument == "--recovery-port") config.recovery.port = static_cast<uint16_t>(std::stoul(value));
        else if (argument == "--subscriber") config.subscriber_id = value;
        else return false;
    }
    // Dual reconciliation owns sequencing; disable each single-feed receiver's
    // independent gap counter to avoid misleading duplicate warnings.
    config.feed_a.enable_logging = false;
    config.feed_b.enable_logging = false;
    config.feed_a.validate_sequence_numbers = false;
    config.feed_b.validate_sequence_numbers = false;
    return true;
}

MarketDataEvent copy_event(const MessageHeader& header, const void* data, const size_t length)
{
    MarketDataEvent event;
    event.header = header;
    const auto* bytes = static_cast<const uint8_t*>(data);
    event.payload.assign(bytes, bytes + length);
    return event;
}
} // namespace

int main(const int argc, char* argv[])
{
    Config config;
    if (!parse_args(argc, argv, config)) {
        if (argc == 2 && (std::string(argv[1]) == "--help" || std::string(argv[1]) == "-h")) return 0;
        usage(argv[0]);
        return 1;
    }
    std::signal(SIGINT, on_signal);
    std::signal(SIGTERM, on_signal);

    std::atomic<uint64_t> delivered{0};
    std::atomic<uint64_t> recovery_requests{0};
    std::atomic<uint64_t> recovery_failures{0};
    std::unique_ptr<DualFeedSubscriber> subscriber;
    subscriber = std::make_unique<DualFeedSubscriber>(
        [&](const MarketDataEvent& event) {
            ++delivered;
            std::cout << "DELIVERED seq=" << event.header.sequence_number
                      << " feed=" << (event.feed_id == FeedId::A ? 'A' : 'B')
                      << " type=" << event.header.message_type << '\n';
        },
        [&](const MissingRange range) {
            ++recovery_requests;
            RecoveryTcpClient client(config.recovery);
            std::string error;
            const auto response = client.request_missing_data({config.subscriber_id, range}, error);
            if (!response || response->status != RecoveryStatus::OK) {
                ++recovery_failures;
                std::cerr << "RECOVERY FAILED range=" << range.start_sequence << '-'
                          << range.end_sequence << ": "
                          << (!response ? error : response->detail) << '\n';
                return;
            }
            std::cout << "RECOVERY OK range=" << range.start_sequence << '-'
                      << range.end_sequence << " events=" << response->events.size() << '\n';
            for (auto event : response->events) subscriber->on_recovered_event(std::move(event));
        });

    MulticastReceiver receiver_a(config.feed_a);
    receiver_a.set_message_handler([&](const MessageHeader& header, const void* data, const size_t length) {
        if (static_cast<MessageType>(header.message_type) == MessageType::HEARTBEAT) {
            subscriber->on_heartbeat(header.sequence_number);
        } else {
            subscriber->on_packet(FeedId::A, copy_event(header, data, length));
        }
    });
    std::unique_ptr<MulticastReceiver> receiver_b;
    if (config.dual_feed) {
        receiver_b = std::make_unique<MulticastReceiver>(config.feed_b);
        receiver_b->set_message_handler([&](const MessageHeader& header, const void* data, const size_t length) {
            if (static_cast<MessageType>(header.message_type) == MessageType::HEARTBEAT) {
                subscriber->on_heartbeat(header.sequence_number);
            } else {
                subscriber->on_packet(FeedId::B, copy_event(header, data, length));
            }
        });
    }
    if (!receiver_a.start() || (receiver_b && !receiver_b->start())) {
        std::cerr << "Could not start multicast receiver(s); check group, port, interface, and OS permissions.\n";
        receiver_a.stop();
        if (receiver_b) receiver_b->stop();
        return 1;
    }
    std::cout << "Reliable subscriber started: Feed A=" << config.feed_a.multicast_ip << ':'
              << config.feed_a.multicast_port;
    if (receiver_b) std::cout << " Feed B=" << config.feed_b.multicast_ip << ':' << config.feed_b.multicast_port;
    std::cout << " recovery=" << config.recovery.host << ':' << config.recovery.port << '\n';

    while (keep_running.load()) std::this_thread::sleep_for(std::chrono::milliseconds(100));
    receiver_a.stop();
    if (receiver_b) receiver_b->stop();
    std::cout << "SUMMARY delivered=" << delivered << " duplicates="
              << subscriber->sequence_tracker().duplicate_count() << " gaps="
              << subscriber->sequence_tracker().gap_count() << " recovery_requests="
              << recovery_requests << " recovery_failures=" << recovery_failures << '\n';
    return 0;
}
