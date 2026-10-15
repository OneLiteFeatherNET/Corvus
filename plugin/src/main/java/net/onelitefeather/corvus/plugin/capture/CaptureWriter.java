package net.onelitefeather.corvus.plugin.capture;

import net.onelitefeather.corvus.plugin.time.NanoClock;
import net.onelitefeather.corvus.proto.capture.v1.CaptureBatch;
import net.onelitefeather.corvus.proto.capture.v1.Envelope;
import net.onelitefeather.corvus.proto.capture.v1.Frame;
import net.onelitefeather.corvus.proto.capture.v1.SessionEnd;

import java.io.BufferedOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/**
 * Drains the queue and writes one {@code captures/<yyyy-MM-dd>/<session-id-hex>.cvc} file per session
 * (length-delimited {@link Envelope}s). Not thread-safe: exactly one writer thread (or a test) calls it.
 */
public final class CaptureWriter {

    private static final DateTimeFormatter DAY = DateTimeFormatter.ofPattern("yyyy-MM-dd").withZone(ZoneOffset.UTC);

    private static final class Target {
        final SessionState session;
        OutputStream out;
        boolean failed;
        final List<Frame> pending = new ArrayList<>();
        int pendingSegment;
        long lastFlushNanos;

        Target(SessionState session) {
            this.session = session;
        }
    }

    private final Path capturesDir;
    private final ByteBoundedQueue queue;
    private final int batchSize;
    private final long flushIntervalNanos;
    private final NanoClock clock;
    private final Metrics metrics;
    private final Consumer<String> warn;
    private final Map<SessionState, Target> targets = new HashMap<>();

    public CaptureWriter(Path capturesDir, ByteBoundedQueue queue, int batchSize, long flushIntervalMillis,
                         NanoClock clock, Metrics metrics, Consumer<String> warn) {
        this.capturesDir = capturesDir;
        this.queue = queue;
        this.batchSize = batchSize;
        this.flushIntervalNanos = flushIntervalMillis * 1_000_000L;
        this.clock = clock;
        this.metrics = metrics;
        this.warn = warn;
    }

    /** Processes up to {@code max} queued items. @return the number processed. */
    public int drain(int max) {
        int n = 0;
        WriteItem item;
        while (n < max && (item = queue.poll()) != null) {
            n++;
            try {
                handle(item);
            } catch (Throwable t) {
                fail(targetFor(item.session()), t);
                if (item instanceof WriteItem.End) {
                    targets.remove(item.session());
                }
            }
        }
        return n;
    }

    /** Flushes sessions whose oldest buffered frames have waited for the flush interval. */
    public void flushDue() {
        long now = clock.nanoTime();
        for (Target t : targets.values()) {
            if (!t.pending.isEmpty() && now - t.lastFlushNanos >= flushIntervalNanos) {
                try {
                    flushPending(t);
                } catch (Throwable e) {
                    fail(t, e);
                }
            }
        }
    }

    /** Drains everything that is queued, flushes and closes all files. */
    public void closeAll() {
        while (drain(1024) > 0) {
            // keep draining
        }
        for (Iterator<Target> it = targets.values().iterator(); it.hasNext(); ) {
            Target t = it.next();
            try {
                flushPending(t);
            } catch (Throwable e) {
                fail(t, e);
            }
            close(t);
            it.remove();
        }
    }

    private void handle(WriteItem item) throws IOException {
        Target t = targetFor(item.session());
        switch (item) {
            case WriteItem.Start s -> {
                open(t, s.session());
                write(t, s.envelope());
                t.out.flush();
                t.lastFlushNanos = clock.nanoTime();
            }
            case WriteItem.Segment s -> {
                flushPending(t);
                write(t, s.envelope());
            }
            case WriteItem.FrameItem f -> {
                if (t.failed) {
                    return;
                }
                if (!t.pending.isEmpty() && t.pendingSegment != f.segment()) {
                    flushPending(t);
                }
                if (t.pending.isEmpty()) {
                    t.pendingSegment = f.segment();
                }
                t.pending.add(f.frame());
                if (t.pending.size() >= batchSize) {
                    flushPending(t);
                }
            }
            case WriteItem.End e -> {
                flushPending(t);
                SessionEnd end = SessionEnd.newBuilder()
                        .setSessionId(e.session().idBytes())
                        .setEndedAtUnixMillis(e.endedAtMillis())
                        .setCounters(e.session().takeCounters())
                        .build();
                write(t, Envelope.newBuilder().setSessionEnd(end).build());
                close(t);
                targets.remove(e.session());
            }
        }
    }

    private Target targetFor(SessionState session) {
        return targets.computeIfAbsent(session, s -> {
            Target t = new Target(s);
            t.lastFlushNanos = clock.nanoTime();
            return t;
        });
    }

    private void open(Target t, SessionState session) throws IOException {
        Path dir = capturesDir.resolve(DAY.format(Instant.ofEpochMilli(session.startedAtMillis())));
        Files.createDirectories(dir);
        t.out = new BufferedOutputStream(Files.newOutputStream(dir.resolve(session.idHex() + ".cvc")), 64 * 1024);
    }

    private void write(Target t, Envelope envelope) throws IOException {
        if (t.failed || t.out == null) {
            return;
        }
        envelope.writeDelimitedTo(t.out);
    }

    private void flushPending(Target t) throws IOException {
        if (t.pending.isEmpty() || t.failed || t.out == null) {
            t.pending.clear();
            return;
        }
        CaptureBatch batch = CaptureBatch.newBuilder()
                .setSessionId(t.session.idBytes())
                .setSegment(t.pendingSegment)
                .addAllFrames(t.pending)
                .setCounters(t.session.takeCounters())
                .build();
        t.pending.clear();
        write(t, Envelope.newBuilder().setBatch(batch).build());
        t.out.flush();
        t.lastFlushNanos = clock.nanoTime();
    }

    private void fail(Target t, Throwable cause) {
        if (!t.failed) {
            t.failed = true;
            metrics.writerErrors.increment();
            warn.accept("Capture file of session " + t.session.idHex() + " failed, dropping the rest of it: " + cause);
        }
        t.pending.clear();
        close(t);
    }

    private void close(Target t) {
        if (t.out != null) {
            try {
                t.out.close();
            } catch (IOException ignored) {
                // nothing sensible left to do
            }
            t.out = null;
        }
    }
}
