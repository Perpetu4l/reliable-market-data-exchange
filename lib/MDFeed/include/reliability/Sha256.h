#pragma once

#include <cstdint>
#include <span>
#include <string>
#include <string_view>

namespace mdfeed::reliability {

// Small dependency-free SHA-256 used for tamper-evident checkpoints. It is
// intentionally kept separate from snapshot code so NIST vectors can test it
// directly and live/replay code can share the exact implementation.
class Sha256 {
public:
    [[nodiscard]] static std::string hex_digest(std::span<const uint8_t> bytes);
    [[nodiscard]] static std::string hex_digest(std::string_view text);
};

} // namespace mdfeed::reliability
