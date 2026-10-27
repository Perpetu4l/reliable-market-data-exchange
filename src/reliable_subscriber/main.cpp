#include "receiver/MulticastReceiver.h"
#include "reliability/DualFeed.h"
#include "reliability/RecoveryTcp.h"
#include "reliability/BookBuilder.h"
#include "reliability/Snapshot.h"
#include "reliability/FaultInjector.h"
#include "reliability/Metrics.h"

#include <atomic>
#include <csignal>
#include <cstring>
#include <iostream>
#include <memory>
#include <string>
#include <optional>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <thread>
#include <algorithm>
#include <unordered_set>

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
    uint64_t gap_grace_ms{2};
    bool no_recovery{false};
    std::string loss_model{"none"};
    double drop_a{0.0};
    double drop_b{0.0};
    double correlated_loss{0.0};
    uint64_t drop_seed{42};
    bool verbose_deliveries{false};
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
              << "  --run-seconds <N>         Exit after N seconds (0 waits for Ctrl+C)\n"
              << "  --gap-grace-ms <N>        Wait for the other feed before recovery (default: 2)\n"
              << "  --no-recovery             Observe gaps without TCP recovery (negative control)\n"
              << "  --loss-model <name>       none|independent|correlated (default: none)\n"
              << "  --drop-a <rate>           Receive-side Feed A application loss [0,1]\n"
              << "  --drop-b <rate>           Receive-side Feed B application loss [0,1]\n"
              << "  --corr-loss <rate>        Receive-side correlated application loss [0,1]\n"
              << "  --drop-seed <N>           Deterministic receive-side fault seed (default: 42)\n"
              << "  --verbose-deliveries      Print every applied event (off by default)\n";
}

bool parse_args(int argc, char* argv[], Config& config)
{
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];
        if (argument == "--help" || argument == "-h") { usage(argv[0]); return false; }
        if (argument == "--dual-feed") { config.dual_feed = true; continue; }
        if (argument == "--no-recovery") { config.no_recovery = true; continue; }
        if (argument == "--verbose-deliveries") { config.verbose_deliveries = true; continue; }
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
        else if (argument == "--gap-grace-ms") config.gap_grace_ms = std::stoull(value);
        else if (argument == "--loss-model") config.loss_model = value;
        else if (argument == "--drop-a") config.drop_a = std::stod(value);
        else if (argument == "--drop-b") config.drop_b = std::stod(value);
        else if (argument == "--corr-loss") config.correlated_loss = std::stod(value);
        else if (argument == "--drop-seed") config.drop_seed = std::stoull(value);
        else return false;
    }
    // Dual reconciliation owns sequencing; disable each single-feed receiver's
    // independent gap counter to avoid misleading duplicate warnings.
    config.feed_a.enable_logging = false;
    config.feed_b.enable_logging = false;
    config.feed_a.validate_sequence_numbers = false;
    config.feed_b.validate_sequence_numbers = false;
    return (config.loss_model == "none" || config.loss_model == "independent" || config.loss_model == "correlated")
        && config.drop_a >= 0.0 && config.drop_a <= 1.0
        && config.drop_b >= 0.0 && config.drop_b <= 1.0
        && config.correlated_loss >= 0.0 && config.correlated_loss <= 1.0;
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
    std::atomic<uint64_t> recovered_events{0};
    std::atomic<uint64_t> recovery_failures{0};
    std::atomic<uint64_t> snapshot_recoveries{0};
    // Snapshot coverage is reported separately from individually delivered
    // events, so a run does not claim that it received every event over UDP.
    std::atomic<uint64_t> events_covered_by_snapshot{0};
    std::atomic<uint64_t> datagrams_received_a{0};
    std::atomic<uint64_t> datagrams_received_b{0};
    std::atomic<uint64_t> dropped_by_injector_a{0};
    std::atomic<uint64_t> dropped_by_injector_b{0};
    std::mutex accounting_mutex;
    std::unordered_set<uint64_t> individually_delivered_sequences;
    std::unordered_set<uint64_t> snapshot_covered_sequences;
    std::mutex local_book_mutex;
    std::mutex recovery_mutex;
    std::condition_variable recovery_ready;
    std::condition_variable recovery_idle;
    std::deque<MissingRange> recovery_queue;
    uint64_t recovery_scheduled_through{0};
    std::atomic<bool> recovery_worker_running{true};
    uint64_t recovery_tasks_in_flight{0}; // protected by recovery_mutex
    BookBuilder local_book;
    SnapshotHasher snapshot_hasher;
    MetricsCollector recovery_metrics;
    FaultInjector receive_fault_a({config.drop_a, 0.0, 0.0, {}, config.drop_seed});
    FaultInjector receive_fault_b({config.drop_b, 0.0, 0.0, {}, config.drop_seed + 1});
    FaultInjector receive_fault_correlated({config.correlated_loss, 0.0, 0.0, {}, config.drop_seed});
    std::unique_ptr<DualFeedSubscriber> subscriber;
    subscriber = std::make_unique<DualFeedSubscriber>(
        [&](const MarketDataEvent& event) {
            std::lock_guard lock(local_book_mutex);
            (void)local_book.apply(event);
            {
                std::lock_guard accounting_lock(accounting_mutex);
                if (individually_delivered_sequences.insert(event.header.sequence_number).second) ++delivered;
            }
            if (config.verbose_deliveries) {
                std::cout << "DELIVERED seq=" << event.header.sequence_number
                          << " feed=" << (event.feed_id == FeedId::A ? 'A' : 'B')
                          << " type=" << event.header.message_type << '\n';
            }
        },
        [&](const MissingRange range) {
            if (config.no_recovery) return;
            std::lock_guard lock(recovery_mutex);
            const auto start = std::max(range.start_sequence, recovery_scheduled_through + 1);
            if (start > range.end_sequence) return;
            recovery_queue.push_back({start, range.end_sequence});
            recovery_scheduled_through = range.end_sequence;
            recovery_ready.notify_one();
        });

    std::thread recovery_worker([&] {
        while (true) {
            MissingRange range;
            {
                std::unique_lock lock(recovery_mutex);
                recovery_ready.wait(lock, [&] { return !recovery_worker_running.load() || !recovery_queue.empty(); });
                if (!recovery_worker_running.load() && recovery_queue.empty()) break;
                range = recovery_queue.front();
                recovery_queue.pop_front();
                ++recovery_tasks_in_flight;
            }
            const auto finish_task = [&] {
                std::lock_guard lock(recovery_mutex);
                --recovery_tasks_in_flight;
                if (recovery_queue.empty() && recovery_tasks_in_flight == 0) recovery_idle.notify_all();
            };

            std::this_thread::sleep_for(std::chrono::milliseconds(config.gap_grace_ms));
            range.start_sequence = std::max(range.start_sequence, subscriber->expected_sequence());
            if (!range.valid()) {
                finish_task(); // Feed B filled the range during grace.
                continue;
            }

            ++recovery_requests;
            const auto recovery_started = std::chrono::steady_clock::now();
            RecoveryTcpClient client(config.recovery);
            std::string error;
            const auto response = client.request_missing_data({config.subscriber_id, range}, error);
            if (response && response->status == RecoveryStatus::OK) {
                std::cout << "RECOVERY OK range=" << range.start_sequence << '-'
                          << range.end_sequence << " events=" << response->events.size() << '\n';
                for (auto event : response->events) subscriber->on_recovered_event(std::move(event));
                recovered_events += response->events.size();
                recovery_metrics.record_recovery_latency(static_cast<uint64_t>(
                    std::chrono::duration_cast<std::chrono::nanoseconds>(
                        std::chrono::steady_clock::now() - recovery_started).count()));
            } else if (response && response->status == RecoveryStatus::TOO_OLD) {
                const auto snapshot_response = client.request_snapshot(range.end_sequence, error);
                if (snapshot_response && snapshot_response->status == RecoveryStatus::OK
                    && snapshot_response->snapshot
                    && snapshot_response->snapshot->sequence_number >= range.end_sequence
                    && snapshot_hasher.verify(*snapshot_response->snapshot)) {
                    const auto covered_from = subscriber->expected_sequence();
                    {
                        std::lock_guard lock(local_book_mutex);
                        local_book.apply_snapshot(*snapshot_response->snapshot);
                    }
                    subscriber->resynchronise_after_snapshot(snapshot_response->snapshot->sequence_number);
                        if (snapshot_response->snapshot->sequence_number >= covered_from) {
                            uint64_t newly_covered = 0;
                            std::lock_guard accounting_lock(accounting_mutex);
                            for (uint64_t sequence = covered_from;
                                 sequence <= snapshot_response->snapshot->sequence_number; ++sequence) {
                                if (!individually_delivered_sequences.contains(sequence)
                                    && snapshot_covered_sequences.insert(sequence).second) {
                                    ++newly_covered;
                                }
                            }
                            events_covered_by_snapshot += newly_covered;
                    }
                    ++snapshot_recoveries;
                    recovery_metrics.record_recovery_latency(static_cast<uint64_t>(
                        std::chrono::duration_cast<std::chrono::nanoseconds>(
                            std::chrono::steady_clock::now() - recovery_started).count()));
                    std::cout << "SNAPSHOT RECOVERY OK checkpoint="
                              << snapshot_response->snapshot->sequence_number << " hash="
                              << snapshot_response->snapshot->hash << '\n';
                } else {
                    ++recovery_failures;
                    std::cerr << "SNAPSHOT RECOVERY FAILED range=" << range.start_sequence << '-'
                              << range.end_sequence << ": "
                              << (!snapshot_response ? error : snapshot_response->detail) << '\n';
                }
            } else {
                ++recovery_failures;
                std::cerr << "RECOVERY FAILED range=" << range.start_sequence << '-'
                          << range.end_sequence << ": "
                          << (!response ? error : response->detail) << '\n';
            }
            finish_task();
        }
    });

    MulticastReceiver receiver_a(config.feed_a);
    receiver_a.set_message_handler([&](const MessageHeader& header, const void* data, const size_t length) {
        ++datagrams_received_a;
        if (static_cast<MessageType>(header.message_type) == MessageType::HEARTBEAT) {
            subscriber->on_heartbeat(header.sequence_number);
        } else {
            auto event = copy_event(header, data, length);
            const bool drop = config.loss_model == "correlated"
                ? receive_fault_correlated.drops(event)
                : config.loss_model == "independent" && receive_fault_a.drops(event);
            if (drop) { ++dropped_by_injector_a; return; }
            subscriber->on_packet(FeedId::A, std::move(event));
        }
    });
    std::unique_ptr<MulticastReceiver> receiver_b;
    if (config.dual_feed) {
        receiver_b = std::make_unique<MulticastReceiver>(config.feed_b);
        receiver_b->set_message_handler([&](const MessageHeader& header, const void* data, const size_t length) {
            ++datagrams_received_b;
            if (static_cast<MessageType>(header.message_type) == MessageType::HEARTBEAT) {
                subscriber->on_heartbeat(header.sequence_number);
            } else {
                auto event = copy_event(header, data, length);
                const bool drop = config.loss_model == "correlated"
                    ? receive_fault_correlated.drops(event)
                    : config.loss_model == "independent" && receive_fault_b.drops(event);
                if (drop) { ++dropped_by_injector_b; return; }
                subscriber->on_packet(FeedId::B, std::move(event));
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
    const auto measurement_started = std::chrono::steady_clock::now();

    const auto deadline = config.run_seconds == 0
        ? std::optional<std::chrono::steady_clock::time_point>{}
        : std::optional{std::chrono::steady_clock::now() + std::chrono::seconds(config.run_seconds)};
    while (keep_running.load() && (!deadline || std::chrono::steady_clock::now() < *deadline)) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    // Stop UDP before choosing the final cut sequence. Otherwise events can
    // arrive during receiver shutdown after STATUS was sampled, making an
    // apparently impossible delivered > expected result.
    receiver_a.stop();
    if (receiver_b) receiver_b->stop();

    // Final reconciliation closes the tail-loss hole. The exchange may still
    // be publishing, so require two equal STATUS observations before treating
    // its latest sequence as the measurement cut.
    uint64_t exchange_latest_sequence = 0;
    bool status_reconciled = false;
    {
        RecoveryTcpClient client(config.recovery);
        std::optional<RecoveryStatusInfo> previous;
        for (int attempt = 0; attempt < 5; ++attempt) {
            std::string status_error;
            const auto status = client.request_status(status_error);
            if (!status) {
                std::cerr << "STATUS RECONCILIATION FAILED: " << status_error << '\n';
                break;
            }
            if (previous && previous->latest_sequence == status->latest_sequence
                && previous->history_minimum_sequence == status->history_minimum_sequence) {
                exchange_latest_sequence = status->latest_sequence;
                status_reconciled = true;
                break;
            }
            previous = status;
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }
    }
    if (status_reconciled) subscriber->on_heartbeat(exchange_latest_sequence);
    // A result is emitted only after queued repair work has settled.  This
    // makes a timeout visible instead of silently dropping the final range.
    {
        std::unique_lock lock(recovery_mutex);
        if (!recovery_idle.wait_for(lock, std::chrono::seconds(10), [&] {
                return recovery_queue.empty() && recovery_tasks_in_flight == 0;
            })) {
            ++recovery_failures;
            std::cerr << "RECOVERY DRAIN FAILED: queued recovery did not finish within 10 seconds\n";
        }
    }
    recovery_worker_running.store(false);
    recovery_ready.notify_all();
    recovery_worker.join();
    const auto runtime_ns = static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now() - measurement_started).count());
    const auto latency_summary = recovery_metrics.snapshot().recovery_latency;
    const auto delivered_events_per_second = runtime_ns == 0 ? 0.0
        : static_cast<double>(delivered.load()) * 1'000'000'000.0 / static_cast<double>(runtime_ns);
    uint64_t accounted_events = 0;
    uint64_t permanently_lost = 0;
    bool accounting_valid = status_reconciled;
    if (status_reconciled) {
        std::lock_guard lock(accounting_mutex);
        for (uint64_t sequence = 1; sequence <= exchange_latest_sequence; ++sequence) {
            const bool individually_delivered = individually_delivered_sequences.contains(sequence);
            const bool snapshot_covered = snapshot_covered_sequences.contains(sequence);
            if (individually_delivered && snapshot_covered) {
                accounting_valid = false;
                continue;
            }
            if (individually_delivered || snapshot_covered) ++accounted_events;
            else ++permanently_lost;
        }
        accounting_valid = accounting_valid && accounted_events + permanently_lost == exchange_latest_sequence
            && delivered.load() <= exchange_latest_sequence;
    }
    const bool unresolved_gap = !status_reconciled
        || subscriber->pending_event_count() != 0
        || (config.no_recovery && subscriber->sequence_tracker().gap_count() != 0)
        || recovery_failures.load() != 0
        || permanently_lost != 0;
    const std::string run_status = !status_reconciled ? "failed_not_quiescent"
        : !accounting_valid ? "failed_accounting"
        : unresolved_gap ? "failed_integrity" : "completed";
    std::cout << "SUMMARY delivered=" << delivered << " duplicates="
              << subscriber->sequence_tracker().duplicate_count() << " gaps="
              << subscriber->sequence_tracker().gap_count() << " recovery_requests="
              << recovery_requests << " snapshot_recoveries=" << snapshot_recoveries
              << " snapshot_covered=" << events_covered_by_snapshot
              << " exchange_latest=" << exchange_latest_sequence
              << " accounted=" << accounted_events
              << " permanently_lost=" << permanently_lost
              << " runtime_ms=" << runtime_ns / 1'000'000
              << " delivered_events_per_second=" << delivered_events_per_second
              << " recovery_failures=" << recovery_failures << '\n';
    std::cout << "RELIABLE_SUBSCRIBER_RESULT delivered=" << delivered
              << " duplicates=" << subscriber->sequence_tracker().duplicate_count()
              << " gaps=" << subscriber->sequence_tracker().gap_count()
              << " recovery_requests=" << recovery_requests
              << " recovered_events=" << recovered_events
              << " snapshot_recoveries=" << snapshot_recoveries
              << " events_covered_by_snapshot=" << events_covered_by_snapshot
              << " exchange_latest_sequence=" << exchange_latest_sequence
              << " events_expected=" << exchange_latest_sequence
              << " accounting_valid=" << (accounting_valid ? 1 : 0)
              << " permanently_lost=" << permanently_lost
              << " runtime_ms=" << runtime_ns / 1'000'000
              << " delivered_events_per_second=" << delivered_events_per_second
              << " recovery_p50_ns=" << latency_summary.p50_ns
              << " recovery_p95_ns=" << latency_summary.p95_ns
              << " recovery_p99_ns=" << latency_summary.p99_ns
              << " recovery_failures=" << recovery_failures
              << " gap_grace_ms=" << config.gap_grace_ms
              << " datagrams_received_a=" << datagrams_received_a
              << " datagrams_received_b=" << datagrams_received_b
              << " dropped_by_injector_a=" << dropped_by_injector_a
              << " dropped_by_injector_b=" << dropped_by_injector_b
              << " loss_model=" << config.loss_model
              << " run_status=" << run_status
              << " feed_mode=" << (config.dual_feed ? "dual" : "single")
              << " recovery_server=" << config.recovery.host << ':' << config.recovery.port << '\n';
    if (run_status == "completed") return 0;
    if (run_status == "failed_accounting") return 3;
    if (run_status == "failed_not_quiescent") return 4;
    return 2;
}
