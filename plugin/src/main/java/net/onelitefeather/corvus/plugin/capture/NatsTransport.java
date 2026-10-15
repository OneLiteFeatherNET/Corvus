package net.onelitefeather.corvus.plugin.capture;

import net.onelitefeather.corvus.plugin.time.NanoClock;
import net.onelitefeather.corvus.proto.capture.v1.CaptureBatch;
import net.onelitefeather.corvus.proto.capture.v1.CaptureCounters;
import net.onelitefeather.corvus.proto.capture.v1.Envelope;
import net.onelitefeather.corvus.proto.capture.v1.Frame;

import com.google.protobuf.CodedOutputStream;

import java.time.Duration;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Consumer;

/**
 * Publishes envelopes to {@code <prefix>.<proxy_id>} through an {@link EnvelopePublisher}, following the
 * contract in docs/reference/data-lake.md. Never blocks: when the in-flight byte budget is exhausted or a
 * publish fails, the envelope is dropped and its frames are counted as {@code dropped_transport}.
 */
public final class NatsTransport implements EnvelopeTransport {

    public static final String SCHEMA_HEADER = "Corvus-Schema";
    public static final String SCHEMA_VALUE = "capture.v1";
    public static final String MSG_ID_HEADER = "Nats-Msg-Id";
    /** Every serialized envelope stays below this size (the stream allows 1 MiB). */
    public static final int MAX_ENVELOPE_BYTES = 512 * 1024;
    public static final Duration SHUTDOWN_TIMEOUT = Duration.ofSeconds(3);
    public static final int DEFAULT_BACKLOG_MAX_COUNT = 50_000;
    public static final long DEFAULT_BACKLOG_MAX_BYTES = 8L * 1024 * 1024;

    private static final int ENVELOPE_OVERHEAD = 16;
    private static final long WARN_INTERVAL_NANOS = 60_000_000_000L;

    private final EnvelopePublisher publisher;
    private final String subject;
    private final long maxInFlightBytes;
    private final NanoClock clock;
    private final Metrics metrics;
    private final Consumer<String> warn;
    private final AtomicLong inFlightBytes = new AtomicLong();
    private final Map<SessionState, Sess> sessions = new HashMap<>();
    /** Lifecycle envelopes not yet handed to the publisher, oldest first. Writer thread only. */
    private final ArrayDeque<Lifecycle> backlog = new ArrayDeque<>();
    private long backlogBytes;
    /** Lifecycle envelopes whose publish failed; merged back into the backlog by the writer thread. */
    private final ConcurrentLinkedQueue<Lifecycle> retries = new ConcurrentLinkedQueue<>();
    private final int backlogMaxCount;
    private final long backlogMaxBytes;
    private boolean warned;
    private long lastWarnNanos;
    private long suppressedWarnings;

    /** Per-session transport state. */
    private static final class Sess {
        long next;
        /** True once the SessionStart was handed to the publisher; reset when that publish fails. */
        volatile boolean startSent;
    }

    private enum Kind { START, SEGMENT, END }

    private record Lifecycle(SessionState session, Sess sess, Kind kind, long n, byte[] body) {
    }

    public NatsTransport(EnvelopePublisher publisher, String subjectPrefix, String proxyId, long maxInFlightBytes,
                         NanoClock clock, Metrics metrics, Consumer<String> warn) {
        this(publisher, subjectPrefix, proxyId, maxInFlightBytes, DEFAULT_BACKLOG_MAX_COUNT,
                DEFAULT_BACKLOG_MAX_BYTES, clock, metrics, warn);
    }

    public NatsTransport(EnvelopePublisher publisher, String subjectPrefix, String proxyId, long maxInFlightBytes,
                         int backlogMaxCount, long backlogMaxBytes, NanoClock clock, Metrics metrics,
                         Consumer<String> warn) {
        this.backlogMaxCount = backlogMaxCount;
        this.backlogMaxBytes = backlogMaxBytes;
        this.publisher = publisher;
        this.subject = subjectPrefix + "." + proxyId;
        this.maxInFlightBytes = maxInFlightBytes;
        this.clock = clock;
        this.metrics = metrics;
        this.warn = warn;
    }

    public long inFlightBytes() {
        return inFlightBytes.get();
    }

    @Override
    public void begin(SessionState session, Envelope sessionStart) {
        Sess sess = new Sess();
        sessions.put(session, sess);
        enqueueLifecycle(session, sess, Kind.START, sessionStart);
    }

    @Override
    public void send(SessionState session, Envelope envelope, int frameCount) {
        Sess sess = sessions.computeIfAbsent(session, s -> new Sess());
        if (!envelope.hasBatch()) {
            enqueueLifecycle(session, sess, envelope.hasSessionEnd() ? Kind.END : Kind.SEGMENT, envelope);
            return;
        }
        CaptureBatch batch = envelope.getBatch();
        List<CaptureBatch> parts = split(batch, MAX_ENVELOPE_BYTES);
        for (int i = 0; i < parts.size(); i++) {
            CaptureBatch part = parts.get(i);
            // Only the first part carries the counters so they are not summed twice by ingest.
            CaptureCounters carried = i == 0 ? part.getCounters() : null;
            publishBatch(session, sess, Envelope.newBuilder().setBatch(part).build(), part.getFramesCount(),
                    carried);
        }
    }

    @Override
    public void end(SessionState session) {
        // Queued lifecycle entries keep their own reference to the per-session state.
        sessions.remove(session);
    }

    @Override
    public void poll() {
        drainBacklog();
    }

    @Override
    public void close() {
        drainBacklog();
        publisher.close(SHUTDOWN_TIMEOUT);
    }

    /** Lifecycle envelopes wait here until a connection exists; they are published first and in order. */
    private void enqueueLifecycle(SessionState session, Sess sess, Kind kind, Envelope envelope) {
        // The counter always advances, also for dropped envelopes: a timed-out publish may still have been
        // stored, and reusing its id would make JetStream discard the next real envelope as a duplicate.
        long n = sess.next++;
        byte[] body = envelope.toByteArray();
        backlog.addLast(new Lifecycle(session, sess, kind, n, body));
        backlogBytes += body.length;
        enforceBacklogBounds();
        drainBacklog();
    }

    /** Overflow policy: oldest SessionEnd first, then oldest SegmentStart; a SessionStart is never dropped. */
    private void enforceBacklogBounds() {
        while (backlog.size() > backlogMaxCount || backlogBytes > backlogMaxBytes) {
            Lifecycle victim = oldest(Kind.END);
            if (victim == null) {
                victim = oldest(Kind.SEGMENT);
            }
            if (victim == null) {
                return;
            }
            backlog.remove(victim);
            backlogBytes -= victim.body().length;
            metrics.lifecycleDropped.increment();
            warnThrottled("lifecycle backlog full, dropped a " + victim.kind() + " envelope", 0, null);
        }
    }

    private Lifecycle oldest(Kind kind) {
        for (Lifecycle l : backlog) {
            if (l.kind() == kind) {
                return l;
            }
        }
        return null;
    }

    private void drainBacklog() {
        for (Lifecycle l; (l = retries.poll()) != null; ) {
            backlog.addFirst(l);
            backlogBytes += l.body().length;
        }
        while (!backlog.isEmpty() && publisher.isReady()) {
            Lifecycle l = backlog.pollFirst();
            backlogBytes -= l.body().length;
            publishLifecycle(l);
        }
    }

    private void publishLifecycle(Lifecycle l) {
        inFlightBytes.addAndGet(l.body().length);
        if (l.kind() == Kind.START) {
            l.sess().startSent = true;
        }
        try {
            publisher.publish(subject, headers(l.session(), l.n()), l.body()).whenComplete((v, error) -> {
                inFlightBytes.addAndGet(-l.body().length);
                if (error != null) {
                    requeue(l, error);
                }
            });
        } catch (Throwable t) {
            inFlightBytes.addAndGet(-l.body().length);
            requeue(l, t);
        }
    }

    private void requeue(Lifecycle l, Throwable error) {
        if (l.kind() == Kind.START) {
            l.sess().startSent = false;
        }
        retries.add(l);
        warnThrottled("lifecycle publish failed, will retry", 0, error);
    }

    private void publishBatch(SessionState session, Sess sess, Envelope envelope, int frames,
                              CaptureCounters carried) {
        long n = sess.next++;
        byte[] body = envelope.toByteArray();
        if (!sess.startSent) {
            // Never send frames ahead of the SessionStart: ingest could not link them to a pseudonym.
            drop(session, frames, carried, "SessionStart not published yet", null);
            return;
        }
        if (body.length > MAX_ENVELOPE_BYTES || !reserve(body.length, false)) {
            drop(session, frames, carried, body.length > MAX_ENVELOPE_BYTES
                    ? "envelope larger than " + MAX_ENVELOPE_BYTES + " bytes"
                    : "in-flight budget of " + maxInFlightBytes + " bytes exhausted", null);
            return;
        }
        try {
            publisher.publish(subject, headers(session, n), body).whenComplete((v, error) -> {
                inFlightBytes.addAndGet(-body.length);
                if (error != null) {
                    drop(session, frames, carried, "publish failed", error);
                }
            });
        } catch (Throwable t) {
            inFlightBytes.addAndGet(-body.length);
            drop(session, frames, carried, "publish failed", t);
        }
    }

    private static Map<String, String> headers(SessionState session, long n) {
        return Map.of(MSG_ID_HEADER, session.idHex() + "-" + n, SCHEMA_HEADER, SCHEMA_VALUE);
    }

    private boolean reserve(long size, boolean control) {
        // Only frames are budgeted; lifecycle envelopes are small and bounded by their own backlog.
        if (!control && inFlightBytes.get() + size > maxInFlightBytes) {
            return false;
        }
        inFlightBytes.addAndGet(size);
        return true;
    }

    private void drop(SessionState session, int frames, CaptureCounters carried, String reason, Throwable error) {
        metrics.transportFailures.increment();
        if (frames > 0) {
            metrics.droppedTransport.add(frames);
            session.addDroppedTransport(frames);
        }
        if (carried != null) {
            session.restoreCounters(carried);
        }
        warnThrottled(reason, frames, error);
    }

    private synchronized void warnThrottled(String reason, int frames, Throwable error) {
        long now = clock.nanoTime();
        if (warned && now - lastWarnNanos < WARN_INTERVAL_NANOS) {
            suppressedWarnings++;
            return;
        }
        String cause = error == null ? "" : ": " + rootMessage(error);
        String hint = looksLikeMissingStream(error)
                ? " (does the JetStream stream CORVUS_CAPTURE with subject " + subject + " exist? "
                + "ingest/ops own it, the plugin does not create it)"
                : "";
        warn.accept("NATS " + reason + cause + ", dropped " + frames + " frame(s) on " + subject + hint
                + (suppressedWarnings > 0 ? " [" + suppressedWarnings + " more since the last warning]" : ""));
        warned = true;
        lastWarnNanos = now;
        suppressedWarnings = 0;
    }

    private static boolean looksLikeMissingStream(Throwable error) {
        for (Throwable t = error; t != null; t = t.getCause()) {
            String m = String.valueOf(t.getMessage()).toLowerCase(java.util.Locale.ROOT);
            if (m.contains("no responders") || m.contains("stream not found") || m.contains("503")
                    || m.contains("10059")) {
                return true;
            }
        }
        return false;
    }

    private static String rootMessage(Throwable error) {
        Throwable t = error;
        while (t.getCause() != null) {
            t = t.getCause();
        }
        return t.getClass().getSimpleName() + (t.getMessage() == null ? "" : " " + t.getMessage());
    }

    /**
     * Splits a batch so that each serialized envelope stays below {@code maxEnvelopeBytes}. Frames keep their
     * order; the counters stay on the first part only.
     */
    static List<CaptureBatch> split(CaptureBatch batch, int maxEnvelopeBytes) {
        if (batch.getSerializedSize() + ENVELOPE_OVERHEAD <= maxEnvelopeBytes) {
            return List.of(batch);
        }
        CaptureBatch empty = batch.toBuilder().clearFrames().build();
        long base = empty.getSerializedSize() + ENVELOPE_OVERHEAD;
        List<CaptureBatch> out = new ArrayList<>();
        CaptureBatch.Builder current = null;
        long size = 0;
        for (Frame f : batch.getFramesList()) {
            long cost = CodedOutputStream.computeMessageSize(CaptureBatch.FRAMES_FIELD_NUMBER, f);
            if (current != null && base + size + cost > maxEnvelopeBytes) {
                out.add(current.build());
                current = null;
            }
            if (current == null) {
                current = empty.toBuilder();
                if (!out.isEmpty()) {
                    current.clearCounters();
                }
                size = 0;
            }
            current.addFrames(f);
            size += cost;
        }
        if (current != null) {
            out.add(current.build());
        }
        return out;
    }
}
