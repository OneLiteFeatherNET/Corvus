package net.onelitefeather.corvus.plugin.vocab;

import com.github.retrooper.packetevents.protocol.packettype.PacketType;
import com.github.retrooper.packetevents.protocol.packettype.PacketTypeCommon;
import net.onelitefeather.corvus.proto.capture.v1.ConnectionState;
import net.onelitefeather.corvus.proto.capture.v1.Direction;

import java.util.HashMap;
import java.util.Map;

/**
 * Corvus' own, version-stable packet vocabulary. Ids are APPEND-ONLY: never renumber, never reuse an id,
 * never delete an entry (see VOCABULARY.md). 0 means "unmapped".
 * Ranges: 1-99 serverbound play, 100-199 clientbound play, 200-299 configuration.
 */
public enum CorvusPacket {
    // serverbound play
    C_TELEPORT_CONFIRM(1, PacketType.Play.Client.TELEPORT_CONFIRM),
    C_CLIENT_STATUS(2, PacketType.Play.Client.CLIENT_STATUS),
    C_CLIENT_SETTINGS(3, PacketType.Play.Client.CLIENT_SETTINGS),
    C_WINDOW_CONFIRMATION(4, PacketType.Play.Client.WINDOW_CONFIRMATION),
    C_CLICK_WINDOW_BUTTON(5, PacketType.Play.Client.CLICK_WINDOW_BUTTON),
    C_CLICK_WINDOW(6, PacketType.Play.Client.CLICK_WINDOW),
    C_CLOSE_WINDOW(7, PacketType.Play.Client.CLOSE_WINDOW),
    C_PLUGIN_MESSAGE(8, PacketType.Play.Client.PLUGIN_MESSAGE),
    C_INTERACT_ENTITY(9, PacketType.Play.Client.INTERACT_ENTITY),
    C_KEEP_ALIVE(10, PacketType.Play.Client.KEEP_ALIVE),
    C_PLAYER_POSITION(11, PacketType.Play.Client.PLAYER_POSITION),
    C_PLAYER_POSITION_AND_ROTATION(12, PacketType.Play.Client.PLAYER_POSITION_AND_ROTATION),
    C_PLAYER_ROTATION(13, PacketType.Play.Client.PLAYER_ROTATION),
    C_PLAYER_FLYING(14, PacketType.Play.Client.PLAYER_FLYING),
    C_VEHICLE_MOVE(15, PacketType.Play.Client.VEHICLE_MOVE),
    C_STEER_BOAT(16, PacketType.Play.Client.STEER_BOAT),
    C_PLAYER_ABILITIES(17, PacketType.Play.Client.PLAYER_ABILITIES),
    C_PLAYER_DIGGING(18, PacketType.Play.Client.PLAYER_DIGGING),
    C_ENTITY_ACTION(19, PacketType.Play.Client.ENTITY_ACTION),
    C_STEER_VEHICLE(20, PacketType.Play.Client.STEER_VEHICLE),
    C_PONG(21, PacketType.Play.Client.PONG),
    C_HELD_ITEM_CHANGE(22, PacketType.Play.Client.HELD_ITEM_CHANGE),
    C_CREATIVE_INVENTORY_ACTION(23, PacketType.Play.Client.CREATIVE_INVENTORY_ACTION),
    C_ANIMATION(24, PacketType.Play.Client.ANIMATION),
    C_PLAYER_BLOCK_PLACEMENT(25, PacketType.Play.Client.PLAYER_BLOCK_PLACEMENT),
    C_USE_ITEM(26, PacketType.Play.Client.USE_ITEM),
    C_CLIENT_TICK_END(27, PacketType.Play.Client.CLIENT_TICK_END),
    C_PLAYER_INPUT(28, PacketType.Play.Client.PLAYER_INPUT),
    C_ATTACK(29, PacketType.Play.Client.ATTACK),
    C_PUNCH(30, PacketType.Play.Client.PUNCH),
    C_SPECTATE(31, PacketType.Play.Client.SPECTATE),
    C_PICK_ITEM(32, PacketType.Play.Client.PICK_ITEM),
    C_CONFIGURATION_ACK(33, PacketType.Play.Client.CONFIGURATION_ACK),
    C_CHUNK_BATCH_ACK(34, PacketType.Play.Client.CHUNK_BATCH_ACK),
    C_RESOURCE_PACK_STATUS(35, PacketType.Play.Client.RESOURCE_PACK_STATUS),
    C_PLAYER_LOADED(36, PacketType.Play.Client.PLAYER_LOADED),
    // text-bearing types: recorded without payload (see PayloadPolicy)
    C_EDIT_BOOK(37, PacketType.Play.Client.EDIT_BOOK),
    C_NAME_ITEM(38, PacketType.Play.Client.NAME_ITEM),
    C_UPDATE_COMMAND_BLOCK(39, PacketType.Play.Client.UPDATE_COMMAND_BLOCK),
    C_UPDATE_COMMAND_BLOCK_MINECART(40, PacketType.Play.Client.UPDATE_COMMAND_BLOCK_MINECART),
    C_UPDATE_STRUCTURE_BLOCK(41, PacketType.Play.Client.UPDATE_STRUCTURE_BLOCK),
    C_UPDATE_JIGSAW_BLOCK(42, PacketType.Play.Client.UPDATE_JIGSAW_BLOCK),
    C_UPDATE_SIGN(43, PacketType.Play.Client.UPDATE_SIGN),
    C_COOKIE_RESPONSE(44, PacketType.Play.Client.COOKIE_RESPONSE),
    C_SET_TEST_BLOCK(45, PacketType.Play.Client.SET_TEST_BLOCK),
    C_TEST_INSTANCE_BLOCK_ACTION(46, PacketType.Play.Client.TEST_INSTANCE_BLOCK_ACTION),
    C_CUSTOM_CLICK_ACTION(47, PacketType.Play.Client.CUSTOM_CLICK_ACTION),
    C_SET_GAME_RULE(48, PacketType.Play.Client.SET_GAME_RULE),
    // privacy-dropped types (never recorded; ids exist so the analyzer can deny them)
    C_CHAT_PREVIEW(49, PacketType.Play.Client.CHAT_PREVIEW),
    C_CHAT_MESSAGE(50, PacketType.Play.Client.CHAT_MESSAGE),
    C_TAB_COMPLETE(51, PacketType.Play.Client.TAB_COMPLETE),
    C_CHAT_COMMAND(52, PacketType.Play.Client.CHAT_COMMAND),
    C_CHAT_COMMAND_UNSIGNED(53, PacketType.Play.Client.CHAT_COMMAND_UNSIGNED),
    C_CHAT_ACK(54, PacketType.Play.Client.CHAT_ACK),
    C_CHAT_SESSION_UPDATE(55, PacketType.Play.Client.CHAT_SESSION_UPDATE),

    // clientbound play
    S_ENTITY_RELATIVE_MOVE(100, PacketType.Play.Server.ENTITY_RELATIVE_MOVE),
    S_ENTITY_RELATIVE_MOVE_AND_ROTATION(101, PacketType.Play.Server.ENTITY_RELATIVE_MOVE_AND_ROTATION),
    S_ENTITY_ROTATION(102, PacketType.Play.Server.ENTITY_ROTATION),
    S_ENTITY_HEAD_LOOK(103, PacketType.Play.Server.ENTITY_HEAD_LOOK),
    S_ENTITY_TELEPORT(104, PacketType.Play.Server.ENTITY_TELEPORT),
    S_ENTITY_VELOCITY(105, PacketType.Play.Server.ENTITY_VELOCITY),
    S_ENTITY_POSITION_SYNC(106, PacketType.Play.Server.ENTITY_POSITION_SYNC),
    S_PLAYER_POSITION_AND_LOOK(107, PacketType.Play.Server.PLAYER_POSITION_AND_LOOK),
    S_KEEP_ALIVE(108, PacketType.Play.Server.KEEP_ALIVE),
    S_PING(109, PacketType.Play.Server.PING),
    S_WINDOW_CONFIRMATION(110, PacketType.Play.Server.WINDOW_CONFIRMATION),
    S_DAMAGE_EVENT(111, PacketType.Play.Server.DAMAGE_EVENT),
    S_HURT_ANIMATION(112, PacketType.Play.Server.HURT_ANIMATION),
    S_ENTITY_ANIMATION(113, PacketType.Play.Server.ENTITY_ANIMATION),
    S_ENTITY_STATUS(114, PacketType.Play.Server.ENTITY_STATUS),
    S_EXPLOSION(115, PacketType.Play.Server.EXPLOSION),
    S_UPDATE_HEALTH(116, PacketType.Play.Server.UPDATE_HEALTH),
    S_RESPAWN(117, PacketType.Play.Server.RESPAWN),
    S_JOIN_GAME(118, PacketType.Play.Server.JOIN_GAME),
    S_DESTROY_ENTITIES(119, PacketType.Play.Server.DESTROY_ENTITIES),
    S_SPAWN_ENTITY(120, PacketType.Play.Server.SPAWN_ENTITY),
    S_ENTITY_EFFECT(121, PacketType.Play.Server.ENTITY_EFFECT),
    S_REMOVE_ENTITY_EFFECT(122, PacketType.Play.Server.REMOVE_ENTITY_EFFECT),
    S_ENTITY_EQUIPMENT(123, PacketType.Play.Server.ENTITY_EQUIPMENT),
    S_ENTITY_METADATA(124, PacketType.Play.Server.ENTITY_METADATA),
    S_BLOCK_CHANGE(125, PacketType.Play.Server.BLOCK_CHANGE),
    S_ACKNOWLEDGE_PLAYER_DIGGING(126, PacketType.Play.Server.ACKNOWLEDGE_PLAYER_DIGGING),
    S_ACKNOWLEDGE_BLOCK_CHANGES(127, PacketType.Play.Server.ACKNOWLEDGE_BLOCK_CHANGES),
    S_CHANGE_GAME_STATE(128, PacketType.Play.Server.CHANGE_GAME_STATE),
    S_PLAYER_ABILITIES(129, PacketType.Play.Server.PLAYER_ABILITIES),
    S_SET_SLOT(130, PacketType.Play.Server.SET_SLOT),
    S_WINDOW_ITEMS(131, PacketType.Play.Server.WINDOW_ITEMS),
    S_CLOSE_WINDOW(132, PacketType.Play.Server.CLOSE_WINDOW),
    S_OPEN_WINDOW(133, PacketType.Play.Server.OPEN_WINDOW),
    S_UPDATE_ATTRIBUTES(134, PacketType.Play.Server.UPDATE_ATTRIBUTES),
    S_PLUGIN_MESSAGE(135, PacketType.Play.Server.PLUGIN_MESSAGE),
    S_DISCONNECT(136, PacketType.Play.Server.DISCONNECT),
    S_SET_PASSENGERS(137, PacketType.Play.Server.SET_PASSENGERS),
    S_ATTACH_ENTITY(138, PacketType.Play.Server.ATTACH_ENTITY),
    S_COLLECT_ITEM(139, PacketType.Play.Server.COLLECT_ITEM),
    S_ENTITY_MOVEMENT(140, PacketType.Play.Server.ENTITY_MOVEMENT),
    S_PLAYER_ROTATION(141, PacketType.Play.Server.PLAYER_ROTATION),
    S_MOVE_MINECART(142, PacketType.Play.Server.MOVE_MINECART),
    S_BUNDLE(143, PacketType.Play.Server.BUNDLE),
    S_TIME_UPDATE(144, PacketType.Play.Server.TIME_UPDATE),
    S_SET_COOLDOWN(145, PacketType.Play.Server.SET_COOLDOWN),
    // privacy-dropped types (never recorded; ids exist so the analyzer can deny them)
    S_CHAT_PREVIEW_PACKET(146, PacketType.Play.Server.CHAT_PREVIEW_PACKET),
    S_PLAYER_CHAT_HEADER(147, PacketType.Play.Server.PLAYER_CHAT_HEADER),
    S_TAB_COMPLETE(148, PacketType.Play.Server.TAB_COMPLETE),
    S_DECLARE_COMMANDS(149, PacketType.Play.Server.DECLARE_COMMANDS),
    S_CHAT_MESSAGE(150, PacketType.Play.Server.CHAT_MESSAGE),
    S_SYSTEM_CHAT_MESSAGE(151, PacketType.Play.Server.SYSTEM_CHAT_MESSAGE),
    S_DELETE_CHAT(152, PacketType.Play.Server.DELETE_CHAT),
    S_DISGUISED_CHAT(153, PacketType.Play.Server.DISGUISED_CHAT),
    S_DISPLAY_CHAT_PREVIEW(154, PacketType.Play.Server.DISPLAY_CHAT_PREVIEW),
    S_CUSTOM_CHAT_COMPLETIONS(155, PacketType.Play.Server.CUSTOM_CHAT_COMPLETIONS),
    S_SERVER_DATA(156, PacketType.Play.Server.SERVER_DATA),
    S_PLAYER_INFO(157, PacketType.Play.Server.PLAYER_INFO),
    S_PLAYER_INFO_UPDATE(158, PacketType.Play.Server.PLAYER_INFO_UPDATE),
    S_PLAYER_INFO_REMOVE(159, PacketType.Play.Server.PLAYER_INFO_REMOVE),
    // spawn of players (legacy protocol versions only; decoded for the position, payload never recorded)
    S_SPAWN_PLAYER(160, PacketType.Play.Server.SPAWN_PLAYER),

    // configuration
    CFG_C_CLIENT_SETTINGS(200, PacketType.Configuration.Client.CLIENT_SETTINGS),
    CFG_C_PLUGIN_MESSAGE(201, PacketType.Configuration.Client.PLUGIN_MESSAGE),
    CFG_C_CONFIGURATION_END_ACK(202, PacketType.Configuration.Client.CONFIGURATION_END_ACK),
    CFG_C_KEEP_ALIVE(203, PacketType.Configuration.Client.KEEP_ALIVE),
    CFG_C_PONG(204, PacketType.Configuration.Client.PONG),
    CFG_C_RESOURCE_PACK_STATUS(205, PacketType.Configuration.Client.RESOURCE_PACK_STATUS),
    CFG_C_SELECT_KNOWN_PACKS(206, PacketType.Configuration.Client.SELECT_KNOWN_PACKS),
    CFG_S_PLUGIN_MESSAGE(220, PacketType.Configuration.Server.PLUGIN_MESSAGE),
    CFG_S_DISCONNECT(221, PacketType.Configuration.Server.DISCONNECT),
    CFG_S_CONFIGURATION_END(222, PacketType.Configuration.Server.CONFIGURATION_END),
    CFG_S_KEEP_ALIVE(223, PacketType.Configuration.Server.KEEP_ALIVE),
    CFG_S_PING(224, PacketType.Configuration.Server.PING),
    CFG_S_REGISTRY_DATA(225, PacketType.Configuration.Server.REGISTRY_DATA),
    CFG_S_UPDATE_TAGS(226, PacketType.Configuration.Server.UPDATE_TAGS),
    CFG_S_SELECT_KNOWN_PACKS(227, PacketType.Configuration.Server.SELECT_KNOWN_PACKS),
    CFG_S_RESET_CHAT(228, PacketType.Configuration.Server.RESET_CHAT);

    private static final Map<PacketTypeCommon, CorvusPacket> BY_TYPE = new HashMap<>();

    static {
        for (CorvusPacket p : values()) {
            BY_TYPE.put(p.type, p);
        }
    }

    private final int id;
    private final PacketTypeCommon type;

    CorvusPacket(int id, PacketTypeCommon type) {
        this.id = id;
        this.type = type;
    }

    public int id() {
        return id;
    }

    public PacketTypeCommon packetEventsType() {
        return type;
    }

    /** Direction of the packet as seen from the proxy: serverbound = sent by the client. */
    public Direction direction() {
        return type instanceof PacketType.Play.Client || type instanceof PacketType.Configuration.Client
                ? Direction.DIRECTION_SERVERBOUND : Direction.DIRECTION_CLIENTBOUND;
    }

    /** Connection state of the packet (play or configuration). */
    public ConnectionState state() {
        return type instanceof PacketType.Configuration.Client || type instanceof PacketType.Configuration.Server
                ? ConnectionState.CONNECTION_STATE_CONFIGURATION : ConnectionState.CONNECTION_STATE_PLAY;
    }

    /** @return the stable id of the given type, or 0 if unmapped. */
    public static int idOf(PacketTypeCommon type) {
        CorvusPacket p = BY_TYPE.get(type);
        return p == null ? 0 : p.id;
    }
}
