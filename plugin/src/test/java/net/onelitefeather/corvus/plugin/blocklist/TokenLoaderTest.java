package net.onelitefeather.corvus.plugin.blocklist;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class TokenLoaderTest {

    private final List<String> warnings = new ArrayList<>();

    @Test
    void environmentVariableWins(@TempDir Path dir) throws IOException {
        Files.writeString(dir.resolve("token"), "from-file\n");

        Optional<String> token = TokenLoader.load(Map.of(TokenLoader.ENV_VAR, " from-env ")::get, "token", dir,
                warnings::add);

        assertEquals(Optional.of("from-env"), token);
    }

    @Test
    void fileIsUsedWithoutEnvironmentVariable(@TempDir Path dir) throws IOException {
        Files.writeString(dir.resolve("token"), "from-file\n");

        assertEquals(Optional.of("from-file"), TokenLoader.load(k -> null, "token", dir, warnings::add));
    }

    @Test
    void missingTokenDisablesWithAWarning(@TempDir Path dir) {
        assertTrue(TokenLoader.load(k -> null, "", dir, warnings::add).isEmpty());
        assertTrue(TokenLoader.load(k -> null, "absent", dir, warnings::add).isEmpty());
        assertEquals(2, warnings.size());
    }
}
