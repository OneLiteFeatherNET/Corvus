package net.onelitefeather.corvus.plugin.config;

import com.moandjiezana.toml.Toml;
import net.onelitefeather.corvus.plugin.privacy.PayloadMode;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Keeps deploy/pilot/proxy/corvus-config.toml in line with what the plugin actually reads. */
class PilotConfigTest {

    private static String pilotToml() throws IOException {
        return Files.readString(Path.of(System.getProperty("corvus.pilot.config")), StandardCharsets.UTF_8);
    }

    private static Set<String> keys(String prefix, Map<String, Object> table) {
        Set<String> out = new TreeSet<>();
        for (Map.Entry<String, Object> e : table.entrySet()) {
            String path = prefix.isEmpty() ? e.getKey() : prefix + "." + e.getKey();
            if (e.getValue() instanceof Map<?, ?> nested && !path.equals("game_context.servers")) {
                @SuppressWarnings("unchecked")
                Map<String, Object> child = (Map<String, Object>) nested;
                out.addAll(keys(path, child));
            } else {
                out.add(path);
            }
        }
        return out;
    }

    @Test
    void pilotConfigUsesOnlyKeysThePluginReads() throws IOException {
        Set<String> known = keys("", new Toml().read(CorvusConfig.DEFAULT_TOML).toMap());
        Set<String> used = keys("", new Toml().read(pilotToml()).toMap());

        Set<String> unknown = new TreeSet<>(used);
        unknown.removeAll(known);
        assertTrue(unknown.isEmpty(), "pilot config has keys the plugin ignores: " + unknown);
    }

    @Test
    void pilotConfigMapsBothBackendsToTheirContexts() throws IOException {
        CorvusConfig c = CorvusConfig.parse(pilotToml());

        assertEquals("lobby", c.serverContexts().get("lobby"));
        assertEquals("pvp", c.serverContexts().get("pvp"));
    }

    @Test
    void pilotConfigRecordsServerboundPayloadsOnly() throws IOException {
        assertEquals(PayloadMode.SERVERBOUND, CorvusConfig.parse(pilotToml()).payloadMode());
    }

    @Test
    void pilotConfigNamesItsPseudonymKeyVersion() throws IOException {
        assertEquals("pilot-1", CorvusConfig.parse(pilotToml()).keyId());
    }
}
