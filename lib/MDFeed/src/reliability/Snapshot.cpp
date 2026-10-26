#include "reliability/Snapshot.h"
#include "reliability/Sha256.h"

#include <algorithm>
#include <vector>

namespace mdfeed::reliability {
namespace {
template <typename T>
void append_big_endian(std::vector<uint8_t>& output, const T value)
{
    for (int shift = static_cast<int>(sizeof(T) * 8U) - 8; shift >= 0; shift -= 8) {
        output.push_back(static_cast<uint8_t>(value >> shift));
    }
}
}

std::string SnapshotHasher::compute_hash(const OrderBookSnapshot& snapshot, const std::string_view previous_hash) const
{
    std::vector<uint8_t> material;
    material.reserve(32 + snapshot.book_state.size() * 21 + previous_hash.size());
    append_big_endian(material, snapshot.sequence_number);
    append_big_endian(material, snapshot.instrument_id);
    append_big_endian(material, snapshot.timestamp_ns);
    auto levels = snapshot.book_state;
    std::sort(levels.begin(), levels.end(), [](const SnapshotPriceLevel& lhs, const SnapshotPriceLevel& rhs) {
        if (lhs.instrument_id != rhs.instrument_id) return lhs.instrument_id < rhs.instrument_id;
        if (lhs.side != rhs.side) return static_cast<uint8_t>(lhs.side) < static_cast<uint8_t>(rhs.side);
        return lhs.price < rhs.price;
    });
    append_big_endian(material, static_cast<uint32_t>(levels.size()));
    for (const auto& level : levels) {
        append_big_endian(material, level.instrument_id);
        append_big_endian(material, static_cast<uint8_t>(level.side));
        append_big_endian(material, level.price);
        append_big_endian(material, level.quantity);
    }
    material.insert(material.end(), previous_hash.begin(), previous_hash.end());
    return Sha256::hex_digest(material);
}

bool SnapshotHasher::verify(const OrderBookSnapshot& snapshot) const
{
    return snapshot.hash == compute_hash(snapshot, snapshot.previous_hash);
}

bool SnapshotHasher::verify_chain(const std::deque<OrderBookSnapshot>& snapshots) const
{
    if (snapshots.empty()) return true;

    // Retention may have evicted the ancestor of the first retained snapshot.
    // Its own hash remains verifiable because previous_hash is part of the
    // canonical material; links are then verified within the retained window.
    auto current = snapshots.begin();
    if (!verify(*current)) return false;
    std::string expected_previous = current->hash;
    for (++current; current != snapshots.end(); ++current) {
        if (current->previous_hash != expected_previous || !verify(*current)) return false;
        expected_previous = current->hash;
    }
    return true;
}

SnapshotStore::SnapshotStore(const size_t retention_count) : retention_count_(retention_count) {}
void SnapshotStore::store(OrderBookSnapshot snapshot)
{
    if (retention_count_ == 0) return;
    snapshots_.push_back(std::move(snapshot));
    while (snapshots_.size() > retention_count_) snapshots_.pop_front();
}
std::optional<OrderBookSnapshot> SnapshotStore::latest() const
{
    if (snapshots_.empty()) return std::nullopt;
    return snapshots_.back();
}
std::optional<OrderBookSnapshot> SnapshotStore::for_sequence(const uint64_t sequence) const
{
    for (auto it = snapshots_.rbegin(); it != snapshots_.rend(); ++it) {
        if (it->sequence_number <= sequence) return *it;
    }
    return std::nullopt;
}
std::optional<OrderBookSnapshot> SnapshotStore::at_or_after(const uint64_t sequence) const
{
    for (const auto& snapshot : snapshots_) {
        if (snapshot.sequence_number >= sequence) return snapshot;
    }
    return std::nullopt;
}

SnapshotManager::SnapshotManager(SnapshotConfig config) : config_(config), store_(config.retention_count) {}
bool SnapshotManager::should_checkpoint(const uint64_t sequence) const
{
    return config_.enabled && config_.interval != 0 && sequence % config_.interval == 0;
}
OrderBookSnapshot SnapshotManager::create_snapshot(const uint64_t sequence, const uint32_t instrument_id,
    std::vector<SnapshotPriceLevel> book_state, const uint64_t timestamp_ns) const
{
    std::lock_guard lock(mutex_);
    OrderBookSnapshot snapshot;
    snapshot.sequence_number = sequence;
    snapshot.instrument_id = instrument_id;
    snapshot.timestamp_ns = timestamp_ns;
    snapshot.book_state = std::move(book_state);
    if (const auto previous = store_.latest()) snapshot.previous_hash = previous->hash;
    snapshot.hash = hasher_.compute_hash(snapshot, snapshot.previous_hash);
    return snapshot;
}
void SnapshotManager::store_snapshot(OrderBookSnapshot snapshot)
{
    std::lock_guard lock(mutex_);
    store_.store(std::move(snapshot));
}
std::optional<OrderBookSnapshot> SnapshotManager::get_latest_snapshot() const
{
    std::lock_guard lock(mutex_);
    return store_.latest();
}
std::optional<OrderBookSnapshot> SnapshotManager::get_snapshot_for_sequence(const uint64_t sequence) const
{
    std::lock_guard lock(mutex_);
    return store_.for_sequence(sequence);
}
std::optional<OrderBookSnapshot> SnapshotManager::get_snapshot_at_or_after(const uint64_t sequence) const
{
    std::lock_guard lock(mutex_);
    return store_.at_or_after(sequence);
}
size_t SnapshotManager::snapshot_count() const
{
    std::lock_guard lock(mutex_);
    return store_.all().size();
}
bool SnapshotManager::verify_snapshots() const
{
    std::lock_guard lock(mutex_);
    return hasher_.verify_chain(store_.all());
}

} // namespace mdfeed::reliability
