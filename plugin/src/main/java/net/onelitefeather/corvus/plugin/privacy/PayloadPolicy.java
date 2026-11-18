package net.onelitefeather.corvus.plugin.privacy;

import com.github.retrooper.packetevents.protocol.packettype.PacketType;
import com.github.retrooper.packetevents.protocol.packettype.PacketTypeCommon;
import net.onelitefeather.corvus.plugin.vocab.CorvusPacket;
import net.onelitefeather.corvus.proto.capture.v1.ConnectionState;
import net.onelitefeather.corvus.proto.capture.v1.Direction;

import java.util.Set;
import java.util.stream.Stream;

/**
 * Pure decision: may the raw payload of a packet be written to the capture? The decision depends on the configured
 * {@link PayloadMode} and on {@link PrivacyOptions}. The default options are the capture defaults (complete raw
 * stream, names kept). The one-argument constructor and {@link #levelOf(PacketTypeCommon, Direction, ConnectionState)}
 * use {@link PrivacyOptions#ANALYSIS}, the policy of the committed vocabulary export.
 */
public final class PayloadPolicy {

    /** Per-type payload level; {@link #json()} is the value written to {@code vocabulary.json}. */
    public enum Level {
        /** The whole frame is dropped (chat, commands, player lists under the drop setting, login...). */
        FRAME_DROPPED("frame_dropped"),
        /** The frame is recorded but its payload is always stripped. */
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

    /** Credential material: stripped in every setting. */
    private static final Set<PacketTypeCommon> ALWAYS_STRIPPED_SERVERBOUND = Set.of(
            // cookies carry tokens and identities defined by the server (credential material, not names)
            PacketType.Play.Client.COOKIE_RESPONSE);

    /**
     * Serverbound play types that carry free text, item data with custom names, or other identifiers. Their payload
     * is stripped under {@link PrivacyOptions#ANALYSIS} and recorded when {@code redact_names = false}.
     */
    private static final Set<PacketTypeCommon> REDACTABLE_SERVERBOUND = Set.of(
            PacketType.Play.Client.UPDATE_SIGN,
            PacketType.Play.Client.EDIT_BOOK,
            PacketType.Play.Client.NAME_ITEM,
            PacketType.Play.Client.PLUGIN_MESSAGE,
            PacketType.Play.Client.UPDATE_COMMAND_BLOCK,
            PacketType.Play.Client.UPDATE_COMMAND_BLOCK_MINECART,
            PacketType.Play.Client.UPDATE_STRUCTURE_BLOCK,
            PacketType.Play.Client.UPDATE_JIGSAW_BLOCK,
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

    /** Every serverbound play type whose payload the analysis policy strips. */
    private static final Set<PacketTypeCommon> STRIPPED_SERVERBOUND = Set.copyOf(Stream.concat(
            ALWAYS_STRIPPED_SERVERBOUND.stream(), REDACTABLE_SERVERBOUND.stream()).toList());

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
    private final PrivacyOptions options;

    /** The analysis policy ({@link PrivacyOptions#ANALYSIS}). */
    public PayloadPolicy(PayloadMode mode) {
        this(mode, PrivacyOptions.ANALYSIS);
    }

    public PayloadPolicy(PayloadMode mode, PrivacyOptions options) {
        this.mode = mode;
        this.options = options;
    }

    public static Set<PacketTypeCommon> strippedServerboundTypes() {
        return STRIPPED_SERVERBOUND;
    }

    public static Set<PacketTypeCommon> clientboundAllowlist() {
        return CLIENTBOUND_ALLOWLIST;
    }

    /** The level of a packet type under the analysis policy, independent of the capture settings. */
    public static Level levelOf(PacketTypeCommon type, Direction direction, ConnectionState state) {
        return levelOf(type, direction, state, PrivacyOptions.ANALYSIS);
    }

    /** The level of a packet type under the given privacy options, independent of the configured mode. */
    public static Level levelOf(PacketTypeCommon type, Direction direction, ConnectionState state,
                                PrivacyOptions options) {
        if (PrivacyFilter.isPrivate(type, state, options.textPackets())) {
            return Level.FRAME_DROPPED;
        }
        if (state != ConnectionState.CONNECTION_STATE_PLAY) {
            return Level.ALL_ONLY;
        }
        if (ALWAYS_STRIPPED_SERVERBOUND.contains(type)) {
            return Level.STRIPPED;
        }
        if (REDACTABLE_SERVERBOUND.contains(type) || PrivacyFilter.privateTypes().contains(type)) {
            return options.redactNames() ? Level.STRIPPED : Level.DEFAULT;
        }
        if (direction == Direction.DIRECTION_SERVERBOUND) {
            // unknown (unmapped) serverbound types are not vetted for free text: fail closed
            return CorvusPacket.idOf(type) != 0 ? Level.DEFAULT : Level.ALL_ONLY;
        }
        return CLIENTBOUND_ALLOWLIST.contains(type) ? Level.DEFAULT : Level.ALL_ONLY;
    }

    /** True if the payload of this packet may be recorded under the configured mode and options. */
    public boolean recordsPayload(PacketTypeCommon type, Direction direction, ConnectionState state) {
        return switch (mode) {
            case NONE -> false;
            case SERVERBOUND -> levelOf(type, direction, state, options) == Level.DEFAULT;
            case ALL -> {
                Level level = levelOf(type, direction, state, options);
                yield level == Level.DEFAULT || level == Level.ALL_ONLY;
            }
        };
    }
}
