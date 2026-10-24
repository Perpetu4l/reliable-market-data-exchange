#include "reliability/DualFeed.h"
#include "reliability/Metrics.h"
#include "reliability/RecoveryProtocol.h"
#include "reliability/RecoveryScheduler.h"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <string>

namespace {
using namespace mdfeed;
using namespace mdfeed::reliability;

struct DemoConfig {
    uint64_t event_count{30};
    double loss_rate{0.20};
    uint64_t seed{42};
    size_t history_size{128};
    uint64_t snapshot_interval{10};
    size_t snapshot_retention{8};
    std::string scheduler{"fcfs"};
    std::string mode{"independent"};
    std::string csv_path;
};

void print_usage(const char* program)
{
    std::cout << "Usage: " << program
              << " [--events N] [--loss RATE] [--mode single|independent|correlated]"
                 " [--seed N] [--history N] [--snapshot-interval N] [--retention N]"
                 " [--scheduler fcfs|round-robin] [--csv FILE]\n";
}

bool parse_arguments(const int argc, char* argv[], DemoConfig& config)
{
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];
        if (argument == "--help") {
            print_usage(argv[0]);
            return false;
        }
        if (index + 1 >= argc) return false;
        const std::string value = argv[++index];
        if (argument == "--events") config.event_count = std::stoull(value);
        else if (argument == "--loss") config.loss_rate = std::stod(value);
        else if (argument == "--mode") config.mode = value;
        else if (argument == "--seed") config.seed = std::stoull(value);
        else if (argument == "--history") config.history_size = std::stoull(value);
        else if (argument == "--snapshot-interval") config.snapshot_interval = std::stoull(value);
        else if (argument == "--retention") config.snapshot_retention = std::stoull(value);
        else if (argument == "--scheduler") config.scheduler = value;
        else if (argument == "--csv") config.csv_path = value;
        else return false;
    }
    return config.loss_rate >= 0.0 && config.loss_rate <= 1.0
        && (config.mode == "single" || config.mode == "independent" || config.mode == "correlated")
        && (config.scheduler == "fcfs" || config.scheduler == "round-robin");
}

MarketDataEvent make_event(const uint64_t sequence)
{
    MarketDataEvent event;
    event.header.sequence_number = sequence;
    event.header.message_type = static_cast<uint16_t>(MessageType::PRICE_LEVEL_UPDATE);
    event.header.timestamp_ns = message_utils::get_timestamp_ns();
    event.header.instrument_id = 1;
    return event;
}

} // namespace

int main(int argc, char* argv[])
{
    DemoConfig config;
    if (!parse_arguments(argc, argv, config)) {
        print_usage(argv[0]);
        return argc > 1 && std::string(argv[1]) == "--help" ? EXIT_SUCCESS : EXIT_FAILURE;
    }

    auto history = std::make_shared<RecoveryHistory>(config.history_size);
    auto snapshots = std::make_shared<SnapshotManager>(SnapshotConfig{
        config.snapshot_interval, config.snapshot_retention, config.snapshot_interval != 0});
    RecoveryServer recovery_server(history, snapshots);
    RecoveryClient recovery_client("demo-subscriber");
    RecoveryScheduler scheduler(config.scheduler == "round-robin"
        ? SchedulingPolicy::ROUND_ROBIN : SchedulingPolicy::FCFS);
    MetricsCollector metrics;
    std::unique_ptr<DualFeedSubscriber> subscriber;

    uint64_t delivered_events = 0;
    uint64_t gaps_avoided_by_second_feed = 0;
    subscriber = std::make_unique<DualFeedSubscriber>(
        [&](const MarketDataEvent& event) {
            ++delivered_events;
            std::cout << "DELIVERED seq=" << event.header.sequence_number
                      << " feed=" << (event.feed_id == FeedId::A ? "A" : "B") << '\n';
        },
        [&](const MissingRange range) {
            const auto recovery_started = std::chrono::steady_clock::now();
            scheduler.enqueue({"demo-subscriber", range});
            const auto scheduled = scheduler.next();
            if (!scheduled.has_value()) return;
            auto response = recovery_client.recover(recovery_server, scheduled->request.missing_range);
            const auto latency = std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now() - recovery_started).count();
            auto& values = metrics.mutable_metrics();
            ++values.gap_count;
            ++values.recovery_request_count;
            metrics.record_recovery_latency(latency);

            if (response.status == RecoveryStatus::OK) {
                scheduler.record_completed(*scheduled, response.events.size());
                values.recovered_events += response.events.size();
                std::cout << "RECOVERY OK range=" << range.start_sequence << '-' << range.end_sequence
                          << " events=" << response.events.size() << '\n';
                for (auto& event : response.events) subscriber->on_recovered_event(std::move(event));
                return;
            }

            const auto snapshot_response = recovery_server.send_snapshot(range.end_sequence);
            if (snapshot_response.status == RecoveryStatus::OK) {
                scheduler.record_completed(*scheduled, 0);
                ++values.snapshot_recoveries;
                std::cout << "RECOVERY SNAPSHOT RESYNC checkpoint="
                          << snapshot_response.snapshot->sequence_number << '\n';
                subscriber->resynchronise_after_snapshot(snapshot_response.snapshot->sequence_number);
            } else {
                scheduler.record_completed(*scheduled, 0);
                ++values.unrecoverable_gaps;
                std::cout << "RECOVERY UNAVAILABLE range=" << range.start_sequence << '-'
                          << range.end_sequence << '\n';
            }
        });

    // Correlated mode applies loss once before both feeds publish. Independent
    // mode gives each feed its own deterministic loss generator.
    const double per_feed_loss = config.mode == "correlated" ? 0.0 : config.loss_rate;
    const FaultConfig feed_a_faults{per_feed_loss, 0.05, 0.0, {}, config.seed};
    const FaultConfig feed_b_faults{per_feed_loss, 0.05, 0.0, {}, config.seed + 1};
    auto feed_a = std::make_shared<FaultInjectingFeed>(FeedId::A, feed_a_faults,
        [&](MarketDataEvent event) { subscriber->on_packet(FeedId::A, std::move(event)); });
    std::shared_ptr<FaultInjectingFeed> feed_b;
    if (config.mode != "single") {
        feed_b = std::make_shared<FaultInjectingFeed>(FeedId::B, feed_b_faults,
            [&](MarketDataEvent event) { subscriber->on_packet(FeedId::B, std::move(event)); });
    }
    DualFeedPublisher publisher(feed_a, feed_b);
    FaultInjector correlated_faults({config.loss_rate, 0.0, 0.0, {}, config.seed});

    std::cout << "Reliability demonstration: mode=" << config.mode
              << " events=" << config.event_count << " loss=" << config.loss_rate
              << " seed=" << config.seed << "\n";

    const auto experiment_started = std::chrono::steady_clock::now();
    for (uint64_t sequence = 1; sequence <= config.event_count; ++sequence) {
        auto event = make_event(sequence);
        history->append(event);
        if (snapshots->should_checkpoint(sequence)) {
            snapshots->store_snapshot(snapshots->create_snapshot(sequence, 1, {}, event.header.timestamp_ns));
        }
        ++metrics.mutable_metrics().packets_sent;

        if (config.mode == "correlated" && correlated_faults.should_drop()) {
            ++metrics.mutable_metrics().packets_lost;
            std::cout << "CORRELATED LOSS seq=" << sequence << '\n';
            continue;
        }
        publisher.publish(event);
        // A is the single-feed baseline. If it lost this exact sequence while
        // B delivered it, the second feed avoided a would-be single-feed gap.
        if (feed_b && feed_a->last_publish_was_dropped()
            && !feed_b->last_publish_was_dropped()) {
            ++gaps_avoided_by_second_feed;
        }
    }

    // In the live system this arrives in a non-sequenced HEARTBEAT datagram.
    // The deterministic driver emits one final heartbeat so dropped tail events
    // are not silently excluded from the result.
    subscriber->on_heartbeat(config.event_count);

    auto result = metrics.snapshot();
    const auto elapsed_seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - experiment_started).count();
    result.throughput_events_per_second = elapsed_seconds > 0.0
        ? static_cast<double>(config.event_count) / elapsed_seconds : 0.0;
    result.duplicate_count = subscriber->sequence_tracker().duplicate_count();
    const auto feed_a_stats = feed_a->stats();
    result.packets_lost += feed_a_stats.dropped_packets;
    if (feed_b) {
        const auto feed_b_stats = feed_b->stats();
        result.packets_lost += feed_b_stats.dropped_packets;
        result.redundancy_effectiveness = feed_a_stats.dropped_packets == 0 ? 0.0
            : static_cast<double>(gaps_avoided_by_second_feed)
                / static_cast<double>(feed_a_stats.dropped_packets);
    }

    const auto latest_snapshot = snapshots->get_latest_snapshot();
    const bool checkpoint_chain_valid = snapshots->verify_snapshots();
    const auto& scheduler_metrics = scheduler.metrics();
    const auto service_it = scheduler_metrics.per_subscriber_service_distribution.find("demo-subscriber");
    const auto demo_subscriber_services = service_it == scheduler_metrics.per_subscriber_service_distribution.end()
        ? 0 : service_it->second;

    std::cout << "\nSUMMARY\n"
              << "  delivered events: " << delivered_events << '\n'
              << "  duplicates suppressed: " << result.duplicate_count << '\n'
              << "  gaps detected: " << result.gap_count << '\n'
              << "  recovery requests: " << result.recovery_request_count << '\n'
              << "  recovered events: " << result.recovered_events << '\n'
              << "  snapshot recoveries: " << result.snapshot_recoveries << '\n'
              << "  unrecoverable gaps: " << result.unrecoverable_gaps << '\n'
              << "  feed A dropped packets: " << feed_a_stats.dropped_packets << '\n'
              << "  feed B dropped packets: " << (feed_b ? feed_b->stats().dropped_packets : 0) << '\n'
              << "  simulated loss count: " << result.packets_lost << '\n'
              << "  redundancy effectiveness: " << result.redundancy_effectiveness << '\n'
              << "  checkpoint chain valid: " << (checkpoint_chain_valid ? "yes" : "no") << '\n';

    if (!config.csv_path.empty()) {
        if (!MetricsExporter{}.export_csv(result, config.csv_path)) {
            std::cerr << "Unable to write CSV: " << config.csv_path << '\n';
            return EXIT_FAILURE;
        }
        std::cout << "  CSV written: " << config.csv_path << '\n';
    }

    // Stable one-line output consumed by the GUI. The values come from this
    // deterministic in-process experiment, not live UDP/TCP recovery sockets.
    std::cout << "RELIABILITY_RESULT"
              << " mode=" << config.mode
              << " events=" << config.event_count
              << " loss=" << config.loss_rate
              << " packets_sent=" << result.packets_sent
              << " packets_lost=" << result.packets_lost
              << " duplicates=" << result.duplicate_count
              << " gaps=" << result.gap_count
              << " recovery_requests=" << result.recovery_request_count
              << " recovered_events=" << result.recovered_events
              << " snapshot_recoveries=" << result.snapshot_recoveries
              << " unrecoverable_gaps=" << result.unrecoverable_gaps
              << " feed_a_dropped=" << feed_a_stats.dropped_packets
              << " feed_b_dropped=" << (feed_b ? feed_b->stats().dropped_packets : 0)
              << " gaps_avoided=" << gaps_avoided_by_second_feed
              << " redundancy_effectiveness=" << result.redundancy_effectiveness
              << " throughput_eps=" << result.throughput_events_per_second
              << " p50_ns=" << result.recovery_latency.p50_ns
              << " p95_ns=" << result.recovery_latency.p95_ns
              << " p99_ns=" << result.recovery_latency.p99_ns
              << " scheduler=" << config.scheduler
              << " scheduler_requests=" << scheduler_metrics.recovery_request_count
              << " scheduler_messages=" << scheduler_metrics.messages_recovered
              << " scheduler_demo_subscriber=" << demo_subscriber_services
              << " checkpoint_sequence=" << (latest_snapshot ? latest_snapshot->sequence_number : 0)
              << " checkpoint_hash=" << (latest_snapshot ? latest_snapshot->hash : "none")
              << " chain_valid=" << (checkpoint_chain_valid ? 1 : 0)
              << '\n';
    return EXIT_SUCCESS;
}
