#include "reliability/FaultInjector.h"

#include <algorithm>

namespace mdfeed::reliability {

FaultInjector::FaultInjector(FaultConfig config) : config_(std::move(config)), random_(config_.random_seed) {}
bool FaultInjector::roll(const double rate) { return distribution_(random_) < std::clamp(rate, 0.0, 1.0); }
bool FaultInjector::should_drop() { return roll(config_.packet_loss_rate); }
bool FaultInjector::should_duplicate() { return roll(config_.duplication_rate); }
bool FaultInjector::should_reorder() { return roll(config_.reorder_rate); }
std::vector<MarketDataEvent> FaultInjector::apply(const MarketDataEvent& event)
{
    // TODO: queue reorder/delay decisions in a transport test harness. This only exposes deterministic choices.
    if (should_drop()) return {};
    std::vector<MarketDataEvent> result{event};
    if (should_duplicate()) result.push_back(event);
    return result;
}

} // namespace mdfeed::reliability
