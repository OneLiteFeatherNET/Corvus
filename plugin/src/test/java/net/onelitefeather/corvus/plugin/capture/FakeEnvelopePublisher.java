package net.onelitefeather.corvus.plugin.capture;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;

/** Records publishes; each returns a future the test completes (or fails) by hand, or completes at once. */
final class FakeEnvelopePublisher implements EnvelopePublisher {

    record Published(String subject, Map<String, String> headers, byte[] body, CompletableFuture<Void> ack) {
    }

    final List<Published> published = new ArrayList<>();
    boolean autoAck = true;
    RuntimeException throwOnPublish;
    Duration closedWith;

    @Override
    public CompletionStage<Void> publish(String subject, Map<String, String> headers, byte[] body) {
        if (throwOnPublish != null) {
            throw throwOnPublish;
        }
        CompletableFuture<Void> ack = new CompletableFuture<>();
        published.add(new Published(subject, headers, body, ack));
        if (autoAck) {
            ack.complete(null);
        }
        return ack;
    }

    @Override
    public void close(Duration timeout) {
        closedWith = timeout;
    }
}
