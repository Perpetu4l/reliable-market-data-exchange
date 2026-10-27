#include "client/OrderEntryClient.h"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <random>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;

struct WorkloadConfig {
    std::string server{"127.0.0.1"};
    uint16_t port{8080};
    uint64_t client_id{1};
    std::string symbol{"AAPL"};
    std::string side{"alternating"};
    double duration_seconds{5.0};
    double rate_per_second{20.0};
    uint64_t seed{1};
};

struct WorkloadMetrics {
    uint64_t generated{0};
    uint64_t sent{0};
    uint64_t acknowledgements{0};
    uint64_t rejected{0};
    uint64_t executions{0};
    uint64_t timed_out{0};
    std::vector<uint64_t> acknowledgement_rtt_us;
};

void print_usage(const char* program)
{
    std::cout << "Usage: " << program << " [options]\n"
              << "  --server <IPv4>       Exchange address (default: 127.0.0.1)\n"
              << "  --port <port>         Exchange TCP port (default: 8080)\n"
              << "  --client-id <id>      Distinct client identity (default: 1)\n"
              << "  --symbol <symbol>     Instrument (default: AAPL)\n"
              << "  --side <buy|sell|alternating|random> (default: alternating)\n"
              << "  --duration <seconds>  Workload duration (default: 5)\n"
              << "  --rate <orders/s>     Offered order rate (default: 20)\n"
              << "  --seed <seed>         Random seed (default: 1)\n";
}

bool parse_arguments(const int argc, char* argv[], WorkloadConfig& config)
{
    for (int i = 1; i < argc; ++i) {
        const std::string argument = argv[i];
        const auto need_value = [&](const char* option) -> const char* {
            if (i + 1 >= argc) {
                std::cerr << "Missing value for " << option << '\n';
                return nullptr;
            }
            return argv[++i];
        };
        if (argument == "--server") {
            if (const auto* value = need_value("--server")) config.server = value; else return false;
        } else if (argument == "--port") {
            if (const auto* value = need_value("--port")) config.port = static_cast<uint16_t>(std::stoul(value)); else return false;
        } else if (argument == "--client-id") {
            if (const auto* value = need_value("--client-id")) config.client_id = std::stoull(value); else return false;
        } else if (argument == "--symbol") {
            if (const auto* value = need_value("--symbol")) config.symbol = value; else return false;
        } else if (argument == "--side") {
            if (const auto* value = need_value("--side")) config.side = value; else return false;
        } else if (argument == "--duration") {
            if (const auto* value = need_value("--duration")) config.duration_seconds = std::stod(value); else return false;
        } else if (argument == "--rate") {
            if (const auto* value = need_value("--rate")) config.rate_per_second = std::stod(value); else return false;
        } else if (argument == "--seed") {
            if (const auto* value = need_value("--seed")) config.seed = std::stoull(value); else return false;
        } else if (argument == "--help" || argument == "-h") {
            print_usage(argv[0]);
            std::exit(0);
        } else {
            std::cerr << "Unknown option: " << argument << '\n';
            return false;
        }
    }
    if (config.duration_seconds <= 0.0 || config.rate_per_second <= 0.0) {
        std::cerr << "Duration and rate must be greater than zero\n";
        return false;
    }
    if (config.side != "buy" && config.side != "sell" && config.side != "alternating" && config.side != "random") {
        std::cerr << "Unsupported side mode: " << config.side << '\n';
        return false;
    }
    return true;
}

uint64_t percentile(std::vector<uint64_t> samples, const double fraction)
{
    if (samples.empty()) return 0;
    std::sort(samples.begin(), samples.end());
    const auto index = static_cast<size_t>(std::ceil(fraction * static_cast<double>(samples.size()))) - 1;
    return samples[std::min(index, samples.size() - 1)];
}

void process_responses(orderentry::OrderEntryClient& client,
    std::unordered_map<uint64_t, Clock::time_point>& outstanding,
    WorkloadMetrics& metrics)
{
    orderentry::OrderEntryClient::ReceivedMessage response;
    while (client.receive_message(response, 0)) {
        if (response.type == orderentry::MessageType::ORDER_ACK && response.bytes.size() == sizeof(orderentry::OrderAckMessage)) {
            orderentry::OrderAckMessage acknowledgement{};
            std::memcpy(&acknowledgement, response.bytes.data(), sizeof(acknowledgement));
            ++metrics.acknowledgements;
            if (const auto it = outstanding.find(acknowledgement.client_order_id); it != outstanding.end()) {
                const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(Clock::now() - it->second).count();
                metrics.acknowledgement_rtt_us.push_back(static_cast<uint64_t>(elapsed));
                outstanding.erase(it);
            }
        } else if (response.type == orderentry::MessageType::ORDER_REJECT) {
            ++metrics.rejected;
        } else if (response.type == orderentry::MessageType::EXECUTION_REPORT) {
            ++metrics.executions;
        }
    }
}

} // namespace

int main(const int argc, char* argv[])
{
    WorkloadConfig config;
    if (!parse_arguments(argc, argv, config)) {
        print_usage(argv[0]);
        return 2;
    }

    orderentry::OrderEntryClient client;
    if (!client.connect(config.server, config.port)) {
        std::cerr << "WORKLOAD_ERROR client_id=" << config.client_id << " reason=connect_failed\n";
        return 3;
    }
    if (!client.send_client_hello(config.client_id, "workload-" + std::to_string(config.client_id))) {
        std::cerr << "WORKLOAD_ERROR client_id=" << config.client_id << " reason=hello_send_failed\n";
        return 4;
    }
    orderentry::OrderEntryClient::ReceivedMessage welcome;
    if (!client.receive_message(welcome, 1000) || welcome.type != orderentry::MessageType::SERVER_WELCOME) {
        std::cerr << "WORKLOAD_ERROR client_id=" << config.client_id << " reason=hello_timeout\n";
        return 5;
    }

    std::cout << "WORKLOAD_READY client_id=" << config.client_id
              << " server=" << config.server << ':' << config.port << std::endl;

    WorkloadMetrics metrics;
    std::unordered_map<uint64_t, Clock::time_point> outstanding;
    std::mt19937_64 random(config.seed);
    std::bernoulli_distribution side_distribution;
    const auto interval = std::chrono::duration<double>(1.0 / config.rate_per_second);
    const auto finish_at = Clock::now() + std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double>(config.duration_seconds));
    auto next_send = Clock::now();

    while (Clock::now() < finish_at && client.is_connected()) {
        process_responses(client, outstanding, metrics);
        const auto now = Clock::now();
        if (now < next_send) {
            std::this_thread::sleep_for(std::min(std::chrono::milliseconds(1),
                std::chrono::duration_cast<std::chrono::milliseconds>(next_send - now)));
            continue;
        }

        const uint64_t order_id = (config.client_id << 48U) | (++metrics.generated & 0x0000FFFFFFFFFFFFULL);
        const bool buy = config.side == "buy" || (config.side == "alternating" && (metrics.generated % 2) != 0)
            || (config.side == "random" && side_distribution(random));
        const auto sent_at = Clock::now();
        if (client.send_new_order(order_id, config.symbol, buy ? orderentry::Side::BUY : orderentry::Side::SELL,
            50000, 10)) {
            ++metrics.sent;
            outstanding.emplace(order_id, sent_at);
        } else {
            break;
        }
        next_send += std::chrono::duration_cast<Clock::duration>(interval);
    }

    const auto response_deadline = Clock::now() + std::chrono::seconds(2);
    while (!outstanding.empty() && Clock::now() < response_deadline && client.is_connected()) {
        orderentry::OrderEntryClient::ReceivedMessage ignored;
        if (!client.receive_message(ignored, 20)) continue;
        // Put the one blocking response through the same accounting path.
        // The next non-blocking drain handles it only after restoring it is not
        // possible, therefore decode this result directly below.
        if (ignored.type == orderentry::MessageType::ORDER_ACK && ignored.bytes.size() == sizeof(orderentry::OrderAckMessage)) {
            orderentry::OrderAckMessage acknowledgement{};
            std::memcpy(&acknowledgement, ignored.bytes.data(), sizeof(acknowledgement));
            ++metrics.acknowledgements;
            if (const auto it = outstanding.find(acknowledgement.client_order_id); it != outstanding.end()) {
                const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(Clock::now() - it->second).count();
                metrics.acknowledgement_rtt_us.push_back(static_cast<uint64_t>(elapsed));
                outstanding.erase(it);
            }
        } else if (ignored.type == orderentry::MessageType::ORDER_REJECT) {
            ++metrics.rejected;
        } else if (ignored.type == orderentry::MessageType::EXECUTION_REPORT) {
            ++metrics.executions;
        }
        process_responses(client, outstanding, metrics);
    }
    metrics.timed_out = outstanding.size();

    const auto p50 = percentile(metrics.acknowledgement_rtt_us, 0.50);
    const auto p95 = percentile(metrics.acknowledgement_rtt_us, 0.95);
    const auto p99 = percentile(metrics.acknowledgement_rtt_us, 0.99);
    std::cout << "WORKLOAD_RESULT"
              << " client_id=" << config.client_id
              << " generated=" << metrics.generated
              << " sent=" << metrics.sent
              << " acknowledgements=" << metrics.acknowledgements
              << " rejected=" << metrics.rejected
              << " executions=" << metrics.executions
              << " timed_out=" << metrics.timed_out
              << " ack_p50_us=" << p50
              << " ack_p95_us=" << p95
              << " ack_p99_us=" << p99
              << std::endl;
    client.disconnect();
    return metrics.timed_out == 0 ? 0 : 6;
}
