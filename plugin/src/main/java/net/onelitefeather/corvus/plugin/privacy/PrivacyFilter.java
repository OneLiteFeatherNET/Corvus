package net.onelitefeather.corvus.plugin.privacy;

import com.github.retrooper.packetevents.protocol.packettype.PacketType;
import com.github.retrooper.packetevents.protocol.packettype.PacketTypeCommon;
import net.onelitefeather.corvus.proto.capture.v1.ConnectionState;

import java.util.Set;

/**
 * Decides which packets must not be recorded. Handshake, status and login frames are never recorded. The text
 * bearing play types (chat, commands, signing, chat sessions, completions, player lists) are dropped only when
 * {@code capture.text_packets = "drop"}; by default they are recorded, see {@link PrivacyOptions}.
 */
public final class PrivacyFilter {

    private static final Set<PacketTypeCommon> PRIVATE_TYPES = Set.of(
            // serverbound: chat, commands, signing, acknowledgements, completions that echo typed text
            PacketType.Play.Client.CHAT_PREVIEW,
            PacketType.Play.Client.CHAT_MESSAGE,
            PacketType.Play.Client.TAB_COMPLETE,
            PacketType.Play.Client.CHAT_COMMAND,
            PacketType.Play.Client.CHAT_COMMAND_UNSIGNED,
            PacketType.Play.Client.CHAT_ACK,
            PacketType.Play.Client.CHAT_SESSION_UPDATE,
            // clientbound: system/player/disguised chat, previews, completions, command tree
            PacketType.Play.Server.CHAT_PREVIEW_PACKET,
            PacketType.Play.Server.PLAYER_CHAT_HEADER,
            PacketType.Play.Server.TAB_COMPLETE,
            PacketType.Play.Server.DECLARE_COMMANDS,
            PacketType.Play.Server.CHAT_MESSAGE,
            PacketType.Play.Server.SYSTEM_CHAT_MESSAGE,
            PacketType.Play.Server.DELETE_CHAT,
            PacketType.Play.Server.DISGUISED_CHAT,
            PacketType.Play.Server.DISPLAY_CHAT_PREVIEW,
            PacketType.Play.Server.CUSTOM_CHAT_COMPLETIONS,
            PacketType.Play.Server.SERVER_DATA,
            // clientbound: player lists carry names, UUIDs and chat session keys
            PacketType.Play.Server.PLAYER_INFO,
            PacketType.Play.Server.PLAYER_INFO_UPDATE,
            PacketType.Play.Server.PLAYER_INFO_REMOVE,
            // configuration state
            PacketType.Configuration.Server.RESET_CHAT);

    private PrivacyFilter() {
    }

    /** The packet types that are private under {@link PrivacyOptions#ANALYSIS} (play and configuration). */
    public static Set<PacketTypeCommon> privateTypes() {
        return PRIVATE_TYPES;
    }

    /**
     * True if a frame of this type/state must not be recorded at all (counted as dropped_privacy). Handshake,
     * status and login frames always are; text bearing types only with {@link PrivacyOptions.TextPackets#DROP}.
     */
    public static boolean isPrivate(PacketTypeCommon type, ConnectionState state,
                                    PrivacyOptions.TextPackets textPackets) {
        if (state != ConnectionState.CONNECTION_STATE_PLAY && state != ConnectionState.CONNECTION_STATE_CONFIGURATION) {
            // handshake (server address), status and login (name, UUID) never reach the data lake
            return true;
        }
        if (type == null) {
            return true;
        }
        return textPackets == PrivacyOptions.TextPackets.DROP && PRIVATE_TYPES.contains(type);
    }
}
