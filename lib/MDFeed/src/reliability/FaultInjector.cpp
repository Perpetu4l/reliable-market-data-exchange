#include "reliability/FaultInjector.h"

#include <algorithm>
#include <limits>

namespace mdfeed::reliability {
namespace {
uint64_t mix(uint64_t value)
{
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30U)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27U)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31U);
}
}

FaultInjector::FaultInjector(FaultConfig config) : config_(std::move(config)), random_(config_.random_seed) {}
bool FaultInjector::roll(const double rate) { return distribution_(random_) < std::clamp(rate, 0.0, 1.0); }
bool FaultInjector::should_drop() { return roll(config_.packet_loss_rate); }
bool FaultInjector::should_duplicate() { return roll(config_.duplication_rate); }
bool FaultInjector::should_reorder() { return roll(config_.reorder_rate); }
bool FaultInjector::sequence_roll(const uint64_t sequence, const uint64_t stream_tag, const double rate) const
{
    const auto bounded = std::clamp(rate, 0.0, 1.0);
    const auto value = mix(config_.random_seed ^ mix(sequence) ^ stream_tag);
    const long double unit = static_cast<long double>(value)
        / static_cast<long double>(std::numeric_limits<uint64_t>::max());
    return unit < bounded;
}
bool FaultInjector::drops(const MarketDataEvent& event) const
{
    return sequence_roll(event.header.sequence_number, 0x44524f50ULL, config_.packet_loss_rate);
}
bool FaultInjector::duplicates(const MarketDataEvent& event) const
{
    return sequence_roll(event.header.sequence_number, 0x4455504cULL, config_.duplication_rate);
}
std::vector<MarketDataEvent> FaultInjector::apply(const MarketDataEvent& event)
{
    // Reorder/delay require a transport hold-back queue and are intentionally
    // not claimed by this stateless stage. Loss/duplication are deterministic
    // per sequence, independent of callback timing.
    if (drops(event)) return {};
    std::vector<MarketDataEvent> result{event};
    if (duplicates(event)) result.push_back(event);
    return result;
}

} // namespace mdfeed::reliability
