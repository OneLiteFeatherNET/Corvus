package net.onelitefeather.corvus.plugin.capture;

import com.google.protobuf.ByteString;
import net.onelitefeather.corvus.plugin.support.EngineFixture;
import net.onelitefeather.corvus.plugin.support.FakePacketView;
import net.onelitefeather.corvus.proto.capture.v1.CaptureBatch;
import net.onelitefeather.corvus.proto.capture.v1.CaptureCounters;
import net.onelitefeather.corvus.proto.capture.v1.Envelope;
import net.onelitefeather.corvus.proto.capture.v1.Frame;
import net.onelitefeather.corvus.proto.capture.v1.SessionStart;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class NatsTransportTest {

    private static final CaptureCounters NONE = CaptureCounters.getDefaultInstance();

    private final FakeEnvelopePublisher publisher = new FakeEnvelopePublisher();
    private final Metrics metrics = new Metrics();
    private final AtomicLong nanos = new AtomicLong(1_000);
    private final List<String> warnings = new ArrayList<>();

    private NatsTransport transport(long budget) {
        return new NatsTransport(publisher, "corvus.capture.v1", "proxy-a", 16, budget, nanos::get, metrics,
                warnings::add);
    }

    private static SessionState session(int n) {
        byte[] id = new byte[16];
        id[0] = (byte) 0xab;
        id[15] = (byte) n;
        return new SessionState(id, 0, 0, new Object());
    }

    private static Envelope start(SessionState s) {
        return Envelope.newBuilder().setSessionStart(SessionStart.newBuilder().setSessionId(s.idBytes())).build();
    }

    private static Envelope batch(SessionState s, int frames, int payload, CaptureCounters counters) {
        CaptureBatch.Builder b = CaptureBatch.newBuilder().setSessionId(s.idBytes()).setCounters(counters);
        for (int i = 0; i < frames; i++) {
            b.addFrames(Frame.newBuilder().setSeq(i).setPayload(ByteString.copyFrom(new byte[payload])));
        }
        return Envelope.newBuilder().setBatch(b).build();
    }

    @Test
    void publishesToPrefixDotProxyIdWithSchemaAndMsgIdHeaders() throws Exception {
        SessionState s = session(1);
        NatsTransport t = transport(1 << 20);

        t.begin(s, start(s));

        FakeEnvelopePublisher.Published p = publisher.published.get(0);
        assertEquals("corvus.capture.v1.proxy-a.00", p.subject());
        assertEquals("capture.v1", p.headers().get("Corvus-Schema"));
        assertEquals(s.idHex() + "-0", p.headers().get("Nats-Msg-Id"));
        assertEquals(start(s), Envelope.parseFrom(p.body()), "body is the bare envelope without length prefix");
    }

    @Test
    void msgIdCounterIsMonotonicPerSession() {
        SessionState a = session(1);
        SessionState b = session(2);
        NatsTransport t = transport(1 << 20);

        t.begin(a, start(a));
        t.begin(b, start(b));
        t.send(a, batch(a, 1, 10, NONE), 1);
        t.send(a, batch(a, 1, 10, NONE), 1);
        t.send(b, batch(b, 1, 10, NONE), 1);

        List<String> ids = publisher.published.stream().map(p -> p.headers().get("Nats-Msg-Id")).toList();
        assertEquals(List.of(a.idHex() + "-0", b.idHex() + "-0", a.idHex() + "-1", a.idHex() + "-2",
                b.idHex() + "-1"), ids);
    }

    @Test
    void counterAdvancesForDroppedEnvelopesSoIdsAreNeverReused() {
        SessionState s = session(1);
        NatsTransport t = transport(1 << 20);
        t.begin(s, start(s));
        publisher.autoAck = false;
        t.send(s, batch(s, 1, 10, NONE), 1);
        publisher.published.get(1).ack().completeExceptionally(new RuntimeException("timeout"));

        publisher.autoAck = true;
        t.send(s, batch(s, 1, 10, NONE), 1);

        assertEquals(s.idHex() + "-2", publisher.published.get(2).headers().get("Nats-Msg-Id"));
    }

    @Test
    void oversizedBatchIsSplitIntoEnvelopesBelow512KiB() throws Exception {
        SessionState s = session(1);
        NatsTransport t = transport(64L << 20);
        t.begin(s, start(s));

        t.send(s, batch(s, 600, 2048, NONE), 600);

        List<FakeEnvelopePublisher.Published> parts = publisher.published.subList(1, publisher.published.size());
        assertTrue(parts.size() >= 3, "1.2 MB of frames needs at least three envelopes, got " + parts.size());
        int frames = 0;
        for (FakeEnvelopePublisher.Published p : parts) {
            assertTrue(p.body().length < 512 * 1024, "envelope of " + p.body().length + " bytes is too large");
            frames += Envelope.parseFrom(p.body()).getBatch().getFramesCount();
        }
        assertEquals(600, frames, "no frame is lost by splitting");
    }

    @Test
    void splittingKeepsFrameOrderAndCountersOnlyOnTheFirstPart() throws Exception {
        SessionState s = session(1);
        NatsTransport t = transport(64L << 20);
        t.begin(s, start(s));
        CaptureCounters counters = CaptureCounters.newBuilder().setDroppedPrivacy(7).build();

        t.send(s, batch(s, 400, 2048, counters), 400);

        long drops = 0;
        long next = 0;
        for (FakeEnvelopePublisher.Published p : publisher.published.subList(1, publisher.published.size())) {
            CaptureBatch b = Envelope.parseFrom(p.body()).getBatch();
            drops += b.getCounters().getDroppedPrivacy();
            for (Frame f : b.getFramesList()) {
                assertEquals(next++, f.getSeq(), "frames stay in order across parts");
            }
        }
        assertEquals(7, drops, "counters are reported exactly once");
    }

    @Test
    void smallBatchIsNotSplit() {
        SessionState s = session(1);
        NatsTransport t = transport(1 << 20);
        t.begin(s, start(s));

        t.send(s, batch(s, 10, 100, NONE), 10);

        assertEquals(2, publisher.published.size());
    }

    @Test
    void failedPublishCountsFramesAsDroppedTransportAndReportsThemInTheNextBatch() {
        SessionState s = session(1);
        NatsTransport t = transport(1 << 20);
        t.begin(s, start(s));
        publisher.autoAck = false;

        t.send(s, batch(s, 5, 10, NONE), 5);
        publisher.published.get(1).ack().completeExceptionally(new IllegalStateException("ack timeout"));

        assertEquals(5, metrics.droppedTransport.sum());
        assertEquals(5, s.takeCounters().getDroppedTransport(), "reported with the next batch or session end");
        assertEquals(0, s.takeCounters().getDroppedTransport(), "and only once");
    }

    @Test
    void synchronousPublisherExceptionIsADropNotACrash() {
        SessionState s = session(1);
        NatsTransport t = transport(1 << 20);
        t.begin(s, start(s));
        publisher.throwOnPublish = new IllegalStateException("closed");

        t.send(s, batch(s, 3, 10, NONE), 3);

        assertEquals(3, metrics.droppedTransport.sum());
        assertEquals(0, t.inFlightBytes(), "the reserved budget is released");
    }

    @Test
    void droppedBatchGivesItsCountersBackToTheSession() {
        SessionState s = session(1);
        NatsTransport t = transport(1 << 20);
        t.begin(s, start(s));
        publisher.throwOnPublish = new IllegalStateException("closed");
        CaptureCounters taken = CaptureCounters.newBuilder().setDroppedQueueFull(4).setListenerErrors(2).build();

        t.send(s, batch(s, 3, 10, taken), 3);

        CaptureCounters next = s.takeCounters();
        assertEquals(4, next.getDroppedQueueFull());
        assertEquals(2, next.getListenerErrors());
        assertEquals(3, next.getDroppedTransport());
    }

    @Test
    void exhaustedBudgetDropsTheBatchWithoutPublishingOrBlocking() {
        SessionState s = session(1);
        NatsTransport t = transport(5_000);
        t.begin(s, start(s));
        publisher.autoAck = false;
        t.send(s, batch(s, 2, 1500, NONE), 2);
        int publishedBefore = publisher.published.size();

        t.send(s, batch(s, 2, 1500, NONE), 2);
        t.send(s, batch(s, 2, 1500, NONE), 2);

        assertEquals(publishedBefore, publisher.published.size(), "over-budget batches never reach the broker");
        assertEquals(4, metrics.droppedTransport.sum());
        assertEquals(4, s.takeCounters().getDroppedTransport());
    }

    @Test
    void budgetIsReleasedWhenAnAckArrives() {
        SessionState s = session(1);
        NatsTransport t = transport(5_000);
        t.begin(s, start(s));
        publisher.autoAck = false;
        t.send(s, batch(s, 2, 1500, NONE), 2);
        assertTrue(t.inFlightBytes() > 3_000);

        publisher.published.get(1).ack().complete(null);

        assertEquals(0, t.inFlightBytes());
        t.send(s, batch(s, 2, 1500, NONE), 2);
        assertEquals(0, metrics.droppedTransport.sum());
    }

    @Test
    void lifecycleEnvelopesAreNotDroppedByAnExhaustedBudget() {
        SessionState s = session(1);
        NatsTransport t = transport(2_000);
        t.begin(s, start(s));
        publisher.autoAck = false;
        t.send(s, batch(s, 1, 1900, NONE), 1);

        t.send(s, Envelope.newBuilder().setSegmentStart(
                net.onelitefeather.corvus.proto.capture.v1.SegmentStart.newBuilder().setSessionId(s.idBytes()))
                .build(), 0);

        assertEquals(3, publisher.published.size());
        assertEquals(0, metrics.transportFailures.sum());
    }

    private static Envelope segment(SessionState s) {
        return Envelope.newBuilder().setSegmentStart(
                net.onelitefeather.corvus.proto.capture.v1.SegmentStart.newBuilder().setSessionId(s.idBytes()))
                .build();
    }

    private static Envelope end(SessionState s) {
        return Envelope.newBuilder().setSessionEnd(
                net.onelitefeather.corvus.proto.capture.v1.SessionEnd.newBuilder().setSessionId(s.idBytes()))
                .build();
    }

    @Test
    void sessionStartBeforeConnectIsPublishedFirstAndInOrderAfterConnect() throws Exception {
        SessionState s = session(1);
        NatsTransport t = transport(1 << 20);
        publisher.ready = false;

        t.begin(s, start(s));
        t.send(s, segment(s), 0);
        assertEquals(0, publisher.published.size(), "nothing is published without a connection");

        publisher.ready = true;
        t.poll();

        assertEquals(2, publisher.published.size());
        assertEquals(Envelope.BodyCase.SESSION_START, Envelope.parseFrom(publisher.published.get(0).body()).getBodyCase());
        assertEquals(s.idHex() + "-0", publisher.published.get(0).headers().get("Nats-Msg-Id"));
        assertEquals(s.idHex() + "-1", publisher.published.get(1).headers().get("Nats-Msg-Id"));
    }

    @Test
    void batchWhileSessionStartIsUnpublishedIsDroppedAndCounted() {
        SessionState s = session(1);
        NatsTransport t = transport(1 << 20);
        publisher.ready = false;
        t.begin(s, start(s));

        t.send(s, batch(s, 4, 10, NONE), 4);

        assertEquals(0, publisher.published.size(), "a batch is never sent ahead of its SessionStart");
        assertEquals(4, metrics.droppedTransport.sum());
        assertEquals(4, s.takeCounters().getDroppedTransport());

        publisher.ready = true;
        t.poll();
        t.send(s, batch(s, 1, 10, NONE), 1);
        assertEquals(2, publisher.published.size(), "start, then the new batch");
    }

    @Test
    void failedSessionStartIsRetriedAndBatchesAreDroppedMeanwhile() {
        SessionState s = session(1);
        NatsTransport t = transport(1 << 20);
        publisher.autoAck = false;
        t.begin(s, start(s));
        publisher.published.get(0).ack().completeExceptionally(new RuntimeException("no ack"));

        t.send(s, batch(s, 2, 10, NONE), 2);
        assertEquals(2, metrics.droppedTransport.sum());

        publisher.autoAck = true;
        t.poll();
        assertEquals(2, publisher.published.size(), "the start is published again with the same id");
        assertEquals(publisher.published.get(0).headers().get("Nats-Msg-Id"),
                publisher.published.get(1).headers().get("Nats-Msg-Id"));
    }

    @Test
    void backlogOverflowDropsOldestSessionEndThenSegmentButNeverASessionStart() {
        NatsTransport t = new NatsTransport(publisher, "corvus.capture.v1", "proxy-a", 16, 1 << 20, 4, 1 << 20,
                nanos::get, metrics, warnings::add);
        publisher.ready = false;
        SessionState a = session(1);
        SessionState b = session(2);
        t.begin(a, start(a));
        t.send(a, end(a), 0);
        t.begin(b, start(b));
        t.send(b, segment(b), 0);

        t.begin(session(3), start(session(3)));
        assertEquals(1, metrics.lifecycleDropped.sum(), "the SessionEnd goes first");
        t.begin(session(4), start(session(4)));
        assertEquals(2, metrics.lifecycleDropped.sum(), "then the SegmentStart");
        t.begin(session(5), start(session(5)));
        assertEquals(2, metrics.lifecycleDropped.sum(), "SessionStarts are kept even beyond the bound");

        publisher.ready = true;
        t.poll();
        assertEquals(5, publisher.published.size());
    }

    @Test
    void backlogIsBoundedByBytesToo() {
        NatsTransport t = new NatsTransport(publisher, "corvus.capture.v1", "proxy-a", 16, 1 << 20, 100, 40,
                nanos::get, metrics, warnings::add);
        publisher.ready = false;
        SessionState a = session(1);
        t.begin(a, start(a));
        t.send(a, end(a), 0);
        t.send(a, end(a), 0);

        assertTrue(metrics.lifecycleDropped.sum() >= 1, "ends are dropped once the byte bound is hit");
    }

    @Test
    void failuresWarnOncePerMinuteAndHintAtAMissingStream() {
        SessionState s = session(1);
        NatsTransport t = transport(1 << 20);
        t.begin(s, start(s));
        publisher.throwOnPublish = new IllegalStateException("503 No responders available for request");

        t.send(s, batch(s, 1, 10, NONE), 1);
        t.send(s, batch(s, 1, 10, NONE), 1);
        assertEquals(1, warnings.size(), "second failure within the minute is suppressed");
        assertTrue(warnings.get(0).contains("CORVUS_CAPTURE"), warnings.get(0));

        nanos.addAndGet(61_000_000_000L);
        t.send(s, batch(s, 1, 10, NONE), 1);
        assertEquals(2, warnings.size());
        assertTrue(warnings.get(1).contains("1 more"), warnings.get(1));
    }

    @Test
    void closeFlushesWithAShortBoundedTimeout() {
        transport(1 << 20).close();

        assertEquals(Duration.ofSeconds(3), publisher.closedWith);
    }

    @Test
    void writerBatchingFeedsTheNatsTransport() throws Exception {
        ByteBoundedQueue queue = new ByteBoundedQueue(1 << 20);
        EngineFixture f = EngineFixture.withSink(queue);
        NatsTransport t = new NatsTransport(publisher, "corvus.capture.v1", "proxy-test", 16, 1 << 20, f.nanos::get,
                f.metrics, f.logs::add);
        CaptureWriter writer = new CaptureWriter(t, queue, 2, 5_000, f.nanos::get, f.metrics, f.logs::add);
        for (int i = 0; i < 5; i++) {
            f.engine.onPacket(new FakePacketView());
        }
        f.engine.onDisconnect(FakePacketView.PLAYER);

        writer.drain(100);
        writer.closeAll();

        List<Envelope.BodyCase> kinds = new ArrayList<>();
        for (FakeEnvelopePublisher.Published p : publisher.published) {
            kinds.add(Envelope.parseFrom(p.body()).getBodyCase());
        }
        assertEquals(List.of(Envelope.BodyCase.SESSION_START, Envelope.BodyCase.BATCH, Envelope.BodyCase.BATCH,
                Envelope.BodyCase.BATCH, Envelope.BodyCase.SESSION_END), kinds);
        assertEquals(Duration.ofSeconds(3), publisher.closedWith);
    }
}
