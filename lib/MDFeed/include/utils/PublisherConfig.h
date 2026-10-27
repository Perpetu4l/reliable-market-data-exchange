#pragma once
#include <string>
#include <chrono>
#include <cstddef>
#include <cstdint>

namespace mdfeed {
struct PublisherConfig {
    std::string multicast_ip = "239.1.1.1";
    uint16_t multicast_port = 9999;
    bool dual_feed = false;
    std::string feed_b_multicast_ip = "239.1.1.2";
    uint16_t feed_b_multicast_port = 10000;
    std::string interface_ip = "127.0.0.1";
    std::chrono::milliseconds heartbeat_interval{1000};
    bool use_busy_wait = false;
    std::chrono::microseconds spin_duration{100};
    size_t ring_buffer_size = 65536;
    // Number of published (non-heartbeat) market-data messages retained for
    // subscriber-initiated TCP recovery.  Zero disables in-memory recovery.
    size_t recovery_history_events = 65536;
    uint64_t snapshot_interval_events = 0;
    size_t snapshot_retention_count = 8;
    // TCP recovery is enabled when this is non-zero. It is intentionally a
    // separate endpoint from order entry because recovery clients have a
    // different framed protocol.
    uint16_t recovery_port = 9090;
    std::string recovery_scheduling_policy{"fcfs"};
    // Deterministic application-layer withholding for live experiments. These
    // are not operating-system or physical-network packet-loss measurements.
    double feed_a_loss_rate{0.0};
    double feed_b_loss_rate{0.0};
    double correlated_loss_rate{0.0};
    uint64_t fault_random_seed{42};
};
}

