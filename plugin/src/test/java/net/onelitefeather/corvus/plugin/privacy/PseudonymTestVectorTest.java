package net.onelitefeather.corvus.plugin.privacy;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;
import java.util.HexFormat;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;

/** Pins the pseudonym to docs/reference/pseudonym-test-vector.md, shared with the control-plane API. */
class PseudonymTestVectorTest {

    private static String pseudonymHex(String keyBase64, String uuid, Path dir) {
        PseudonymKey key = KeyLoader.load(
                Map.of(KeyLoader.ENV_VAR, keyBase64)::get, "", dir, "k1", w -> { }).orElseThrow();
        return HexFormat.of().formatHex(new Pseudonymizer(key).pseudonym(UUID.fromString(uuid)));
    }

    @Test
    void vector1MatchesTheDocumentedPseudonym(@TempDir Path dir) {
        assertEquals("40642e55746eeaffde2e9de80d506b79cd3dbbc299a84da87a3812759417da84",
                pseudonymHex("MDEyMzQ1Njc4OWFiY2RlZg==", "11111111-2222-3333-4444-555555555555", dir));
    }

    @Test
    void vector2MatchesTheDocumentedPseudonym(@TempDir Path dir) {
        assertEquals("ff921230177d146ee8401eb82173128410002ba6f997e8e837e804407c8cdd38",
                pseudonymHex("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=", "00000000-0000-0000-0000-000000000000", dir));
    }
}
