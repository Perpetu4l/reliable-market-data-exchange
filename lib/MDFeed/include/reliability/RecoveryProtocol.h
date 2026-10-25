#pragma once

#include "reliability/RecoveryHistory.h"
#include "reliability/Snapshot.h"

#include <memory>
#include <string>

namespace mdfeed::reliability {

enum class RecoveryStatus { OK, TOO_OLD, BAD_REQUEST, ERROR };

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

// Application-facing recovery service. TCP framing, authentication, and I/O are deferred.
class RecoveryServer {
public:
    RecoveryServer(std::shared_ptr<RecoveryHistory> history, std::shared_ptr<SnapshotManager> snapshots);
    [[nodiscard]] RecoveryResponse request_missing_data(const RecoveryRequest& request) const;
    [[nodiscard]] RecoveryResponse send_snapshot(uint64_t sequence) const;
private:
    std::shared_ptr<RecoveryHistory> history_;
    std::shared_ptr<SnapshotManager> snapshots_;
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
