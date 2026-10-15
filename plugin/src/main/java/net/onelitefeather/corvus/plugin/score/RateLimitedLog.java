package net.onelitefeather.corvus.plugin.score;

import net.onelitefeather.corvus.plugin.time.NanoClock;

import java.util.function.Consumer;

/** Forwards a warning at most once per interval and counts the suppressed ones. */
public final class RateLimitedLog {

    public static final long DEFAULT_INTERVAL_NANOS = 60_000_000_000L;

    private final NanoClock clock;
    private final Consumer<String> sink;
    private final long intervalNanos;
    private boolean emitted;
    private long lastNanos;
    private long suppressed;

    public RateLimitedLog(NanoClock clock, Consumer<String> sink, long intervalNanos) {
        this.clock = clock;
        this.sink = sink;
        this.intervalNanos = intervalNanos;
    }

    public synchronized void warn(String message) {
        long now = clock.nanoTime();
        if (emitted && now - lastNanos < intervalNanos) {
            suppressed++;
            return;
        }
        sink.accept(suppressed > 0 ? message + " [" + suppressed + " more since the last warning]" : message);
        emitted = true;
        lastNanos = now;
        suppressed = 0;
    }
}
