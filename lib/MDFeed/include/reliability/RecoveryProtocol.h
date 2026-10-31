#pragma once

#include "reliability/BookBuilder.h"
#include "reliability/RecoveryHistory.h"
#include "reliability/Snapshot.h"

#include <atomic>
#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>

namespace mdfeed::reliability {

enum class RecoveryStatus { OK, TOO_OLD, BAD_REQUEST, ERROR };

enum class TamperMode : uint8_t {
    NONE = 0,
    BOOK_VALUE = 1,
    SEQUENCE = 2,
    PREVIOUS_HASH = 3,
    HASH = 4,
};

[[nodiscard]] inline const char* tamper_mode_name(const TamperMode mode)
{
    switch (mode) {
    case TamperMode::NONE: return "none";
    case TamperMode::BOOK_VALUE: return "book_value";
    case TamperMode::SEQUENCE: return "sequence";
    case TamperMode::PREVIOUS_HASH: return "previous_hash";
    case TamperMode::HASH: return "hash";
    }
    return "none";
}

[[nodiscard]] inline std::optional<TamperMode> parse_tamper_mode(const std::string_view text)
{
    if (text == "none" || text.empty()) return TamperMode::NONE;
    if (text == "book_value") return TamperMode::BOOK_VALUE;
    if (text == "sequence") return TamperMode::SEQUENCE;
    if (text == "previous_hash") return TamperMode::PREVIOUS_HASH;
    if (text == "hash") return TamperMode::HASH;
    return std::nullopt;
}

struct RecoveryRequest {
    std::string subscriber_id;
    MissingRange missing_range;
    // Assigned by the TCP server after decoding. This is an in-process
    // correlation token and is intentionally not part of the wire format.
    uint64_t request_id{0};
};

struct RecoveryResponse {
    RecoveryStatus status{RecoveryStatus::ERROR};
    std::vector<MarketDataEvent> events;
    std::optional<OrderBookSnapshot> snapshot;
    std::string detail;
};

struct RecoveryStatusInfo {
    uint64_t latest_sequence{0};
    uint64_t history_minimum_sequence{0};
    std::string latest_book_digest{};
    uint8_t protocol_version{2};
};

// Application-facing recovery service.
class RecoveryServer {
public:
    RecoveryServer(std::shared_ptr<RecoveryHistory> history,
        std::shared_ptr<SnapshotManager> snapshots,
        std::shared_ptr<BookBuilder> book_builder = nullptr,
        bool on_demand_snapshots = true,
        TamperMode tamper_mode = TamperMode::NONE);

    // Atomically appends event to history, updates authoritative book builder,
    // and creates periodic checkpoints under one lock so status() and
    // send_snapshot() always observe a sequence-consistent book digest/state.
    void record_published_event(const MarketDataEvent& event);

    [[nodiscard]] RecoveryResponse request_missing_data(const RecoveryRequest& request) const;
    [[nodiscard]] RecoveryResponse send_snapshot(uint64_t sequence) const;
    [[nodiscard]] RecoveryStatusInfo status() const;

    void set_on_demand_snapshots(bool enabled);
    [[nodiscard]] bool on_demand_snapshots() const;
    void set_tamper_mode(TamperMode mode);
    [[nodiscard]] TamperMode tamper_mode() const;

    [[nodiscard]] uint64_t on_demand_snapshots_created() const { return on_demand_snapshots_created_.load(); }
    void add_snapshot_bytes_sent(uint64_t bytes) const { snapshot_bytes_sent_.fetch_add(bytes); }
    [[nodiscard]] uint64_t snapshot_bytes_sent() const { return snapshot_bytes_sent_.load(); }

private:
    static void apply_tamper(OrderBookSnapshot& snapshot, TamperMode mode);

    std::shared_ptr<RecoveryHistory> history_;
    std::shared_ptr<SnapshotManager> snapshots_;
    std::shared_ptr<BookBuilder> book_builder_;
    bool on_demand_snapshots_{true};
    TamperMode tamper_mode_{TamperMode::NONE};
    uint64_t latest_timestamp_ns_{0};
    mutable std::mutex state_mutex_;
    mutable std::atomic<uint64_t> on_demand_snapshots_created_{0};
    mutable std::atomic<uint64_t> snapshot_bytes_sent_{0};
};

class RecoveryClient {
public:
    explicit RecoveryClient(std::string subscriber_id);
    [[nodiscard]] RecoveryRequest make_request(MissingRange range) const;
    [[nodiscard]] RecoveryResponse recover(RecoveryServer& server, MissingRange range) const;
private:
    std::string subscriber_id_;
};

} // namespace mdfeed::reliability
