package net.onelitefeather.corvus.plugin.support;

import net.onelitefeather.corvus.plugin.capture.CaptureEngine;
import net.onelitefeather.corvus.plugin.capture.CaptureSink;
import net.onelitefeather.corvus.plugin.capture.GameContextMapper;
import net.onelitefeather.corvus.plugin.capture.Metrics;
import net.onelitefeather.corvus.plugin.config.CorvusConfig;
import net.onelitefeather.corvus.plugin.privacy.PayloadMode;
import net.onelitefeather.corvus.plugin.privacy.PlayerFilter;
import net.onelitefeather.corvus.plugin.privacy.PseudonymKey;
import net.onelitefeather.corvus.plugin.privacy.Pseudonymizer;

import java.time.Instant;
import java.time.InstantSource;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicLong;

/** Builds a fresh engine with deterministic collaborators. One instance per test: no shared state. */
public final class EngineFixture {

    public static final PseudonymKey KEY = new PseudonymKey("0123456789abcdef0123456789abcdef".getBytes(), "k1");

    public final Metrics metrics = new Metrics();
    public final AtomicLong nanos = new AtomicLong(1_000);
    public final List<String> logs = new ArrayList<>();
    public final InstantSource wallClock = InstantSource.fixed(Instant.parse("2026-10-15T12:00:00Z"));
    private final AtomicLong sessionCounter = new AtomicLong();

    public CaptureSink sink;
    public CaptureEngine engine;

    public static EngineFixture withSink(CaptureSink sink) {
        return new EngineFixture().build(sink, KEY, PayloadMode.SERVERBOUND, 2048);
    }

    public static EngineFixture withoutKey(CaptureSink sink) {
        return new EngineFixture().build(sink, null, PayloadMode.SERVERBOUND, 2048);
    }

    public EngineFixture build(CaptureSink sink, PseudonymKey key, PayloadMode payloadMode, int maxPayload) {
        return build(sink, key, payloadMode, maxPayload,
                new PlayerFilter(CorvusConfig.ListMode.ALL, Set.of(), Set.of()));
    }

    public EngineFixture build(CaptureSink sink, PseudonymKey key, PayloadMode payloadMode, int maxPayload,
                               PlayerFilter filter) {
        this.sink = sink;
        this.engine = new CaptureEngine(
                new CaptureEngine.Settings(payloadMode, maxPayload, "proxy-test", "0.0.0-test"),
                key == null ? null : new Pseudonymizer(key),
                filter,
                new GameContextMapper(Map.of("lobby", "lobby"), "unknown"),
                sink, nanos::get, wallClock,
                () -> {
                    byte[] id = new byte[16];
                    id[15] = (byte) sessionCounter.incrementAndGet();
                    return id;
                },
                metrics, logs::add);
        return this;
    }

    public static InstantSource utc(String iso) {
        return InstantSource.fixed(Instant.parse(iso).atOffset(ZoneOffset.UTC).toInstant());
    }
}
