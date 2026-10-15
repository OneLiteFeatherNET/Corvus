package net.onelitefeather.corvus.plugin.privacy;

import com.github.retrooper.packetevents.protocol.packettype.PacketType;
import com.github.retrooper.packetevents.protocol.packettype.PacketTypeCommon;
import net.onelitefeather.corvus.plugin.vocab.CorvusPacket;
import net.onelitefeather.corvus.proto.capture.v1.ConnectionState;
import net.onelitefeather.corvus.proto.capture.v1.Direction;

import java.util.Set;

/**
 * Pure decision: may the raw payload of a packet be written to the capture? Payloads of other players' packets
 * (names, UUIDs in scoreboard, teams, titles, spawn, boss bars...) and of text-bearing serverbound packets must
 * never reach the data lake.
 */
public final class PayloadPolicy {

    /** Per-type payload level; {@link #json()} is the value written to {@code vocabulary.json}. */
    public enum Level {
        /** The whole frame is dropped (chat, commands, player lists...), see {@link PrivacyFilter}. */
        FRAME_DROPPED("frame_dropped"),
        /** The frame is recorded but its payload is always stripped, in every mode. */
        STRIPPED("stripped"),
        /** Payload recorded in modes {@code serverbound} and {@code all}. */
        DEFAULT("default"),
        /** Payload recorded only in mode {@code all}. */
        ALL_ONLY("all_only");

        private final String json;

        Level(String json) {
            this.json = json;
        }

        public String json() {
            return json;
        }
    }

    /**
     * Serverbound play types that carry free text, item data with custom names, or other identifiers. Their
     * payload is stripped in every mode.
     */
    private static final Set<PacketTypeCommon> STRIPPED_SERVERBOUND = Set.of(
            PacketType.Play.Client.UPDATE_SIGN,
            PacketType.Play.Client.EDIT_BOOK,
            PacketType.Play.Client.NAME_ITEM,
            PacketType.Play.Client.PLUGIN_MESSAGE,
            PacketType.Play.Client.UPDATE_COMMAND_BLOCK,
            PacketType.Play.Client.UPDATE_COMMAND_BLOCK_MINECART,
            PacketType.Play.Client.UPDATE_STRUCTURE_BLOCK,
            PacketType.Play.Client.UPDATE_JIGSAW_BLOCK,
            // cookies are opaque server-defined data (tokens, identities)
            PacketType.Play.Client.COOKIE_RESPONSE,
            // test blocks and game rules carry free-text names/values
            PacketType.Play.Client.SET_TEST_BLOCK,
            PacketType.Play.Client.TEST_INSTANCE_BLOCK_ACTION,
            PacketType.Play.Client.SET_GAME_RULE,
            // custom click actions carry arbitrary payloads defined by the server
            PacketType.Play.Client.CUSTOM_CLICK_ACTION,
            // spectate carries the UUID of the target entity (a player)
            PacketType.Play.Client.SPECTATE,
            // inventory clicks carry item stacks, which can hold custom names, lore and skull owners
            PacketType.Play.Client.CLICK_WINDOW,
            PacketType.Play.Client.CREATIVE_INVENTORY_ACTION);

    /** Clientbound motion packets: entity ids and numbers only, no names or UUIDs. */
    private static final Set<PacketTypeCommon> CLIENTBOUND_ALLOWLIST = Set.of(
            PacketType.Play.Server.ENTITY_RELATIVE_MOVE,
            PacketType.Play.Server.ENTITY_RELATIVE_MOVE_AND_ROTATION,
            PacketType.Play.Server.ENTITY_ROTATION,
            PacketType.Play.Server.ENTITY_HEAD_LOOK,
            PacketType.Play.Server.ENTITY_TELEPORT,
            PacketType.Play.Server.ENTITY_VELOCITY,
            PacketType.Play.Server.ENTITY_POSITION_SYNC,
            PacketType.Play.Server.PLAYER_POSITION_AND_LOOK);

    private final PayloadMode mode;

    public PayloadPolicy(PayloadMode mode) {
        this.mode = mode;
    }

    public static Set<PacketTypeCommon> strippedServerboundTypes() {
        return STRIPPED_SERVERBOUND;
    }

    public static Set<PacketTypeCommon> clientboundAllowlist() {
        return CLIENTBOUND_ALLOWLIST;
    }

    /** The level of a packet type, independent of the configured mode. */
    public static Level levelOf(PacketTypeCommon type, Direction direction, ConnectionState state) {
        if (PrivacyFilter.isPrivate(type, state)) {
            return Level.FRAME_DROPPED;
        }
        if (state != ConnectionState.CONNECTION_STATE_PLAY) {
            return Level.ALL_ONLY;
        }
        if (direction == Direction.DIRECTION_SERVERBOUND) {
            if (STRIPPED_SERVERBOUND.contains(type)) {
                return Level.STRIPPED;
            }
            // unknown (unmapped) serverbound types are not vetted for free text: fail closed
            return CorvusPacket.idOf(type) != 0 ? Level.DEFAULT : Level.ALL_ONLY;
        }
        return CLIENTBOUND_ALLOWLIST.contains(type) ? Level.DEFAULT : Level.ALL_ONLY;
    }

    /** True if the payload of this packet may be recorded under the configured mode. */
    public boolean recordsPayload(PacketTypeCommon type, Direction direction, ConnectionState state) {
        return switch (mode) {
            case NONE -> false;
            case SERVERBOUND -> levelOf(type, direction, state) == Level.DEFAULT;
            case ALL -> {
                Level level = levelOf(type, direction, state);
                yield level == Level.DEFAULT || level == Level.ALL_ONLY;
            }
        };
    }
}
