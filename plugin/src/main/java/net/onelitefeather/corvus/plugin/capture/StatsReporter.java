package net.onelitefeather.corvus.plugin.capture;

import net.onelitefeather.corvus.plugin.time.NanoClock;

import java.util.Locale;

/** Formats the pilot metrics; rates are measured since the previous call of the same reporter instance. */
public final class StatsReporter {

    private final Metrics metrics;
    private final ByteBoundedQueue queue;
    private final CaptureEngine engine;
    private final NanoClock clock;
    private long lastNanos;
    private long lastFrames;
    private long lastBytes;

    public StatsReporter(Metrics metrics, ByteBoundedQueue queue, CaptureEngine engine, NanoClock clock) {
        this.metrics = metrics;
        this.queue = queue;
        this.engine = engine;
        this.clock = clock;
        this.lastNanos = clock.nanoTime();
    }

    public synchronized String sample() {
        long now = clock.nanoTime();
        long frames = metrics.framesCaptured.sum();
        long bytes = metrics.bytesCaptured.sum();
        double seconds = Math.max(1e-3, (now - lastNanos) / 1e9);
        double fps = (frames - lastFrames) / seconds;
        double bps = (bytes - lastBytes) / seconds;
        lastNanos = now;
        lastFrames = frames;
        lastBytes = bytes;
        double fill = queue.maxBytes() == 0 ? 0 : 100.0 * queue.bytes() / queue.maxBytes();
        return String.format(Locale.ROOT,
                "capturing=%s sessions=%d frames/s=%.1f bytes/s=%.0f queue=%.1f%% (%d/%d B) "
                        + "dropped_queue_full=%d dropped_privacy=%d dropped_transport=%d listener_errors=%d writer_errors=%d "
                        + "sessions_via_before_pe=%d sessions_multi_channel=%d frames_total=%d",
                engine.isCapturing(), engine.activeSessions(), fps, bps, fill, queue.bytes(), queue.maxBytes(),
                metrics.droppedQueueFull.sum(), metrics.droppedPrivacy.sum(), metrics.droppedTransport.sum(),
                metrics.listenerErrors.sum(),
                metrics.writerErrors.sum(), metrics.sessionsViaBeforePacketEvents.sum(),
                metrics.sessionsWithMultipleChannels.sum(), frames);
    }
}
