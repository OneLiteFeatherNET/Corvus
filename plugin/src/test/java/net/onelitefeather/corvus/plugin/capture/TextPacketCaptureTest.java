package net.onelitefeather.corvus.plugin.capture;

import com.github.retrooper.packetevents.protocol.packettype.PacketType;
import net.onelitefeather.corvus.plugin.config.CorvusConfig;
import net.onelitefeather.corvus.plugin.privacy.PayloadMode;
import net.onelitefeather.corvus.plugin.privacy.PlayerFilter;
import net.onelitefeather.corvus.plugin.privacy.PrivacyOptions;
import net.onelitefeather.corvus.plugin.support.EngineFixture;
import net.onelitefeather.corvus.plugin.support.FakePacketView;
import net.onelitefeather.corvus.plugin.support.RecordingSink;
import net.onelitefeather.corvus.plugin.vocab.CorvusPacket;
import net.onelitefeather.corvus.proto.capture.v1.ConnectionState;
import net.onelitefeather.corvus.proto.capture.v1.Frame;
import org.junit.jupiter.api.Test;

import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Chat, commands, signing, player lists and names with the default options (complete raw stream). */
class TextPacketCaptureTest {

    private static final PrivacyOptions REDACT = new PrivacyOptions(PrivacyOptions.TextPackets.FULL, true);

    private static Frame onlyFrame(RecordingSink sink) {
        assertEquals(1, sink.frames.size(), "exactly one frame must be recorded");
        return ((WriteItem.FrameItem) sink.frames.get(0)).frame();
    }

    @Test
    void serverboundChatMessageIsRecordedWithItsPayloadByDefault() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = EngineFixture.withSink(sink);

        f.engine.onPacket(new FakePacketView().type(PacketType.Play.Client.CHAT_MESSAGE).payload(new byte[]{1, 2, 3}));

        assertEquals(CorvusPacket.C_CHAT_MESSAGE.id(), onlyFrame(sink).getPacketType());
        assertEquals(3, onlyFrame(sink).getPayload().size());
        assertEquals(0, f.metrics.droppedPrivacy.sum());
    }

    @Test
    void serverboundCommandIsRecordedByDefault() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = EngineFixture.withSink(sink);

        f.engine.onPacket(new FakePacketView().type(PacketType.Play.Client.CHAT_COMMAND).payload(new byte[]{1}));

        assertEquals(CorvusPacket.C_CHAT_COMMAND.id(), onlyFrame(sink).getPacketType());
        assertEquals(0, f.metrics.droppedPrivacy.sum());
    }

    @Test
    void clientboundPlayerInfoCarryingNamesIsRecordedWithItsPayloadByDefault() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = EngineFixture.withSink(sink);

        f.engine.onPacket(new FakePacketView().type(PacketType.Play.Server.PLAYER_INFO_UPDATE).clientbound()
                .payload(new byte[]{5, 6}));

        assertEquals(2, onlyFrame(sink).getPayload().size());
        assertEquals(0, f.metrics.droppedPrivacy.sum());
    }

    @Test
    void clientboundSystemChatIsRecordedWithItsPayloadByDefault() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = EngineFixture.withSink(sink);

        f.engine.onPacket(new FakePacketView().type(PacketType.Play.Server.SYSTEM_CHAT_MESSAGE).clientbound()
                .payload(new byte[]{7}));

        assertEquals(1, onlyFrame(sink).getPayload().size());
    }

    @Test
    void redactedNamesKeepTheChatFrameWithoutItsPayload() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = EngineFixture.withSink(sink, REDACT);

        f.engine.onPacket(new FakePacketView().type(PacketType.Play.Client.CHAT_MESSAGE).payload(new byte[]{1, 2}));

        assertEquals(CorvusPacket.C_CHAT_MESSAGE.id(), onlyFrame(sink).getPacketType());
        assertTrue(onlyFrame(sink).getPayload().isEmpty());
        assertEquals(0, f.metrics.droppedPrivacy.sum());
    }

    @Test
    void redactedNamesKeepThePlayerInfoFrameWithoutItsPayload() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = EngineFixture.withSink(sink, REDACT);

        f.engine.onPacket(new FakePacketView().type(PacketType.Play.Server.PLAYER_INFO_UPDATE).clientbound()
                .payload(new byte[]{5}));

        assertTrue(onlyFrame(sink).getPayload().isEmpty());
        assertEquals(0, f.metrics.droppedPrivacy.sum());
    }

    @Test
    void cookieResponsePayloadIsStrippedEvenWithoutRedaction() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = EngineFixture.withSink(sink);

        f.engine.onPacket(new FakePacketView().type(PacketType.Play.Client.COOKIE_RESPONSE).payload(new byte[]{1, 2}));

        assertTrue(onlyFrame(sink).getPayload().isEmpty(), "cookies carry credential material");
    }

    @Test
    void rawPayloadsNoneStillRecordsChatWithoutPayload() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = new EngineFixture().build(sink, EngineFixture.KEY, PayloadMode.NONE, 2048,
                new PlayerFilter(CorvusConfig.ListMode.ALL, Set.of(), Set.of()));

        f.engine.onPacket(new FakePacketView().type(PacketType.Play.Client.CHAT_MESSAGE).payload(new byte[]{1}));

        assertEquals(CorvusPacket.C_CHAT_MESSAGE.id(), onlyFrame(sink).getPacketType());
        assertTrue(onlyFrame(sink).getPayload().isEmpty());
    }

    @Test
    void loginStateFramesAreStillNeverRecorded() {
        RecordingSink sink = new RecordingSink();
        EngineFixture f = EngineFixture.withSink(sink);

        f.engine.onPacket(new FakePacketView().type(PacketType.Login.Client.LOGIN_START)
                .state(ConnectionState.CONNECTION_STATE_LOGIN));

        assertEquals(0, sink.frames.size());
        assertEquals(1, f.metrics.droppedPrivacy.sum());
    }
}
