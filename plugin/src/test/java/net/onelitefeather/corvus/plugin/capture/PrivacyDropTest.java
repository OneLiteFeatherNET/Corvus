package net.onelitefeather.corvus.plugin.capture;

import com.github.retrooper.packetevents.protocol.packettype.PacketType;
import com.github.retrooper.packetevents.protocol.packettype.PacketTypeCommon;
import net.onelitefeather.corvus.plugin.support.EngineFixture;
import net.onelitefeather.corvus.plugin.support.FakePacketView;
import net.onelitefeather.corvus.plugin.support.RecordingSink;
import net.onelitefeather.corvus.proto.capture.v1.ConnectionState;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class PrivacyDropTest {

    private void assertDroppedAndCounted(FakePacketView view, String what) {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = EngineFixture.withSink(sink);

        f.engine.onPacket(view);

        assertEquals(0, sink.frames.size(), what + " must not be recorded");
        assertEquals(1, f.metrics.droppedPrivacy.sum(), what + " must be counted as dropped_privacy");
        assertEquals(0, f.metrics.listenerErrors.sum(), what + " must not raise listener errors");
    }

    private FakePacketView serverbound(PacketTypeCommon type) {
        return new FakePacketView().type(type);
    }

    private FakePacketView clientbound(PacketTypeCommon type) {
        return new FakePacketView().type(type).clientbound();
    }

    @Test
    void serverboundChatMessageIsDroppedAndCounted() {
        assertDroppedAndCounted(serverbound(PacketType.Play.Client.CHAT_MESSAGE), "chat message");
    }

    @Test
    void serverboundSignedAndUnsignedCommandsAreDroppedAndCounted() {
        assertDroppedAndCounted(serverbound(PacketType.Play.Client.CHAT_COMMAND), "signed command");
        assertDroppedAndCounted(serverbound(PacketType.Play.Client.CHAT_COMMAND_UNSIGNED), "unsigned command");
    }

    @Test
    void chatSessionUpdateAndMessageAcknowledgementAreDroppedAndCounted() {
        assertDroppedAndCounted(serverbound(PacketType.Play.Client.CHAT_SESSION_UPDATE), "chat session update");
        assertDroppedAndCounted(serverbound(PacketType.Play.Client.CHAT_ACK), "message acknowledgement");
    }

    @Test
    void chatPreviewsAreDroppedAndCounted() {
        assertDroppedAndCounted(serverbound(PacketType.Play.Client.CHAT_PREVIEW), "client chat preview");
        assertDroppedAndCounted(clientbound(PacketType.Play.Server.CHAT_PREVIEW_PACKET), "server chat preview");
        assertDroppedAndCounted(clientbound(PacketType.Play.Server.DISPLAY_CHAT_PREVIEW), "display chat preview");
    }

    @Test
    void systemPlayerAndDisguisedChatAreDroppedAndCounted() {
        assertDroppedAndCounted(clientbound(PacketType.Play.Server.SYSTEM_CHAT_MESSAGE), "system chat");
        assertDroppedAndCounted(clientbound(PacketType.Play.Server.CHAT_MESSAGE), "player chat");
        assertDroppedAndCounted(clientbound(PacketType.Play.Server.DISGUISED_CHAT), "disguised chat");
        assertDroppedAndCounted(clientbound(PacketType.Play.Server.PLAYER_CHAT_HEADER), "player chat header");
        assertDroppedAndCounted(clientbound(PacketType.Play.Server.DELETE_CHAT), "delete chat");
    }

    @Test
    void chatResetInConfigurationStateIsDroppedAndCounted() {
        assertDroppedAndCounted(clientbound(PacketType.Configuration.Server.RESET_CHAT)
                .state(ConnectionState.CONNECTION_STATE_CONFIGURATION), "configuration chat reset");
    }

    @Test
    void tabCompletionAndCommandTreeAreDroppedAndCounted() {
        assertDroppedAndCounted(serverbound(PacketType.Play.Client.TAB_COMPLETE), "tab complete request");
        assertDroppedAndCounted(clientbound(PacketType.Play.Server.TAB_COMPLETE), "tab complete response");
        assertDroppedAndCounted(clientbound(PacketType.Play.Server.DECLARE_COMMANDS), "command tree");
        assertDroppedAndCounted(clientbound(PacketType.Play.Server.CUSTOM_CHAT_COMPLETIONS), "chat completions");
    }

    @Test
    void playerInfoPacketsCarryingNamesAndUuidsAreDroppedAndCounted() {
        assertDroppedAndCounted(clientbound(PacketType.Play.Server.PLAYER_INFO_UPDATE), "player info update");
        assertDroppedAndCounted(clientbound(PacketType.Play.Server.PLAYER_INFO_REMOVE), "player info remove");
    }

    @Test
    void loginAndHandshakeStatePacketsAreDroppedAndCounted() {
        assertDroppedAndCounted(serverbound(PacketType.Login.Client.LOGIN_START)
                .state(ConnectionState.CONNECTION_STATE_LOGIN), "login start");
        assertDroppedAndCounted(serverbound(PacketType.Handshaking.Client.HANDSHAKE)
                .state(ConnectionState.CONNECTION_STATE_HANDSHAKING), "handshake");
    }

    @Test
    void movementPacketIsRecordedAndNotCountedAsPrivacyDrop() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = EngineFixture.withSink(sink);

        f.engine.onPacket(serverbound(PacketType.Play.Client.PLAYER_POSITION));

        assertEquals(1, sink.frames.size(), "movement must be recorded");
        assertEquals(0, f.metrics.droppedPrivacy.sum());
    }
}
