#include "reliability/FaultInjector.h"
#include "reliability/Sha256.h"

#include <algorithm>
#include <limits>
#include <sstream>

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
bool FaultInjector::reorders(const MarketDataEvent& event) const
{
    return sequence_roll(event.header.sequence_number, 0x52454f52ULL, config_.reorder_rate);
}
std::vector<MarketDataEvent> FaultInjector::apply(const MarketDataEvent& event)
{
    // Stateless per-event loss and duplication decision. Stateful reorder and
    // delay hold-back are implemented by ImpairmentStage in the receive path.
    if (drops(event)) return {};
    std::vector<MarketDataEvent> result{event};
    if (duplicates(event)) result.push_back(event);
    return result;
}

ImpairmentStage::ImpairmentStage(FaultConfig config)
    : config_(config), injector_(config)
{
}

void ImpairmentStage::enqueue_for_delay_locked(MarketDataEvent event, const uint64_t now_ns)
{
    const uint64_t delay_ns = static_cast<uint64_t>(std::max<int64_t>(0, config_.delay.count())) * 1'000'000ULL;
    if (delay_ns > 0) {
        ++delayed_count_;
    }
    delay_queue_.push_back({now_ns + delay_ns, std::move(event)});
}

std::vector<MarketDataEvent> ImpairmentStage::collect_ready_locked(const uint64_t now_ns)
{
    std::vector<MarketDataEvent> ready;
    while (!delay_queue_.empty() && delay_queue_.front().release_ns <= now_ns) {
        ready.push_back(std::move(delay_queue_.front().event));
        delay_queue_.pop_front();
    }
    return ready;
}

std::vector<MarketDataEvent> ImpairmentStage::on_receive(const MarketDataEvent& event, const uint64_t now_ns)
{
    std::lock_guard lock(mutex_);
    if (injector_.drops(event)) {
        ++dropped_count_;
        dropped_sequences_.insert(event.header.sequence_number);
        return collect_ready_locked(now_ns);
    }

    const bool do_duplicate = injector_.duplicates(event);
    const bool do_reorder = config_.reorder_depth > 0 && injector_.reorders(event);

    // Advance existing held-back reordered packets by one arrival step.
    for (auto& held : reorder_buffer_) {
        if (held.remaining_depth > 0) {
            --held.remaining_depth;
        }
    }

    if (do_reorder) {
        ++reordered_count_;
        reorder_buffer_.push_back({event, config_.reorder_depth});
        if (do_duplicate) {
            ++duplicated_count_;
            reorder_buffer_.push_back({event, config_.reorder_depth});
        }
    } else {
        enqueue_for_delay_locked(event, now_ns);
        if (do_duplicate) {
            ++duplicated_count_;
            enqueue_for_delay_locked(event, now_ns);
        }
    }

    // Release any held-back packets whose hold depth has reached 0 after the
    // non-reordered packet (or depth overflow) was queued.
    for (auto it = reorder_buffer_.begin(); it != reorder_buffer_.end();) {
        if (it->remaining_depth == 0) {
            enqueue_for_delay_locked(std::move(it->event), now_ns);
            it = reorder_buffer_.erase(it);
        } else {
            ++it;
        }
    }

    return collect_ready_locked(now_ns);
}

std::vector<MarketDataEvent> ImpairmentStage::poll_ready(const uint64_t now_ns)
{
    std::lock_guard lock(mutex_);
    return collect_ready_locked(now_ns);
}

std::vector<MarketDataEvent> ImpairmentStage::flush()
{
    std::lock_guard lock(mutex_);
    while (!reorder_buffer_.empty()) {
        enqueue_for_delay_locked(std::move(reorder_buffer_.front().event), 0);
        reorder_buffer_.pop_front();
    }
    std::vector<MarketDataEvent> all;
    while (!delay_queue_.empty()) {
        all.push_back(std::move(delay_queue_.front().event));
        delay_queue_.pop_front();
    }
    return all;
}

uint64_t ImpairmentStage::dropped_count() const
{
    std::lock_guard lock(mutex_);
    return dropped_count_;
}

uint64_t ImpairmentStage::duplicated_count() const
{
    std::lock_guard lock(mutex_);
    return duplicated_count_;
}

uint64_t ImpairmentStage::reordered_count() const
{
    std::lock_guard lock(mutex_);
    return reordered_count_;
}

uint64_t ImpairmentStage::delayed_count() const
{
    std::lock_guard lock(mutex_);
    return delayed_count_;
}

std::vector<uint64_t> ImpairmentStage::dropped_sequences(const uint64_t max_sequence) const
{
    std::lock_guard lock(mutex_);
    std::vector<uint64_t> result;
    for (const auto seq : dropped_sequences_) {
        if (seq <= max_sequence) result.push_back(seq);
    }
    return result;
}

std::string ImpairmentStage::dropped_sequences_hash(const uint64_t max_sequence) const
{
    const auto list = dropped_sequences(max_sequence);
    std::ostringstream oss;
    for (size_t i = 0; i < list.size(); ++i) {
        if (i != 0) oss << ',';
        oss << list[i];
    }
    return Sha256::hex_digest(oss.str());
}

} // namespace mdfeed::reliability
