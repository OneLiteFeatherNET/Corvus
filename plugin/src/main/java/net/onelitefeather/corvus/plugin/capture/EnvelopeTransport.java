package net.onelitefeather.corvus.plugin.capture;

import net.onelitefeather.corvus.proto.capture.v1.Envelope;

import java.io.IOException;

/**
 * Where finished envelopes go (a file per session or NATS JetStream). Called only from the single writer
 * thread. The batching of frames stays in {@link CaptureWriter}; implementations only move envelopes.
 */
public interface EnvelopeTransport {

    /** Delivers the first envelope ({@code SessionStart}) of a session. */
    void begin(SessionState session, Envelope sessionStart) throws IOException;

    /**
     * Delivers a later envelope of a session.
     *
     * @param frameCount number of frames inside the envelope (0 unless it is a {@code CaptureBatch}); used to
     *                   account frames that could not be delivered
     */
    void send(SessionState session, Envelope envelope, int frameCount) throws IOException;

    /** Releases the per-session resources; no further envelopes follow for this session. */
    void end(SessionState session);

    /** Releases everything, waiting only a short bounded time for outstanding deliveries. */
    /** Called regularly by the writer thread; lets a transport retry deferred work. Default: nothing. */
    default void poll() {
    }

    void close();
}
