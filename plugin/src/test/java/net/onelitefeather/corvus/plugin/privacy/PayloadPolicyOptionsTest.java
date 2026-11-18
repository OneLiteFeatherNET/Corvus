package net.onelitefeather.corvus.plugin.privacy;

import com.github.retrooper.packetevents.protocol.packettype.PacketType;
import com.github.retrooper.packetevents.protocol.packettype.PacketTypeCommon;
import net.onelitefeather.corvus.proto.capture.v1.ConnectionState;
import net.onelitefeather.corvus.proto.capture.v1.Direction;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Capture settings: text_packets and redact_names on top of the payload mode. */
class PayloadPolicyOptionsTest {

    private static final Direction SB = Direction.DIRECTION_SERVERBOUND;
    private static final Direction CB = Direction.DIRECTION_CLIENTBOUND;
    private static final ConnectionState PLAY = ConnectionState.CONNECTION_STATE_PLAY;

    private static final PrivacyOptions REDACT = new PrivacyOptions(PrivacyOptions.TextPackets.FULL, true);
    private static final PrivacyOptions DROP = new PrivacyOptions(PrivacyOptions.TextPackets.DROP, false);

    private static boolean records(PayloadMode mode, PacketTypeCommon type, Direction d, PrivacyOptions options) {
        return new PayloadPolicy(mode, options).recordsPayload(type, d, PLAY);
    }

    @Test
    void defaultCaptureRecordsChatPayloadInServerboundMode() {
        assertTrue(records(PayloadMode.SERVERBOUND, PacketType.Play.Client.CHAT_MESSAGE, SB, PrivacyOptions.DEFAULT));
    }

    @Test
    void defaultCaptureRecordsSignAndPlayerListPayloads() {
        assertTrue(records(PayloadMode.SERVERBOUND, PacketType.Play.Client.UPDATE_SIGN, SB, PrivacyOptions.DEFAULT));
        assertTrue(records(PayloadMode.SERVERBOUND, PacketType.Play.Server.PLAYER_INFO_UPDATE, CB,
                PrivacyOptions.DEFAULT));
    }

    @Test
    void redactNamesStripsSignAndPlayerListPayloads() {
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Play.Client.UPDATE_SIGN, SB, REDACT));
        assertFalse(records(PayloadMode.SERVERBOUND, PacketType.Play.Server.PLAYER_INFO_UPDATE, CB, REDACT));
    }

    @Test
    void redactNamesKeepsChatAsFrameWithoutPayload() {
        assertEquals(PayloadPolicy.Level.STRIPPED,
                PayloadPolicy.levelOf(PacketType.Play.Client.CHAT_MESSAGE, SB, PLAY, REDACT));
    }

    @Test
    void dropRemovesChatAndPlayerListsInEveryMode() {
        assertEquals(PayloadPolicy.Level.FRAME_DROPPED,
                PayloadPolicy.levelOf(PacketType.Play.Client.CHAT_COMMAND, SB, PLAY, DROP));
        assertFalse(records(PayloadMode.ALL, PacketType.Play.Client.CHAT_MESSAGE, SB, DROP));
        assertFalse(records(PayloadMode.ALL, PacketType.Play.Server.PLAYER_INFO_UPDATE, CB, DROP));
    }

    @Test
    void cookieResponseIsStrippedInEverySetting() {
        assertEquals(PayloadPolicy.Level.STRIPPED,
                PayloadPolicy.levelOf(PacketType.Play.Client.COOKIE_RESPONSE, SB, PLAY, PrivacyOptions.DEFAULT));
        assertFalse(records(PayloadMode.ALL, PacketType.Play.Client.COOKIE_RESPONSE, SB, PrivacyOptions.DEFAULT));
    }

    @Test
    void analysisPolicyIsIndependentOfCaptureDefaults() {
        assertEquals(PayloadPolicy.levelOf(PacketType.Play.Client.CHAT_MESSAGE, SB, PLAY, PrivacyOptions.ANALYSIS),
                PayloadPolicy.levelOf(PacketType.Play.Client.CHAT_MESSAGE, SB, PLAY));
        assertEquals(PayloadPolicy.Level.FRAME_DROPPED,
                PayloadPolicy.levelOf(PacketType.Play.Client.CHAT_MESSAGE, SB, PLAY));
    }
}
