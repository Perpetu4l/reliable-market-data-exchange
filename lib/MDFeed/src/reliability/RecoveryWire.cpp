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

std::vector<uint8_t> RecoveryWireCodec::encode_snapshot_request(const uint64_t minimum_sequence)
{
    std::vector<uint8_t> result;
    put_u64(result, minimum_sequence);
    return result;
}

std::optional<uint64_t> RecoveryWireCodec::decode_snapshot_request(
    const std::span<const uint8_t> payload, std::string& error)
{
    error.clear();
    size_t at = 0;
    uint64_t sequence = 0;
    if (!read_u64(payload, at, sequence) || at != payload.size() || sequence == 0) {
        error = "invalid snapshot request";
        return std::nullopt;
    }
    return sequence;
}

std::vector<uint8_t> RecoveryWireCodec::encode_snapshot_response(const RecoveryResponse& response)
{
    std::vector<uint8_t> result;
    result.push_back(static_cast<uint8_t>(response.status));
    if (response.status == RecoveryStatus::OK && response.snapshot) {
        const auto& snapshot = *response.snapshot;
        put_u64(result, snapshot.sequence_number);
        put_u32(result, snapshot.instrument_id);
        put_u64(result, snapshot.timestamp_ns);
        put_u32(result, static_cast<uint32_t>(snapshot.book_state.size()));
        for (const auto& level : snapshot.book_state) {
            put_u32(result, level.instrument_id);
            put_u64(result, level.price);
            put_u64(result, level.quantity);
            result.push_back(static_cast<uint8_t>(level.side));
        }
        put_u16(result, static_cast<uint16_t>(std::min<size_t>(snapshot.previous_hash.size(), UINT16_MAX)));
        result.insert(result.end(), snapshot.previous_hash.begin(), snapshot.previous_hash.begin()
            + std::min<size_t>(snapshot.previous_hash.size(), UINT16_MAX));
        put_u16(result, static_cast<uint16_t>(std::min<size_t>(snapshot.hash.size(), UINT16_MAX)));
        result.insert(result.end(), snapshot.hash.begin(), snapshot.hash.begin()
            + std::min<size_t>(snapshot.hash.size(), UINT16_MAX));
    } else {
        put_u64(result, 0);
        put_u32(result, 0);
        put_u64(result, 0);
        put_u32(result, 0);
        put_u16(result, 0);
        put_u16(result, 0);
    }
    const auto detail_size = std::min<size_t>(response.detail.size(), UINT16_MAX);
    put_u16(result, static_cast<uint16_t>(detail_size));
    result.insert(result.end(), response.detail.begin(), response.detail.begin() + detail_size);
    return result;
}

std::optional<RecoveryResponse> RecoveryWireCodec::decode_snapshot_response(
    const std::span<const uint8_t> payload, std::string& error)
{
    error.clear();
    size_t at = 0;
    if (payload.empty() || payload[0] > static_cast<uint8_t>(RecoveryStatus::ERROR)) {
        error = "invalid snapshot response";
        return std::nullopt;
    }
    RecoveryResponse result;
    result.status = static_cast<RecoveryStatus>(payload[at++]);
    OrderBookSnapshot snapshot;
    uint32_t level_count = 0;
    if (!read_u64(payload, at, snapshot.sequence_number) || !read_u32(payload, at, snapshot.instrument_id)
        || !read_u64(payload, at, snapshot.timestamp_ns) || !read_u32(payload, at, level_count)
        || level_count > 65536) {
        error = "truncated snapshot response";
        return std::nullopt;
    }
    snapshot.book_state.reserve(level_count);
    for (uint32_t index = 0; index < level_count; ++index) {
        SnapshotPriceLevel level;
        uint8_t side = 0;
        if (!read_u32(payload, at, level.instrument_id) || !read_u64(payload, at, level.price)
            || !read_u64(payload, at, level.quantity) || at >= payload.size()) {
            error = "truncated snapshot level";
            return std::nullopt;
        }
        side = payload[at++];
        if (side != static_cast<uint8_t>(Side::BUY) && side != static_cast<uint8_t>(Side::SELL)) {
            error = "invalid snapshot side";
            return std::nullopt;
        }
        level.side = static_cast<Side>(side);
        snapshot.book_state.push_back(level);
    }
    uint16_t previous_size = 0, hash_size = 0, detail_size = 0;
    if (!read_u16(payload, at, previous_size) || payload.size() - at < previous_size) {
        error = "truncated snapshot previous hash";
        return std::nullopt;
    }
    snapshot.previous_hash.assign(payload.begin() + at, payload.begin() + at + previous_size);
    at += previous_size;
    if (!read_u16(payload, at, hash_size) || payload.size() - at < hash_size) {
        error = "truncated snapshot hash";
        return std::nullopt;
    }
    snapshot.hash.assign(payload.begin() + at, payload.begin() + at + hash_size);
    at += hash_size;
    if (!read_u16(payload, at, detail_size) || payload.size() - at != detail_size) {
        error = "malformed snapshot detail";
        return std::nullopt;
    }
    result.detail.assign(payload.begin() + at, payload.end());
    if (result.status == RecoveryStatus::OK) {
        if (snapshot.sequence_number == 0 || snapshot.hash.empty()) {
            error = "successful snapshot response has no snapshot";
            return std::nullopt;
        }
        result.snapshot = std::move(snapshot);
    }
    return result;
}

} // namespace mdfeed::reliability
