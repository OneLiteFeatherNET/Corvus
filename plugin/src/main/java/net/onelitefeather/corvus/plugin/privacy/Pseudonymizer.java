package net.onelitefeather.corvus.plugin.privacy;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.ByteBuffer;
import java.security.GeneralSecurityException;
import java.util.UUID;

/** Derives the player pseudonym: HMAC-SHA-256(key, uuid as 16 big-endian bytes). */
public final class Pseudonymizer {

    private static final String ALGORITHM = "HmacSHA256";

    private final PseudonymKey key;

    public Pseudonymizer(PseudonymKey key) {
        this.key = key;
    }

    public String keyId() {
        return key.keyId();
    }

    public byte[] pseudonym(UUID uuid) {
        try {
            Mac mac = Mac.getInstance(ALGORITHM);
            mac.init(new SecretKeySpec(key.material(), ALGORITHM));
            byte[] input = ByteBuffer.allocate(16)
                    .putLong(uuid.getMostSignificantBits())
                    .putLong(uuid.getLeastSignificantBits())
                    .array();
            return mac.doFinal(input);
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("HMAC-SHA-256 unavailable", e);
        }
    }
}
