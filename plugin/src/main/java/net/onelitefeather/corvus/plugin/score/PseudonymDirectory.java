package net.onelitefeather.corvus.plugin.score;

import java.util.HexFormat;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Function;

/**
 * Pseudonym to online player lookup. The pseudonym of a player is computed once on login with the capture HMAC
 * key and cached until the player disconnects, so a score is mapped with one hash lookup and no HMAC.
 */
public final class PseudonymDirectory {

    private final Function<UUID, byte[]> pseudonymOf;
    private final ConcurrentHashMap<UUID, String> byPlayer = new ConcurrentHashMap<>();
    private final ConcurrentHashMap<String, UUID> byPseudonym = new ConcurrentHashMap<>();

    public PseudonymDirectory(Function<UUID, byte[]> pseudonymOf) {
        this.pseudonymOf = pseudonymOf;
    }

    /** Computes and caches the pseudonym of a player that came online. */
    public void add(UUID player) {
        String pseudonym = HexFormat.of().formatHex(pseudonymOf.apply(player));
        String previous = byPlayer.put(player, pseudonym);
        if (previous != null && !previous.equals(pseudonym)) {
            byPseudonym.remove(previous, player);
        }
        byPseudonym.put(pseudonym, player);
    }

    /**
     * Lower-case hex pseudonym of a player, computed on first use (packets can arrive before the login event) and
     * cached until {@link #remove}.
     */
    public String hexOf(UUID player) {
        String cached = byPlayer.get(player);
        if (cached == null) {
            add(player);
            cached = byPlayer.get(player);
        }
        return cached;
    }

    /** Forgets a player that went offline. */
    public void remove(UUID player) {
        String pseudonym = byPlayer.remove(player);
        if (pseudonym != null) {
            byPseudonym.remove(pseudonym, player);
        }
    }

    public Optional<UUID> lookup(byte[] pseudonym) {
        return Optional.ofNullable(byPseudonym.get(HexFormat.of().formatHex(pseudonym)));
    }

    public int size() {
        return byPlayer.size();
    }
}
