package net.onelitefeather.corvus.plugin.capture;

import com.google.protobuf.ByteString;
import net.onelitefeather.corvus.proto.capture.v1.CaptureCounters;

import java.util.HexFormat;
import java.util.concurrent.atomic.AtomicLong;

/** Per-connection capture state. Counters are written by Netty threads and drained by the writer. */
public final class SessionState {

    private final byte[] id;
    private final String idHex;
    private final long startNanos;
    private final long startedAtMillis;
    private final Object channelKey;
    private final AtomicLong seq = new AtomicLong();
    private final AtomicLong droppedQueueFull = new AtomicLong();
    private final AtomicLong droppedPrivacy = new AtomicLong();
    private final AtomicLong listenerErrors = new AtomicLong();
    private volatile int segment;
    private volatile boolean closed;
    private volatile boolean multiChannelLogged;

    SessionState(byte[] id, long startNanos, long startedAtMillis, Object channelKey) {
        this.id = id;
        this.idHex = HexFormat.of().formatHex(id);
        this.startNanos = startNanos;
        this.startedAtMillis = startedAtMillis;
        this.channelKey = channelKey;
    }

    public ByteString idBytes() {
        return ByteString.copyFrom(id);
    }

    public String idHex() {
        return idHex;
    }

    long startNanos() {
        return startNanos;
    }

    public long startedAtMillis() {
        return startedAtMillis;
    }

    Object channelKey() {
        return channelKey;
    }

    long nextSeq() {
        return seq.getAndIncrement();
    }

    int segment() {
        return segment;
    }

    int nextSegment() {
        return ++segment;
    }

    boolean isClosed() {
        return closed;
    }

    void close() {
        closed = true;
    }

    boolean markMultiChannelLogged() {
        if (multiChannelLogged) {
            return false;
        }
        multiChannelLogged = true;
        return true;
    }

    void addDroppedQueueFull() {
        droppedQueueFull.incrementAndGet();
    }

    void addDroppedPrivacy() {
        droppedPrivacy.incrementAndGet();
    }

    void addListenerError() {
        listenerErrors.incrementAndGet();
    }

    /** Returns the counters accumulated since the previous call and resets them. */
    public CaptureCounters takeCounters() {
        return CaptureCounters.newBuilder()
                .setDroppedQueueFull(droppedQueueFull.getAndSet(0))
                .setDroppedPrivacy(droppedPrivacy.getAndSet(0))
                .setListenerErrors(listenerErrors.getAndSet(0))
                .build();
    }
}
