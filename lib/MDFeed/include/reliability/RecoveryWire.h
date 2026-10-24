#pragma once

#include "reliability/RecoveryProtocol.h"

#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <vector>

namespace mdfeed::reliability {

// Recovery TCP frames are: u32 big-endian payload length, u8 message type,
// then type-specific bytes. The size prefix excludes itself and is bounded so
// malformed peers cannot force unbounded allocations.
enum class RecoveryWireType : uint8_t {
    RECOVERY_REQUEST = 1,
    RECOVERY_RESPONSE = 2,
    SNAPSHOT_REQUEST = 3,
    SNAPSHOT_RESPONSE = 4,
    STATUS_REQUEST = 5,
    STATUS_RESPONSE = 6,
};

struct RecoveryWireFrame {
    RecoveryWireType type;
    std::vector<uint8_t> payload;
};

class RecoveryWireCodec {
public:
    static constexpr uint32_t MAX_FRAME_BYTES = 1024 * 1024;
    [[nodiscard]] static std::vector<uint8_t> frame(RecoveryWireType type, std::span<const uint8_t> payload);
    // Consumes exactly one complete frame from `stream`; incomplete input
    // returns nullopt, whereas malformed input populates `error`.
    [[nodiscard]] static std::optional<RecoveryWireFrame> take_frame(
        std::vector<uint8_t>& stream, std::string& error);

    [[nodiscard]] static std::vector<uint8_t> encode_request(const RecoveryRequest& request);
    [[nodiscard]] static std::optional<RecoveryRequest> decode_request(
        std::span<const uint8_t> payload, std::string& error);
    [[nodiscard]] static std::vector<uint8_t> encode_response(const RecoveryResponse& response);
    [[nodiscard]] static std::optional<RecoveryResponse> decode_response(
        std::span<const uint8_t> payload, std::string& error);
};

} // namespace mdfeed::reliability
