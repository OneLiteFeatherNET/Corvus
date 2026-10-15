package net.onelitefeather.corvus.plugin.config;

import org.junit.jupiter.api.Test;

import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class CorvusConfigTest {

    @Test
    void defaultConfigParsesWithDocumentedDefaults() {
        CorvusConfig c = CorvusConfig.parse(CorvusConfig.DEFAULT_TOML);

        assertEquals(CorvusConfig.ListMode.ALL, c.listMode());
        assertTrue(c.rawPayloads());
        assertEquals(64L * 1024 * 1024, c.queueMaxBytes());
        assertEquals("unknown", c.defaultGameContext());
        assertEquals("lobby", c.serverContexts().get("lobby"));
        assertEquals("k1", c.keyId());
    }

    @Test
    void allowlistAndBlocklistUuidsAreParsed() {
        UUID a = UUID.randomUUID();
        UUID b = UUID.randomUUID();
        CorvusConfig c = CorvusConfig.parse("[players]\nmode = \"allowlist\"\nallowlist = [\"" + a
                + "\"]\nblocklist = [\"" + b + "\"]\n");

        assertEquals(CorvusConfig.ListMode.ALLOWLIST, c.listMode());
        assertTrue(c.allowlist().contains(a));
        assertTrue(c.blocklist().contains(b));
    }

    @Test
    void unknownListModeIsRejected() {
        assertThrows(IllegalArgumentException.class, () -> CorvusConfig.parse("[players]\nmode = \"everyone\"\n"));
    }
}
