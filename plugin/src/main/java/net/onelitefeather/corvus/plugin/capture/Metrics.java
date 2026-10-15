package net.onelitefeather.corvus.plugin.capture;

import java.util.concurrent.atomic.LongAdder;

/** Process-wide counters for the pilot (`/corvus stats` and the periodic log line). */
public final class Metrics {

    public final LongAdder framesCaptured = new LongAdder();
    public final LongAdder bytesCaptured = new LongAdder();
    public final LongAdder droppedQueueFull = new LongAdder();
    public final LongAdder droppedPrivacy = new LongAdder();
    public final LongAdder listenerErrors = new LongAdder();
    public final LongAdder writerErrors = new LongAdder();
    public final LongAdder ignoredNoPlayer = new LongAdder();
    public final LongAdder sessionsStarted = new LongAdder();
    public final LongAdder sessionsViaBeforePacketEvents = new LongAdder();
    public final LongAdder sessionsWithMultipleChannels = new LongAdder();
}
