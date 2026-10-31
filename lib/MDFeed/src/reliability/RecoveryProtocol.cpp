#include "reliability/RecoveryProtocol.h"

#include <algorithm>

namespace mdfeed::reliability {

RecoveryServer::RecoveryServer(std::shared_ptr<RecoveryHistory> history,
    std::shared_ptr<SnapshotManager> snapshots,
    std::shared_ptr<BookBuilder> book_builder,
    const bool on_demand_snapshots,
    const TamperMode tamper_mode)
    : history_(std::move(history)),
      snapshots_(std::move(snapshots)),
      book_builder_(book_builder ? std::move(book_builder) : std::make_shared<BookBuilder>()),
      on_demand_snapshots_(on_demand_snapshots),
      tamper_mode_(tamper_mode)
{
}

void RecoveryServer::record_published_event(const MarketDataEvent& event)
{
    std::lock_guard lock(state_mutex_);
    if (history_) {
        history_->append(event);
    }
    latest_timestamp_ns_ = event.header.timestamp_ns;
    const bool applied = book_builder_ && book_builder_->apply(event);
    if (applied && snapshots_ && snapshots_->should_checkpoint(event.header.sequence_number)) {
        snapshots_->store_snapshot(snapshots_->create_snapshot(
            event.header.sequence_number, 0, book_builder_->all_levels(), event.header.timestamp_ns));
    }
}

RecoveryResponse RecoveryServer::request_missing_data(const RecoveryRequest& request) const
{
    if (!request.missing_range.valid()) {
        return {RecoveryStatus::BAD_REQUEST, {}, std::nullopt, "invalid sequence range"};
    }
    std::lock_guard lock(state_mutex_);
    if (!history_) return {RecoveryStatus::ERROR, {}, std::nullopt, "history service unavailable"};
    const auto events = history_->get_range(request.missing_range);
    if (!events) return {RecoveryStatus::TOO_OLD, {}, std::nullopt, "requested range is outside retained history"};
    return {RecoveryStatus::OK, *events, std::nullopt, {}};
}

void RecoveryServer::apply_tamper(OrderBookSnapshot& snapshot, const TamperMode mode)
{
    switch (mode) {
    case TamperMode::NONE:
        return;
    case TamperMode::BOOK_VALUE:
        if (snapshot.book_state.empty()) {
            snapshot.book_state.push_back({100, 1, Side::BUY, 1});
        } else {
            snapshot.book_state.front().quantity += 1;
        }
        return;
    case TamperMode::SEQUENCE:
        snapshot.sequence_number += 1;
        return;
    case TamperMode::PREVIOUS_HASH:
        snapshot.previous_hash = (snapshot.previous_hash == "tampered_previous_hash")
            ? "tampered_previous_hash_alt"
            : "tampered_previous_hash";
        return;
    case TamperMode::HASH:
        if (snapshot.hash.empty()) {
            snapshot.hash = "0000000000000000000000000000000000000000000000000000000000000000";
        } else {
            snapshot.hash[0] = (snapshot.hash[0] == '0') ? '1' : '0';
        }
        return;
    }
}

RecoveryResponse RecoveryServer::send_snapshot(const uint64_t sequence) const
{
    std::lock_guard lock(state_mutex_);
    if (!snapshots_) return {RecoveryStatus::ERROR, {}, std::nullopt, "snapshot service unavailable"};
    auto snapshot = snapshots_->get_snapshot_at_or_after(sequence);
    if (!snapshot && on_demand_snapshots_) {
        const uint64_t history_latest = history_ ? history_->latest_sequence().value_or(0) : 0;
        const uint64_t book_latest = book_builder_ ? book_builder_->last_sequence() : 0;
        const uint64_t latest_seq = std::max(history_latest, book_latest);
        if (latest_seq >= sequence && latest_seq > 0) {
            auto created = snapshots_->create_snapshot(
                latest_seq, 0,
                book_builder_ ? book_builder_->all_levels() : std::vector<SnapshotPriceLevel>{},
                latest_timestamp_ns_);
            snapshots_->store_snapshot(created);
            ++on_demand_snapshots_created_;
            snapshot = std::move(created);
        }
    }
    if (!snapshot) return {RecoveryStatus::TOO_OLD, {}, std::nullopt, "no retained snapshot for sequence"};
    if (tamper_mode_ != TamperMode::NONE) {
        apply_tamper(*snapshot, tamper_mode_);
    }
    return {RecoveryStatus::OK, {}, snapshot, {}};
}

RecoveryStatusInfo RecoveryServer::status() const
{
    std::lock_guard lock(state_mutex_);
    const uint64_t history_latest = history_ ? history_->latest_sequence().value_or(0) : 0;
    const uint64_t book_latest = book_builder_ ? book_builder_->last_sequence() : 0;
    const uint64_t latest_seq = std::max(history_latest, book_latest);
    const uint64_t min_seq = history_ ? history_->minimum_sequence().value_or(0) : 0;
    const std::string digest = book_builder_ ? book_builder_->digest() : BookBuilder{}.digest();
    return {latest_seq, min_seq, digest, 2};
}

void RecoveryServer::set_on_demand_snapshots(const bool enabled)
{
    std::lock_guard lock(state_mutex_);
    on_demand_snapshots_ = enabled;
}

bool RecoveryServer::on_demand_snapshots() const
{
    std::lock_guard lock(state_mutex_);
    return on_demand_snapshots_;
}

void RecoveryServer::set_tamper_mode(const TamperMode mode)
{
    std::lock_guard lock(state_mutex_);
    tamper_mode_ = mode;
}

TamperMode RecoveryServer::tamper_mode() const
{
    std::lock_guard lock(state_mutex_);
    return tamper_mode_;
}

RecoveryClient::RecoveryClient(std::string subscriber_id) : subscriber_id_(std::move(subscriber_id)) {}
RecoveryRequest RecoveryClient::make_request(const MissingRange range) const { return {subscriber_id_, range}; }
RecoveryResponse RecoveryClient::recover(RecoveryServer& server, const MissingRange range) const
{
    return server.request_missing_data(make_request(range));
}

} // namespace mdfeed::reliability
