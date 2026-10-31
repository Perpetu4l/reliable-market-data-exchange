#include "receiver/MulticastReceiver.h"
#include "reliability/DualFeed.h"
#include "reliability/RecoveryTcp.h"
#include "reliability/BookBuilder.h"
#include "reliability/Snapshot.h"
#include "reliability/FaultInjector.h"
#include "reliability/Metrics.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <csignal>
#include <cstring>
#include <deque>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace {
using namespace mdfeed;
using namespace mdfeed::reliability;

std::atomic<bool> keep_running{true};
void on_signal(int) { keep_running.store(false); }

uint64_t steady_now_ns()
{
    return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
}

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
    std::string run_id{"manual"};
    uint64_t run_seconds{0};
    uint64_t stop_when_idle_ms{0};
    uint64_t until_seq{0};
    uint64_t gap_grace_ms{2};
    uint64_t stats_interval_ms{500};
    size_t recovery_concurrency{1};
    size_t recovery_retries{5};
    uint64_t recovery_retry_base_ms{20};
    bool no_recovery{false};
    std::string loss_model{"none"};
    double drop_a{0.0};
    double drop_b{0.0};
    double correlated_loss{0.0};
    double duplication_rate{0.0};
    double reorder_rate{0.0};
    size_t reorder_depth{1};
    uint64_t delay_ms{0};
    uint64_t drop_seed{42};
    std::string csv_path{};
    std::string latency_csv_path{};
    bool verbose_deliveries{false};
};

void usage(const char* program)
{
    std::cout << "Usage: " << program << " [options]\n"
              << "  --feed-a-ip <group>             Feed A group (default: 239.1.1.1)\n"
              << "  --feed-a-port <port>            Feed A port (default: 9999)\n"
              << "  --dual-feed                     Also join Feed B\n"
              << "  --feed-b-ip <group>             Feed B group (default: 239.1.1.2)\n"
              << "  --feed-b-port <port>            Feed B port (default: 10000)\n"
              << "  --interface <IPv4>              Local multicast interface (default: 127.0.0.1)\n"
              << "  --recovery-server <IPv4>        Exchange recovery server (default: 127.0.0.1)\n"
              << "  --recovery-port <port>          Recovery TCP port (default: 9090)\n"
              << "  --subscriber <id>               Recovery subscriber identity\n"
              << "  --run-id <id>                   Run identifier for CSV outputs\n"
              << "  --run-seconds <N>               Exit after N seconds (0 waits for stop condition)\n"
              << "  --stop-when-idle-ms <X>         Stop when exchange sequence is idle for X ms and caught up\n"
              << "  --until-seq <N>                 Stop once local book has applied through sequence N\n"
              << "  --gap-grace-ms <N>              Wait for the other feed before recovery (default: 2)\n"
              << "  --recovery-concurrency <K>      Number of concurrent TCP recovery workers (default: 1)\n"
              << "  --recovery-retries <N>          Max retries per recovery range with backoff (default: 5)\n"
              << "  --recovery-retry-base-ms <ms>   Base backoff in ms for retries (default: 20)\n"
              << "  --no-recovery                   Observe gaps without TCP recovery (negative control)\n"
              << "  --loss-model <name>             none|independent|correlated (default: none)\n"
              << "  --drop-a <rate>                 Receive-side Feed A application loss [0,1]\n"
              << "  --drop-b <rate>                 Receive-side Feed B application loss [0,1]\n"
              << "  --corr-loss <rate>              Receive-side correlated application loss [0,1]\n"
              << "  --dup <rate>                    Receive-side packet duplication rate [0,1]\n"
              << "  --reorder <rate>                Receive-side packet reorder rate [0,1]\n"
              << "  --reorder-depth <K>             Receive-side reorder hold-back depth (default: 1)\n"
              << "  --delay-ms <D>                  Receive-side hold-back delay in ms (default: 0)\n"
              << "  --drop-seed <N>                 Deterministic receive-side fault seed (default: 42)\n"
              << "  --stats-interval-ms <ms>        Emit SUBSCRIBER_STATS every N ms (default: 500; 0 disables)\n"
              << "  --csv <path>                    Write one-row summary CSV\n"
              << "  --latency-csv <path>            Write per-missing-event latency samples CSV\n"
              << "  --verbose-deliveries            Print every applied event (off by default)\n";
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
        else if (argument == "--run-id") config.run_id = value;
        else if (argument == "--run-seconds") config.run_seconds = std::stoull(value);
        else if (argument == "--stop-when-idle-ms") config.stop_when_idle_ms = std::stoull(value);
        else if (argument == "--until-seq") config.until_seq = std::stoull(value);
        else if (argument == "--gap-grace-ms") config.gap_grace_ms = std::stoull(value);
        else if (argument == "--recovery-concurrency") config.recovery_concurrency = std::max<size_t>(1, std::stoull(value));
        else if (argument == "--recovery-retries") config.recovery_retries = std::stoull(value);
        else if (argument == "--recovery-retry-base-ms") config.recovery_retry_base_ms = std::max<uint64_t>(1, std::stoull(value));
        else if (argument == "--loss-model") config.loss_model = value;
        else if (argument == "--drop-a") config.drop_a = std::stod(value);
        else if (argument == "--drop-b") config.drop_b = std::stod(value);
        else if (argument == "--corr-loss") config.correlated_loss = std::stod(value);
        else if (argument == "--dup") config.duplication_rate = std::stod(value);
        else if (argument == "--reorder") config.reorder_rate = std::stod(value);
        else if (argument == "--reorder-depth") config.reorder_depth = std::stoull(value);
        else if (argument == "--delay-ms") config.delay_ms = std::stoull(value);
        else if (argument == "--drop-seed") config.drop_seed = std::stoull(value);
        else if (argument == "--stats-interval-ms") config.stats_interval_ms = std::stoull(value);
        else if (argument == "--csv") config.csv_path = value;
        else if (argument == "--latency-csv") config.latency_csv_path = value;
        else return false;
    }
    config.feed_a.enable_logging = false;
    config.feed_b.enable_logging = false;
    config.feed_a.validate_sequence_numbers = false;
    config.feed_b.validate_sequence_numbers = false;
    return (config.loss_model == "none" || config.loss_model == "independent" || config.loss_model == "correlated")
        && config.drop_a >= 0.0 && config.drop_a <= 1.0
        && config.drop_b >= 0.0 && config.drop_b <= 1.0
        && config.correlated_loss >= 0.0 && config.correlated_loss <= 1.0
        && config.duplication_rate >= 0.0 && config.duplication_rate <= 1.0
        && config.reorder_rate >= 0.0 && config.reorder_rate <= 1.0;
}

MarketDataEvent copy_event(const MessageHeader& header, const void* data, const size_t length)
{
    MarketDataEvent event;
    event.header = header;
    const auto* bytes = static_cast<const uint8_t*>(data);
    event.payload.assign(bytes, bytes + length);
    return event;
}

std::string detect_transport(const std::string& interface_ip)
{
    if (interface_ip == "127.0.0.1" || interface_ip == "localhost" || interface_ip.starts_with("127.")) {
        return "udp_loopback";
    }
    return "udp_lan";
}

std::string detect_loss_injection(const Config& config)
{
    const bool any_receive_impairment = (config.loss_model != "none"
        && (config.drop_a > 0.0 || config.drop_b > 0.0 || config.correlated_loss > 0.0))
        || config.duplication_rate > 0.0
        || config.reorder_rate > 0.0
        || config.delay_ms > 0;
    return any_receive_impairment ? "app_level_receive_side" : "none";
}

std::string format_double_or_na(const std::optional<double>& val)
{
    if (!val.has_value()) return "n/a";
    std::ostringstream oss;
    oss << std::fixed << std::setprecision(6) << *val;
    return oss.str();
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
    std::atomic<uint64_t> gap_ranges_requested{0};
    std::atomic<uint64_t> recovery_requests{0};
    std::atomic<uint64_t> recovery_retries{0};
    std::atomic<uint64_t> recovered_events{0};
    std::atomic<uint64_t> too_old_responses{0};
    std::atomic<uint64_t> snapshot_recoveries{0};
    std::atomic<uint64_t> snapshot_rejected{0};
    std::atomic<uint64_t> recovery_failures{0};
    std::atomic<uint64_t> datagrams_received_a{0};
    std::atomic<uint64_t> datagrams_received_b{0};
    std::atomic<uint64_t> last_datagram_ns_a{0};
    std::atomic<uint64_t> last_datagram_ns_b{0};

    std::mutex local_book_mutex;
    BookBuilder local_book;
    SnapshotHasher snapshot_hasher;
    std::mutex rtt_mutex;
    MetricsCollector tcp_rtt_collector;

    std::mutex recovery_mutex;
    std::condition_variable recovery_ready;
    std::condition_variable recovery_idle;
    std::deque<MissingRange> recovery_queue;
    uint64_t recovery_scheduled_through{0};
    std::atomic<bool> recovery_worker_running{true};
    uint64_t recovery_tasks_in_flight{0};

    // Configure receive-side impairment stages for Feed A and Feed B.
    const double eff_loss_a = config.loss_model == "correlated" ? config.correlated_loss
        : (config.loss_model == "independent" ? config.drop_a : 0.0);
    const double eff_loss_b = config.loss_model == "correlated" ? config.correlated_loss
        : (config.loss_model == "independent" ? config.drop_b : 0.0);
    const uint64_t seed_a = config.drop_seed;
    const uint64_t seed_b = config.loss_model == "correlated" ? config.drop_seed : (config.drop_seed + 1);

    ImpairmentStage impairment_a({
        eff_loss_a, config.duplication_rate, config.reorder_rate,
        std::chrono::milliseconds(config.delay_ms), seed_a, config.reorder_depth});
    ImpairmentStage impairment_b({
        eff_loss_b, config.duplication_rate, config.reorder_rate,
        std::chrono::milliseconds(config.delay_ms), seed_b, config.reorder_depth});

    std::unique_ptr<DualFeedSubscriber> subscriber;

    // When concurrency > 1, split large missing ranges into chunks so that a
    // noisy subscriber with --recovery-concurrency K can enqueue up to K
    // concurrent requests and create genuine scheduler contention.
    const auto enqueue_missing_range = [&](const MissingRange range, const bool force_enqueue) {
        if (config.no_recovery || !range.valid()) return;
        std::lock_guard lock(recovery_mutex);
        const uint64_t start = force_enqueue
            ? range.start_sequence
            : std::max(range.start_sequence, recovery_scheduled_through + 1);
        if (start > range.end_sequence) return;

        const uint64_t span = range.end_sequence - start + 1;
        const uint64_t max_slices = std::max<uint64_t>(1, config.recovery_concurrency);
        const uint64_t slice_size = (max_slices > 1 && span > 1)
            ? std::max<uint64_t>(1, (span + max_slices - 1) / max_slices)
            : span;

        for (uint64_t cur = start; cur <= range.end_sequence;) {
            const uint64_t slice_end = std::min(range.end_sequence, cur + slice_size - 1);
            recovery_queue.push_back({cur, slice_end});
            cur = slice_end + 1;
        }
        recovery_scheduled_through = std::max(recovery_scheduled_through, range.end_sequence);
        if (config.recovery_concurrency > 1) {
            recovery_ready.notify_all();
        } else {
            recovery_ready.notify_one();
        }
    };

    subscriber = std::make_unique<DualFeedSubscriber>(
        [&](const MarketDataEvent& event) {
            {
                std::lock_guard lock(local_book_mutex);
                (void)local_book.apply(event);
            }
            ++delivered;
            if (config.verbose_deliveries) {
                std::cout << "DELIVERED seq=" << event.header.sequence_number
                          << " feed=" << (event.from_recovery ? 'R' : (event.feed_id == FeedId::A ? 'A' : 'B'))
                          << " type=" << event.header.message_type << '\n';
            }
        },
        [&](const MissingRange range) {
            enqueue_missing_range(range, false);
        });

    auto worker_fn = [&] {
        while (true) {
            MissingRange range;
            {
                std::unique_lock lock(recovery_mutex);
                recovery_ready.wait(lock, [&] {
                    return !recovery_worker_running.load() || !recovery_queue.empty();
                });
                if (!recovery_worker_running.load() && recovery_queue.empty()) break;
                range = recovery_queue.front();
                recovery_queue.pop_front();
                ++recovery_tasks_in_flight;
            }
            const auto finish_task = [&] {
                std::lock_guard lock(recovery_mutex);
                --recovery_tasks_in_flight;
                if (recovery_queue.empty() && recovery_tasks_in_flight == 0) {
                    recovery_idle.notify_all();
                }
            };

            if (config.gap_grace_ms > 0) {
                std::this_thread::sleep_for(std::chrono::milliseconds(config.gap_grace_ms));
            }
            range.start_sequence = std::max(range.start_sequence, subscriber->expected_sequence());
            if (!range.valid()) {
                finish_task();
                continue;
            }

            subscriber->mark_tcp_requested(range);
            ++gap_ranges_requested;

            bool range_resolved = false;
            bool aborted_by_tamper = false;
            for (size_t attempt = 0; attempt <= config.recovery_retries; ++attempt) {
                if (attempt > 0) {
                    ++recovery_retries;
                    const uint64_t backoff_ms = config.recovery_retry_base_ms * (1ULL << std::min<size_t>(attempt - 1, 6));
                    std::this_thread::sleep_for(std::chrono::milliseconds(backoff_ms));
                    range.start_sequence = std::max(range.start_sequence, subscriber->expected_sequence());
                    if (!range.valid()) {
                        range_resolved = true;
                        break;
                    }
                }

                ++recovery_requests;
                const auto rtt_started = std::chrono::steady_clock::now();
                RecoveryTcpClient client(config.recovery);
                std::string error;
                const auto response = client.request_missing_data({config.subscriber_id, range}, error);
                const auto rtt_ns = static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                    std::chrono::steady_clock::now() - rtt_started).count());

                if (response && response->status == RecoveryStatus::OK) {
                    {
                        std::lock_guard rtt_lock(rtt_mutex);
                        tcp_rtt_collector.record_recovery_latency(rtt_ns);
                    }
                    for (auto event : response->events) {
                        subscriber->on_recovered_event(std::move(event));
                    }
                    recovered_events += response->events.size();
                    range_resolved = true;
                    break;
                }

                if (response && response->status == RecoveryStatus::TOO_OLD) {
                    ++too_old_responses;
                    const auto snap_started = std::chrono::steady_clock::now();
                    const auto snapshot_response = client.request_snapshot(range.end_sequence, error);
                    const auto snap_rtt_ns = static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
                        std::chrono::steady_clock::now() - snap_started).count());
                    if (snapshot_response && snapshot_response->status == RecoveryStatus::OK
                        && snapshot_response->snapshot) {
                        if (!snapshot_hasher.verify(*snapshot_response->snapshot)) {
                            ++snapshot_rejected;
                            std::cerr << "SNAPSHOT REJECTED (hash mismatch) checkpoint="
                                      << snapshot_response->snapshot->sequence_number << '\n';
                            aborted_by_tamper = true;
                            break;
                        }
                        if (snapshot_response->snapshot->sequence_number >= range.end_sequence) {
                            {
                                std::lock_guard rtt_lock(rtt_mutex);
                                tcp_rtt_collector.record_recovery_latency(rtt_ns + snap_rtt_ns);
                            }
                            {
                                std::lock_guard lock(local_book_mutex);
                                local_book.apply_snapshot(*snapshot_response->snapshot);
                            }
                            subscriber->resynchronise_after_snapshot(snapshot_response->snapshot->sequence_number);
                            ++snapshot_recoveries;
                            range_resolved = true;
                            break;
                        }
                    }
                }
            }

            if (!range_resolved) {
                ++recovery_failures;
                (void)aborted_by_tamper;
            }
            finish_task();
        }
    };

    std::vector<std::thread> recovery_workers;
    recovery_workers.reserve(config.recovery_concurrency);
    for (size_t i = 0; i < config.recovery_concurrency; ++i) {
        recovery_workers.emplace_back(worker_fn);
    }

    MulticastReceiver receiver_a(config.feed_a);
    receiver_a.set_message_handler([&](const MessageHeader& header, const void* data, const size_t length) {
        ++datagrams_received_a;
        const uint64_t now_ns = steady_now_ns();
        last_datagram_ns_a.store(now_ns);
        if (static_cast<MessageType>(header.message_type) == MessageType::HEARTBEAT) {
            subscriber->on_heartbeat(header.sequence_number);
        } else {
            auto event = copy_event(header, data, length);
            auto ready_events = impairment_a.on_receive(event, now_ns);
            for (auto& ready_ev : ready_events) {
                subscriber->on_packet(FeedId::A, std::move(ready_ev));
            }
        }
    });

    std::unique_ptr<MulticastReceiver> receiver_b;
    if (config.dual_feed) {
        receiver_b = std::make_unique<MulticastReceiver>(config.feed_b);
        receiver_b->set_message_handler([&](const MessageHeader& header, const void* data, const size_t length) {
            ++datagrams_received_b;
            const uint64_t now_ns = steady_now_ns();
            last_datagram_ns_b.store(now_ns);
            if (static_cast<MessageType>(header.message_type) == MessageType::HEARTBEAT) {
                subscriber->on_heartbeat(header.sequence_number);
            } else {
                auto event = copy_event(header, data, length);
                auto ready_events = impairment_b.on_receive(event, now_ns);
                for (auto& ready_ev : ready_events) {
                    subscriber->on_packet(FeedId::B, std::move(ready_ev));
                }
            }
        });
    }

    if (!receiver_a.start() || (receiver_b && !receiver_b->start())) {
        std::cerr << "Could not start multicast receiver(s); check group, port, interface, and OS permissions.\n";
        receiver_a.stop();
        if (receiver_b) receiver_b->stop();
        recovery_worker_running.store(false);
        recovery_ready.notify_all();
        for (auto& w : recovery_workers) if (w.joinable()) w.join();
        return 1;
    }

    // Normative lifecycle step 1: send a STATUS request carrying subscriber_id
    // (acts as HELLO and reachability check) before printing SUBSCRIBER_READY.
    bool hello_ok = false;
    {
        RecoveryTcpClient hello_client(config.recovery);
        for (int attempt = 0; attempt < 10; ++attempt) {
            std::string hello_err;
            const auto st = hello_client.request_status(hello_err, config.subscriber_id);
            if (st.has_value()) {
                hello_ok = true;
                break;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
    }
    if (!hello_ok) {
        std::cerr << "Could not reach recovery server at " << config.recovery.host << ':' << config.recovery.port << '\n';
        receiver_a.stop();
        if (receiver_b) receiver_b->stop();
        recovery_worker_running.store(false);
        recovery_ready.notify_all();
        for (auto& w : recovery_workers) if (w.joinable()) w.join();
        return 1;
    }

    const uint64_t start_ns = steady_now_ns();
    last_datagram_ns_a.store(start_ns);
    last_datagram_ns_b.store(start_ns);

    std::cout << "SUBSCRIBER_READY id=" << config.subscriber_id
              << " feeds=" << (config.dual_feed ? "ab" : "a") << std::endl;

    const auto emit_stats = [&](const char* phase, const uint64_t lost_so_far) {
        if (config.stats_interval_ms == 0) return;
        const uint64_t now_ns = steady_now_ns();
        const bool a_stale = (now_ns - last_datagram_ns_a.load()) > 2'000'000'000ULL;
        const bool b_stale = (now_ns - last_datagram_ns_b.load()) > 2'000'000'000ULL;
        std::cout << "SUBSCRIBER_STATS id=" << config.subscriber_id
                  << " phase=" << phase
                  << " last_seq=" << subscriber->last_delivered_sequence()
                  << " delivered=" << delivered.load()
                  << " duplicates=" << subscriber->sequence_tracker().duplicate_count()
                  << " gap_ranges=" << gap_ranges_requested.load()
                  << " recovery_requests=" << recovery_requests.load()
                  << " recovered=" << recovered_events.load()
                  << " snapshot_recoveries=" << snapshot_recoveries.load()
                  << " lost_so_far=" << lost_so_far
                  << " feed_a=" << (a_stale ? "stale" : "ok")
                  << " feed_b=" << (!config.dual_feed ? "off" : (b_stale ? "stale" : "ok"))
                  << std::endl;
    };

    const auto measurement_started = std::chrono::steady_clock::now();
    const auto deadline = config.run_seconds == 0
        ? std::optional<std::chrono::steady_clock::time_point>{}
        : std::optional{measurement_started + std::chrono::seconds(config.run_seconds)};

    auto last_stats_tp = measurement_started;
    auto last_idle_poll_tp = measurement_started;
    uint64_t observed_exchange_seq = 0;
    auto observed_seq_changed_tp = measurement_started;

    while (keep_running.load()) {
        const auto now_tp = std::chrono::steady_clock::now();
        const uint64_t now_ns = steady_now_ns();

        // Pump any delayed packets from receive-side impairment queues.
        for (auto& ev : impairment_a.poll_ready(now_ns)) {
            subscriber->on_packet(FeedId::A, std::move(ev));
        }
        if (config.dual_feed) {
            for (auto& ev : impairment_b.poll_ready(now_ns)) {
                subscriber->on_packet(FeedId::B, std::move(ev));
            }
        }

        if (deadline && now_tp >= *deadline) break;
        if (config.until_seq > 0 && subscriber->expected_sequence() > config.until_seq) break;

        if (config.stop_when_idle_ms > 0
            && std::chrono::duration_cast<std::chrono::milliseconds>(now_tp - last_idle_poll_tp).count() >= 200) {
            last_idle_poll_tp = now_tp;
            RecoveryTcpClient poll_client(config.recovery);
            std::string poll_err;
            if (const auto st = poll_client.request_status(poll_err, config.subscriber_id)) {
                if (st->latest_sequence != observed_exchange_seq) {
                    observed_exchange_seq = st->latest_sequence;
                    observed_seq_changed_tp = now_tp;
                } else if (observed_exchange_seq > 0) {
                    const auto idle_ms = static_cast<uint64_t>(
                        std::chrono::duration_cast<std::chrono::milliseconds>(now_tp - observed_seq_changed_tp).count());
                    if (idle_ms >= config.stop_when_idle_ms) {
                        if (subscriber->expected_sequence() > observed_exchange_seq) {
                            break;
                        }
                        subscriber->on_heartbeat(observed_exchange_seq);
                        if (config.no_recovery || snapshot_rejected.load() > 0 || recovery_failures.load() > 0) {
                            break;
                        }
                    }
                }
            }
        }

        if (config.stats_interval_ms > 0
            && static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
                now_tp - last_stats_tp).count()) >= config.stats_interval_ms) {
            last_stats_tp = now_tp;
            emit_stats("running", 0);
        }

        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }

    emit_stats("reconciling", 0);

    // Step 3(a): stop UDP receivers (< 200 ms) and flush held-back packets.
    receiver_a.stop();
    if (receiver_b) receiver_b->stop();
    for (auto& ev : impairment_a.flush()) {
        subscriber->on_packet(FeedId::A, std::move(ev));
    }
    if (config.dual_feed) {
        for (auto& ev : impairment_b.flush()) {
            subscriber->on_packet(FeedId::B, std::move(ev));
        }
    }

    const auto receive_window_ns = static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now() - measurement_started).count());

    // Step 3(b): STATUS convergence -> L, digest_L (performed even for --no-recovery).
    uint64_t exchange_latest_sequence = 0;
    std::string exchange_book_digest{};
    bool status_reconciled = false;
    {
        RecoveryTcpClient client(config.recovery);
        std::optional<RecoveryStatusInfo> previous;
        const int max_attempts = config.until_seq > 0 ? 25 : 5;
        for (int attempt = 0; attempt < max_attempts; ++attempt) {
            std::string status_error;
            const auto status = client.request_status(status_error, config.subscriber_id);
            if (!status) {
                std::cerr << "STATUS RECONCILIATION FAILED: " << status_error << '\n';
                break;
            }
            if (previous
                && previous->latest_sequence == status->latest_sequence
                && previous->history_minimum_sequence == status->history_minimum_sequence
                && previous->latest_book_digest == status->latest_book_digest) {
                exchange_latest_sequence = status->latest_sequence;
                exchange_book_digest = status->latest_book_digest;
                status_reconciled = true;
                break;
            }
            previous = status;
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }
    }

    // Step 3(c) & 3(d): feed on_heartbeat(L) and recover tail until expected_sequence == L + 1 or retries exhausted.
    if (status_reconciled && exchange_latest_sequence > 0) {
        subscriber->on_heartbeat(exchange_latest_sequence);
        if (!config.no_recovery) {
            for (size_t round = 0; round <= config.recovery_retries + 2; ++round) {
                {
                    std::unique_lock lock(recovery_mutex);
                    if (!recovery_idle.wait_for(lock, std::chrono::seconds(15), [&] {
                            return recovery_queue.empty() && recovery_tasks_in_flight == 0;
                        })) {
                        ++recovery_failures;
                        std::cerr << "RECOVERY DRAIN FAILED: queued recovery did not finish within 15 seconds\n";
                        break;
                    }
                }
                if (subscriber->expected_sequence() > exchange_latest_sequence
                    || snapshot_rejected.load() > 0
                    || recovery_failures.load() > 0) {
                    break;
                }
                subscriber->on_heartbeat(exchange_latest_sequence);
                const uint64_t exp_seq = subscriber->expected_sequence();
                if (exp_seq <= exchange_latest_sequence) {
                    enqueue_missing_range({exp_seq, exchange_latest_sequence}, true);
                }
            }
        }
    }

    recovery_worker_running.store(false);
    recovery_ready.notify_all();
    for (auto& w : recovery_workers) {
        if (w.joinable()) w.join();
    }

    const auto runtime_ns = static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now() - measurement_started).count());

    // Step 3(e): compute results from bitsets and local BookBuilder.
    const auto accounting = status_reconciled
        ? subscriber->compute_accounting(exchange_latest_sequence)
        : AccountingBitsetSummary{0, delivered.load(), subscriber->delivered_via_feed_a(),
              subscriber->delivered_via_feed_b(), subscriber->delivered_via_recovery(),
              subscriber->events_covered_by_snapshot(), 0, false};
    const auto redundancy = subscriber->compute_redundancy(exchange_latest_sequence);

    std::string book_digest_final;
    uint64_t book_last_seq = 0;
    {
        std::lock_guard lock(local_book_mutex);
        book_digest_final = local_book.digest();
        book_last_seq = local_book.last_sequence();
    }

    std::string book_matches_exchange = "n/a";
    std::string book_mismatch_reason = "status_unavailable";
    if (snapshot_rejected.load() > 0) {
        book_matches_exchange = "0";
        book_mismatch_reason = "snapshot_rejected";
    } else if (status_reconciled) {
        if (subscriber->expected_sequence() != exchange_latest_sequence + 1
            || accounting.permanently_lost != 0) {
            book_matches_exchange = "0";
            book_mismatch_reason = "sequence_incomplete";
        } else if (!exchange_book_digest.empty() && book_digest_final != exchange_book_digest) {
            book_matches_exchange = "0";
            book_mismatch_reason = "digest_mismatch";
        } else {
            book_matches_exchange = "1";
            book_mismatch_reason = "none";
        }
    }

    // Step 3(f): send BYE.
    {
        RecoveryTcpClient bye_client(config.recovery);
        std::string bye_error;
        (void)bye_client.send_bye(config.subscriber_id, bye_error);
    }

    emit_stats("done", accounting.permanently_lost);

    const auto lat_tcp = subscriber->latency_summary(MissingEventClass::TCP_RECOVERY);
    const auto lat_other = subscriber->latency_summary(MissingEventClass::OTHER_FEED);
    const auto lat_snap = subscriber->latency_summary(MissingEventClass::SNAPSHOT);
    LatencySummary rtt_summary{};
    {
        std::lock_guard rtt_lock(rtt_mutex);
        rtt_summary = tcp_rtt_collector.snapshot().recovery_latency;
    }

    const uint64_t effective_window_ns = receive_window_ns > 0 ? receive_window_ns : runtime_ns;
    const double delivered_events_per_second = effective_window_ns == 0 ? 0.0
        : static_cast<double>(accounting.delivered) * 1'000'000'000.0 / static_cast<double>(effective_window_ns);

    const bool unresolved_gap = !status_reconciled
        || subscriber->pending_event_count() != 0
        || (config.no_recovery && subscriber->gap_detections() != 0)
        || recovery_failures.load() != 0
        || snapshot_rejected.load() != 0
        || accounting.permanently_lost != 0
        || book_matches_exchange != "1";

    const std::string run_status = snapshot_rejected.load() > 0 ? "failed_integrity"
        : !status_reconciled ? "failed_not_quiescent"
        : !accounting.accounting_valid ? "failed_accounting"
        : unresolved_gap ? "failed_integrity"
        : "completed";
    const std::string failure_reason = snapshot_rejected.load() > 0 ? "snapshot_rejected"
        : !status_reconciled ? "not_quiescent"
        : !accounting.accounting_valid ? "accounting_violation"
        : book_matches_exchange == "0" ? book_mismatch_reason
        : recovery_failures.load() > 0 ? "recovery_failed"
        : "none";

    const std::string transport = detect_transport(config.feed_a.interface_ip);
    const std::string loss_injection = detect_loss_injection(config);
    const uint64_t hash_cut_seq = config.until_seq > 0
        ? std::min(config.until_seq, exchange_latest_sequence)
        : exchange_latest_sequence;
    const std::string dropped_hash_a = impairment_a.dropped_sequences_hash(hash_cut_seq);
    const std::string dropped_hash_b = impairment_b.dropped_sequences_hash(hash_cut_seq);
    const std::string redundancy_eff_str = format_double_or_na(redundancy.redundancy_effectiveness);

    // Step 3(g): write CSV and latency CSV if requested.
    if (!config.latency_csv_path.empty()) {
        std::filesystem::path lpath(config.latency_csv_path);
        if (lpath.has_parent_path()) {
            std::error_code ec;
            std::filesystem::create_directories(lpath.parent_path(), ec);
        }
        std::ofstream lout(lpath);
        if (lout) {
            lout << "run_id,subscriber_id,sequence_number,event_class,gap_detected_ns,delivered_ns,latency_ns\n";
            for (const auto& sample : subscriber->latency_samples()) {
                lout << config.run_id << ','
                     << config.subscriber_id << ','
                     << sample.sequence_number << ','
                     << missing_event_class_name(sample.event_class) << ','
                     << sample.gap_detected_ns << ','
                     << sample.delivered_ns << ','
                     << sample.latency_ns << '\n';
            }
        }
    }

    if (!config.csv_path.empty()) {
        std::filesystem::path cpath(config.csv_path);
        if (cpath.has_parent_path()) {
            std::error_code ec;
            std::filesystem::create_directories(cpath.parent_path(), ec);
        }
        std::ofstream cout_csv(cpath);
        if (cout_csv) {
            cout_csv << "schema_version,run_id,subscriber_id,transport,loss_injection,feed_mode,loss_model,"
                     << "drop_a,drop_b,correlated_loss,dup_rate,reorder_rate,reorder_depth,delay_ms,drop_seed,"
                     << "gap_grace_ms,recovery_concurrency,recovery_retries,no_recovery,"
                     << "dropped_seq_hash_a,dropped_seq_hash_b,"
                     << "events_expected,delivered,delivered_via_feed_a,delivered_via_feed_b,delivered_via_recovery,"
                     << "events_covered_by_snapshot,permanently_lost,accounting_valid,"
                     << "datagrams_received_a,datagrams_received_b,dropped_by_injector_a,dropped_by_injector_b,"
                     << "duplicates_suppressed,gap_detections,gap_ranges_requested,events_in_gaps,"
                     << "recovery_requests,recovery_retries_count,recovered_events,too_old_responses,"
                     << "snapshot_recoveries,snapshot_rejected,recovery_failures,"
                     << "potential_single_feed_gaps,gaps_avoided_by_second_feed,redundancy_effectiveness,"
                     << "book_digest_final,exchange_book_digest,book_matches_exchange,book_mismatch_reason,"
                     << "runtime_ms,receive_window_ms,delivered_events_per_second,"
                     << "tcp_recovery_n,tcp_recovery_insufficient,tcp_recovery_insufficient_p999,"
                     << "tcp_recovery_p50_ns,tcp_recovery_p95_ns,tcp_recovery_p99_ns,tcp_recovery_p999_ns,tcp_recovery_max_ns,tcp_recovery_mean_ns,"
                     << "other_feed_n,other_feed_insufficient,other_feed_p50_ns,other_feed_p95_ns,other_feed_p99_ns,other_feed_p999_ns,other_feed_max_ns,other_feed_mean_ns,"
                     << "snapshot_lat_n,snapshot_lat_insufficient,snapshot_lat_p50_ns,snapshot_lat_p95_ns,snapshot_lat_p99_ns,snapshot_lat_p999_ns,snapshot_lat_max_ns,snapshot_lat_mean_ns,"
                     << "tcp_request_rtt_n,tcp_request_rtt_p50_ns,tcp_request_rtt_p95_ns,tcp_request_rtt_p99_ns,tcp_request_rtt_max_ns,tcp_request_rtt_mean_ns,"
                     << "run_status,failure_reason\n";
            cout_csv << "2,"
                     << config.run_id << ','
                     << config.subscriber_id << ','
                     << transport << ','
                     << loss_injection << ','
                     << (config.dual_feed ? "dual" : "single") << ','
                     << config.loss_model << ','
                     << config.drop_a << ','
                     << config.drop_b << ','
                     << config.correlated_loss << ','
                     << config.duplication_rate << ','
                     << config.reorder_rate << ','
                     << config.reorder_depth << ','
                     << config.delay_ms << ','
                     << config.drop_seed << ','
                     << config.gap_grace_ms << ','
                     << config.recovery_concurrency << ','
                     << config.recovery_retries << ','
                     << (config.no_recovery ? 1 : 0) << ','
                     << dropped_hash_a << ','
                     << dropped_hash_b << ','
                     << accounting.events_expected << ','
                     << accounting.delivered << ','
                     << accounting.delivered_via_feed_a << ','
                     << accounting.delivered_via_feed_b << ','
                     << accounting.delivered_via_recovery << ','
                     << accounting.events_covered_by_snapshot << ','
                     << accounting.permanently_lost << ','
                     << (accounting.accounting_valid ? 1 : 0) << ','
                     << datagrams_received_a.load() << ','
                     << datagrams_received_b.load() << ','
                     << impairment_a.dropped_count() << ','
                     << impairment_b.dropped_count() << ','
                     << subscriber->sequence_tracker().duplicate_count() << ','
                     << subscriber->gap_detections() << ','
                     << gap_ranges_requested.load() << ','
                     << subscriber->events_in_gaps() << ','
                     << recovery_requests.load() << ','
                     << recovery_retries.load() << ','
                     << recovered_events.load() << ','
                     << too_old_responses.load() << ','
                     << snapshot_recoveries.load() << ','
                     << snapshot_rejected.load() << ','
                     << recovery_failures.load() << ','
                     << redundancy.potential_single_feed_gaps << ','
                     << redundancy.gaps_avoided_by_second_feed << ','
                     << redundancy_eff_str << ','
                     << book_digest_final << ','
                     << exchange_book_digest << ','
                     << book_matches_exchange << ','
                     << book_mismatch_reason << ','
                     << runtime_ns / 1'000'000 << ','
                     << effective_window_ns / 1'000'000 << ','
                     << delivered_events_per_second << ','
                     << lat_tcp.count << ','
                     << (lat_tcp.insufficient_samples ? 1 : 0) << ','
                     << (lat_tcp.insufficient_p999 ? 1 : 0) << ','
                     << lat_tcp.p50_ns << ','
                     << lat_tcp.p95_ns << ','
                     << lat_tcp.p99_ns << ','
                     << lat_tcp.p999_ns << ','
                     << lat_tcp.max_ns << ','
                     << lat_tcp.mean_ns << ','
                     << lat_other.count << ','
                     << (lat_other.insufficient_samples ? 1 : 0) << ','
                     << lat_other.p50_ns << ','
                     << lat_other.p95_ns << ','
                     << lat_other.p99_ns << ','
                     << lat_other.p999_ns << ','
                     << lat_other.max_ns << ','
                     << lat_other.mean_ns << ','
                     << lat_snap.count << ','
                     << (lat_snap.insufficient_samples ? 1 : 0) << ','
                     << lat_snap.p50_ns << ','
                     << lat_snap.p95_ns << ','
                     << lat_snap.p99_ns << ','
                     << lat_snap.p999_ns << ','
                     << lat_snap.max_ns << ','
                     << lat_snap.mean_ns << ','
                     << rtt_summary.count << ','
                     << rtt_summary.p50_ns << ','
                     << rtt_summary.p95_ns << ','
                     << rtt_summary.p99_ns << ','
                     << rtt_summary.max_ns << ','
                     << rtt_summary.mean_ns << ','
                     << run_status << ','
                     << failure_reason << '\n';
        }
    }

    std::cout << "SUMMARY delivered=" << accounting.delivered
              << " via_a=" << accounting.delivered_via_feed_a
              << " via_b=" << accounting.delivered_via_feed_b
              << " via_recovery=" << accounting.delivered_via_recovery
              << " duplicates_suppressed=" << subscriber->sequence_tracker().duplicate_count()
              << " gap_detections=" << subscriber->gap_detections()
              << " gap_ranges_requested=" << gap_ranges_requested
              << " events_in_gaps=" << subscriber->events_in_gaps()
              << " recovery_requests=" << recovery_requests
              << " recovery_retries=" << recovery_retries
              << " snapshot_recoveries=" << snapshot_recoveries
              << " snapshot_rejected=" << snapshot_rejected
              << " snapshot_covered=" << accounting.events_covered_by_snapshot
              << " exchange_latest=" << exchange_latest_sequence
              << " permanently_lost=" << accounting.permanently_lost
              << " book_matches_exchange=" << book_matches_exchange
              << " runtime_ms=" << runtime_ns / 1'000'000
              << " delivered_events_per_second=" << delivered_events_per_second
              << " recovery_failures=" << recovery_failures << '\n';

    std::cout << "RELIABLE_SUBSCRIBER_RESULT"
              << " schema_version=2"
              << " run_id=" << config.run_id
              << " subscriber_id=" << config.subscriber_id
              << " transport=" << transport
              << " loss_injection=" << loss_injection
              << " delivered=" << accounting.delivered
              << " delivered_via_feed_a=" << accounting.delivered_via_feed_a
              << " delivered_via_feed_b=" << accounting.delivered_via_feed_b
              << " delivered_via_recovery=" << accounting.delivered_via_recovery
              << " duplicates=" << subscriber->sequence_tracker().duplicate_count()
              << " duplicates_suppressed=" << subscriber->sequence_tracker().duplicate_count()
              << " gaps=" << subscriber->gap_detections()
              << " gap_detections=" << subscriber->gap_detections()
              << " gap_ranges_requested=" << gap_ranges_requested
              << " events_in_gaps=" << subscriber->events_in_gaps()
              << " recovery_requests=" << recovery_requests
              << " recovery_retries=" << recovery_retries
              << " recovered_events=" << recovered_events
              << " too_old_responses=" << too_old_responses
              << " snapshot_recoveries=" << snapshot_recoveries
              << " snapshot_rejected=" << snapshot_rejected
              << " events_covered_by_snapshot=" << accounting.events_covered_by_snapshot
              << " exchange_latest_sequence=" << exchange_latest_sequence
              << " events_expected=" << accounting.events_expected
              << " accounting_valid=" << (accounting.accounting_valid ? 1 : 0)
              << " permanently_lost=" << accounting.permanently_lost
              << " potential_single_feed_gaps=" << redundancy.potential_single_feed_gaps
              << " gaps_avoided_by_second_feed=" << redundancy.gaps_avoided_by_second_feed
              << " redundancy_effectiveness=" << redundancy_eff_str
              << " book_last_sequence=" << book_last_seq
              << " book_digest_final=" << book_digest_final
              << " exchange_book_digest=" << exchange_book_digest
              << " book_matches_exchange=" << book_matches_exchange
              << " book_mismatch_reason=" << book_mismatch_reason
              << " runtime_ms=" << runtime_ns / 1'000'000
              << " receive_window_ms=" << effective_window_ns / 1'000'000
              << " delivered_events_per_second=" << delivered_events_per_second
              << " recovery_n=" << lat_tcp.count
              << " insufficient_samples=" << (lat_tcp.insufficient_samples ? 1 : 0)
              << " insufficient_p999=" << (lat_tcp.insufficient_p999 ? 1 : 0)
              << " recovery_p50_ns=" << lat_tcp.p50_ns
              << " recovery_p95_ns=" << lat_tcp.p95_ns
              << " recovery_p99_ns=" << lat_tcp.p99_ns
              << " recovery_p999_ns=" << lat_tcp.p999_ns
              << " recovery_max_ns=" << lat_tcp.max_ns
              << " recovery_mean_ns=" << lat_tcp.mean_ns
              << " other_feed_n=" << lat_other.count
              << " other_feed_insufficient=" << (lat_other.insufficient_samples ? 1 : 0)
              << " other_feed_p50_ns=" << lat_other.p50_ns
              << " other_feed_p95_ns=" << lat_other.p95_ns
              << " other_feed_p99_ns=" << lat_other.p99_ns
              << " other_feed_p999_ns=" << lat_other.p999_ns
              << " other_feed_max_ns=" << lat_other.max_ns
              << " other_feed_mean_ns=" << lat_other.mean_ns
              << " snapshot_lat_n=" << lat_snap.count
              << " snapshot_lat_insufficient=" << (lat_snap.insufficient_samples ? 1 : 0)
              << " snapshot_lat_p50_ns=" << lat_snap.p50_ns
              << " snapshot_lat_p95_ns=" << lat_snap.p95_ns
              << " snapshot_lat_p99_ns=" << lat_snap.p99_ns
              << " snapshot_lat_p999_ns=" << lat_snap.p999_ns
              << " snapshot_lat_max_ns=" << lat_snap.max_ns
              << " snapshot_lat_mean_ns=" << lat_snap.mean_ns
              << " tcp_request_rtt_n=" << rtt_summary.count
              << " tcp_request_rtt_p50_ns=" << rtt_summary.p50_ns
              << " tcp_request_rtt_p95_ns=" << rtt_summary.p95_ns
              << " tcp_request_rtt_p99_ns=" << rtt_summary.p99_ns
              << " tcp_request_rtt_max_ns=" << rtt_summary.max_ns
              << " tcp_request_rtt_mean_ns=" << rtt_summary.mean_ns
              << " recovery_failures=" << recovery_failures
              << " gap_grace_ms=" << config.gap_grace_ms
              << " recovery_concurrency=" << config.recovery_concurrency
              << " datagrams_received_a=" << datagrams_received_a
              << " datagrams_received_b=" << datagrams_received_b
              << " dropped_by_injector_a=" << impairment_a.dropped_count()
              << " dropped_by_injector_b=" << impairment_b.dropped_count()
              << " duplicated_by_injector=" << (impairment_a.duplicated_count() + impairment_b.duplicated_count())
              << " reordered_by_injector=" << (impairment_a.reordered_count() + impairment_b.reordered_count())
              << " delayed_by_injector=" << (impairment_a.delayed_count() + impairment_b.delayed_count())
              << " dropped_seq_hash_a=" << dropped_hash_a
              << " dropped_seq_hash_b=" << dropped_hash_b
              << " loss_model=" << config.loss_model
              << " drop_a=" << config.drop_a
              << " drop_b=" << config.drop_b
              << " correlated_loss=" << config.correlated_loss
              << " dup_rate=" << config.duplication_rate
              << " reorder_rate=" << config.reorder_rate
              << " reorder_depth=" << config.reorder_depth
              << " delay_ms=" << config.delay_ms
              << " drop_seed=" << config.drop_seed
              << " run_status=" << run_status
              << " reason=" << failure_reason
              << " feed_mode=" << (config.dual_feed ? "dual" : "single")
              << " recovery_server=" << config.recovery.host << ':' << config.recovery.port << '\n';

    if (run_status == "completed") return 0;
    if (run_status == "failed_accounting") return 3;
    if (run_status == "failed_not_quiescent") return 4;
    return 2;
}
