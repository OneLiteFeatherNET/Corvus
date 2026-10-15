package net.onelitefeather.corvus.plugin.config;

import net.onelitefeather.corvus.plugin.privacy.PayloadMode;
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
        assertEquals(PayloadMode.SERVERBOUND, c.payloadMode());
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
    void rawPayloadsStringIsParsed() {
        assertEquals(PayloadMode.ALL, CorvusConfig.parse("[capture]\nraw_payloads = \"all\"\n").payloadMode());
        assertEquals(PayloadMode.NONE, CorvusConfig.parse("[capture]\nraw_payloads = \"none\"\n").payloadMode());
    }

    @Test
    void legacyBooleanTrueMeansServerbound() {
        assertEquals(PayloadMode.SERVERBOUND, CorvusConfig.parse("[capture]\nraw_payloads = true\n").payloadMode());
    }

    @Test
    void legacyBooleanFalseMeansNone() {
        assertEquals(PayloadMode.NONE, CorvusConfig.parse("[capture]\nraw_payloads = false\n").payloadMode());
    }

    @Test
    void missingRawPayloadsKeyDefaultsToServerbound() {
        assertEquals(PayloadMode.SERVERBOUND, CorvusConfig.parse("proxy_id = \"x\"\n").payloadMode());
    }

    @Test
    void unknownRawPayloadsValueIsRejected() {
        assertThrows(IllegalArgumentException.class,
                () -> CorvusConfig.parse("[capture]\nraw_payloads = \"clientbound\"\n"));
    }

    @Test
    void unknownListModeIsRejected() {
        assertThrows(IllegalArgumentException.class, () -> CorvusConfig.parse("[players]\nmode = \"everyone\"\n"));
    }
}
