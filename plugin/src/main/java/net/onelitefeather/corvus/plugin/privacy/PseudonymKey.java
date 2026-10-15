package net.onelitefeather.corvus.plugin.privacy;

import java.util.Arrays;

/** HMAC key plus its rotation identifier. The key material never leaves this package. */
public final class PseudonymKey {

    public static final int MIN_KEY_BYTES = 16;

    private final byte[] key;
    private final String keyId;

    public PseudonymKey(byte[] key, String keyId) {
        if (key == null || key.length < MIN_KEY_BYTES) {
            throw new IllegalArgumentException("pseudonym key must be at least " + MIN_KEY_BYTES + " bytes");
        }
        this.key = Arrays.copyOf(key, key.length);
        this.keyId = keyId;
    }

    public String keyId() {
        return keyId;
    }

    byte[] material() {
        return key;
    }

    @Override
    public String toString() {
        return "PseudonymKey[" + keyId + "]";
    }
}
