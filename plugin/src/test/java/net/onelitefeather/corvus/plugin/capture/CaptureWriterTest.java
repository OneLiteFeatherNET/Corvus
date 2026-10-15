package net.onelitefeather.corvus.plugin.capture;

import com.github.retrooper.packetevents.protocol.packettype.PacketType;
import net.onelitefeather.corvus.plugin.support.EngineFixture;
import net.onelitefeather.corvus.plugin.support.FakePacketView;
import net.onelitefeather.corvus.proto.capture.v1.Envelope;
import net.onelitefeather.corvus.proto.capture.v1.Frame;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class CaptureWriterTest {

    private record Setup(EngineFixture fixture, CaptureWriter writer, Path captures) {
    }

    private Setup setup(Path dir, int batchSize) {
        ByteBoundedQueue queue = new ByteBoundedQueue(1 << 20);
        EngineFixture f = EngineFixture.withSink(queue);
        Path captures = dir.resolve("captures");
        CaptureWriter writer = new CaptureWriter(captures, queue, batchSize, 5_000, f.nanos::get, f.metrics, f.logs::add);
        return new Setup(f, writer, captures);
    }

    private static List<Envelope> readAll(Path file) throws IOException {
        List<Envelope> out = new ArrayList<>();
        try (InputStream in = Files.newInputStream(file)) {
            Envelope e;
            while ((e = Envelope.parseDelimitedFrom(in)) != null) {
                out.add(e);
            }
        }
        return out;
    }

    private static Path onlyCaptureFile(Path captures) throws IOException {
        try (Stream<Path> files = Files.walk(captures)) {
            List<Path> found = files.filter(p -> p.toString().endsWith(".cvc")).toList();
            assertEquals(1, found.size(), "exactly one file per session");
            return found.get(0);
        }
    }

    @Test
    void writerProducesParseableDelimitedEnvelopesInOrder(@TempDir Path dir) throws IOException {
        Setup s = setup(dir, 200);
        s.fixture.engine.onPacket(new FakePacketView().payload(new byte[]{1, 2, 3}));
        s.fixture.engine.onBackendConnected(FakePacketView.PLAYER, "lobby", "vanilla");
        s.fixture.engine.onPacket(new FakePacketView().type(PacketType.Play.Server.ENTITY_VELOCITY).clientbound());
        s.fixture.engine.onDisconnect(FakePacketView.PLAYER);

        s.writer.drain(100);
        s.writer.closeAll();

        List<Envelope> envelopes = readAll(onlyCaptureFile(s.captures));
        List<Envelope.BodyCase> kinds = envelopes.stream().map(Envelope::getBodyCase).toList();
        assertEquals(List.of(Envelope.BodyCase.SESSION_START, Envelope.BodyCase.BATCH,
                Envelope.BodyCase.SEGMENT_START, Envelope.BodyCase.BATCH, Envelope.BodyCase.SESSION_END), kinds);
        Frame first = envelopes.get(1).getBatch().getFrames(0);
        assertEquals(0, envelopes.get(1).getBatch().getSegment(), "pre-backend frames belong to segment 0");
        assertEquals(1, envelopes.get(3).getBatch().getSegment());
        assertEquals(3, first.getPayload().size());
    }

    @Test
    void fileIsPlacedUnderDateDirectoryNamedBySessionIdHex(@TempDir Path dir) throws IOException {
        Setup s = setup(dir, 200);
        s.fixture.engine.onPacket(new FakePacketView());
        s.fixture.engine.onDisconnect(FakePacketView.PLAYER);

        s.writer.drain(100);
        s.writer.closeAll();

        Path file = onlyCaptureFile(s.captures);
        assertEquals("2026-10-15", file.getParent().getFileName().toString());
        assertEquals("00000000000000000000000000000001.cvc", file.getFileName().toString());
    }

    @Test
    void batchesAreCutAtConfiguredSizeAndCarryCounters(@TempDir Path dir) throws IOException {
        Setup s = setup(dir, 2);
        for (int i = 0; i < 5; i++) {
            s.fixture.engine.onPacket(new FakePacketView());
        }
        s.fixture.engine.onPacket(new FakePacketView().type(PacketType.Play.Client.CHAT_MESSAGE));
        s.fixture.engine.onDisconnect(FakePacketView.PLAYER);

        s.writer.drain(100);
        s.writer.closeAll();

        List<Envelope> envelopes = readAll(onlyCaptureFile(s.captures));
        long batches = envelopes.stream().filter(Envelope::hasBatch).count();
        assertEquals(3, batches, "5 frames with batch size 2 give 2 + 2 + 1");
        long dropped = envelopes.stream()
                .mapToLong(e -> e.hasBatch() ? e.getBatch().getCounters().getDroppedPrivacy()
                        : e.hasSessionEnd() ? e.getSessionEnd().getCounters().getDroppedPrivacy() : 0)
                .sum();
        assertEquals(1, dropped, "privacy drop is reported exactly once across batches and end");
    }

    @Test
    void pendingFramesAreFlushedOnceTheIntervalHasElapsedWithoutWaiting(@TempDir Path dir) throws IOException {
        Setup s = setup(dir, 200);
        s.fixture.engine.onPacket(new FakePacketView());
        s.writer.drain(100);
        long sizeBeforeFlush = Files.size(onlyCaptureFile(s.captures));

        s.fixture.nanos.addAndGet(6_000L * 1_000_000L);
        s.writer.flushDue();

        assertTrue(Files.size(onlyCaptureFile(s.captures)) > sizeBeforeFlush, "batch must be on disk after flushDue");
        s.writer.closeAll();
    }

    @Test
    void shutdownClosesOpenSessionFilesWithSessionEnd(@TempDir Path dir) throws IOException {
        Setup s = setup(dir, 200);
        s.fixture.engine.onPacket(new FakePacketView());

        s.fixture.engine.shutdown();
        s.writer.closeAll();

        List<Envelope> envelopes = readAll(onlyCaptureFile(s.captures));
        assertEquals(Envelope.BodyCase.SESSION_END, envelopes.get(envelopes.size() - 1).getBodyCase());
    }
}
