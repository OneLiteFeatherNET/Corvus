package net.onelitefeather.corvus.plugin.capture;

import net.onelitefeather.corvus.plugin.time.NanoClock;
import net.onelitefeather.corvus.proto.capture.v1.CaptureBatch;
import net.onelitefeather.corvus.proto.capture.v1.Envelope;
import net.onelitefeather.corvus.proto.capture.v1.Frame;
import net.onelitefeather.corvus.proto.capture.v1.SessionEnd;

import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/**
 * Drains the queue, batches frames per session and hands the resulting {@link Envelope}s to an
 * {@link EnvelopeTransport}. Not thread-safe: exactly one writer thread (or a test) calls it.
 */
public final class CaptureWriter {

    private static final class Target {
        final SessionState session;
        boolean opened;
        boolean failed;
        final List<Frame> pending = new ArrayList<>();
        int pendingSegment;
        long lastFlushNanos;

        Target(SessionState session) {
            this.session = session;
        }
    }

    private final EnvelopeTransport transport;
    private final ByteBoundedQueue queue;
    private final int batchSize;
    private final long flushIntervalNanos;
    private final NanoClock clock;
    private final Metrics metrics;
    private final Consumer<String> warn;
    private final Map<SessionState, Target> targets = new HashMap<>();

    /** File sink: one {@code .cvc} file per session below {@code capturesDir}. */
    public CaptureWriter(Path capturesDir, ByteBoundedQueue queue, int batchSize, long flushIntervalMillis,
                         NanoClock clock, Metrics metrics, Consumer<String> warn) {
        this(new FileTransport(capturesDir), queue, batchSize, flushIntervalMillis, clock, metrics, warn);
    }

    public CaptureWriter(EnvelopeTransport transport, ByteBoundedQueue queue, int batchSize,
                         long flushIntervalMillis, NanoClock clock, Metrics metrics, Consumer<String> warn) {
        this.transport = transport;
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
        transport.poll();
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

    /** Drains everything that is queued, flushes and closes all sessions and the transport. */
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
        transport.close();
    }

    private void handle(WriteItem item) throws IOException {
        Target t = targetFor(item.session());
        switch (item) {
            case WriteItem.Start s -> {
                transport.begin(t.session, s.envelope());
                t.opened = true;
                t.lastFlushNanos = clock.nanoTime();
            }
            case WriteItem.Segment s -> {
                flushPending(t);
                send(t, s.envelope(), 0);
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
                send(t, Envelope.newBuilder().setSessionEnd(end).build(), 0);
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

    private void send(Target t, Envelope envelope, int frameCount) throws IOException {
        if (t.failed || !t.opened) {
            return;
        }
        transport.send(t.session, envelope, frameCount);
    }

    private void flushPending(Target t) throws IOException {
        if (t.pending.isEmpty() || t.failed || !t.opened) {
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
        send(t, Envelope.newBuilder().setBatch(batch).build(), batch.getFramesCount());
        t.lastFlushNanos = clock.nanoTime();
    }

    private void fail(Target t, Throwable cause) {
        if (!t.failed) {
            t.failed = true;
            metrics.writerErrors.increment();
            warn.accept("Capture output of session " + t.session.idHex() + " failed, dropping the rest of it: "
                    + cause);
        }
        t.pending.clear();
        close(t);
    }

    private void close(Target t) {
        if (t.opened) {
            t.opened = false;
            transport.end(t.session);
        }
    }
}
