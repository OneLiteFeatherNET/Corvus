package net.onelitefeather.corvus.plugin.capture;

import net.onelitefeather.corvus.proto.capture.v1.Envelope;

import java.io.BufferedOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Writes {@code captures/<yyyy-MM-dd>/<session-id-hex>.cvc} files (length-delimited {@link Envelope}s). */
public final class FileTransport implements EnvelopeTransport {

    private static final DateTimeFormatter DAY = DateTimeFormatter.ofPattern("yyyy-MM-dd").withZone(ZoneOffset.UTC);

    private final Path capturesDir;
    private final Map<SessionState, OutputStream> streams = new HashMap<>();

    public FileTransport(Path capturesDir) {
        this.capturesDir = capturesDir;
    }

    @Override
    public void begin(SessionState session, Envelope sessionStart) throws IOException {
        Path dir = capturesDir.resolve(DAY.format(Instant.ofEpochMilli(session.startedAtMillis())));
        Files.createDirectories(dir);
        OutputStream out = new BufferedOutputStream(
                Files.newOutputStream(dir.resolve(session.idHex() + ".cvc")), 64 * 1024);
        streams.put(session, out);
        send(session, sessionStart, 0);
    }

    @Override
    public void send(SessionState session, Envelope envelope, int frameCount) throws IOException {
        OutputStream out = streams.get(session);
        if (out == null) {
            return;
        }
        envelope.writeDelimitedTo(out);
        out.flush();
    }

    @Override
    public void end(SessionState session) {
        OutputStream out = streams.remove(session);
        if (out != null) {
            try {
                out.close();
            } catch (IOException ignored) {
                // nothing sensible left to do
            }
        }
    }

    @Override
    public void close() {
        for (SessionState s : List.copyOf(streams.keySet())) {
            end(s);
        }
    }
}
