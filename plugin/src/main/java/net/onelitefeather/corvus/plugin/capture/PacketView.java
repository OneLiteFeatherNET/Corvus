package net.onelitefeather.corvus.plugin.capture;

import com.github.retrooper.packetevents.protocol.packettype.PacketTypeCommon;
import net.onelitefeather.corvus.proto.capture.v1.ConnectionState;
import net.onelitefeather.corvus.proto.capture.v1.Direction;
import net.onelitefeather.corvus.proto.capture.v1.Frame;

import java.util.UUID;

/**
 * Read-only port over one packet event. The PacketEvents adapter implements it; tests use fakes.
 * Implementations must never modify, cancel or re-encode the underlying packet.
 */
public interface PacketView {

    Direction direction();

    PacketTypeCommon packetType();

    int wirePacketId();

    ConnectionState state();

    /** The player this packet belongs to, or null if not (yet) known (login phase). */
    UUID playerId();

    /** Identity of the underlying channel, used to detect whether more than one channel feeds a session. */
    Object channelKey();

    /** The single byte copy per frame: the payload after the packet id, at most {@code maxBytes} long. */
    byte[] copyPayload(int maxBytes);

    /** Fills the decoded event of the frame, if this packet type has one. May throw; the engine tolerates that. */
    void decodeEvent(Frame.Builder target);

    /** Called once per session. */
    SessionInfo sessionInfo();

    record SessionInfo(int clientProtocolVersion, int observedProtocolVersion, boolean viaBeforePacketEvents,
                       String diagnostics) {
    }
}
