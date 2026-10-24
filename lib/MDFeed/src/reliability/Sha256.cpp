#include "reliability/Sha256.h"

#include <array>
#include <iomanip>
#include <sstream>
#include <vector>

namespace mdfeed::reliability {
namespace {
constexpr std::array<uint32_t, 64> k = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};

constexpr uint32_t rotr(const uint32_t value, const uint32_t shift)
{
    return (value >> shift) | (value << (32U - shift));
}

std::array<uint8_t, 32> digest(std::span<const uint8_t> input)
{
    std::vector<uint8_t> bytes(input.begin(), input.end());
    const uint64_t bit_length = static_cast<uint64_t>(bytes.size()) * 8U;
    bytes.push_back(0x80);
    while ((bytes.size() % 64) != 56) bytes.push_back(0);
    for (int shift = 56; shift >= 0; shift -= 8) bytes.push_back(static_cast<uint8_t>(bit_length >> shift));

    uint32_t h0=0x6a09e667, h1=0xbb67ae85, h2=0x3c6ef372, h3=0xa54ff53a;
    uint32_t h4=0x510e527f, h5=0x9b05688c, h6=0x1f83d9ab, h7=0x5be0cd19;
    for (size_t block = 0; block < bytes.size(); block += 64) {
        std::array<uint32_t, 64> w{};
        for (size_t i = 0; i < 16; ++i) {
            const size_t offset = block + i * 4;
            w[i] = (static_cast<uint32_t>(bytes[offset]) << 24) | (static_cast<uint32_t>(bytes[offset + 1]) << 16)
                | (static_cast<uint32_t>(bytes[offset + 2]) << 8) | bytes[offset + 3];
        }
        for (size_t i = 16; i < w.size(); ++i) {
            const uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
            const uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }
        uint32_t a=h0,b=h1,c=h2,d=h3,e=h4,f=h5,g=h6,h=h7;
        for (size_t i = 0; i < 64; ++i) {
            const uint32_t s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
            const uint32_t choice = (e & f) ^ ((~e) & g);
            const uint32_t t1 = h + s1 + choice + k[i] + w[i];
            const uint32_t s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
            const uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
            const uint32_t t2 = s0 + majority;
            h=g; g=f; f=e; e=d+t1; d=c; c=b; b=a; a=t1+t2;
        }
        h0+=a; h1+=b; h2+=c; h3+=d; h4+=e; h5+=f; h6+=g; h7+=h;
    }
    std::array<uint8_t, 32> output{};
    const std::array<uint32_t, 8> state{h0,h1,h2,h3,h4,h5,h6,h7};
    for (size_t i = 0; i < state.size(); ++i) {
        output[i*4] = static_cast<uint8_t>(state[i] >> 24);
        output[i*4+1] = static_cast<uint8_t>(state[i] >> 16);
        output[i*4+2] = static_cast<uint8_t>(state[i] >> 8);
        output[i*4+3] = static_cast<uint8_t>(state[i]);
    }
    return output;
}
}

std::string Sha256::hex_digest(const std::span<const uint8_t> bytes)
{
    const auto hash = digest(bytes);
    std::ostringstream output;
    for (const auto byte : hash) output << std::hex << std::setw(2) << std::setfill('0') << static_cast<unsigned>(byte);
    return output.str();
}

std::string Sha256::hex_digest(const std::string_view text)
{
    return hex_digest(std::span(reinterpret_cast<const uint8_t*>(text.data()), text.size()));
}

} // namespace mdfeed::reliability
