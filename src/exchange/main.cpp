#include "Exchange.h"
#include "utils/PublisherConfig.h"
#include <csignal>
#include <iostream>
#include <memory>
#include <string>

namespace {
    std::unique_ptr<Exchange> exchange;

    void signal_handler(const int signal)
    {
        std::cout << "\nReceived signal " << signal
                  << ", shutting down exchange..." << std::endl;
        if (exchange) { exchange->stop(); }
        exit(0);
    }

    bool parse_bool_flag(const std::string& value, bool& out)
    {
        if (value == "1" || value == "true" || value == "on" || value == "yes") { out = true; return true; }
        if (value == "0" || value == "false" || value == "off" || value == "no") { out = false; return true; }
        return false;
    }
} // namespace

void print_usage(const char* program_name)
{
    std::cout << "Usage: " << program_name << " [options]\n";
    std::cout << "Options:\n";
    std::cout << "  --mode <mode>                          Exchange mode: 'simulation' or 'client' (default: simulation)\n";
    std::cout << "  --sim-interval-ms <ms>                 Simulation order interval in ms (default: 200)\n";
    std::cout << "  --md-ip <address>                      Market data multicast IP (default: 239.1.1.1)\n";
    std::cout << "  --md-port <port>                       Market data multicast port (default: 9999)\n";
    std::cout << "  --md-interface <ip>                    Market data interface IP (default: 127.0.0.1)\n";
    std::cout << "  --dual-feed                            Publish the same event stream on Feed A and Feed B\n";
    std::cout << "  --feed-b-ip <address>                  Feed B multicast IP (default: 239.1.1.2)\n";
    std::cout << "  --feed-b-port <port>                   Feed B multicast port (default: 10000)\n";
    std::cout << "  --heartbeat-ms <ms>                    Market-data heartbeat interval (default: 1000)\n";
    std::cout << "  --history <events>                     Events retained for recovery (default: 65536; 0 disables)\n";
    std::cout << "  --snapshot-interval <N>                Create composite snapshot every N events (0 disables)\n";
    std::cout << "  --snapshot-retention <N>               Number of snapshots retained (default: 8)\n";
    std::cout << "  --snapshot-on-demand [0|1|true|false]  Create on-demand snapshot if none retained (default: on)\n";
    std::cout << "  --no-snapshot-on-demand                Disable on-demand snapshot creation\n";
    std::cout << "  --tamper-snapshot <mode>               none|book_value|sequence|previous_hash|hash (default: none)\n";
    std::cout << "  --recovery-port <port>                 Recovery TCP port (default: 9090; 0 disables)\n";
    std::cout << "  --recovery-policy <name>               Recovery scheduler: fcfs|round-robin (default: fcfs)\n";
    std::cout << "  --recovery-workers <N>                 Number of recovery worker threads (default: 1)\n";
    std::cout << "  --service-delay-us-per-request <us>    Modelled server capacity delay per request (default: 0)\n";
    std::cout << "  --service-delay-us-per-event <us>      Modelled server capacity delay per replayed event (default: 0)\n";
    std::cout << "  --subscriber-idle-timeout-s <sec>      Subscriber idle disconnect timeout (default: 10)\n";
    std::cout << "  --results-dir <dir>                    Directory to write recovery-requests.csv\n";
    std::cout << "  --run-id <id>                          Experiment run identifier\n";
    std::cout << "  --requests-csv <path>                  Explicit path for recovery-requests.csv\n";
    std::cout << "  --feed-a-loss <rate>                   Application-layer loss rate for Feed A [0,1]\n";
    std::cout << "  --feed-b-loss <rate>                   Application-layer loss rate for Feed B [0,1]\n";
    std::cout << "  --correlated-loss <rate>               Application-layer loss applied to both feeds [0,1]\n";
    std::cout << "  --fault-seed <N>                       Deterministic live fault seed (default: 42)\n";
    std::cout << "  --oe-port <port>                       Order entry TCP port (default: 8080, client mode only)\n";
    std::cout << "  --help, -h                             Show this help message\n";
}

int main(const int argc, char* argv[])
{
    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    auto mode = Exchange::Mode::SIMULATION;
    mdfeed::PublisherConfig md_config;
    uint16_t oe_port = 8080;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--mode" && i + 1 < argc) {
            if (std::string mode_str = argv[++i]; mode_str == "simulation") {
                mode = Exchange::Mode::SIMULATION;
            }
            else if (mode_str == "client") {
                mode = Exchange::Mode::CLIENT_ORDERS;
            }
            else {
                std::cerr << "Invalid mode: " << mode_str << std::endl;
                print_usage(argv[0]);
                return 1;
            }
        }
        else if (arg == "--sim-interval-ms" && i + 1 < argc) {
            md_config.sim_interval_ms = std::stoull(argv[++i]);
        }
        else if (arg == "--md-ip" && i + 1 < argc) {
            md_config.multicast_ip = argv[++i];
        }
        else if (arg == "--md-port" && i + 1 < argc) {
            md_config.multicast_port = static_cast<uint16_t>(std::stoul(argv[++i]));
        }
        else if (arg == "--md-interface" && i + 1 < argc) {
            md_config.interface_ip = argv[++i];
        }
        else if (arg == "--dual-feed") {
            md_config.dual_feed = true;
        }
        else if (arg == "--feed-b-ip" && i + 1 < argc) {
            md_config.feed_b_multicast_ip = argv[++i];
        }
        else if (arg == "--feed-b-port" && i + 1 < argc) {
            md_config.feed_b_multicast_port = static_cast<uint16_t>(std::stoul(argv[++i]));
        }
        else if (arg == "--heartbeat-ms" && i + 1 < argc) {
            md_config.heartbeat_interval = std::chrono::milliseconds(std::stoull(argv[++i]));
        }
        else if (arg == "--history" && i + 1 < argc) {
            md_config.recovery_history_events = std::stoull(argv[++i]);
        }
        else if (arg == "--snapshot-interval" && i + 1 < argc) {
            md_config.snapshot_interval_events = std::stoull(argv[++i]);
        }
        else if (arg == "--snapshot-retention" && i + 1 < argc) {
            md_config.snapshot_retention_count = std::stoull(argv[++i]);
        }
        else if (arg == "--snapshot-on-demand") {
            if (i + 1 < argc && argv[i + 1][0] != '-') {
                bool parsed = true;
                if (!parse_bool_flag(argv[++i], parsed)) {
                    std::cerr << "Invalid boolean for --snapshot-on-demand\n";
                    return 1;
                }
                md_config.snapshot_on_demand = parsed;
            } else {
                md_config.snapshot_on_demand = true;
            }
        }
        else if (arg == "--no-snapshot-on-demand") {
            md_config.snapshot_on_demand = false;
        }
        else if (arg == "--tamper-snapshot" && i + 1 < argc) {
            md_config.tamper_snapshot = argv[++i];
            if (!mdfeed::reliability::parse_tamper_mode(md_config.tamper_snapshot).has_value()) {
                std::cerr << "Invalid tamper mode: " << md_config.tamper_snapshot << std::endl;
                return 1;
            }
        }
        else if (arg == "--recovery-port" && i + 1 < argc) {
            md_config.recovery_port = static_cast<uint16_t>(std::stoul(argv[++i]));
        }
        else if (arg == "--recovery-policy" && i + 1 < argc) {
            md_config.recovery_scheduling_policy = argv[++i];
            if (md_config.recovery_scheduling_policy != "fcfs"
                && md_config.recovery_scheduling_policy != "round-robin") {
                std::cerr << "Invalid recovery policy: " << md_config.recovery_scheduling_policy << std::endl;
                return 1;
            }
        }
        else if (arg == "--recovery-workers" && i + 1 < argc) {
            md_config.recovery_workers = std::max<size_t>(1, std::stoull(argv[++i]));
        }
        else if (arg == "--service-delay-us-per-request" && i + 1 < argc) {
            md_config.service_delay_us_per_request = std::stoull(argv[++i]);
        }
        else if (arg == "--service-delay-us-per-event" && i + 1 < argc) {
            md_config.service_delay_us_per_event = std::stoull(argv[++i]);
        }
        else if (arg == "--subscriber-idle-timeout-s" && i + 1 < argc) {
            md_config.subscriber_idle_timeout_s = std::stoull(argv[++i]);
        }
        else if (arg == "--results-dir" && i + 1 < argc) {
            md_config.results_dir = argv[++i];
        }
        else if (arg == "--run-id" && i + 1 < argc) {
            md_config.run_id = argv[++i];
        }
        else if (arg == "--requests-csv" && i + 1 < argc) {
            md_config.requests_csv_path = argv[++i];
        }
        else if (arg == "--feed-a-loss" && i + 1 < argc) {
            md_config.feed_a_loss_rate = std::stod(argv[++i]);
        }
        else if (arg == "--feed-b-loss" && i + 1 < argc) {
            md_config.feed_b_loss_rate = std::stod(argv[++i]);
        }
        else if (arg == "--correlated-loss" && i + 1 < argc) {
            md_config.correlated_loss_rate = std::stod(argv[++i]);
        }
        else if (arg == "--fault-seed" && i + 1 < argc) {
            md_config.fault_random_seed = std::stoull(argv[++i]);
        }
        else if (arg == "--oe-port" && i + 1 < argc) {
            oe_port = static_cast<uint16_t>(std::stoul(argv[++i]));
        }
        else if (arg == "--help" || arg == "-h") {
            print_usage(argv[0]);
            return 0;
        }
        else {
            std::cerr << "Unknown argument: " << arg << std::endl;
            print_usage(argv[0]);
            return 1;
        }
    }

    if (md_config.feed_a_loss_rate < 0.0 || md_config.feed_a_loss_rate > 1.0
        || md_config.feed_b_loss_rate < 0.0 || md_config.feed_b_loss_rate > 1.0
        || md_config.correlated_loss_rate < 0.0 || md_config.correlated_loss_rate > 1.0) {
        std::cerr << "Feed loss rates must be in [0, 1]" << std::endl;
        return 1;
    }

    std::cout << "=== ENHANCED EXCHANGE ===" << std::endl;
    std::cout << "Mode: "
              << (mode == Exchange::Mode::SIMULATION ? "SIMULATION" : "CLIENT_ORDERS")
              << std::endl;
    std::cout << "Market Data: " << md_config.multicast_ip << ":"
              << md_config.multicast_port << std::endl;
    std::cout << "MD Interface: " << md_config.interface_ip << std::endl;
    std::cout << "Recovery history: " << md_config.recovery_history_events
              << " events" << std::endl;
    std::cout << "Snapshot interval: " << md_config.snapshot_interval_events
              << " events; retention: " << md_config.snapshot_retention_count
              << "; on-demand: " << (md_config.snapshot_on_demand ? "on" : "off") << std::endl;
    std::cout << "Recovery TCP: "
              << (md_config.recovery_port == 0 ? "disabled" : std::to_string(md_config.recovery_port))
              << std::endl;
    std::cout << "Recovery scheduling policy: " << md_config.recovery_scheduling_policy
              << " (workers=" << md_config.recovery_workers
              << ", delay_us_per_req=" << md_config.service_delay_us_per_request
              << ", delay_us_per_event=" << md_config.service_delay_us_per_event << ")" << std::endl;
    std::cout << "Application-layer feed loss A/B/correlated: " << md_config.feed_a_loss_rate << '/'
              << md_config.feed_b_loss_rate << '/' << md_config.correlated_loss_rate
              << " (seed " << md_config.fault_random_seed << ")" << std::endl;
    if (md_config.dual_feed) {
        std::cout << "Market Data Feed B: " << md_config.feed_b_multicast_ip << ':'
                  << md_config.feed_b_multicast_port << std::endl;
    }
    if (mode == Exchange::Mode::CLIENT_ORDERS) {
        std::cout << "Order Entry Port: " << oe_port << std::endl;
    }

    std::cout << "Press Ctrl+C to stop..." << std::endl;

    try {
        exchange = std::make_unique<Exchange>(mode, md_config, oe_port);
        exchange->run();
    }
    catch (const std::exception& e) {
        std::cerr << "Exchange error: " << e.what() << std::endl;
        return 1;
    }

    std::cout << "Enhanced exchange stopped." << std::endl;
    return 0;
}
