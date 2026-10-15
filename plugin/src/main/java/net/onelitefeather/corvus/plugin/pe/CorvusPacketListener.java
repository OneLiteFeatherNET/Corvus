package net.onelitefeather.corvus.plugin.pe;

import com.github.retrooper.packetevents.event.PacketListenerAbstract;
import com.github.retrooper.packetevents.event.PacketListenerPriority;
import com.github.retrooper.packetevents.event.PacketReceiveEvent;
import com.github.retrooper.packetevents.event.PacketSendEvent;
import com.github.retrooper.packetevents.event.ProtocolPacketEvent;
import net.onelitefeather.corvus.plugin.capture.CaptureEngine;

/**
 * Passive MONITOR listener. Read-only: never cancels, never marks for re-encode, never touches the buffer
 * indices. Any failure is counted and swallowed so Corvus can never kick a player or break chat signing.
 */
public final class CorvusPacketListener extends PacketListenerAbstract {

    private final CaptureEngine engine;

    public CorvusPacketListener(CaptureEngine engine) {
        super(PacketListenerPriority.MONITOR);
        this.engine = engine;
    }

    @Override
    public void onPacketReceive(PacketReceiveEvent event) {
        handle(event);
    }

    @Override
    public void onPacketSend(PacketSendEvent event) {
        handle(event);
    }

    private void handle(ProtocolPacketEvent event) {
        try {
            engine.onPacket(new PacketEventsView(event));
        } catch (Throwable t) {
            try {
                engine.recordListenerError(null);
            } catch (Throwable ignored) {
                // nothing left that is safe to do on the Netty thread
            }
        }
    }
}
