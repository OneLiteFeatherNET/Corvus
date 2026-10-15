package net.onelitefeather.corvus.plugin.pe;

import com.github.retrooper.packetevents.event.PacketReceiveEvent;
import com.github.retrooper.packetevents.event.PacketSendEvent;
import com.github.retrooper.packetevents.event.ProtocolPacketEvent;
import com.github.retrooper.packetevents.protocol.packettype.PacketType;
import com.github.retrooper.packetevents.protocol.packettype.PacketTypeCommon;
import com.github.retrooper.packetevents.protocol.player.DiggingAction;
import com.github.retrooper.packetevents.protocol.player.InteractionHand;
import com.github.retrooper.packetevents.protocol.world.Location;
import com.github.retrooper.packetevents.util.Vector3d;
import com.github.retrooper.packetevents.util.Vector3i;
import com.github.retrooper.packetevents.wrapper.play.client.WrapperPlayClientAnimation;
import com.github.retrooper.packetevents.wrapper.play.client.WrapperPlayClientAttack;
import com.github.retrooper.packetevents.wrapper.play.client.WrapperPlayClientHeldItemChange;
import com.github.retrooper.packetevents.wrapper.play.client.WrapperPlayClientInteractEntity;
import com.github.retrooper.packetevents.wrapper.play.client.WrapperPlayClientPlayerBlockPlacement;
import com.github.retrooper.packetevents.wrapper.play.client.WrapperPlayClientPlayerDigging;
import com.github.retrooper.packetevents.wrapper.play.client.WrapperPlayClientPlayerFlying;
import com.github.retrooper.packetevents.wrapper.play.server.WrapperPlayServerEntityPositionSync;
import com.github.retrooper.packetevents.wrapper.play.server.WrapperPlayServerEntityRelativeMove;
import com.github.retrooper.packetevents.wrapper.play.server.WrapperPlayServerEntityRelativeMoveAndRotation;
import com.github.retrooper.packetevents.wrapper.play.server.WrapperPlayServerEntityTeleport;
import com.github.retrooper.packetevents.wrapper.play.server.WrapperPlayServerEntityVelocity;
import net.onelitefeather.corvus.proto.capture.v1.DigEvent;
import net.onelitefeather.corvus.proto.capture.v1.EntityMotionEvent;
import net.onelitefeather.corvus.proto.capture.v1.Frame;
import net.onelitefeather.corvus.proto.capture.v1.HeldItemEvent;
import net.onelitefeather.corvus.proto.capture.v1.InteractEntityEvent;
import net.onelitefeather.corvus.proto.capture.v1.MovementEvent;
import net.onelitefeather.corvus.proto.capture.v1.PlaceEvent;
import net.onelitefeather.corvus.proto.capture.v1.SwingEvent;

/**
 * Fills the decoded event of a frame. Wrappers are NEVER built from the original event: with PacketEvents'
 * default re-encode setting that would make the decoder re-write the packet. We decode on a clone (own
 * buffer indices, own lastUsedWrapper) and release it afterwards.
 */
final class EventDecoder {

    private EventDecoder() {
    }

    static void decode(ProtocolPacketEvent original, Frame.Builder target) {
        PacketTypeCommon type = original.getPacketType();
        if (!isDecodable(type)) {
            return;
        }
        ProtocolPacketEvent copy = original.clone();
        try {
            if (copy instanceof PacketReceiveEvent receive) {
                decodeServerbound(receive, type, target);
            } else if (copy instanceof PacketSendEvent send) {
                decodeClientbound(send, type, target);
            }
        } finally {
            copy.cleanUp();
        }
    }

    private static boolean isDecodable(PacketTypeCommon type) {
        return type == PacketType.Play.Client.PLAYER_POSITION
                || type == PacketType.Play.Client.PLAYER_POSITION_AND_ROTATION
                || type == PacketType.Play.Client.PLAYER_ROTATION
                || type == PacketType.Play.Client.PLAYER_FLYING
                || type == PacketType.Play.Client.INTERACT_ENTITY
                || type == PacketType.Play.Client.ATTACK
                || type == PacketType.Play.Client.ANIMATION
                || type == PacketType.Play.Client.PLAYER_DIGGING
                || type == PacketType.Play.Client.PLAYER_BLOCK_PLACEMENT
                || type == PacketType.Play.Client.HELD_ITEM_CHANGE
                || type == PacketType.Play.Server.ENTITY_RELATIVE_MOVE
                || type == PacketType.Play.Server.ENTITY_RELATIVE_MOVE_AND_ROTATION
                || type == PacketType.Play.Server.ENTITY_TELEPORT
                || type == PacketType.Play.Server.ENTITY_POSITION_SYNC
                || type == PacketType.Play.Server.ENTITY_VELOCITY;
    }

    private static void decodeServerbound(PacketReceiveEvent e, PacketTypeCommon type, Frame.Builder target) {
        if (type == PacketType.Play.Client.PLAYER_POSITION
                || type == PacketType.Play.Client.PLAYER_POSITION_AND_ROTATION
                || type == PacketType.Play.Client.PLAYER_ROTATION
                || type == PacketType.Play.Client.PLAYER_FLYING) {
            WrapperPlayClientPlayerFlying w = new WrapperPlayClientPlayerFlying(e);
            Location l = w.getLocation();
            target.setMovement(MovementEvent.newBuilder()
                    .setHasPosition(w.hasPositionChanged())
                    .setHasRotation(w.hasRotationChanged())
                    .setX(l.getX()).setY(l.getY()).setZ(l.getZ())
                    .setYaw(l.getYaw()).setPitch(l.getPitch())
                    .setOnGround(w.isOnGround()));
        } else if (type == PacketType.Play.Client.INTERACT_ENTITY) {
            WrapperPlayClientInteractEntity w = new WrapperPlayClientInteractEntity(e);
            target.setInteractEntity(InteractEntityEvent.newBuilder()
                    .setEntityId(w.getEntityId())
                    .setAction(switch (w.getAction()) {
                        case INTERACT -> InteractEntityEvent.Action.ACTION_INTERACT;
                        case ATTACK -> InteractEntityEvent.Action.ACTION_ATTACK;
                        case INTERACT_AT -> InteractEntityEvent.Action.ACTION_INTERACT_AT;
                    })
                    .setSneaking(w.isSneaking().orElse(false))
                    .setOffHand(w.getHand() == InteractionHand.OFF_HAND));
        } else if (type == PacketType.Play.Client.ATTACK) {
            WrapperPlayClientAttack w = new WrapperPlayClientAttack(e);
            target.setInteractEntity(InteractEntityEvent.newBuilder()
                    .setEntityId(w.getEntityId())
                    .setAction(InteractEntityEvent.Action.ACTION_ATTACK));
        } else if (type == PacketType.Play.Client.ANIMATION) {
            WrapperPlayClientAnimation w = new WrapperPlayClientAnimation(e);
            target.setSwing(SwingEvent.newBuilder().setOffHand(w.getHand() == InteractionHand.OFF_HAND));
        } else if (type == PacketType.Play.Client.PLAYER_DIGGING) {
            WrapperPlayClientPlayerDigging w = new WrapperPlayClientPlayerDigging(e);
            Vector3i p = w.getBlockPosition();
            target.setDig(DigEvent.newBuilder()
                    .setStatus(digStatus(w.getAction()))
                    .setX(p.getX()).setY(p.getY()).setZ(p.getZ())
                    .setFace(w.getBlockFaceId()));
        } else if (type == PacketType.Play.Client.PLAYER_BLOCK_PLACEMENT) {
            WrapperPlayClientPlayerBlockPlacement w = new WrapperPlayClientPlayerBlockPlacement(e);
            Vector3i p = w.getBlockPosition();
            target.setPlace(PlaceEvent.newBuilder()
                    .setX(p.getX()).setY(p.getY()).setZ(p.getZ())
                    .setFace(w.getFaceId())
                    .setOffHand(w.getHand() == InteractionHand.OFF_HAND));
        } else if (type == PacketType.Play.Client.HELD_ITEM_CHANGE) {
            WrapperPlayClientHeldItemChange w = new WrapperPlayClientHeldItemChange(e);
            target.setHeldItem(HeldItemEvent.newBuilder().setSlot(w.getSlot()));
        }
    }

    private static void decodeClientbound(PacketSendEvent e, PacketTypeCommon type, Frame.Builder target) {
        if (type == PacketType.Play.Server.ENTITY_RELATIVE_MOVE) {
            WrapperPlayServerEntityRelativeMove w = new WrapperPlayServerEntityRelativeMove(e);
            target.setEntityMotion(motion(w.getEntityId(), EntityMotionEvent.Kind.KIND_RELATIVE,
                    w.getDeltaX(), w.getDeltaY(), w.getDeltaZ()));
        } else if (type == PacketType.Play.Server.ENTITY_RELATIVE_MOVE_AND_ROTATION) {
            WrapperPlayServerEntityRelativeMoveAndRotation w = new WrapperPlayServerEntityRelativeMoveAndRotation(e);
            target.setEntityMotion(motion(w.getEntityId(), EntityMotionEvent.Kind.KIND_RELATIVE,
                    w.getDeltaX(), w.getDeltaY(), w.getDeltaZ()));
        } else if (type == PacketType.Play.Server.ENTITY_TELEPORT) {
            WrapperPlayServerEntityTeleport w = new WrapperPlayServerEntityTeleport(e);
            Vector3d p = w.getPosition();
            target.setEntityMotion(motion(w.getEntityId(), EntityMotionEvent.Kind.KIND_TELEPORT,
                    p.getX(), p.getY(), p.getZ()));
        } else if (type == PacketType.Play.Server.ENTITY_POSITION_SYNC) {
            WrapperPlayServerEntityPositionSync w = new WrapperPlayServerEntityPositionSync(e);
            Vector3d p = w.getValues().getPosition();
            target.setEntityMotion(motion(w.getId(), EntityMotionEvent.Kind.KIND_TELEPORT,
                    p.getX(), p.getY(), p.getZ()));
        } else if (type == PacketType.Play.Server.ENTITY_VELOCITY) {
            WrapperPlayServerEntityVelocity w = new WrapperPlayServerEntityVelocity(e);
            Vector3d v = w.getVelocity();
            target.setEntityMotion(motion(w.getEntityId(), EntityMotionEvent.Kind.KIND_VELOCITY,
                    v.getX(), v.getY(), v.getZ()));
        }
    }

    private static EntityMotionEvent.Builder motion(int entityId, EntityMotionEvent.Kind kind, double x, double y,
                                                    double z) {
        return EntityMotionEvent.newBuilder().setEntityId(entityId).setKind(kind).setX(x).setY(y).setZ(z);
    }

    /** Protocol status ids of the dig packet (version independent in practice). */
    static int digStatus(DiggingAction action) {
        return switch (action) {
            case START_DIGGING -> 0;
            case CANCELLED_DIGGING -> 1;
            case FINISHED_DIGGING -> 2;
            case DROP_ITEM_STACK -> 3;
            case DROP_ITEM -> 4;
            case RELEASE_USE_ITEM -> 5;
            case SWAP_ITEM_WITH_OFFHAND -> 6;
            default -> -1;
        };
    }
}
