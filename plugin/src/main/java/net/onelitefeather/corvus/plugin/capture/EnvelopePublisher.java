package net.onelitefeather.corvus.plugin.capture;

import java.time.Duration;
import java.util.Map;
import java.util.concurrent.CompletionStage;

/**
 * Port over the single publish call of the transport (JetStream async publish in production, a fake in tests).
 * Must never block: it returns immediately and completes the stage when the broker acknowledged the message, or
 * exceptionally when the publish failed or timed out.
 */
public interface EnvelopePublisher {

    CompletionStage<Void> publish(String subject, Map<String, String> headers, byte[] body);

    /** Flushes what is buffered, waiting at most {@code timeout}, then releases the connection. */
    /** True when a connection exists, so deferred lifecycle envelopes can be published. */
    boolean isReady();

    void close(Duration timeout);
}
