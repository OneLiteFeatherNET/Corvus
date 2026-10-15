package net.onelitefeather.corvus.plugin.pe;

import com.github.retrooper.packetevents.PacketEvents;
import com.github.retrooper.packetevents.event.PacketReceiveEvent;
import com.github.retrooper.packetevents.event.ProtocolPacketEvent;
import com.github.retrooper.packetevents.protocol.packettype.PacketTypeCommon;
import com.github.retrooper.packetevents.protocol.player.ClientVersion;
import com.github.retrooper.packetevents.protocol.player.User;
import com.velocitypowered.api.proxy.Player;
import io.netty.buffer.ByteBuf;
import io.netty.channel.Channel;
import net.onelitefeather.corvus.plugin.capture.PacketView;
import net.onelitefeather.corvus.plugin.capture.PayloadCopier;
import net.onelitefeather.corvus.proto.capture.v1.ConnectionState;
import net.onelitefeather.corvus.proto.capture.v1.Direction;
import net.onelitefeather.corvus.proto.capture.v1.Frame;

import java.util.List;
import java.util.Locale;
import java.util.UUID;

/**
 * Adapter over a PacketEvents event. Strictly read-only: it never cancels, never marks for re-encode and never
 * builds a wrapper from the original event. The event's buffer is only read with absolute indices and is not
 * retained beyond the listener callback.
 */
final class PacketEventsView implements PacketView {

    private final ProtocolPacketEvent event;

    PacketEventsView(ProtocolPacketEvent event) {
        this.event = event;
    }

    @Override
    public Direction direction() {
        return event instanceof PacketReceiveEvent ? Direction.DIRECTION_SERVERBOUND : Direction.DIRECTION_CLIENTBOUND;
    }

    @Override
    public PacketTypeCommon packetType() {
        return event.getPacketType();
    }

    @Override
    public int wirePacketId() {
        return event.getPacketId();
    }

    @Override
    public ConnectionState state() {
        return switch (event.getConnectionState()) {
            case HANDSHAKING -> ConnectionState.CONNECTION_STATE_HANDSHAKING;
            case STATUS -> ConnectionState.CONNECTION_STATE_STATUS;
            case LOGIN -> ConnectionState.CONNECTION_STATE_LOGIN;
            case CONFIGURATION -> ConnectionState.CONNECTION_STATE_CONFIGURATION;
            case PLAY -> ConnectionState.CONNECTION_STATE_PLAY;
        };
    }

    @Override
    public UUID playerId() {
        Object player = event.getPlayer();
        if (player instanceof Player p) {
            return p.getUniqueId();
        }
        return event.getUser().getUUID();
    }

    @Override
    public Object channelKey() {
        return event.getChannel();
    }

    @Override
    public byte[] copyPayload(int maxBytes) {
        return PayloadCopier.copyReadable((ByteBuf) event.getByteBuf(), maxBytes);
    }

    @Override
    public void decodeEvent(Frame.Builder target) {
        EventDecoder.decode(event, target);
    }

    @Override
    public SessionInfo sessionInfo() {
        User user = event.getUser();
        Object player = event.getPlayer();
        int observed = protocolOf(event.getServerVersion().toClientVersion());
        int client = player instanceof Player p ? p.getProtocolVersion().getProtocol() : protocolOf(user.getClientVersion());

        boolean viaBeforePe = false;
        String pipelineText = "pipeline=n/a";
        String channelType = String.valueOf(event.getChannel().getClass().getSimpleName());
        if (user.getChannel() instanceof Channel channel) {
            List<String> names = channel.pipeline().names();
            int pe = indexOfPrefix(names, PacketEvents.DECODER_NAME == null ? "pe-decoder" : PacketEvents.DECODER_NAME);
            int via = indexOfContains(names, "via");
            viaBeforePe = pe >= 0 && via >= 0 && via < pe;
            pipelineText = "pipeline=" + names;
        }
        String diagnostics = "user=" + user.getClass().getSimpleName() + " channel=" + channelType
                + " pe_client_version=" + protocolOf(user.getClientVersion())
                + " decoder_state=" + user.getDecoderState() + " encoder_state=" + user.getEncoderState()
                + " first_event=" + (event instanceof PacketReceiveEvent ? "serverbound" : "clientbound")
                + " " + pipelineText;
        return new SessionInfo(client, observed, viaBeforePe, diagnostics);
    }

    private static int protocolOf(ClientVersion version) {
        return version == null ? -1 : version.getProtocolVersion();
    }

    private static int indexOfPrefix(List<String> names, String prefix) {
        for (int i = 0; i < names.size(); i++) {
            if (names.get(i).startsWith(prefix)) {
                return i;
            }
        }
        return -1;
    }

    private static int indexOfContains(List<String> names, String needle) {
        for (int i = 0; i < names.size(); i++) {
            if (names.get(i).toLowerCase(Locale.ROOT).contains(needle)) {
                return i;
            }
        }
        return -1;
    }
}
