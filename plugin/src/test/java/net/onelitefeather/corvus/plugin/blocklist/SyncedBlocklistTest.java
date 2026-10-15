package net.onelitefeather.corvus.plugin.blocklist;

import net.onelitefeather.corvus.plugin.score.RateLimitedLog;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.time.Instant;
import java.time.InstantSource;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class SyncedBlocklistTest {

    private static final class FakeClient implements BlocklistClient {
        Set<String> next = Set.of();
        IOException failure;

        @Override
        public Set<String> fetchBlocked() throws IOException {
            if (failure != null) {
                throw failure;
            }
            return next;
        }
    }

    private final FakeClient client = new FakeClient();
    private final List<String> warnings = new ArrayList<>();
    private final AtomicInteger changes = new AtomicInteger();
    private final SyncedBlocklist blocklist = new SyncedBlocklist(client,
            InstantSource.fixed(Instant.parse("2026-10-15T12:00:00Z")),
            new RateLimitedLog(() -> 0L, warnings::add, 60_000_000_000L), changes::incrementAndGet);

    @Test
    void beforeTheFirstSyncNothingIsBlockedAndStatusSaysNever() {
        assertFalse(blocklist.contains("aa"));
        assertEquals("blocklist_size=0 blocklist_last_sync=never blocklist_last_result=\"never synced\"",
                blocklist.status());
    }

    @Test
    void successfulSyncReplacesTheSet() {
        client.next = Set.of("aa", "bb");
        blocklist.syncOnce();
        client.next = Set.of("cc");
        blocklist.syncOnce();

        assertTrue(blocklist.contains("cc"));
        assertFalse(blocklist.contains("aa"), "an entry removed upstream is no longer blocked");
        assertEquals(1, blocklist.size());
        assertEquals(2, changes.get());
    }

    @Test
    void unchangedSetDoesNotNotify() {
        client.next = Set.of("aa");
        blocklist.syncOnce();
        blocklist.syncOnce();

        assertEquals(1, changes.get());
    }

    @Test
    void failedSyncKeepsTheLastGoodSetAndLogs() {
        client.next = Set.of("aa");
        blocklist.syncOnce();
        client.failure = new IOException("connection refused");
        blocklist.syncOnce();

        assertTrue(blocklist.contains("aa"));
        assertEquals(1, changes.get(), "a failure is not a change");
        assertEquals(1, warnings.size());
        assertTrue(blocklist.status().contains("blocklist_size=1"));
        assertTrue(blocklist.status().contains("failed at 2026-10-15T12:00:00Z: connection refused"));
        assertTrue(blocklist.status().contains("blocklist_last_sync=2026-10-15T12:00:00Z"));
    }

    @Test
    void failureOnFirstStartLeavesTheSetEmpty() {
        client.failure = new IOException("timeout");
        blocklist.syncOnce();

        assertEquals(0, blocklist.size());
        assertEquals(0, changes.get());
        assertTrue(blocklist.status().contains("blocklist_last_sync=never"));
    }

    @Test
    void repeatedFailuresAreRateLimited() {
        client.failure = new IOException("down");
        blocklist.syncOnce();
        blocklist.syncOnce();
        blocklist.syncOnce();

        assertEquals(1, warnings.size());
    }
}
