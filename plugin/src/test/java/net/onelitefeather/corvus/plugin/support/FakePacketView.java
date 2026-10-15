package net.onelitefeather.corvus.plugin.support;

import com.github.retrooper.packetevents.protocol.packettype.PacketType;
import com.github.retrooper.packetevents.protocol.packettype.PacketTypeCommon;
import net.onelitefeather.corvus.plugin.capture.PacketView;
import net.onelitefeather.corvus.proto.capture.v1.ConnectionState;
import net.onelitefeather.corvus.proto.capture.v1.Direction;
import net.onelitefeather.corvus.proto.capture.v1.Frame;
import net.onelitefeather.corvus.proto.capture.v1.SwingEvent;

import java.util.UUID;

/** Hand-written test double for {@link PacketView}; each test builds its own instance. */
public class FakePacketView implements PacketView {

    private static final Object DEFAULT_CHANNEL = new Object();
    public static final UUID PLAYER = UUID.fromString("11111111-2222-3333-4444-555555555555");

    private PacketTypeCommon type = PacketType.Play.Client.PLAYER_POSITION;
    private Direction direction = Direction.DIRECTION_SERVERBOUND;
    private ConnectionState state = ConnectionState.CONNECTION_STATE_PLAY;
    private UUID player = PLAYER;
    private Object channel = DEFAULT_CHANNEL;
    private byte[] payload = {1, 2, 3};
    private RuntimeException decodeFailure;
    private boolean decodesSwing;
    private RuntimeException payloadFailure;

    public FakePacketView type(PacketTypeCommon type) {
        this.type = type;
        return this;
    }

    public FakePacketView clientbound() {
        this.direction = Direction.DIRECTION_CLIENTBOUND;
        return this;
    }

    public FakePacketView state(ConnectionState state) {
        this.state = state;
        return this;
    }

    public FakePacketView player(UUID player) {
        this.player = player;
        return this;
    }

    public FakePacketView channel(Object channel) {
        this.channel = channel;
        return this;
    }

    public FakePacketView payload(byte[] payload) {
        this.payload = payload;
        return this;
    }

    public FakePacketView decodeFails(RuntimeException failure) {
        this.decodeFailure = failure;
        return this;
    }

    public FakePacketView payloadCopyFails(RuntimeException failure) {
        this.payloadFailure = failure;
        return this;
    }

    public FakePacketView decodesSwing() {
        this.decodesSwing = true;
        return this;
    }

    @Override
    public Direction direction() {
        return direction;
    }

    @Override
    public PacketTypeCommon packetType() {
        return type;
    }

    @Override
    public int wirePacketId() {
        return 42;
    }

    @Override
    public ConnectionState state() {
        return state;
    }

    @Override
    public UUID playerId() {
        return player;
    }

    @Override
    public Object channelKey() {
        return channel;
    }

    @Override
    public byte[] copyPayload(int maxBytes) {
        if (payloadFailure != null) {
            throw payloadFailure;
        }
        return payload.length <= maxBytes ? payload.clone() : java.util.Arrays.copyOf(payload, maxBytes);
    }

    @Override
    public void decodeEvent(Frame.Builder target) {
        if (decodeFailure != null) {
            throw decodeFailure;
        }
        if (decodesSwing) {
            target.setSwing(SwingEvent.newBuilder().setOffHand(true));
        }
    }

    @Override
    public SessionInfo sessionInfo() {
        return new SessionInfo(767, 767, false, "test");
    }
}
