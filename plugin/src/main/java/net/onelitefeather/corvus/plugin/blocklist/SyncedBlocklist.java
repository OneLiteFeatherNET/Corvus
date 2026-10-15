package net.onelitefeather.corvus.plugin.blocklist;

import net.onelitefeather.corvus.plugin.score.RateLimitedLog;

import java.io.IOException;
import java.time.Instant;
import java.time.InstantSource;
import java.util.Set;

/**
 * The blocked pseudonyms last fetched from the control plane. Reads are lock-free; {@link #syncOnce()} runs on
 * the dedicated sync thread. A failed sync keeps the last good set; before the first success the set is empty,
 * so only the config lists apply.
 */
public final class SyncedBlocklist {

    private final BlocklistClient client;
    private final InstantSource clock;
    private final RateLimitedLog log;
    private final Runnable onChange;

    private volatile Set<String> blocked = Set.of();
    private volatile Instant lastSuccess;
    private volatile String lastResult = "never synced";

    /** @param onChange runs after every successful sync that changed the set. */
    public SyncedBlocklist(BlocklistClient client, InstantSource clock, RateLimitedLog log, Runnable onChange) {
        this.client = client;
        this.clock = clock;
        this.log = log;
        this.onChange = onChange;
    }

    /** @param pseudonymHex lower-case hex pseudonym */
    public boolean contains(String pseudonymHex) {
        Set<String> current = blocked;
        return !current.isEmpty() && current.contains(pseudonymHex);
    }

    public int size() {
        return blocked.size();
    }

    /** Never throws. */
    public void syncOnce() {
        Instant attempt = clock.instant();
        try {
            Set<String> fetched = Set.copyOf(client.fetchBlocked());
            boolean changed = !fetched.equals(blocked);
            blocked = fetched;
            lastSuccess = attempt;
            lastResult = "ok";
            if (changed) {
                onChange.run();
            }
        } catch (IOException | RuntimeException e) {
            lastResult = "failed at " + attempt + ": " + e.getMessage();
            log.warn("Blocklist sync failed, keeping the last good list (" + blocked.size() + " entries): "
                    + e.getMessage());
        }
    }

    /** One line for {@code /corvus stats}. */
    public String status() {
        return "blocklist_size=" + blocked.size()
                + " blocklist_last_sync=" + (lastSuccess == null ? "never" : lastSuccess.toString())
                + " blocklist_last_result=\"" + lastResult + "\"";
    }
}
