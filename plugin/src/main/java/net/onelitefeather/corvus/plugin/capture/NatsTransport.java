package net.onelitefeather.corvus.plugin.capture;

import net.onelitefeather.corvus.plugin.time.NanoClock;
import net.onelitefeather.corvus.proto.capture.v1.CaptureBatch;
import net.onelitefeather.corvus.proto.capture.v1.CaptureCounters;
import net.onelitefeather.corvus.proto.capture.v1.Envelope;
import net.onelitefeather.corvus.proto.capture.v1.Frame;

import com.google.protobuf.CodedOutputStream;

import java.time.Duration;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
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

    private static final int ENVELOPE_OVERHEAD = 16;
    private static final long WARN_INTERVAL_NANOS = 60_000_000_000L;

    private final EnvelopePublisher publisher;
    private final String subject;
    private final long maxInFlightBytes;
    private final NanoClock clock;
    private final Metrics metrics;
    private final Consumer<String> warn;
    private final AtomicLong inFlightBytes = new AtomicLong();
    private final Map<SessionState, Long> counters = new HashMap<>();
    private boolean warned;
    private long lastWarnNanos;
    private long suppressedWarnings;

    public NatsTransport(EnvelopePublisher publisher, String subjectPrefix, String proxyId, long maxInFlightBytes,
                         NanoClock clock, Metrics metrics, Consumer<String> warn) {
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
        counters.put(session, 0L);
        publish(session, sessionStart, 0, null);
    }

    @Override
    public void send(SessionState session, Envelope envelope, int frameCount) {
        if (!envelope.hasBatch()) {
            publish(session, envelope, 0, null);
            return;
        }
        CaptureBatch batch = envelope.getBatch();
        List<CaptureBatch> parts = split(batch, MAX_ENVELOPE_BYTES);
        for (int i = 0; i < parts.size(); i++) {
            CaptureBatch part = parts.get(i);
            // Only the first part carries the counters so they are not summed twice by ingest.
            CaptureCounters carried = i == 0 ? part.getCounters() : null;
            publish(session, Envelope.newBuilder().setBatch(part).build(), part.getFramesCount(), carried);
        }
    }

    @Override
    public void end(SessionState session) {
        counters.remove(session);
    }

    @Override
    public void close() {
        publisher.close(SHUTDOWN_TIMEOUT);
    }

    private void publish(SessionState session, Envelope envelope, int frames, CaptureCounters carried) {
        // The counter always advances, also for dropped envelopes: a timed-out publish may still have been
        // stored, and reusing its id would make JetStream discard the next real envelope as a duplicate.
        long n = counters.merge(session, 1L, Long::sum) - 1;
        byte[] body = envelope.toByteArray();
        boolean control = frames == 0;
        if (body.length > MAX_ENVELOPE_BYTES || !reserve(body.length, control)) {
            drop(session, frames, carried, body.length > MAX_ENVELOPE_BYTES
                    ? "envelope larger than " + MAX_ENVELOPE_BYTES + " bytes"
                    : "in-flight budget of " + maxInFlightBytes + " bytes exhausted", null);
            return;
        }
        Map<String, String> headers = Map.of(
                MSG_ID_HEADER, session.idHex() + "-" + n,
                SCHEMA_HEADER, SCHEMA_VALUE);
        try {
            publisher.publish(subject, headers, body).whenComplete((v, error) -> {
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

    private boolean reserve(long size, boolean control) {
        // Small lifecycle envelopes may exceed the budget slightly so a busy link does not lose session metadata.
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
