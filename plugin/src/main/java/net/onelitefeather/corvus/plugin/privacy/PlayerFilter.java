package net.onelitefeather.corvus.plugin.privacy;

import net.onelitefeather.corvus.plugin.config.CorvusConfig;

import java.util.Set;
import java.util.UUID;
import java.util.function.Predicate;

/**
 * UUID allow/blocklist from the config plus an optional dynamic blocklist (the synced pseudonyms), applied before
 * anything is captured. The blocklist always wins. The dynamic predicate runs on Netty threads: it must be cheap.
 */
public final class PlayerFilter {

    private final CorvusConfig.ListMode mode;
    private final Set<UUID> allowlist;
    private final Set<UUID> blocklist;
    private final Predicate<UUID> dynamicBlocked;

    public PlayerFilter(CorvusConfig.ListMode mode, Set<UUID> allowlist, Set<UUID> blocklist) {
        this(mode, allowlist, blocklist, uuid -> false);
    }

    public PlayerFilter(CorvusConfig.ListMode mode, Set<UUID> allowlist, Set<UUID> blocklist,
                        Predicate<UUID> dynamicBlocked) {
        this.mode = mode;
        this.allowlist = Set.copyOf(allowlist);
        this.blocklist = Set.copyOf(blocklist);
        this.dynamicBlocked = dynamicBlocked;
    }

    public boolean allows(UUID uuid) {
        if (blocklist.contains(uuid) || dynamicBlocked.test(uuid)) {
            return false;
        }
        return mode == CorvusConfig.ListMode.ALL || allowlist.contains(uuid);
    }
}
