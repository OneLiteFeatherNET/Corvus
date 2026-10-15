package net.onelitefeather.corvus.plugin.vocab;

import com.github.retrooper.packetevents.protocol.packettype.PacketType;
import org.junit.jupiter.api.Test;

import java.util.HashSet;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class CorvusPacketTest {

    @Test
    void vocabularyIdsAreUniqueAndPositive() {
        Set<Integer> seen = new HashSet<>();
        for (CorvusPacket p : CorvusPacket.values()) {
            assertTrue(p.id() > 0, p + " must have a positive id (0 is reserved for unmapped)");
            assertTrue(seen.add(p.id()), p + " reuses id " + p.id());
        }
    }

    @Test
    void eachPacketEventsTypeIsMappedOnlyOnce() {
        Set<Object> seen = new HashSet<>();
        for (CorvusPacket p : CorvusPacket.values()) {
            assertTrue(seen.add(p.packetEventsType()), p + " maps an already mapped PacketEvents type");
        }
    }

    @Test
    void lookupReturnsStableIdAndZeroForUnmapped() {
        assertEquals(CorvusPacket.C_CLIENT_TICK_END.id(), CorvusPacket.idOf(PacketType.Play.Client.CLIENT_TICK_END));
        assertEquals(0, CorvusPacket.idOf(PacketType.Play.Client.CHAT_MESSAGE), "chat has no vocabulary id");
    }

    @Test
    void existingIdsKeepTheirValues() {
        // Append-only contract: these values are written into capture files and must never change.
        assertEquals(11, CorvusPacket.C_PLAYER_POSITION.id());
        assertEquals(27, CorvusPacket.C_CLIENT_TICK_END.id());
        assertEquals(100, CorvusPacket.S_ENTITY_RELATIVE_MOVE.id());
        assertEquals(105, CorvusPacket.S_ENTITY_VELOCITY.id());
    }
}
