package net.onelitefeather.corvus.plugin.privacy;

import net.onelitefeather.corvus.plugin.config.CorvusConfig;

import java.util.Set;
import java.util.UUID;

/** UUID allow/blocklist, applied before anything is captured. The blocklist always wins. */
public final class PlayerFilter {

    private final CorvusConfig.ListMode mode;
    private final Set<UUID> allowlist;
    private final Set<UUID> blocklist;

    public PlayerFilter(CorvusConfig.ListMode mode, Set<UUID> allowlist, Set<UUID> blocklist) {
        this.mode = mode;
        this.allowlist = Set.copyOf(allowlist);
        this.blocklist = Set.copyOf(blocklist);
    }

    public boolean allows(UUID uuid) {
        if (blocklist.contains(uuid)) {
            return false;
        }
        return mode == CorvusConfig.ListMode.ALL || allowlist.contains(uuid);
    }
}
