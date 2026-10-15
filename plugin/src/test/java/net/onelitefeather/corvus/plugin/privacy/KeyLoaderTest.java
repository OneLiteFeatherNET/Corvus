package net.onelitefeather.corvus.plugin.privacy;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class KeyLoaderTest {

    private static final String KEY_B64 = Base64.getEncoder().encodeToString("0123456789abcdef0123".getBytes());

    @Test
    void noKeyAnywhereYieldsEmptyAndWarnsClearly(@TempDir Path dir) {
        List<String> warnings = new ArrayList<>();

        Optional<PseudonymKey> key = KeyLoader.load(Map.<String, String>of()::get, "", dir, "k1", warnings::add);

        assertTrue(key.isEmpty());
        assertEquals(1, warnings.size());
        assertTrue(warnings.get(0).contains("DISABLED"), warnings.get(0));
    }

    @Test
    void keyFromEnvironmentVariableIsLoaded(@TempDir Path dir) {
        Optional<PseudonymKey> key = KeyLoader.load(
                Map.of(KeyLoader.ENV_VAR, KEY_B64)::get, "", dir, "k7", w -> { });

        assertEquals("k7", key.orElseThrow().keyId());
    }

    @Test
    void keyFromFileIsLoadedWhenEnvIsUnset(@TempDir Path dir) throws IOException {
        Files.writeString(dir.resolve("pseudonym.key"), KEY_B64 + "\n");

        Optional<PseudonymKey> key = KeyLoader.load(
                Map.<String, String>of()::get, "pseudonym.key", dir, "k2", w -> { });

        assertEquals("k2", key.orElseThrow().keyId());
    }

    @Test
    void environmentWinsOverFile(@TempDir Path dir) throws IOException {
        Files.writeString(dir.resolve("pseudonym.key"), "!!!not base64!!!");

        Optional<PseudonymKey> key = KeyLoader.load(
                Map.of(KeyLoader.ENV_VAR, KEY_B64)::get, "pseudonym.key", dir, "k1", w -> { });

        assertTrue(key.isPresent());
    }

    @Test
    void invalidBase64FailsClosed(@TempDir Path dir) {
        List<String> warnings = new ArrayList<>();

        Optional<PseudonymKey> key = KeyLoader.load(
                Map.of(KeyLoader.ENV_VAR, "%%%")::get, "", dir, "k1", warnings::add);

        assertTrue(key.isEmpty());
        assertEquals(1, warnings.size());
    }

    @Test
    void tooShortKeyFailsClosed(@TempDir Path dir) {
        Optional<PseudonymKey> key = KeyLoader.load(
                Map.of(KeyLoader.ENV_VAR, Base64.getEncoder().encodeToString(new byte[4]))::get, "", dir, "k1",
                w -> { });

        assertTrue(key.isEmpty());
    }

    @Test
    void missingKeyFileFailsClosed(@TempDir Path dir) {
        List<String> warnings = new ArrayList<>();

        Optional<PseudonymKey> key = KeyLoader.load(
                Map.<String, String>of()::get, "absent.key", dir, "k1", warnings::add);

        assertTrue(key.isEmpty());
        assertEquals(1, warnings.size());
    }
}
