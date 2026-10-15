package net.onelitefeather.corvus.plugin.capture;

import com.github.retrooper.packetevents.protocol.packettype.PacketType;
import net.onelitefeather.corvus.plugin.config.CorvusConfig;
import net.onelitefeather.corvus.plugin.privacy.PlayerFilter;
import net.onelitefeather.corvus.plugin.support.EngineFixture;
import net.onelitefeather.corvus.plugin.support.FakePacketView;
import net.onelitefeather.corvus.plugin.support.RecordingSink;
import net.onelitefeather.corvus.plugin.vocab.CorvusPacket;
import net.onelitefeather.corvus.proto.capture.v1.Frame;
import org.junit.jupiter.api.Test;

import java.util.Set;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class CaptureEngineTest {

    private static final UUID OTHER = UUID.fromString("99999999-8888-7777-6666-555555555555");

    @Test
    void listenerNeverThrowsWhenSinkThrows() {
        CaptureSink throwingSink = new CaptureSink() {
            @Override
            public boolean offer(WriteItem item) {
                throw new IllegalStateException("sink down");
            }

            @Override
            public void offerControl(WriteItem item) {
            }
        };
        EngineFixture f = EngineFixture.withSink(throwingSink);

        f.engine.onPacket(new FakePacketView());

        assertEquals(1, f.metrics.listenerErrors.sum(), "the failure must be counted");
    }

    @Test
    void listenerNeverThrowsWhenPayloadCopyThrows() {
        EngineFixture f = EngineFixture.withSink(new RecordingSink());

        f.engine.onPacket(new FakePacketView().payloadCopyFails(new IllegalStateException("buffer released")));

        assertEquals(1, f.metrics.listenerErrors.sum());
    }

    @Test
    void listenerNeverThrowsWhenViewItselfIsBroken() {
        EngineFixture f = EngineFixture.withSink(new RecordingSink());
        PacketView broken = new FakePacketView() {
            @Override
            public java.util.UUID playerId() {
                throw new IllegalStateException("no user");
            }
        };

        f.engine.onPacket(broken);

        assertEquals(1, f.metrics.listenerErrors.sum());
    }

    @Test
    void frameIsKeptWithoutEventWhenDecoderThrows() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = EngineFixture.withSink(sink);

        f.engine.onPacket(new FakePacketView().decodeFails(new IllegalArgumentException("bad wrapper")));

        assertEquals(1, sink.frames.size(), "raw frame survives a decode failure");
        Frame frame = ((WriteItem.FrameItem) sink.frames.get(0)).frame();
        assertEquals(Frame.EventCase.EVENT_NOT_SET, frame.getEventCase());
        assertEquals(1, f.metrics.listenerErrors.sum());
    }

    @Test
    void framesAreDroppedAndCountedWhenQueueIsFull() {
        ByteBoundedQueue tinyQueue = new ByteBoundedQueue(100);
        EngineFixture f = EngineFixture.withSink(tinyQueue);

        f.engine.onPacket(new FakePacketView().payload(new byte[500]));
        long bytesAfterFirstDrop = tinyQueue.bytes();
        f.engine.onPacket(new FakePacketView().payload(new byte[500]));

        assertEquals(2, f.metrics.droppedQueueFull.sum());
        assertEquals(0, f.metrics.framesCaptured.sum());
        assertEquals(bytesAfterFirstDrop, tinyQueue.bytes(), "a rejected frame must not hold queue bytes");
    }

    @Test
    void queueRejectsOfferThatWouldExceedByteBound() {
        ByteBoundedQueue queue = new ByteBoundedQueue(300); // session start (128) + one 164-byte frame
        EngineFixture f = EngineFixture.withSink(queue);

        f.engine.onPacket(new FakePacketView().payload(new byte[100]));
        f.engine.onPacket(new FakePacketView().payload(new byte[100]));

        assertEquals(1, f.metrics.framesCaptured.sum());
        assertEquals(1, f.metrics.droppedQueueFull.sum());
    }

    @Test
    void sequenceNumberGapsMarkDroppedFrames() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = EngineFixture.withSink(sink);

        f.engine.onPacket(new FakePacketView());
        sink.acceptFrames = false;
        f.engine.onPacket(new FakePacketView());
        sink.acceptFrames = true;
        f.engine.onPacket(new FakePacketView());

        assertEquals(0, ((WriteItem.FrameItem) sink.frames.get(0)).frame().getSeq());
        assertEquals(2, ((WriteItem.FrameItem) sink.frames.get(1)).frame().getSeq(), "dropped frame leaves a gap");
    }

    @Test
    void nothingIsCapturedWithoutPseudonymKey() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = EngineFixture.withoutKey(sink);

        f.engine.onPacket(new FakePacketView());

        assertFalse(f.engine.isCapturing());
        assertEquals(0, sink.frames.size());
        assertEquals(0, sink.controls.size(), "no session may be started without a key");
        assertEquals(0, f.metrics.listenerErrors.sum());
    }

    @Test
    void allowlistModeCapturesOnlyListedPlayers() {
        RecordingSink sink = new RecordingSink();
        PlayerFilter filter = new PlayerFilter(CorvusConfig.ListMode.ALLOWLIST, Set.of(FakePacketView.PLAYER), Set.of());
        EngineFixture f = new EngineFixture().build(sink, EngineFixture.KEY, true, 2048, filter);

        f.engine.onPacket(new FakePacketView().player(OTHER));
        f.engine.onPacket(new FakePacketView());

        assertEquals(1, sink.frames.size());
        assertEquals(1, f.engine.activeSessions());
    }

    @Test
    void blocklistExcludesPlayerInAllMode() {
        RecordingSink sink = new RecordingSink();
        PlayerFilter filter = new PlayerFilter(CorvusConfig.ListMode.ALL, Set.of(), Set.of(OTHER));
        EngineFixture f = new EngineFixture().build(sink, EngineFixture.KEY, true, 2048, filter);

        f.engine.onPacket(new FakePacketView().player(OTHER));

        assertEquals(0, sink.frames.size());
        assertEquals(0, f.engine.activeSessions(), "a blocked player never gets a session");
    }

    @Test
    void rawPayloadIsOmittedWhenDisabled() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = new EngineFixture().build(sink, EngineFixture.KEY, false, 2048);

        f.engine.onPacket(new FakePacketView().payload(new byte[]{9, 9}));

        Frame frame = ((WriteItem.FrameItem) sink.frames.get(0)).frame();
        assertTrue(frame.getPayload().isEmpty());
    }

    @Test
    void payloadIsTruncatedToConfiguredMaximum() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = new EngineFixture().build(sink, EngineFixture.KEY, true, 4);

        f.engine.onPacket(new FakePacketView().payload(new byte[100]));

        assertEquals(4, ((WriteItem.FrameItem) sink.frames.get(0)).frame().getPayload().size());
    }

    @Test
    void frameCarriesStableVocabularyIdAndOffsetFromInjectedClock() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = EngineFixture.withSink(sink);
        f.engine.onPacket(new FakePacketView().type(PacketType.Play.Client.CLIENT_TICK_END));
        f.nanos.addAndGet(5_000);

        f.engine.onPacket(new FakePacketView().type(PacketType.Play.Client.CLIENT_TICK_END));

        Frame second = ((WriteItem.FrameItem) sink.frames.get(1)).frame();
        assertEquals(CorvusPacket.C_CLIENT_TICK_END.id(), second.getPacketType());
        assertEquals(5_000, second.getOffsetNanos());
    }

    @Test
    void sessionStartCarriesPseudonymAndNoRawUuid() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = EngineFixture.withSink(sink);

        f.engine.onPacket(new FakePacketView());

        var start = ((WriteItem.Start) sink.controls.get(0)).envelope().getSessionStart();
        assertEquals(32, start.getPlayerPseudonym().size());
        assertEquals("k1", start.getPseudonymKeyId());
        assertEquals(16, start.getSessionId().size());
        assertFalse(f.logs.stream().anyMatch(l -> l.contains(FakePacketView.PLAYER.toString())),
                "logs must not contain the player UUID");
    }

    @Test
    void backendConnectStartsSegmentWithMappedGameContext() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = EngineFixture.withSink(sink);
        f.engine.onPacket(new FakePacketView());

        f.engine.onBackendConnected(FakePacketView.PLAYER, "Lobby", "vanilla");
        f.engine.onBackendConnected(FakePacketView.PLAYER, "skyblock", null);

        var first = ((WriteItem.Segment) sink.controls.get(1)).envelope().getSegmentStart();
        var second = ((WriteItem.Segment) sink.controls.get(2)).envelope().getSegmentStart();
        assertEquals("lobby", first.getGameContext());
        assertEquals("vanilla", first.getClientBrand());
        assertEquals(1, first.getSegment());
        assertEquals("unknown", second.getGameContext());
        assertEquals(2, second.getSegment());
    }

    @Test
    void disconnectEndsSessionExactlyOnce() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = EngineFixture.withSink(sink);
        f.engine.onPacket(new FakePacketView());

        f.engine.onDisconnect(FakePacketView.PLAYER);
        f.engine.onDisconnect(FakePacketView.PLAYER);

        long ends = sink.controls.stream().filter(i -> i instanceof WriteItem.End).count();
        assertEquals(1, ends);
        assertEquals(0, f.engine.activeSessions());
    }

    @Test
    void sessionFedByTwoChannelsIsReported() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = EngineFixture.withSink(sink);

        f.engine.onPacket(new FakePacketView().channel("client-channel"));
        f.engine.onPacket(new FakePacketView().channel("backend-channel"));
        f.engine.onPacket(new FakePacketView().channel("backend-channel"));

        assertEquals(1, f.metrics.sessionsWithMultipleChannels.sum(), "reported once per session");
    }
}
