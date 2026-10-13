#include "reliability/Snapshot.h"

#include <iomanip>
#include <sstream>

namespace mdfeed::reliability {
namespace {
uint64_t fnv1a(const std::string& value)
{
    uint64_t hash = 14695981039346656037ULL;
    for (const unsigned char byte : value) { hash = (hash ^ byte) * 1099511628211ULL; }
    return hash;
}
}

std::string SnapshotHasher::compute_hash(const OrderBookSnapshot& snapshot, const std::string_view previous_hash) const
{
    std::ostringstream material;
    material << snapshot.sequence_number << ':' << snapshot.instrument_id << ':' << snapshot.timestamp_ns << ':' << previous_hash;
    for (const auto& level : snapshot.book_state) {
        material << ':' << level.price << ':' << level.quantity << ':' << static_cast<unsigned>(level.side);
    }
    std::ostringstream result;
    result << std::hex << std::setw(16) << std::setfill('0') << fnv1a(material.str());
    return result.str();
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

SnapshotManager::SnapshotManager(SnapshotConfig config) : config_(config), store_(config.retention_count) {}
bool SnapshotManager::should_checkpoint(const uint64_t sequence) const
{
    return config_.enabled && config_.interval != 0 && sequence % config_.interval == 0;
}
OrderBookSnapshot SnapshotManager::create_snapshot(const uint64_t sequence, const uint32_t instrument_id,
    std::vector<SnapshotPriceLevel> book_state, const uint64_t timestamp_ns) const
{
    OrderBookSnapshot snapshot;
    snapshot.sequence_number = sequence;
    snapshot.instrument_id = instrument_id;
    snapshot.timestamp_ns = timestamp_ns;
    snapshot.book_state = std::move(book_state);
    if (const auto previous = store_.latest()) snapshot.previous_hash = previous->hash;
    snapshot.hash = hasher_.compute_hash(snapshot, snapshot.previous_hash);
    return snapshot;
}
void SnapshotManager::store_snapshot(OrderBookSnapshot snapshot) { store_.store(std::move(snapshot)); }
std::optional<OrderBookSnapshot> SnapshotManager::get_latest_snapshot() const { return store_.latest(); }
std::optional<OrderBookSnapshot> SnapshotManager::get_snapshot_for_sequence(const uint64_t sequence) const { return store_.for_sequence(sequence); }
bool SnapshotManager::verify_snapshots() const { return hasher_.verify_chain(store_.all()); }

} // namespace mdfeed::reliability
