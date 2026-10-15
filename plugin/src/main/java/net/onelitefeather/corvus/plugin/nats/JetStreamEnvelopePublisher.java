package net.onelitefeather.corvus.plugin.nats;

import io.nats.client.Connection;
import io.nats.client.ConnectionListener;
import io.nats.client.JetStream;
import io.nats.client.Nats;
import io.nats.client.Options;
import io.nats.client.impl.Headers;
import io.nats.client.impl.NatsMessage;
import net.onelitefeather.corvus.plugin.capture.EnvelopePublisher;
import net.onelitefeather.corvus.plugin.config.CorvusConfig;

import java.time.Duration;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

/**
 * {@link EnvelopePublisher} on the official jnats client. The connection is built on a background thread and
 * jnats keeps reconnecting forever with a bounded reconnect buffer, so an outage never delays the proxy and a
 * publish only ever completes exceptionally, never blocks. Until the first connection exists, isReady() is false
 * and the transport keeps lifecycle envelopes back. The stream is not created here.
 */
public final class JetStreamEnvelopePublisher implements EnvelopePublisher {

    private final long publishTimeoutMillis;
    private final Consumer<String> info;
    private final Consumer<String> warn;
    private volatile JetStream jetStream;
    private volatile Connection connection;
    private volatile boolean closed;

    public JetStreamEnvelopePublisher(CorvusConfig.Nats config, Consumer<String> info, Consumer<String> warn) {
        this.publishTimeoutMillis = config.publishTimeoutMillis();
        this.info = info;
        this.warn = warn;
        Thread t = new Thread(() -> connect(config), "corvus-nats-connect");
        t.setDaemon(true);
        t.start();
    }

    private void connect(CorvusConfig.Nats config) {
        try {
            Options.Builder builder = new Options.Builder()
                    .servers(config.urls().toArray(new String[0]))
                    .connectionName("corvus-capture")
                    .maxReconnects(-1)
                    .reconnectBufferSize(config.reconnectBufferBytes())
                    .connectionListener((conn, type) -> onConnectionEvent(type))
                    .errorListener(new io.nats.client.ErrorListener() {
                        @Override
                        public void errorOccurred(Connection conn, String error) {
                            warn.accept("NATS error: " + error);
                        }

                        @Override
                        public void exceptionOccurred(Connection conn, Exception exp) {
                            warn.accept("NATS exception: " + exp);
                        }
                    });
            if (!config.credsFile().isEmpty()) {
                builder.credentialPath(config.credsFile());
            }
            Connection conn = Nats.connectReconnectOnConnect(builder.build());
            if (closed) {
                conn.close();
                return;
            }
            connection = conn;
            jetStream = conn.jetStream();
        } catch (Throwable t) {
            warn.accept("NATS sink could not be set up, capture batches are dropped: " + t);
        }
    }

    private void onConnectionEvent(ConnectionListener.Events type) {
        switch (type) {
            case CONNECTED -> info.accept("NATS connected");
            case RECONNECTED -> info.accept("NATS reconnected");
            case DISCONNECTED -> warn.accept("NATS disconnected, buffering while reconnecting");
            case CLOSED -> info.accept("NATS connection closed");
            default -> {
                // other events are not interesting
            }
        }
    }

    @Override
    public CompletionStage<Void> publish(String subject, Map<String, String> headers, byte[] body) {
        JetStream js = jetStream;
        if (js == null) {
            return CompletableFuture.failedFuture(new IllegalStateException("NATS connection is not set up yet"));
        }
        try {
            Headers h = new Headers();
            headers.forEach(h::put);
            NatsMessage message = NatsMessage.builder().subject(subject).headers(h).data(body).build();
            return js.publishAsync(message)
                    .orTimeout(publishTimeoutMillis, TimeUnit.MILLISECONDS)
                    .thenApply(ack -> null);
        } catch (Throwable t) {
            return CompletableFuture.failedFuture(t);
        }
    }

    @Override
    public boolean isReady() {
        Connection conn = connection;
        return jetStream != null && conn != null && conn.getStatus() == Connection.Status.CONNECTED;
    }

    @Override
    public void close(Duration timeout) {
        closed = true;
        Connection conn = connection;
        if (conn == null) {
            return;
        }
        try {
            conn.flush(timeout);
        } catch (Exception ignored) {
            // bounded: whatever is still pending is lost, the shutdown must not hang
        }
        try {
            conn.close();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (Exception ignored) {
            // nothing sensible left to do
        }
    }
}
