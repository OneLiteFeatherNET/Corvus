package net.onelitefeather.corvus.plugin.nats;

import io.nats.client.Connection;
import io.nats.client.ConnectionListener;
import io.nats.client.Dispatcher;
import io.nats.client.ErrorListener;
import io.nats.client.Nats;
import io.nats.client.Options;
import net.onelitefeather.corvus.plugin.config.CorvusConfig;

import java.time.Duration;
import java.util.function.Consumer;

/**
 * Core NATS subscription to the score subject (default {@code corvus.score.v1.>}). Scores are only interesting
 * while the player is online, so history is not replayed: a plain subscription is enough and needs no consumer
 * state. The connection is built on a background thread and reconnects forever; the handler runs on the jnats
 * dispatcher thread and must not block.
 */
public final class ScoreSubscriber {

    private final Consumer<byte[]> handler;
    private final Consumer<String> info;
    private final Consumer<String> warn;
    private volatile Connection connection;
    private volatile boolean closed;

    public ScoreSubscriber(CorvusConfig.Nats config, String subject, Consumer<byte[]> handler,
                           Consumer<String> info, Consumer<String> warn) {
        this.handler = handler;
        this.info = info;
        this.warn = warn;
        Thread t = new Thread(() -> connect(config, subject), "corvus-nats-score-connect");
        t.setDaemon(true);
        t.start();
    }

    private void connect(CorvusConfig.Nats config, String subject) {
        try {
            Options.Builder builder = new Options.Builder()
                    .servers(config.urls().toArray(new String[0]))
                    .connectionName("corvus-scores")
                    .maxReconnects(-1)
                    .connectionListener((conn, type) -> onConnectionEvent(type))
                    .errorListener(new ErrorListener() {
                        @Override
                        public void errorOccurred(Connection conn, String error) {
                            warn.accept("NATS score subscription error: " + error);
                        }

                        @Override
                        public void exceptionOccurred(Connection conn, Exception exp) {
                            warn.accept("NATS score subscription exception: " + exp);
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
            Dispatcher dispatcher = conn.createDispatcher(message -> {
                try {
                    handler.accept(message.getData());
                } catch (Throwable t) {
                    warn.accept("Corvus score handler failed: " + t);
                }
            });
            dispatcher.subscribe(subject);
            connection = conn;
            info.accept("Corvus score subscription active on " + subject);
        } catch (Throwable t) {
            warn.accept("Corvus score subscription could not be set up, scores are unavailable: " + t);
        }
    }

    private void onConnectionEvent(ConnectionListener.Events type) {
        switch (type) {
            case CONNECTED -> info.accept("NATS (scores) connected");
            case RECONNECTED -> info.accept("NATS (scores) reconnected");
            case DISCONNECTED -> warn.accept("NATS (scores) disconnected, reconnecting");
            default -> {
                // other events are not interesting
            }
        }
    }

    public void close(Duration timeout) {
        closed = true;
        Connection conn = connection;
        if (conn == null) {
            return;
        }
        try {
            conn.close();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (Exception ignored) {
            // shutdown must not hang
        }
    }
}
