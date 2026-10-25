#include "receiver/MulticastReceiver.h"
#include "reliability/DualFeed.h"
#include "reliability/RecoveryTcp.h"
#include "reliability/BookBuilder.h"
#include "reliability/Snapshot.h"

#include <atomic>
#include <csignal>
#include <cstring>
#include <iostream>
#include <memory>
#include <string>
#include <optional>

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
    uint64_t run_seconds{0};
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
              << "  --subscriber <id>         Recovery subscriber identity\n"
              << "  --run-seconds <N>         Exit after N seconds (0 waits for Ctrl+C)\n";
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
        else if (argument == "--run-seconds") config.run_seconds = std::stoull(value);
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
    std::atomic<uint64_t> snapshot_recoveries{0};
    BookBuilder local_book;
    SnapshotHasher snapshot_hasher;
    std::unique_ptr<DualFeedSubscriber> subscriber;
    subscriber = std::make_unique<DualFeedSubscriber>(
        [&](const MarketDataEvent& event) {
            (void)local_book.apply(event);
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
            if (response && response->status == RecoveryStatus::OK) {
                std::cout << "RECOVERY OK range=" << range.start_sequence << '-'
                          << range.end_sequence << " events=" << response->events.size() << '\n';
                for (auto event : response->events) subscriber->on_recovered_event(std::move(event));
                return;
            }
            if (response && response->status == RecoveryStatus::TOO_OLD) {
                const auto snapshot_response = client.request_snapshot(range.end_sequence, error);
                if (snapshot_response && snapshot_response->status == RecoveryStatus::OK
                    && snapshot_response->snapshot && snapshot_hasher.verify(*snapshot_response->snapshot)) {
                    local_book.apply_snapshot(*snapshot_response->snapshot);
                    subscriber->resynchronise_after_snapshot(
                        snapshot_response->snapshot->sequence_number);
                    ++snapshot_recoveries;
                    std::cout << "SNAPSHOT RECOVERY OK checkpoint="
                              << snapshot_response->snapshot->sequence_number << " hash="
                              << snapshot_response->snapshot->hash << '\n';
                    return;
                }
                ++recovery_failures;
                std::cerr << "SNAPSHOT RECOVERY FAILED range=" << range.start_sequence << '-'
                          << range.end_sequence << ": "
                          << (!snapshot_response ? error : snapshot_response->detail) << '\n';
                return;
            }
            {
                ++recovery_failures;
                std::cerr << "RECOVERY FAILED range=" << range.start_sequence << '-'
                          << range.end_sequence << ": "
                          << (!response ? error : response->detail) << '\n';
                return;
            }
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

    const auto deadline = config.run_seconds == 0
        ? std::optional<std::chrono::steady_clock::time_point>{}
        : std::optional{std::chrono::steady_clock::now() + std::chrono::seconds(config.run_seconds)};
    while (keep_running.load() && (!deadline || std::chrono::steady_clock::now() < *deadline)) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    receiver_a.stop();
    if (receiver_b) receiver_b->stop();
    std::cout << "SUMMARY delivered=" << delivered << " duplicates="
              << subscriber->sequence_tracker().duplicate_count() << " gaps="
              << subscriber->sequence_tracker().gap_count() << " recovery_requests="
              << recovery_requests << " snapshot_recoveries=" << snapshot_recoveries
              << " recovery_failures=" << recovery_failures << '\n';
    std::cout << "RELIABLE_SUBSCRIBER_RESULT delivered=" << delivered
              << " duplicates=" << subscriber->sequence_tracker().duplicate_count()
              << " gaps=" << subscriber->sequence_tracker().gap_count()
              << " recovery_requests=" << recovery_requests
              << " snapshot_recoveries=" << snapshot_recoveries
              << " recovery_failures=" << recovery_failures
              << " feed_mode=" << (config.dual_feed ? "dual" : "single")
              << " recovery_server=" << config.recovery.host << ':' << config.recovery.port << '\n';
    return 0;
}
