#include "reliability/RecoveryWire.h"

#include <algorithm>
#include <cstring>

namespace mdfeed::reliability {
namespace {
void put_u16(std::vector<uint8_t>& out, const uint16_t value)
{
    out.push_back(static_cast<uint8_t>(value >> 8)); out.push_back(static_cast<uint8_t>(value));
}
void put_u32(std::vector<uint8_t>& out, const uint32_t value)
{
    for (int shift = 24; shift >= 0; shift -= 8) out.push_back(static_cast<uint8_t>(value >> shift));
}
void put_u64(std::vector<uint8_t>& out, const uint64_t value)
{
    for (int shift = 56; shift >= 0; shift -= 8) out.push_back(static_cast<uint8_t>(value >> shift));
}
bool read_u16(std::span<const uint8_t> bytes, size_t& at, uint16_t& value)
{
    if (bytes.size() - at < 2) return false;
    value = (static_cast<uint16_t>(bytes[at]) << 8) | bytes[at + 1]; at += 2; return true;
}
bool read_u32(std::span<const uint8_t> bytes, size_t& at, uint32_t& value)
{
    if (bytes.size() - at < 4) return false;
    value = 0; for (int i = 0; i < 4; ++i) value = (value << 8) | bytes[at++]; return true;
}
bool read_u64(std::span<const uint8_t> bytes, size_t& at, uint64_t& value)
{
    if (bytes.size() - at < 8) return false;
    value = 0; for (int i = 0; i < 8; ++i) value = (value << 8) | bytes[at++]; return true;
}
}

std::vector<uint8_t> RecoveryWireCodec::frame(const RecoveryWireType type, const std::span<const uint8_t> payload)
{
    std::vector<uint8_t> result;
    result.reserve(5 + payload.size());
    put_u32(result, static_cast<uint32_t>(payload.size() + 1));
    result.push_back(static_cast<uint8_t>(type));
    result.insert(result.end(), payload.begin(), payload.end());
    return result;
}

std::optional<RecoveryWireFrame> RecoveryWireCodec::take_frame(std::vector<uint8_t>& stream, std::string& error)
{
    error.clear();
    if (stream.size() < 4) return std::nullopt;
    size_t at = 0; uint32_t size = 0;
    if (!read_u32(stream, at, size) || size == 0 || size > MAX_FRAME_BYTES) {
        error = "invalid recovery frame length"; stream.clear(); return std::nullopt;
    }
    if (stream.size() < 4 + size) return std::nullopt;
    const auto raw_type = stream[4];
    if (raw_type < static_cast<uint8_t>(RecoveryWireType::RECOVERY_REQUEST)
        || raw_type > static_cast<uint8_t>(RecoveryWireType::STATUS_RESPONSE)) {
        error = "unknown recovery frame type"; stream.erase(stream.begin(), stream.begin() + 4 + size); return std::nullopt;
    }
    RecoveryWireFrame frame{static_cast<RecoveryWireType>(raw_type), {stream.begin() + 5, stream.begin() + 4 + size}};
    stream.erase(stream.begin(), stream.begin() + 4 + size);
    return frame;
}

std::vector<uint8_t> RecoveryWireCodec::encode_request(const RecoveryRequest& request)
{
    const auto name_size = std::min<size_t>(request.subscriber_id.size(), UINT16_MAX);
    std::vector<uint8_t> result; result.reserve(18 + name_size);
    put_u16(result, static_cast<uint16_t>(name_size));
    result.insert(result.end(), request.subscriber_id.begin(), request.subscriber_id.begin() + name_size);
    put_u64(result, request.missing_range.start_sequence);
    put_u64(result, request.missing_range.end_sequence);
    return result;
}

std::optional<RecoveryRequest> RecoveryWireCodec::decode_request(const std::span<const uint8_t> payload, std::string& error)
{
    error.clear(); size_t at = 0; uint16_t name_size = 0; uint64_t start = 0, end = 0;
    if (!read_u16(payload, at, name_size) || payload.size() - at < name_size) { error = "truncated subscriber id"; return std::nullopt; }
    std::string subscriber(payload.begin() + at, payload.begin() + at + name_size); at += name_size;
    if (!read_u64(payload, at, start) || !read_u64(payload, at, end) || at != payload.size()) { error = "malformed recovery request"; return std::nullopt; }
    RecoveryRequest request{std::move(subscriber), {start, end}};
    if (request.subscriber_id.empty() || !request.missing_range.valid()) { error = "invalid recovery request"; return std::nullopt; }
    return request;
}

std::vector<uint8_t> RecoveryWireCodec::encode_response(const RecoveryResponse& response)
{
    std::vector<uint8_t> result; result.push_back(static_cast<uint8_t>(response.status));
    put_u32(result, static_cast<uint32_t>(response.events.size()));
    for (const auto& event : response.events) {
        put_u32(result, static_cast<uint32_t>(event.payload.size()));
        result.insert(result.end(), event.payload.begin(), event.payload.end());
    }
    const auto detail_size = std::min<size_t>(response.detail.size(), UINT16_MAX);
    put_u16(result, static_cast<uint16_t>(detail_size));
    result.insert(result.end(), response.detail.begin(), response.detail.begin() + detail_size);
    return result;
}

std::optional<RecoveryResponse> RecoveryWireCodec::decode_response(const std::span<const uint8_t> payload, std::string& error)
{
    error.clear(); size_t at = 0;
    if (payload.size() < 5 || payload[0] > static_cast<uint8_t>(RecoveryStatus::ERROR)) { error = "invalid recovery response"; return std::nullopt; }
    RecoveryResponse result; result.status = static_cast<RecoveryStatus>(payload[at++]); uint32_t event_count = 0;
    if (!read_u32(payload, at, event_count) || event_count > 65536) { error = "invalid event count"; return std::nullopt; }
    for (uint32_t index = 0; index < event_count; ++index) {
        uint32_t event_size = 0;
        if (!read_u32(payload, at, event_size) || event_size < sizeof(MessageHeader) || payload.size() - at < event_size) { error = "truncated recovery event"; return std::nullopt; }
        MarketDataEvent event; event.payload.assign(payload.begin() + at, payload.begin() + at + event_size);
        std::memcpy(&event.header, event.payload.data(), sizeof(MessageHeader));
        result.events.push_back(std::move(event)); at += event_size;
    }
    uint16_t detail_size = 0;
    if (!read_u16(payload, at, detail_size) || payload.size() - at != detail_size) { error = "malformed recovery detail"; return std::nullopt; }
    result.detail.assign(payload.begin() + at, payload.end()); return result;
}

} // namespace mdfeed::reliability
