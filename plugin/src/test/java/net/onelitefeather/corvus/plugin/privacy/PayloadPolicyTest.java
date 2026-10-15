package net.onelitefeather.corvus.plugin.privacy;

import com.github.retrooper.packetevents.protocol.packettype.PacketType;
import com.github.retrooper.packetevents.protocol.packettype.PacketTypeCommon;
import net.onelitefeather.corvus.proto.capture.v1.ConnectionState;
import net.onelitefeather.corvus.proto.capture.v1.Direction;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class PayloadPolicyTest {

    private static final Direction SB = Direction.DIRECTION_SERVERBOUND;
    private static final Direction CB = Direction.DIRECTION_CLIENTBOUND;
    private static final ConnectionState PLAY = ConnectionState.CONNECTION_STATE_PLAY;
    private static final ConnectionState CONFIG = ConnectionState.CONNECTION_STATE_CONFIGURATION;

    private static boolean records(PayloadMode mode, PacketTypeCommon type, Direction d, ConnectionState s) {
        return new PayloadPolicy(mode).recordsPayload(type, d, s);
    }

    @Test
    void modeNoneRecordsNoPayloadEvenForMovement() {
        assertFalse(records(PayloadMode.NONE, PacketType.Play.Client.PLAYER_POSITION, SB, PLAY));
        assertFalse(records(PayloadMode.NONE, PacketType.Play.Server.ENTITY_VELOCITY, CB, PLAY));
    }

    @Test
    void serverboundModeRecordsServerboundMovement() {
        assertTrue(records(PayloadMode.SERVERBOUND, PacketType.Play.Client.PLAYER_POSITION, SB, PLAY));
    }

    @Test
    void serverboundModeStripsSignText() {
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Play.Client.UPDATE_SIGN, SB, PLAY));
    }

    @Test
    void serverboundModeStripsBookEdits() {
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Play.Client.EDIT_BOOK, SB, PLAY));
    }

    @Test
    void serverboundModeStripsAnvilItemNames() {
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Play.Client.NAME_ITEM, SB, PLAY));
    }

    @Test
    void serverboundModeStripsPluginMessages() {
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Play.Client.PLUGIN_MESSAGE, SB, PLAY));
    }

    @Test
    void serverboundModeStripsCommandBlocks() {
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Play.Client.UPDATE_COMMAND_BLOCK, SB, PLAY));
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Play.Client.UPDATE_COMMAND_BLOCK_MINECART, SB, PLAY));
    }

    @Test
    void serverboundModeStripsStructureAndJigsawBlocks() {
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Play.Client.UPDATE_STRUCTURE_BLOCK, SB, PLAY));
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Play.Client.UPDATE_JIGSAW_BLOCK, SB, PLAY));
    }

    @Test
    void serverboundModeStripsSpectateBecauseItCarriesATargetUuid() {
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Play.Client.SPECTATE, SB, PLAY));
    }

    @Test
    void serverboundModeStripsInventoryClicksBecauseItemsCanCarryNames() {
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Play.Client.CLICK_WINDOW, SB, PLAY));
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Play.Client.CREATIVE_INVENTORY_ACTION, SB, PLAY));
    }

    @Test
    void everyStrippedTypeIsStrippedEvenInModeAll() {
        for (PacketTypeCommon type : PayloadPolicy.strippedServerboundTypes()) {
            assertFalse(records(PayloadMode.ALL, type, SB, PLAY), type.getName() + " must stay stripped in mode all");
            assertFalse(records(PayloadMode.SERVERBOUND, type, SB, PLAY), type.getName() + " must be stripped");
        }
    }

    @Test
    void serverboundModeFailsClosedForUnmappedServerboundTypes() {
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Play.Client.QUERY_BLOCK_NBT, SB, PLAY));
    }

    @Test
    void serverboundModeRecordsAllowlistedClientboundMotion() {
        for (PacketTypeCommon type : PayloadPolicy.clientboundAllowlist()) {
            assertTrue(records(PayloadMode.SERVERBOUND, type, CB, PLAY), type.getName() + " is allowlisted");
        }
    }

    @Test
    void allowlistCoversExactlyTheDocumentedMotionTypes() {
        assertEquals(java.util.Set.of(
                PacketType.Play.Server.ENTITY_RELATIVE_MOVE,
                PacketType.Play.Server.ENTITY_RELATIVE_MOVE_AND_ROTATION,
                PacketType.Play.Server.ENTITY_ROTATION,
                PacketType.Play.Server.ENTITY_HEAD_LOOK,
                PacketType.Play.Server.ENTITY_TELEPORT,
                PacketType.Play.Server.ENTITY_VELOCITY,
                PacketType.Play.Server.ENTITY_POSITION_SYNC,
                PacketType.Play.Server.PLAYER_POSITION_AND_LOOK), PayloadPolicy.clientboundAllowlist());
    }

    @Test
    void serverboundModeStripsClientboundScoreboardAndTeams() {
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Play.Server.TEAMS, CB, PLAY));
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Play.Server.UPDATE_SCORE, CB, PLAY));
    }

    @Test
    void serverboundModeStripsClientboundSpawnAndBossBar() {
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Play.Server.SPAWN_ENTITY, CB, PLAY));
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Play.Server.BOSS_BAR, CB, PLAY));
    }

    @Test
    void serverboundModeStripsConfigurationPackets() {
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Configuration.Client.CLIENT_SETTINGS, SB, CONFIG));
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Configuration.Server.REGISTRY_DATA, CB, CONFIG));
    }

    @Test
    void modeAllRecordsClientbound() {
        assertTrue(records(PayloadMode.ALL, PacketType.Play.Server.SPAWN_ENTITY, CB, PLAY));
        assertTrue(records(PayloadMode.ALL, PacketType.Configuration.Server.REGISTRY_DATA, CB, CONFIG));
    }

    @Test
    void modeAllStillExcludesPrivacyDroppedTypes() {
        assertFalse(records(PayloadMode.ALL, PacketType.Play.Client.CHAT_MESSAGE, SB, PLAY));
        assertFalse(records(PayloadMode.ALL, PacketType.Play.Server.PLAYER_INFO_UPDATE, CB, PLAY));
    }

    @Test
    void levelOfClassifiesPrivateStrippedDefaultAndAllOnly() {
        assertEquals(PayloadPolicy.Level.FRAME_DROPPED,
                PayloadPolicy.levelOf(PacketType.Play.Client.CHAT_COMMAND, SB, PLAY));
        assertEquals(PayloadPolicy.Level.STRIPPED, PayloadPolicy.levelOf(PacketType.Play.Client.UPDATE_SIGN, SB, PLAY));
        assertEquals(PayloadPolicy.Level.DEFAULT,
                PayloadPolicy.levelOf(PacketType.Play.Client.PLAYER_POSITION, SB, PLAY));
        assertEquals(PayloadPolicy.Level.ALL_ONLY,
                PayloadPolicy.levelOf(PacketType.Play.Server.SPAWN_ENTITY, CB, PLAY));
    }
}
