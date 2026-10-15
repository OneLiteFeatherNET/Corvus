package net.onelitefeather.corvus.plugin.config;

import net.onelitefeather.corvus.plugin.privacy.PayloadMode;
import org.junit.jupiter.api.Test;

import java.util.List;
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

    @Test
    void sinkDefaultsToFileAndNatsDefaultsAreDocumented() {
        CorvusConfig c = CorvusConfig.parse("proxy_id = \"p\"\n");

        assertEquals(CorvusConfig.SinkType.FILE, c.sinkType());
        assertEquals(List.of("nats://localhost:4222"), c.nats().urls());
        assertEquals("", c.nats().credsFile());
        assertEquals("corvus.capture.v1", c.nats().subjectPrefix());
        assertEquals(32L * 1024 * 1024, c.nats().maxInFlightBytes());
        assertEquals(5_000L, c.nats().publishTimeoutMillis());
    }

    @Test
    void natsSinkWithUrlListAndCredsIsParsed() {
        CorvusConfig c = CorvusConfig.parse("""
                [sink]
                type = "NATS"
                [nats]
                url = ["nats://a:4222", "nats://b:4222"]
                creds_file = "/etc/corvus.creds"
                subject_prefix = "x.y"
                max_in_flight_bytes = 1048576
                publish_timeout_seconds = 2
                """);

        assertEquals(CorvusConfig.SinkType.NATS, c.sinkType());
        assertEquals(List.of("nats://a:4222", "nats://b:4222"), c.nats().urls());
        assertEquals("/etc/corvus.creds", c.nats().credsFile());
        assertEquals("x.y", c.nats().subjectPrefix());
        assertEquals(1_048_576L, c.nats().maxInFlightBytes());
        assertEquals(2_000L, c.nats().publishTimeoutMillis());
    }

    @Test
    void singleStringUrlIsAccepted() {
        CorvusConfig c = CorvusConfig.parse("[nats]\nurl = \"nats://only:4222\"\n");

        assertEquals(List.of("nats://only:4222"), c.nats().urls());
    }

    @Test
    void invalidSinkTypeIsRejected() {
        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                () -> CorvusConfig.parse("[sink]\ntype = \"kafka\"\n"));

        assertTrue(e.getMessage().contains("sink.type"), e.getMessage());
    }
}
