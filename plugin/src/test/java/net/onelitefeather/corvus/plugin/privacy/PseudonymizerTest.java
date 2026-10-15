package net.onelitefeather.corvus.plugin.privacy;

import org.junit.jupiter.api.Test;

import java.nio.ByteBuffer;
import java.util.Arrays;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;

class PseudonymizerTest {

    private static final UUID PLAYER = UUID.fromString("11111111-2222-3333-4444-555555555555");

    private static PseudonymKey key(String material) {
        return new PseudonymKey(material.getBytes(), "k1");
    }

    @Test
    void sameKeyAndUuidGiveSamePseudonym() {
        byte[] a = new Pseudonymizer(key("0123456789abcdef")).pseudonym(PLAYER);
        byte[] b = new Pseudonymizer(key("0123456789abcdef")).pseudonym(PLAYER);

        assertArrayEquals(a, b);
        assertEquals(32, a.length, "HMAC-SHA-256 output");
    }

    @Test
    void differentKeysGiveDifferentPseudonyms() {
        byte[] a = new Pseudonymizer(key("0123456789abcdef")).pseudonym(PLAYER);
        byte[] b = new Pseudonymizer(key("fedcba9876543210")).pseudonym(PLAYER);

        assertFalse(Arrays.equals(a, b));
    }

    @Test
    void differentUuidsGiveDifferentPseudonyms() {
        Pseudonymizer p = new Pseudonymizer(key("0123456789abcdef"));

        assertFalse(Arrays.equals(p.pseudonym(PLAYER), p.pseudonym(UUID.randomUUID())));
    }

    @Test
    void pseudonymDoesNotContainRawUuidBytes() {
        byte[] uuidBytes = ByteBuffer.allocate(16)
                .putLong(PLAYER.getMostSignificantBits()).putLong(PLAYER.getLeastSignificantBits()).array();

        byte[] pseudonym = new Pseudonymizer(key("0123456789abcdef")).pseudonym(PLAYER);

        assertFalse(contains(pseudonym, uuidBytes), "raw uuid bytes must not appear");
        assertFalse(contains(pseudonym, Arrays.copyOf(uuidBytes, 8)), "half of the uuid must not appear");
    }

    @Test
    void keyShorterThanMinimumIsRejected() {
        assertThrows(IllegalArgumentException.class, () -> new PseudonymKey(new byte[8], "k1"));
    }

    @Test
    void keyMaterialIsNotPartOfToString() {
        assertEquals("PseudonymKey[k1]", key("0123456789abcdef").toString());
    }

    private static boolean contains(byte[] haystack, byte[] needle) {
        for (int i = 0; i + needle.length <= haystack.length; i++) {
            if (Arrays.equals(Arrays.copyOfRange(haystack, i, i + needle.length), needle)) {
                return true;
            }
        }
        return false;
    }
}
