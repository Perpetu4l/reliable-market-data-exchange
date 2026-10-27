#include "reliability/RecoveryProtocol.h"

namespace mdfeed::reliability {

RecoveryServer::RecoveryServer(std::shared_ptr<RecoveryHistory> history, std::shared_ptr<SnapshotManager> snapshots)
    : history_(std::move(history)), snapshots_(std::move(snapshots)) {}

RecoveryResponse RecoveryServer::request_missing_data(const RecoveryRequest& request) const
{
    if (!request.missing_range.valid()) return {RecoveryStatus::BAD_REQUEST, {}, std::nullopt, "invalid sequence range"};
    if (!history_) return {RecoveryStatus::ERROR, {}, std::nullopt, "history service unavailable"};
    const auto events = history_->get_range(request.missing_range);
    if (!events) return {RecoveryStatus::TOO_OLD, {}, std::nullopt, "requested range is outside retained history"};
    return {RecoveryStatus::OK, *events, std::nullopt, {}};
}

RecoveryResponse RecoveryServer::send_snapshot(const uint64_t sequence) const
{
    if (!snapshots_) return {RecoveryStatus::ERROR, {}, std::nullopt, "snapshot service unavailable"};
    const auto snapshot = snapshots_->get_snapshot_at_or_after(sequence);
    if (!snapshot) return {RecoveryStatus::TOO_OLD, {}, std::nullopt, "no retained snapshot for sequence"};
    return {RecoveryStatus::OK, {}, snapshot, {}};
}

RecoveryStatusInfo RecoveryServer::status() const
{
    if (!history_) return {};
    return {history_->latest_sequence().value_or(0), history_->minimum_sequence().value_or(0)};
}

RecoveryClient::RecoveryClient(std::string subscriber_id) : subscriber_id_(std::move(subscriber_id)) {}
RecoveryRequest RecoveryClient::make_request(const MissingRange range) const { return {subscriber_id_, range}; }
RecoveryResponse RecoveryClient::recover(RecoveryServer& server, const MissingRange range) const
{
    // TODO: replace this in-process call with a TCP request/response transport adapter.
    return server.request_missing_data(make_request(range));
}

} // namespace mdfeed::reliability
