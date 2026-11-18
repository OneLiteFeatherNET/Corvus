package net.onelitefeather.corvus.plugin.capture;

import net.onelitefeather.corvus.plugin.score.RateLimitedLog;
import net.onelitefeather.corvus.plugin.time.NanoClock;

import java.time.Duration;
import java.time.InstantSource;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Consumer;

/**
 * Caches lookups of a {@link ServiceMetadataSource} for a fixed time, negative answers included, so that a server
 * which is not an orchestrator service costs one lookup per interval. A failing delegate is treated as "unknown"
 * and logged at most once per minute. Never throws.
 */
public final class CachedServiceMetadataSource implements ServiceMetadataSource {

    private record Entry(Optional<ServiceMetadata> value, long expiresAtMillis) {
    }

    private final ServiceMetadataSource delegate;
    private final InstantSource clock;
    private final long ttlMillis;
    private final RateLimitedLog failures;
    private final Map<String, Entry> entries = new ConcurrentHashMap<>();

    public CachedServiceMetadataSource(ServiceMetadataSource delegate, InstantSource clock, Duration ttl,
                                       Consumer<String> warn) {
        this(delegate, clock, ttl, warn, NanoClock.system());
    }

    public CachedServiceMetadataSource(ServiceMetadataSource delegate, InstantSource clock, Duration ttl,
                                       Consumer<String> warn, NanoClock nanoClock) {
        this.delegate = delegate;
        this.clock = clock;
        this.ttlMillis = ttl.toMillis();
        this.failures = new RateLimitedLog(nanoClock, warn, RateLimitedLog.DEFAULT_INTERVAL_NANOS);
    }

    @Override
    public Optional<ServiceMetadata> lookup(String serverName) {
        if (serverName == null || serverName.isEmpty()) {
            return Optional.empty();
        }
        long now = clock.millis();
        Entry cached = entries.get(serverName);
        if (cached != null && now < cached.expiresAtMillis()) {
            return cached.value();
        }
        Optional<ServiceMetadata> value;
        try {
            value = delegate.lookup(serverName);
            if (value == null) {
                value = Optional.empty();
            }
        } catch (Throwable t) {
            failures.warn("orchestrator lookup failed, game context falls back to the list: "
                    + t.getClass().getSimpleName());
            value = Optional.empty();
        }
        entries.put(serverName, new Entry(value, now + ttlMillis));
        return value;
    }
}
