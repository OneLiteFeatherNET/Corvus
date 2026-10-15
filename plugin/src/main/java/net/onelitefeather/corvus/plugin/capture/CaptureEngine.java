package net.onelitefeather.corvus.plugin.capture;

import com.google.protobuf.ByteString;
import com.google.protobuf.UnsafeByteOperations;
import net.onelitefeather.corvus.plugin.privacy.PlayerFilter;
import net.onelitefeather.corvus.plugin.privacy.PrivacyFilter;
import net.onelitefeather.corvus.plugin.privacy.Pseudonymizer;
import net.onelitefeather.corvus.plugin.time.NanoClock;
import net.onelitefeather.corvus.plugin.vocab.CorvusPacket;
import net.onelitefeather.corvus.proto.capture.v1.ConnectionState;
import net.onelitefeather.corvus.proto.capture.v1.Envelope;
import net.onelitefeather.corvus.proto.capture.v1.Frame;
import net.onelitefeather.corvus.proto.capture.v1.SegmentStart;
import net.onelitefeather.corvus.proto.capture.v1.SessionStart;

import java.time.InstantSource;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Consumer;
import java.util.function.Supplier;

/**
 * Core capture logic, independent of PacketEvents' runtime. Everything in {@link #onPacket} runs on a Netty
 * event loop: it does at most one payload copy and non-blocking queue offers, and it never throws.
 */
public final class CaptureEngine {

    public record Settings(boolean rawPayloads, int maxPayloadBytes, String proxyId, String pluginVersion) {
    }

    private final Settings settings;
    private final Pseudonymizer pseudonymizer; // null: fail closed, nothing is captured
    private final PlayerFilter playerFilter;
    private final GameContextMapper contexts;
    private final CaptureSink sink;
    private final NanoClock nanoClock;
    private final InstantSource wallClock;
    private final Supplier<byte[]> sessionIds;
    private final Metrics metrics;
    private final Consumer<String> info;
    private final Map<UUID, SessionState> sessions = new ConcurrentHashMap<>();
    private volatile boolean shutdown;

    public CaptureEngine(Settings settings, Pseudonymizer pseudonymizer, PlayerFilter playerFilter,
                         GameContextMapper contexts, CaptureSink sink, NanoClock nanoClock, InstantSource wallClock,
                         Supplier<byte[]> sessionIds, Metrics metrics, Consumer<String> info) {
        this.settings = settings;
        this.pseudonymizer = pseudonymizer;
        this.playerFilter = playerFilter;
        this.contexts = contexts;
        this.sink = sink;
        this.nanoClock = nanoClock;
        this.wallClock = wallClock;
        this.sessionIds = sessionIds;
        this.metrics = metrics;
        this.info = info;
    }

    public boolean isCapturing() {
        return pseudonymizer != null && !shutdown;
    }

    public int activeSessions() {
        return sessions.size();
    }

    /** Never throws. */
    public void onPacket(PacketView view) {
        try {
            handle(view);
        } catch (Throwable t) {
            UUID player = null;
            try {
                player = view.playerId();
            } catch (Throwable ignored) {
                // the failure may be the view itself
            }
            recordListenerError(player);
        }
    }

    /** Counts a listener failure, attributed to the player's session if known. Never throws. */
    public void recordListenerError(UUID player) {
        metrics.listenerErrors.increment();
        if (player != null) {
            SessionState s = sessions.get(player);
            if (s != null) {
                s.addListenerError();
            }
        }
    }

    private void handle(PacketView view) {
        if (!isCapturing()) {
            return;
        }
        UUID player = view.playerId();
        if (player == null) {
            metrics.ignoredNoPlayer.increment();
            return;
        }
        if (!playerFilter.allows(player)) {
            return;
        }
        SessionState session = sessions.computeIfAbsent(player, p -> openSession(p, view));
        Object channel = view.channelKey();
        if (session.channelKey() != channel && session.markMultiChannelLogged()) {
            metrics.sessionsWithMultipleChannels.increment();
            info.accept("session " + session.idHex() + " is fed by more than one channel: PacketEvents "
                    + "also sees another connection of this player (backend side?)");
        }

        ConnectionState state = view.state();
        if (PrivacyFilter.isPrivate(view.packetType(), state)) {
            session.addDroppedPrivacy();
            metrics.droppedPrivacy.increment();
            return;
        }

        Frame.Builder frame = Frame.newBuilder()
                .setSeq(session.nextSeq())
                .setOffsetNanos(Math.max(0L, nanoClock.nanoTime() - session.startNanos()))
                .setDirection(view.direction())
                .setState(state)
                .setWirePacketId(view.wirePacketId())
                .setPacketType(CorvusPacket.idOf(view.packetType()));
        if (settings.rawPayloads()) {
            frame.setPayload(UnsafeByteOperations.unsafeWrap(view.copyPayload(settings.maxPayloadBytes())));
            frame.setPayloadOriginalSize(view.payloadSize());
        }
        try {
            view.decodeEvent(frame);
        } catch (Throwable t) {
            // keep the frame (raw bytes are still valuable), but make the failure visible
            recordListenerError(player);
            frame.clearEvent();
        }

        WriteItem.FrameItem item = new WriteItem.FrameItem(session, session.segment(), frame.build());
        if (sink.offer(item)) {
            metrics.framesCaptured.increment();
            metrics.bytesCaptured.add(item.sizeBytes());
        } else {
            session.addDroppedQueueFull();
            metrics.droppedQueueFull.increment();
        }
    }

    private SessionState openSession(UUID player, PacketView view) {
        PacketView.SessionInfo sessionInfo = view.sessionInfo();
        SessionState session = new SessionState(sessionIds.get(), nanoClock.nanoTime(),
                wallClock.millis(), view.channelKey());
        SessionStart start = SessionStart.newBuilder()
                .setSessionId(session.idBytes())
                .setPlayerPseudonym(ByteString.copyFrom(pseudonymizer.pseudonym(player)))
                .setPseudonymKeyId(pseudonymizer.keyId())
                .setClientProtocolVersion(sessionInfo.clientProtocolVersion())
                .setObservedProtocolVersion(sessionInfo.observedProtocolVersion())
                .setProxyId(settings.proxyId())
                .setPluginVersion(settings.pluginVersion())
                .setStartedAtUnixMillis(session.startedAtMillis())
                .build();
        sink.offerControl(new WriteItem.Start(session, Envelope.newBuilder().setSessionStart(start).build()));
        metrics.sessionsStarted.increment();
        if (sessionInfo.viaBeforePacketEvents()) {
            metrics.sessionsViaBeforePacketEvents.increment();
        }
        info.accept("session " + session.idHex() + " started: client_protocol=" + sessionInfo.clientProtocolVersion()
                + " observed_protocol=" + sessionInfo.observedProtocolVersion()
                + (sessionInfo.clientProtocolVersion() == sessionInfo.observedProtocolVersion()
                ? " (observed == client)" : " (observed != client: frames are NOT in the client's native version)")
                + " via_before_packetevents=" + sessionInfo.viaBeforePacketEvents()
                + " " + sessionInfo.diagnostics());
        return session;
    }

    /** A player connected to (or switched to) a backend server. Never throws. */
    public void onBackendConnected(UUID player, String serverName, String clientBrand) {
        try {
            SessionState session = sessions.get(player);
            if (session == null || !isCapturing()) {
                return;
            }
            SegmentStart segment = SegmentStart.newBuilder()
                    .setSessionId(session.idBytes())
                    .setSegment(session.nextSegment())
                    .setBackendServer(serverName == null ? "" : serverName)
                    .setGameContext(contexts.contextFor(serverName))
                    .setClientBrand(clientBrand == null ? "" : clientBrand)
                    .setStartedAtUnixMillis(wallClock.millis())
                    .build();
            sink.offerControl(new WriteItem.Segment(session, Envelope.newBuilder().setSegmentStart(segment).build()));
        } catch (Throwable t) {
            recordListenerError(player);
        }
    }

    /** The player disconnected: ends the session. Never throws. */
    public void onDisconnect(UUID player) {
        try {
            SessionState session = sessions.remove(player);
            if (session != null) {
                endSession(session);
            }
        } catch (Throwable t) {
            metrics.listenerErrors.increment();
        }
    }

    /** Stops capturing and ends all sessions. */
    public void shutdown() {
        shutdown = true;
        for (UUID player : sessions.keySet()) {
            onDisconnect(player);
        }
    }

    private void endSession(SessionState session) {
        session.close();
        sink.offerControl(new WriteItem.End(session, wallClock.millis()));
    }
}
