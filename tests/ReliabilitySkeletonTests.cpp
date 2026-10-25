#include "reliability/DualFeed.h"
#include "reliability/BookBuilder.h"
#include "reliability/Experiments.h"
#include "reliability/FaultInjector.h"
#include "reliability/RecoveryHistory.h"
#include "reliability/RecoveryProtocol.h"
#include "reliability/RecoveryScheduler.h"
#include "reliability/RecoveryWire.h"
#include "reliability/Snapshot.h"
#include "reliability/Sha256.h"

#include <gtest/gtest.h>

#include <cstring>

using namespace mdfeed::reliability;

namespace {
MarketDataEvent event(const uint64_t sequence)
{
    MarketDataEvent result;
    result.header.sequence_number = sequence;
    return result;
}

template <typename T>
MarketDataEvent packaged_event(const T& message)
{
    MarketDataEvent result;
    result.header = message.header;
    result.payload.resize(sizeof(T));
    std::memcpy(result.payload.data(), &message, sizeof(T));
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

TEST(SequenceTrackerTests, ClearsGapAfterRecoveryAndDoesNotCountRepeatedHeartbeat)
{
    SequenceTracker tracker;
    EXPECT_EQ(tracker.on_event(event(3)).disposition, SequenceDisposition::GAP_DETECTED);
    EXPECT_EQ(tracker.gap_count(), 1U);
    EXPECT_EQ(tracker.on_heartbeat(3).disposition, SequenceDisposition::GAP_DETECTED);
    EXPECT_EQ(tracker.gap_count(), 1U);
    tracker.on_event(event(1));
    tracker.on_event(event(2));
    EXPECT_FALSE(tracker.has_gap());
}

TEST(RecoveryHistoryTests, RejectsRangeOutsideBoundedHistory)
{
    RecoveryHistory history(2);
    history.append(event(1));
    history.append(event(2));
    history.append(event(3));
    EXPECT_FALSE(history.get_range({1, 1}).has_value());
    EXPECT_TRUE(history.get_range({2, 3}).has_value());
    EXPECT_EQ(history.minimum_sequence(), 2);
    EXPECT_EQ(history.latest_sequence(), 3);
}

TEST(RecoveryWireTests, RoundTripsPartialFramedRequest)
{
    const RecoveryRequest source{"subscriber-1", {10, 20}};
    const auto bytes = RecoveryWireCodec::frame(RecoveryWireType::RECOVERY_REQUEST,
        RecoveryWireCodec::encode_request(source));
    std::vector<uint8_t> stream(bytes.begin(), bytes.begin() + 3);
    std::string error;
    EXPECT_FALSE(RecoveryWireCodec::take_frame(stream, error).has_value());
    stream.insert(stream.end(), bytes.begin() + 3, bytes.end());
    const auto frame = RecoveryWireCodec::take_frame(stream, error);
    ASSERT_TRUE(frame.has_value()) << error;
    ASSERT_EQ(frame->type, RecoveryWireType::RECOVERY_REQUEST);
    const auto decoded = RecoveryWireCodec::decode_request(frame->payload, error);
    ASSERT_TRUE(decoded.has_value()) << error;
    EXPECT_EQ(decoded->subscriber_id, source.subscriber_id);
    EXPECT_EQ(decoded->request_id, 0U); // server-only correlation never crosses the wire
    EXPECT_EQ(decoded->missing_range.start_sequence, 10);
    EXPECT_EQ(decoded->missing_range.end_sequence, 20);
}

TEST(RecoveryWireTests, RejectsOversizedFrame)
{
    std::vector<uint8_t> stream{0xff, 0xff, 0xff, 0xff};
    std::string error;
    EXPECT_FALSE(RecoveryWireCodec::take_frame(stream, error).has_value());
    EXPECT_EQ(error, "invalid recovery frame length");
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

TEST(Sha256Tests, MatchesNistVectors)
{
    EXPECT_EQ(Sha256::hex_digest(""), "e3b0c44298fc1c149afbf4c8996fb924"
        "27ae41e4649b934ca495991b7852b855");
    EXPECT_EQ(Sha256::hex_digest("abc"), "ba7816bf8f01cfea414140de5dae2223"
        "b00361a396177a9cb410ff61f20015ad");
    EXPECT_EQ(Sha256::hex_digest("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
        "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
}

TEST(SnapshotManagerTests, RejectsTamperedSnapshot)
{
    SnapshotManager manager({1, 2, true});
    auto snapshot = manager.create_snapshot(1, 7, {{100, 2, Side::BUY}}, 100);
    ++snapshot.book_state.front().quantity;
    manager.store_snapshot(std::move(snapshot));
    EXPECT_FALSE(manager.verify_snapshots());
}

TEST(BookBuilderTests, AppliesAbsolutePriceLevelUpdatesAndDeletes)
{
    BookBuilder builder;
    PriceLevelUpdateMessage update{};
    message_utils::init_header(update, MessageType::PRICE_LEVEL_UPDATE, 1, 7);
    update.price = 50000;
    update.quantity = 10;
    update.side = Side::BUY;
    update.action = UpdateAction::NEW;
    ASSERT_TRUE(builder.apply(packaged_event(update)));

    update.header.sequence_number = 2;
    update.quantity = 25; // CHANGE is an absolute level quantity, not a delta.
    update.action = UpdateAction::CHANGE;
    ASSERT_TRUE(builder.apply(packaged_event(update)));
    ASSERT_EQ(builder.levels(7).size(), 1);
    EXPECT_EQ(builder.levels(7).front().quantity, 25);

    PriceLevelDeleteMessage deletion{};
    message_utils::init_header(deletion, MessageType::PRICE_LEVEL_DELETE, 3, 7);
    deletion.price = 50000;
    deletion.side = Side::BUY;
    ASSERT_TRUE(builder.apply(packaged_event(deletion)));
    EXPECT_TRUE(builder.levels(7).empty());
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

TEST(RecoverySchedulerTests, RoundRobinAlternatesSubscribersUnderContention)
{
    RecoveryScheduler scheduler(SchedulingPolicy::ROUND_ROBIN);
    scheduler.enqueue({"noisy", {1, 1}});
    scheduler.enqueue({"noisy", {2, 2}});
    scheduler.enqueue({"quiet", {3, 3}});
    ASSERT_EQ(scheduler.next()->request.subscriber_id, "noisy");
    ASSERT_EQ(scheduler.next()->request.subscriber_id, "quiet");
    ASSERT_EQ(scheduler.next()->request.subscriber_id, "noisy");
}

TEST(FaultInjectorTests, UsesSeededConfiguration)
{
    FaultInjector injector({1.0, 0.0, 0.0, {}, 123});
    EXPECT_TRUE(injector.apply(event(1)).empty());
}

TEST(FaultInjectorTests, SequenceDecisionsAreTimingIndependent)
{
    FaultInjector first({0.35, 0.25, 0.0, {}, 123});
    FaultInjector second({0.35, 0.25, 0.0, {}, 123});
    for (uint64_t sequence = 1; sequence <= 100; ++sequence) {
        EXPECT_EQ(first.apply(event(sequence)).size(), second.apply(event(sequence)).size());
    }
}

TEST(RedundancyExperimentTests, RecordsDesignLevelMetrics)
{
    RedundancyExperiment experiment({RedundancyMode::DUAL_FEED_INDEPENDENT_LOSS, {}, {}});
    experiment.record_packet_sent();
    experiment.record_gap(true);
    EXPECT_EQ(experiment.metrics().packets_sent, 1);
    EXPECT_EQ(experiment.metrics().gaps_avoided_by_second_feed, 1);
}
