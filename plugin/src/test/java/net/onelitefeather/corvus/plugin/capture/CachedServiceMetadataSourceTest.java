package net.onelitefeather.corvus.plugin.capture;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Duration;
import java.time.Instant;
import java.time.InstantSource;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;
import net.onelitefeather.corvus.plugin.support.FakeServiceMetadataSource;
import net.onelitefeather.corvus.plugin.time.NanoClock;
import org.junit.jupiter.api.Test;

class CachedServiceMetadataSourceTest {

    private final AtomicLong millis = new AtomicLong(Instant.parse("2026-11-18T10:00:00Z").toEpochMilli());
    private final InstantSource clock = () -> Instant.ofEpochMilli(millis.get());
    private final AtomicLong nanos = new AtomicLong();
    private final List<String> warnings = new ArrayList<>();

    private CachedServiceMetadataSource cached(ServiceMetadataSource delegate) {
        return new CachedServiceMetadataSource(delegate, clock, Duration.ofSeconds(30), warnings::add,
                nanos::get);
    }

    @Test
    void answersWithinTtlComeFromCache() {
        FakeServiceMetadataSource fake = new FakeServiceMetadataSource().service("Lobby-1", "Lobby", List.of());
        var source = cached(fake);

        source.lookup("Lobby-1");
        millis.addAndGet(Duration.ofSeconds(29).toMillis());
        source.lookup("Lobby-1");

        assertEquals(1, fake.lookups.size(), "a second lookup inside the TTL must not reach the orchestrator");
    }

    @Test
    void answersAfterTtlAreLookedUpAgain() {
        FakeServiceMetadataSource fake = new FakeServiceMetadataSource().service("Lobby-1", "Lobby", List.of());
        var source = cached(fake);

        source.lookup("Lobby-1");
        millis.addAndGet(Duration.ofSeconds(30).toMillis());
        source.lookup("Lobby-1");

        assertEquals(2, fake.lookups.size(), "an expired entry must be refreshed");
    }

    @Test
    void unknownServersAreCachedToo() {
        FakeServiceMetadataSource fake = new FakeServiceMetadataSource();
        var source = cached(fake);

        assertFalse(source.lookup("limbo").isPresent());
        assertFalse(source.lookup("limbo").isPresent());

        assertEquals(1, fake.lookups.size(), "a server that is not an orchestrator service must not be re-queried");
    }

    @Test
    void failingDelegateIsUnknownAndDoesNotThrow() {
        var source = cached(new FakeServiceMetadataSource().failingWith(new IllegalStateException("boom")));

        assertFalse(source.lookup("Lobby-1").isPresent());
        assertEquals(1, warnings.size(), "the first failure is logged");
        assertTrue(warnings.get(0).contains("IllegalStateException"));
    }

    @Test
    void repeatedFailuresAreLoggedAtMostOncePerInterval() {
        var source = cached(new FakeServiceMetadataSource().failingWith(new IllegalStateException("boom")));

        source.lookup("a");
        source.lookup("b");
        source.lookup("c");

        assertEquals(1, warnings.size(), "warnings are rate limited to one per minute");
    }

    @Test
    void nullOrEmptyNameIsUnknownWithoutLookup() {
        FakeServiceMetadataSource fake = new FakeServiceMetadataSource();
        var source = cached(fake);

        assertFalse(source.lookup(null).isPresent());
        assertFalse(source.lookup("").isPresent());
        assertEquals(0, fake.lookups.size());
    }
}
