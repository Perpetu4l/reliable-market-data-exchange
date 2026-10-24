#include "reliability/DualFeed.h"
#include "reliability/Experiments.h"
#include "reliability/FaultInjector.h"
#include "reliability/RecoveryHistory.h"
#include "reliability/RecoveryProtocol.h"
#include "reliability/RecoveryScheduler.h"
#include "reliability/Snapshot.h"

#include <gtest/gtest.h>

using namespace mdfeed::reliability;

namespace {
MarketDataEvent event(const uint64_t sequence)
{
    MarketDataEvent result;
    result.header.sequence_number = sequence;
    return result;
}
}

TEST(SequenceTrackerTests, DetectsGapAndDuplicate)
{
    SequenceTracker tracker;
    EXPECT_EQ(tracker.on_event(event(1)).disposition, SequenceDisposition::ACCEPTED);
    const auto gap = tracker.on_event(event(3));
    ASSERT_EQ(gap.disposition, SequenceDisposition::GAP_DETECTED);
    ASSERT_TRUE(gap.missing_range.has_value());
    EXPECT_EQ(gap.missing_range->start_sequence, 2);
    EXPECT_EQ(tracker.on_event(event(3)).disposition, SequenceDisposition::DUPLICATE);
}

// Regression for B2: the market-data sequence domain is global and begins at
// one. A late first datagram must reveal the missing head range instead of
// silently redefining the start of the stream.
TEST(SequenceTrackerTests, DetectsMissingHeadFromGlobalSequenceStart)
{
    SequenceTracker tracker;
    const auto observation = tracker.on_event(event(6));
    ASSERT_EQ(observation.disposition, SequenceDisposition::GAP_DETECTED);
    ASSERT_TRUE(observation.missing_range.has_value());
    EXPECT_EQ(observation.missing_range->start_sequence, 1);
    EXPECT_EQ(observation.missing_range->end_sequence, 5);
}

TEST(SequenceTrackerTests, HeartbeatDetectsMissingTail)
{
    SequenceTracker tracker;
    EXPECT_EQ(tracker.on_event(event(1)).disposition, SequenceDisposition::ACCEPTED);
    const auto observation = tracker.on_heartbeat(3);
    ASSERT_EQ(observation.disposition, SequenceDisposition::GAP_DETECTED);
    ASSERT_TRUE(observation.missing_range.has_value());
    EXPECT_EQ(observation.missing_range->start_sequence, 2);
    EXPECT_EQ(observation.missing_range->end_sequence, 3);
}

TEST(RecoveryHistoryTests, RejectsRangeOutsideBoundedHistory)
{
    RecoveryHistory history(2);
    history.append(event(1));
    history.append(event(2));
    history.append(event(3));
    EXPECT_FALSE(history.get_range({1, 1}).has_value());
    EXPECT_TRUE(history.get_range({2, 3}).has_value());
}

TEST(DualFeedSubscriberTests, ReplaysGapBeforeBufferedLaterEvent)
{
    auto history = std::make_shared<RecoveryHistory>(10);
    history->append(event(1));
    history->append(event(2));
    history->append(event(3));
    auto snapshots = std::make_shared<SnapshotManager>();
    RecoveryServer server(history, snapshots);
    RecoveryClient client("test-subscriber");

    std::vector<uint64_t> delivered;
    std::unique_ptr<DualFeedSubscriber> subscriber;
    subscriber = std::make_unique<DualFeedSubscriber>(
        [&](const MarketDataEvent& received) { delivered.push_back(received.header.sequence_number); },
        [&](const MissingRange range) {
            const auto response = client.recover(server, range);
            ASSERT_EQ(response.status, RecoveryStatus::OK);
            for (const auto& recovered : response.events) subscriber->on_recovered_event(recovered);
        });

    subscriber->on_packet(FeedId::A, event(1));
    subscriber->on_packet(FeedId::B, event(3));

    ASSERT_EQ(delivered, (std::vector<uint64_t>{1, 2, 3}));
    EXPECT_EQ(subscriber->pending_event_count(), 0);
}

TEST(DualFeedSubscriberTests, SnapshotResyncReleasesLaterBufferedEvent)
{
    std::vector<uint64_t> delivered;
    DualFeedSubscriber subscriber([&](const MarketDataEvent& received) {
        delivered.push_back(received.header.sequence_number);
    });

    subscriber.on_packet(FeedId::A, event(1));
    subscriber.on_packet(FeedId::A, event(3));
    ASSERT_EQ(subscriber.pending_event_count(), 1);
    subscriber.resynchronise_after_snapshot(2);

    EXPECT_EQ(delivered, (std::vector<uint64_t>{1, 3}));
    EXPECT_EQ(subscriber.pending_event_count(), 0);
}

TEST(SnapshotManagerTests, CreatesVerifiableHashChain)
{
    SnapshotManager manager({1, 2, true});
    manager.store_snapshot(manager.create_snapshot(1, 7, {}, 100));
    manager.store_snapshot(manager.create_snapshot(2, 7, {}, 200));
    EXPECT_TRUE(manager.verify_snapshots());
}

TEST(SnapshotManagerTests, VerifiesTheRetainedSegmentOfAHashChain)
{
    SnapshotManager manager({1, 2, true});
    for (uint64_t sequence = 1; sequence <= 3; ++sequence) {
        manager.store_snapshot(manager.create_snapshot(sequence, 7, {}, sequence * 100));
    }
    EXPECT_TRUE(manager.verify_snapshots());
}

TEST(RecoverySchedulerTests, CanInstantiateBothPolicies)
{
    RecoveryScheduler fcfs(SchedulingPolicy::FCFS);
    RecoveryScheduler round_robin(SchedulingPolicy::ROUND_ROBIN);
    fcfs.enqueue({"one", {1, 2}});
    round_robin.enqueue({"one", {1, 2}});
    EXPECT_TRUE(fcfs.next().has_value());
    EXPECT_TRUE(round_robin.next().has_value());
}

TEST(FaultInjectorTests, UsesSeededConfiguration)
{
    FaultInjector injector({1.0, 0.0, 0.0, {}, 123});
    EXPECT_TRUE(injector.apply(event(1)).empty());
}

TEST(RedundancyExperimentTests, RecordsDesignLevelMetrics)
{
    RedundancyExperiment experiment({RedundancyMode::DUAL_FEED_INDEPENDENT_LOSS, {}, {}});
    experiment.record_packet_sent();
    experiment.record_gap(true);
    EXPECT_EQ(experiment.metrics().packets_sent, 1);
    EXPECT_EQ(experiment.metrics().gaps_avoided_by_second_feed, 1);
}
