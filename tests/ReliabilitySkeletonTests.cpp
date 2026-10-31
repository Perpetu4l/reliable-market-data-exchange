#include "reliability/DualFeed.h"
#include "reliability/BookBuilder.h"
#include "reliability/Experiments.h"
#include "reliability/FaultInjector.h"
#include "reliability/Metrics.h"
#include "reliability/RecoveryHistory.h"
#include "reliability/RecoveryProtocol.h"
#include "reliability/RecoveryScheduler.h"
#include "reliability/RecoveryWire.h"
#include "reliability/Snapshot.h"
#include "reliability/Sha256.h"
#include "receiver/MulticastReceiver.h"

#include <gtest/gtest.h>

#include <cmath>
#include <cstring>
#include <random>

using namespace mdfeed;
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

MarketDataEvent make_price_update_event(
    const uint64_t seq, const uint32_t instrument, const Side side, const uint64_t price, const uint64_t qty)
{
    PriceLevelUpdateMessage update{};
    message_utils::init_header(update, MessageType::PRICE_LEVEL_UPDATE, seq, instrument);
    update.price = price;
    update.quantity = qty;
    update.side = side;
    update.action = UpdateAction::NEW;
    return packaged_event(update);
}
} // namespace

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
    (void)tracker.on_event(event(1));
    (void)tracker.on_event(event(2));
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

TEST(RecoveryWireTests, RoundTripsStatusResponse)
{
    const RecoveryStatusInfo source{200, 101, "abc123digest", RecoveryWireCodec::PROTOCOL_VERSION};
    const auto payload = RecoveryWireCodec::encode_status_response(source);
    std::string error;
    const auto decoded = RecoveryWireCodec::decode_status_response(payload, error);
    ASSERT_TRUE(decoded.has_value()) << error;
    EXPECT_EQ(decoded->latest_sequence, 200U);
    EXPECT_EQ(decoded->history_minimum_sequence, 101U);
    EXPECT_EQ(decoded->latest_book_digest, "abc123digest");
    EXPECT_EQ(decoded->protocol_version, RecoveryWireCodec::PROTOCOL_VERSION);
}

TEST(RecoveryWireTests, RoundTripsStatusRequestAndByeAndRejectsProtocolMismatch)
{
    std::string error;
    const auto req_payload = RecoveryWireCodec::encode_status_request("sub-alpha");
    const auto decoded_req = RecoveryWireCodec::decode_status_request(req_payload, error);
    ASSERT_TRUE(decoded_req.has_value()) << error;
    EXPECT_EQ(*decoded_req, "sub-alpha");

    const auto bye_payload = RecoveryWireCodec::encode_bye("sub-alpha");
    auto stream = RecoveryWireCodec::frame(RecoveryWireType::BYE, bye_payload);
    const auto frame = RecoveryWireCodec::take_frame(stream, error);
    ASSERT_TRUE(frame.has_value()) << error;
    EXPECT_EQ(frame->type, RecoveryWireType::BYE);
    const auto decoded_bye = RecoveryWireCodec::decode_bye(frame->payload, error);
    ASSERT_TRUE(decoded_bye.has_value()) << error;
    EXPECT_EQ(*decoded_bye, "sub-alpha");

    // Version mismatch must be rejected explicitly.
    const auto bad_req = RecoveryWireCodec::encode_status_request("sub-alpha", 99);
    EXPECT_FALSE(RecoveryWireCodec::decode_status_request(bad_req, error).has_value());
    EXPECT_EQ(error, "unsupported recovery protocol version");

    const RecoveryStatusInfo bad_status{10, 1, "digest", 99};
    const auto bad_resp = RecoveryWireCodec::encode_status_response(bad_status);
    EXPECT_FALSE(RecoveryWireCodec::decode_status_response(bad_resp, error).has_value());
    EXPECT_EQ(error, "unsupported recovery protocol version");

    const auto bad_bye = RecoveryWireCodec::encode_bye("sub-alpha", 99);
    EXPECT_FALSE(RecoveryWireCodec::decode_bye(bad_bye, error).has_value());
    EXPECT_EQ(error, "unsupported recovery protocol version");
}

TEST(RecoveryWireTests, AcceptsChunkAndCompletionFrames)
{
    MarketDataEvent source;
    source.header.sequence_number = 12;
    source.payload.resize(sizeof(MessageHeader));
    std::memcpy(source.payload.data(), &source.header, sizeof(MessageHeader));
    const auto chunk_payload = RecoveryWireCodec::encode_response(
        {RecoveryStatus::OK, {source}, std::nullopt, {}});
    auto stream = RecoveryWireCodec::frame(RecoveryWireType::RECOVERY_CHUNK, chunk_payload);
    const auto complete = RecoveryWireCodec::frame(RecoveryWireType::RECOVERY_COMPLETE, {});
    stream.insert(stream.end(), complete.begin(), complete.end());
    std::string error;
    const auto chunk = RecoveryWireCodec::take_frame(stream, error);
    ASSERT_TRUE(chunk.has_value()) << error;
    EXPECT_EQ(chunk->type, RecoveryWireType::RECOVERY_CHUNK);
    const auto decoded = RecoveryWireCodec::decode_response(chunk->payload, error);
    ASSERT_TRUE(decoded.has_value()) << error;
    ASSERT_EQ(decoded->events.size(), 1U);
    EXPECT_EQ(decoded->events.front().header.sequence_number, 12U);
    const auto end = RecoveryWireCodec::take_frame(stream, error);
    ASSERT_TRUE(end.has_value()) << error;
    EXPECT_EQ(end->type, RecoveryWireType::RECOVERY_COMPLETE);
    EXPECT_TRUE(end->payload.empty());
}

TEST(RecoveryWireTests, RoundTripsVerifiableCompositeSnapshot)
{
    SnapshotManager manager({1, 4, true});
    auto snapshot = manager.create_snapshot(10, 0,
        {{100, 2, Side::BUY, 7}, {101, 3, Side::SELL, 8}}, 1234);
    RecoveryResponse source{RecoveryStatus::OK, {}, snapshot, {}};
    const auto payload = RecoveryWireCodec::encode_snapshot_response(source);
    std::string error;
    const auto decoded = RecoveryWireCodec::decode_snapshot_response(payload, error);
    ASSERT_TRUE(decoded.has_value()) << error;
    ASSERT_TRUE(decoded->snapshot.has_value());
    EXPECT_EQ(decoded->snapshot->sequence_number, 10U);
    EXPECT_EQ(decoded->snapshot->book_state.size(), 2U);
    EXPECT_TRUE(SnapshotHasher{}.verify(*decoded->snapshot));
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

TEST(DualFeedSubscriberTests, BuffersOutOfOrderRecoveredEventsUntilContiguous)
{
    std::vector<uint64_t> delivered;
    DualFeedSubscriber subscriber([&](const MarketDataEvent& received) {
        delivered.push_back(received.header.sequence_number);
    });

    subscriber.on_packet(FeedId::A, event(1));
    // Two recovered ranges [4, 5] and [2, 3] arriving in reverse order:
    subscriber.on_recovered_event(event(4));
    subscriber.on_recovered_event(event(5));
    EXPECT_EQ(delivered, (std::vector<uint64_t>{1}));
    EXPECT_EQ(subscriber.pending_event_count(), 2U);

    subscriber.on_recovered_event(event(2));
    subscriber.on_recovered_event(event(3));
    EXPECT_EQ(delivered, (std::vector<uint64_t>{1, 2, 3, 4, 5}));
    EXPECT_EQ(subscriber.pending_event_count(), 0U);
}

TEST(DualFeedSubscriberTests, MeasuresPerEventLatencyClassesWithInjectableClock)
{
    uint64_t now_ns = 1'000'000ULL;
    DualFeedSubscriber subscriber({}, {}, [&] { return now_ns; });

    // Packet 1 arrives at t = 1ms.
    subscriber.on_packet(FeedId::A, event(1));
    // Packet 5 arrives at t = 2ms -> gap [2, 4] detected at t = 2ms.
    now_ns = 2'000'000ULL;
    subscriber.on_packet(FeedId::A, event(5));
    EXPECT_EQ(subscriber.gap_detections(), 1U);
    EXPECT_EQ(subscriber.events_in_gaps(), 3U);

    // Sequence 2 arrives on Feed B at t = 3ms (no TCP request) -> class OTHER_FEED, latency = 1ms.
    now_ns = 3'000'000ULL;
    subscriber.on_packet(FeedId::B, event(2));

    // Sequence 3 is requested over TCP and recovered at t = 6ms -> class TCP_RECOVERY, latency = 4ms.
    subscriber.mark_tcp_requested({3, 3});
    now_ns = 6'000'000ULL;
    subscriber.on_recovered_event(event(3));

    // Sequence 4 is covered by a snapshot at t = 10ms -> class SNAPSHOT, latency = 8ms.
    now_ns = 10'000'000ULL;
    subscriber.resynchronise_after_snapshot(4);

    const auto other_lat = subscriber.latency_summary(MissingEventClass::OTHER_FEED);
    const auto tcp_lat = subscriber.latency_summary(MissingEventClass::TCP_RECOVERY);
    const auto snap_lat = subscriber.latency_summary(MissingEventClass::SNAPSHOT);

    EXPECT_EQ(other_lat.count, 1U);
    EXPECT_EQ(other_lat.p50_ns, 1'000'000ULL);
    EXPECT_TRUE(other_lat.insufficient_samples);

    EXPECT_EQ(tcp_lat.count, 1U);
    EXPECT_EQ(tcp_lat.p50_ns, 4'000'000ULL);
    EXPECT_TRUE(tcp_lat.insufficient_samples);

    EXPECT_EQ(snap_lat.count, 1U);
    EXPECT_EQ(snap_lat.p50_ns, 8'000'000ULL);
    EXPECT_TRUE(snap_lat.insufficient_samples);
}

TEST(DualFeedSubscriberTests, ComputesRedundancyAndAccountingBitsetsExact)
{
    DualFeedSubscriber subscriber;
    // Sequences 1..5:
    // seq 1: arrives on A and B -> delivered via A
    // seq 2: dropped on A, arrives on B -> delivered via B (avoided gap!)
    // seq 3: dropped on A and B -> recovered via TCP
    // seq 4: arrives on A only -> delivered via A
    // seq 5: dropped on A, arrives on B after gap detection (no TCP request) -> avoided gap!
    subscriber.on_packet(FeedId::A, event(1));
    subscriber.on_packet(FeedId::B, event(1));
    subscriber.on_packet(FeedId::B, event(2));
    subscriber.on_packet(FeedId::A, event(4));
    subscriber.on_recovered_event(event(3));
    subscriber.on_heartbeat(5);
    subscriber.on_packet(FeedId::B, event(5));

    const auto red = subscriber.compute_redundancy(5);
    // Feed A missed 2, 3, 5 -> potential_single_feed_gaps = 3.
    // Feed B delivered 2 and 5 without TCP recovery -> gaps_avoided = 2.
    EXPECT_EQ(red.potential_single_feed_gaps, 3U);
    EXPECT_EQ(red.gaps_avoided_by_second_feed, 2U);
    ASSERT_TRUE(red.redundancy_effectiveness.has_value());
    EXPECT_NEAR(*red.redundancy_effectiveness, 2.0 / 3.0, 1e-9);

    const auto acc = subscriber.compute_accounting(5);
    EXPECT_TRUE(acc.accounting_valid);
    EXPECT_EQ(acc.events_expected, 5U);
    EXPECT_EQ(acc.delivered, 5U);
    EXPECT_EQ(acc.delivered_via_feed_a, 2U);
    EXPECT_EQ(acc.delivered_via_feed_b, 2U);
    EXPECT_EQ(acc.delivered_via_recovery, 1U);
    EXPECT_EQ(acc.events_covered_by_snapshot, 0U);
    EXPECT_EQ(acc.permanently_lost, 0U);
}

TEST(DualFeedSubscriberTests, PreservesAccountingInvariantUnderFuzzedArrivals)
{
    std::mt19937_64 rng(20261030ULL);
    for (int trial = 0; trial < 20; ++trial) {
        DualFeedSubscriber subscriber;
        constexpr uint64_t total_events = 200;
        std::vector<std::pair<FeedId, uint64_t>> arrivals;
        for (uint64_t seq = 1; seq <= total_events; ++seq) {
            if (rng() % 100 < 70) arrivals.emplace_back(FeedId::A, seq);
            if (rng() % 100 < 70) arrivals.emplace_back(FeedId::B, seq);
            if (rng() % 100 < 15) arrivals.emplace_back(FeedId::A, seq); // duplicate
        }
        // Local bounded reordering
        for (size_t i = 0; i + 1 < arrivals.size(); ++i) {
            if (rng() % 100 < 25) std::swap(arrivals[i], arrivals[i + 1]);
        }
        for (const auto& [feed, seq] : arrivals) {
            subscriber.on_packet(feed, event(seq));
        }
        subscriber.on_heartbeat(total_events);
        // Recover every second missing event, leave some lost, or cover prefix by snapshot
        if (trial % 3 == 0) {
            subscriber.resynchronise_after_snapshot(50);
        }
        for (uint64_t seq = 1; seq <= total_events; ++seq) {
            if (rng() % 2 == 0) subscriber.on_recovered_event(event(seq));
        }
        const auto acc = subscriber.compute_accounting(total_events);
        EXPECT_TRUE(acc.accounting_valid);
        EXPECT_EQ(acc.delivered + acc.events_covered_by_snapshot + acc.permanently_lost, acc.events_expected);
        EXPECT_LE(acc.delivered, acc.events_expected);
    }
}

TEST(MetricsTests, ComputesNearestRankPercentilesAndSampleThresholds)
{
    std::vector<uint64_t> fifty;
    for (uint64_t i = 1; i <= 50; ++i) fifty.push_back(i * 10);
    const auto s50 = compute_latency_summary(fifty);
    EXPECT_EQ(s50.count, 50U);
    EXPECT_TRUE(s50.insufficient_samples);
    EXPECT_TRUE(s50.insufficient_p999);
    EXPECT_EQ(s50.p50_ns, 250U);
    EXPECT_EQ(s50.p95_ns, 480U);
    EXPECT_EQ(s50.p99_ns, 500U);
    EXPECT_EQ(s50.max_ns, 500U);

    std::vector<uint64_t> thousand;
    for (uint64_t i = 1; i <= 1000; ++i) thousand.push_back(i);
    const auto s1000 = compute_latency_summary(thousand);
    EXPECT_EQ(s1000.count, 1000U);
    EXPECT_FALSE(s1000.insufficient_samples);
    EXPECT_FALSE(s1000.insufficient_p999);
    EXPECT_EQ(s1000.p50_ns, 500U);
    EXPECT_EQ(s1000.p95_ns, 950U);
    EXPECT_EQ(s1000.p99_ns, 990U);
    EXPECT_EQ(s1000.p999_ns, 999U);
    EXPECT_EQ(s1000.max_ns, 1000U);
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

TEST(SnapshotManagerTests, OnDemandCheckpointAndTamperModes)
{
    auto history = std::make_shared<RecoveryHistory>(4);
    auto snapshots = std::make_shared<SnapshotManager>(SnapshotConfig{0, 8, false});
    auto books = std::make_shared<BookBuilder>();
    RecoveryServer server(history, snapshots, books, true, TamperMode::NONE);

    for (uint64_t seq = 1; seq <= 10; ++seq) {
        server.record_published_event(make_price_update_event(seq, 1, Side::BUY, 50000 + seq, 10 * seq));
    }

    // Even with snapshot_interval == 0, on-demand snapshot at sequence 10 succeeds.
    const auto res = server.send_snapshot(6);
    ASSERT_EQ(res.status, RecoveryStatus::OK);
    ASSERT_TRUE(res.snapshot.has_value());
    EXPECT_EQ(res.snapshot->sequence_number, 10U);
    EXPECT_TRUE(SnapshotHasher{}.verify(*res.snapshot));
    EXPECT_EQ(server.on_demand_snapshots_created(), 1U);

    // Verify all tamper modes fail SnapshotHasher::verify().
    for (const auto mode : {TamperMode::BOOK_VALUE, TamperMode::SEQUENCE, TamperMode::PREVIOUS_HASH, TamperMode::HASH}) {
        server.set_tamper_mode(mode);
        const auto tampered = server.send_snapshot(6);
        ASSERT_EQ(tampered.status, RecoveryStatus::OK);
        ASSERT_TRUE(tampered.snapshot.has_value());
        EXPECT_FALSE(SnapshotHasher{}.verify(*tampered.snapshot))
            << "Tamper mode " << tamper_mode_name(mode) << " was not rejected";
    }
}

TEST(SnapshotManagerTests, FindsCheckpointAtOrAfterRecoveryRange)
{
    SnapshotManager manager({1, 4, true});
    manager.store_snapshot(manager.create_snapshot(10, 0, {}, 10));
    manager.store_snapshot(manager.create_snapshot(20, 0, {}, 20));
    const auto snapshot = manager.get_snapshot_at_or_after(15);
    ASSERT_TRUE(snapshot.has_value());
    EXPECT_EQ(snapshot->sequence_number, 20U);
}

TEST(RecoverySchedulerTests, RecordsQueueWaitAndServiceDistribution)
{
    RecoveryScheduler scheduler(SchedulingPolicy::FCFS);
    scheduler.enqueue({"first", {1, 1}});
    const auto scheduled = scheduler.next();
    ASSERT_TRUE(scheduled.has_value());
    scheduler.record_completed(*scheduled, 1, 250);
    const auto& metrics = scheduler.metrics();
    EXPECT_EQ(metrics.recovery_request_count, 1U);
    EXPECT_EQ(metrics.messages_recovered, 1U);
    EXPECT_EQ(metrics.total_queue_wait_ns, 250U);
    EXPECT_EQ(metrics.max_queue_wait_ns, 250U);
    EXPECT_EQ(metrics.per_subscriber_service_distribution.at("first"), 1U);
}

TEST(RecoverySchedulerTests, ConcurrencyFcfsVersusRoundRobinOrdering)
{
    RecoveryScheduler fcfs(SchedulingPolicy::FCFS);
    RecoveryScheduler rr(SchedulingPolicy::ROUND_ROBIN);

    // Noisy subscriber enqueues K=4 concurrent requests; two quiet subscribers enqueue 1 each.
    for (uint64_t i = 1; i <= 4; ++i) {
        fcfs.enqueue({"noisy", {i, i}});
        rr.enqueue({"noisy", {i, i}});
    }
    fcfs.enqueue({"quiet-1", {10, 10}});
    rr.enqueue({"quiet-1", {10, 10}});
    fcfs.enqueue({"quiet-2", {20, 20}});
    rr.enqueue({"quiet-2", {20, 20}});

    std::vector<std::string> fcfs_order;
    while (const auto next = fcfs.next()) fcfs_order.push_back(next->request.subscriber_id);
    EXPECT_EQ(fcfs_order, (std::vector<std::string>{
        "noisy", "noisy", "noisy", "noisy", "quiet-1", "quiet-2"}));

    std::vector<std::string> rr_order;
    while (const auto next = rr.next()) rr_order.push_back(next->request.subscriber_id);
    EXPECT_EQ(rr_order, (std::vector<std::string>{
        "noisy", "quiet-1", "quiet-2", "noisy", "noisy", "noisy"}));
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

TEST(BookBuilderTests, DigestIsCanonicalAndOrderIndependent)
{
    BookBuilder first;
    BookBuilder second;

    ASSERT_TRUE(first.apply(make_price_update_event(1, 2, Side::SELL, 50200, 30)));
    ASSERT_TRUE(first.apply(make_price_update_event(2, 1, Side::BUY, 49900, 10)));
    ASSERT_TRUE(first.apply(make_price_update_event(3, 1, Side::SELL, 50100, 20)));

    // Apply in reverse instrument/side order to second builder:
    ASSERT_TRUE(second.apply(make_price_update_event(1, 1, Side::SELL, 50100, 20)));
    ASSERT_TRUE(second.apply(make_price_update_event(2, 1, Side::BUY, 49900, 10)));
    ASSERT_TRUE(second.apply(make_price_update_event(3, 2, Side::SELL, 50200, 30)));

    EXPECT_EQ(first.digest(), second.digest());
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

TEST(FaultInjectorTests, SequenceDecisionsAreTimingIndependentAndDeterministic)
{
    ImpairmentStage first({0.25, 0.10, 0.0, {}, 123});
    ImpairmentStage second({0.25, 0.10, 0.0, {}, 123});
    ImpairmentStage different_seed({0.25, 0.10, 0.0, {}, 456});
    for (uint64_t sequence = 1; sequence <= 500; ++sequence) {
        (void)first.on_receive(event(sequence), sequence * 1000);
        (void)second.on_receive(event(sequence), sequence * 9999);
        (void)different_seed.on_receive(event(sequence), sequence * 1000);
    }
    EXPECT_EQ(first.dropped_sequences(), second.dropped_sequences());
    EXPECT_EQ(first.dropped_sequences_hash(), second.dropped_sequences_hash());
    EXPECT_NE(first.dropped_sequences_hash(), different_seed.dropped_sequences_hash());
}

TEST(FaultInjectorTests, StatisticalRedundancyMatchesBinomialBandOver50000Events)
{
    constexpr uint64_t N = 50'000;
    constexpr double p = 0.10;
    // Binomial 3-sigma band for N = 50,000:
    // Single / correlated: mean = p = 0.10, sigma = sqrt(p*(1-p)/N) = 0.0013416 -> 3*sigma = 0.00402
    // Dual independent: mean = p^2 = 0.01, sigma = sqrt(p^2*(1-p^2)/N) = 0.000445 -> 3*sigma = 0.00133
    for (uint64_t seed : {11ULL, 22ULL, 33ULL}) {
        FaultInjector single_a({p, 0.0, 0.0, {}, seed});
        FaultInjector indep_a({p, 0.0, 0.0, {}, seed});
        FaultInjector indep_b({p, 0.0, 0.0, {}, seed + 1});
        FaultInjector corr({p, 0.0, 0.0, {}, seed});

        uint64_t single_missing = 0;
        uint64_t indep_missing = 0;
        uint64_t corr_missing = 0;
        for (uint64_t seq = 1; seq <= N; ++seq) {
            const auto ev = event(seq);
            if (single_a.drops(ev)) ++single_missing;
            if (indep_a.drops(ev) && indep_b.drops(ev)) ++indep_missing;
            if (corr.drops(ev)) ++corr_missing;
        }
        const double single_rate = static_cast<double>(single_missing) / static_cast<double>(N);
        const double indep_rate = static_cast<double>(indep_missing) / static_cast<double>(N);
        const double corr_rate = static_cast<double>(corr_missing) / static_cast<double>(N);

        const double sigma_single = std::sqrt(p * (1.0 - p) / static_cast<double>(N));
        const double p2 = p * p;
        const double sigma_indep = std::sqrt(p2 * (1.0 - p2) / static_cast<double>(N));

        EXPECT_NEAR(single_rate, p, 3.5 * sigma_single) << "seed=" << seed;
        EXPECT_NEAR(corr_rate, p, 3.5 * sigma_single) << "seed=" << seed;
        EXPECT_NEAR(indep_rate, p2, 3.5 * sigma_indep) << "seed=" << seed;
    }
}

TEST(ImpairmentStageTests, HoldBackQueueHandlesDuplicatesReorderAndDelay)
{
    // 1. Duplication suppression
    {
        ImpairmentStage dup_stage({0.0, 1.0, 0.0, std::chrono::milliseconds(0), 7, 1});
        DualFeedSubscriber sub;
        for (uint64_t seq = 1; seq <= 10; ++seq) {
            for (auto& ev : dup_stage.on_receive(event(seq), seq * 1000)) {
                sub.on_packet(FeedId::A, std::move(ev));
            }
        }
        EXPECT_EQ(dup_stage.duplicated_count(), 10U);
        EXPECT_EQ(sub.sequence_tracker().duplicate_count(), 10U);
        EXPECT_EQ(sub.expected_sequence(), 11U);
    }

    // 2. Reordered packets inside grace window resolve without TCP recovery
    {
        ImpairmentStage reorder_stage({0.0, 0.0, 1.0, std::chrono::milliseconds(0), 7, 1});
        DualFeedSubscriber sub;
        for (uint64_t seq = 1; seq <= 6; ++seq) {
            for (auto& ev : reorder_stage.on_receive(event(seq), seq * 1000)) {
                sub.on_packet(FeedId::A, std::move(ev));
            }
        }
        for (auto& ev : reorder_stage.flush()) {
            sub.on_packet(FeedId::A, std::move(ev));
        }
        EXPECT_GT(reorder_stage.reordered_count(), 0U);
        EXPECT_EQ(sub.expected_sequence(), 7U);
        EXPECT_EQ(sub.pending_event_count(), 0U);
    }

    // 3. Delayed packets are held until release timestamp
    {
        ImpairmentStage delay_stage({0.0, 0.0, 0.0, std::chrono::milliseconds(10), 7, 1});
        const auto immediate = delay_stage.on_receive(event(1), 1'000'000ULL);
        EXPECT_TRUE(immediate.empty());
        EXPECT_EQ(delay_stage.delayed_count(), 1U);
        EXPECT_TRUE(delay_stage.poll_ready(5'000'000ULL).empty());
        const auto released = delay_stage.poll_ready(11'000'000ULL);
        ASSERT_EQ(released.size(), 1U);
        EXPECT_EQ(released.front().header.sequence_number, 1U);
    }
}

TEST(MulticastReceiverTests, StopReturnsPromptlyUnder500Ms)
{
    MulticastReceiver receiver;
    const auto started = std::chrono::steady_clock::now();
    receiver.stop();
    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - started).count();
    EXPECT_LT(elapsed_ms, 500);
}

TEST(RedundancyExperimentTests, RecordsDesignLevelMetrics)
{
    RedundancyExperiment experiment({RedundancyMode::DUAL_FEED_INDEPENDENT_LOSS, {}, {}});
    experiment.record_packet_sent();
    experiment.record_gap(true);
    EXPECT_EQ(experiment.metrics().packets_sent, 1);
    EXPECT_EQ(experiment.metrics().gaps_avoided_by_second_feed, 1);
}
