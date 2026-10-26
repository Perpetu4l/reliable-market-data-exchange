#pragma once

#include "reliability/ReliabilityTypes.h"

#include <cstdint>
#include <deque>
#include <optional>
#include <mutex>
#include <string>
#include <string_view>
#include <vector>

namespace mdfeed::reliability {

struct SnapshotConfig {
    uint64_t interval{0}; // zero disables automatic checkpoint scheduling
    size_t retention_count{8};
    bool enabled{false};
};

struct SnapshotPriceLevel {
    uint64_t price{0};
    uint64_t quantity{0};
    Side side{Side::BUY};
    // Zero is retained for backwards-compatible single-instrument snapshots.
    // Live exchange checkpoints use an instrument id per level and set the
    // snapshot-level instrument_id to zero, denoting a composite checkpoint.
    uint32_t instrument_id{0};
};

struct OrderBookSnapshot {
    uint64_t sequence_number{0};
    uint32_t instrument_id{0};
    uint64_t timestamp_ns{0};
    std::vector<SnapshotPriceLevel> book_state;
    std::string previous_hash;
    std::string hash;
};

// Checkpoints are tamper-evident, not a tamper-proof database. Hash material
// uses fixed-width big-endian fields and levels sorted by (side, price), then
// includes the previous checkpoint's SHA-256 hex digest.
class SnapshotHasher {
public:
    [[nodiscard]] std::string compute_hash(const OrderBookSnapshot& snapshot, std::string_view previous_hash) const;
    [[nodiscard]] bool verify(const OrderBookSnapshot& snapshot) const;
    [[nodiscard]] bool verify_chain(const std::deque<OrderBookSnapshot>& snapshots) const;
};

class SnapshotStore {
public:
    explicit SnapshotStore(size_t retention_count = 8);
    void store(OrderBookSnapshot snapshot);
    [[nodiscard]] std::optional<OrderBookSnapshot> latest() const;
    [[nodiscard]] std::optional<OrderBookSnapshot> for_sequence(uint64_t sequence) const;
    [[nodiscard]] std::optional<OrderBookSnapshot> at_or_after(uint64_t sequence) const;
    [[nodiscard]] const std::deque<OrderBookSnapshot>& all() const { return snapshots_; }
private:
    size_t retention_count_;
    std::deque<OrderBookSnapshot> snapshots_;
};

class SnapshotManager {
public:
    explicit SnapshotManager(SnapshotConfig config = {});
    [[nodiscard]] bool should_checkpoint(uint64_t sequence) const;
    [[nodiscard]] OrderBookSnapshot create_snapshot(uint64_t sequence, uint32_t instrument_id,
        std::vector<SnapshotPriceLevel> book_state, uint64_t timestamp_ns) const;
    void store_snapshot(OrderBookSnapshot snapshot);
    [[nodiscard]] std::optional<OrderBookSnapshot> get_latest_snapshot() const;
    [[nodiscard]] std::optional<OrderBookSnapshot> get_snapshot_for_sequence(uint64_t sequence) const;
    // Recovery needs a checkpoint that already includes the missing range, so
    // sequence tracking can safely advance beyond it.
    [[nodiscard]] std::optional<OrderBookSnapshot> get_snapshot_at_or_after(uint64_t sequence) const;
    [[nodiscard]] size_t snapshot_count() const;
    [[nodiscard]] bool verify_snapshots() const;
private:
    SnapshotConfig config_;
    SnapshotHasher hasher_;
    mutable std::mutex mutex_;
    SnapshotStore store_;
};

} // namespace mdfeed::reliability
