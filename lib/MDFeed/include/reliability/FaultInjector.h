#pragma once

#include "reliability/ReliabilityTypes.h"

#include <chrono>
#include <cstdint>
#include <random>
#include <vector>

namespace mdfeed::reliability {

struct FaultConfig {
    double packet_loss_rate{0.0};
    double duplication_rate{0.0};
    double reorder_rate{0.0};
    std::chrono::milliseconds delay{0}; // generic impairment only; not a fairness mechanism
    uint64_t random_seed{0};
};

class FaultInjector {
public:
    explicit FaultInjector(FaultConfig config = {});
    [[nodiscard]] bool should_drop();
    [[nodiscard]] bool should_duplicate();
    [[nodiscard]] bool should_reorder();
    [[nodiscard]] std::vector<MarketDataEvent> apply(const MarketDataEvent& event);
    [[nodiscard]] const FaultConfig& config() const { return config_; }
private:
    FaultConfig config_;
    std::mt19937_64 random_;
    std::uniform_real_distribution<double> distribution_{0.0, 1.0};
    [[nodiscard]] bool roll(double rate);
};

} // namespace mdfeed::reliability
