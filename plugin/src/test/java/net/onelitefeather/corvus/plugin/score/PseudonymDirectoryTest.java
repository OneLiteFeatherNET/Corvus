package net.onelitefeather.corvus.plugin.score;

import org.junit.jupiter.api.Test;

import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class PseudonymDirectoryTest {

    private static final UUID PLAYER = new UUID(3, 4);

    private final ScoreFixture f = new ScoreFixture();

    @Test
    void pseudonymOfAnOnlinePlayerResolvesToTheUuid() {
        f.directory.add(PLAYER);

        assertEquals(PLAYER, f.directory.lookup(f.pseudonymizer.pseudonym(PLAYER)).orElseThrow());
    }

    @Test
    void disconnectEvictsThePlayerFromBothDirections() {
        f.directory.add(PLAYER);

        f.directory.remove(PLAYER);

        assertTrue(f.directory.lookup(f.pseudonymizer.pseudonym(PLAYER)).isEmpty());
        assertEquals(0, f.directory.size());
    }

    @Test
    void unknownPseudonymIsEmpty() {
        assertTrue(f.directory.lookup(new byte[32]).isEmpty());
    }

    @Test
    void addingTwiceKeepsOneEntry() {
        f.directory.add(PLAYER);
        f.directory.add(PLAYER);

        assertEquals(1, f.directory.size());
    }

    @Test
    void removingAnUnknownPlayerIsHarmless() {
        f.directory.remove(PLAYER);

        assertEquals(0, f.directory.size());
    }

    @Test
    void hexOfComputesLazilyAndMatchesTheLookup() {
        String hex = f.directory.hexOf(PLAYER);

        assertEquals(java.util.HexFormat.of().formatHex(f.pseudonymizer.pseudonym(PLAYER)), hex);
        assertEquals(PLAYER, f.directory.lookup(f.pseudonymizer.pseudonym(PLAYER)).orElseThrow());
    }
}
