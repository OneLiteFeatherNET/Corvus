package net.onelitefeather.corvus.plugin.score;

import net.onelitefeather.corvus.api.CorvusApi;
import net.onelitefeather.corvus.api.PlayerScore;
import net.onelitefeather.corvus.api.ScoreListener;

import java.util.Collection;
import java.util.Comparator;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.Executor;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.LongAdder;

/**
 * Implementation of {@link CorvusApi}: keeps the latest score per (player, game context, domain) and fires the
 * registered listeners on the given executor. The executor must be bounded; when it rejects work the listener
 * call is dropped and counted, the caller (the NATS dispatcher) is never blocked. A throwing listener is caught,
 * logged rate-limited and does not affect other listeners.
 */
public final class ScoreRegistry implements CorvusApi {

    private record Key(String gameContext, String domain) {
    }

    private final ConcurrentHashMap<UUID, ConcurrentHashMap<Key, PlayerScore>> latest = new ConcurrentHashMap<>();
    private final CopyOnWriteArrayList<ScoreListener> listeners = new CopyOnWriteArrayList<>();
    private final Executor executor;
    private final RateLimitedLog log;
    private final LongAdder listenerErrors = new LongAdder();
    private final LongAdder dispatchRejected = new LongAdder();

    public ScoreRegistry(Executor executor, RateLimitedLog log) {
        this.executor = executor;
        this.log = log;
    }

    /**
     * Stores {@code score} unless a newer one for the same (player, game context, domain) is already known, and
     * notifies the listeners when it was stored.
     *
     * @return true if the score became the latest one
     */
    public boolean update(PlayerScore score) {
        ConcurrentHashMap<Key, PlayerScore> perPlayer = latest.computeIfAbsent(score.player(),
                p -> new ConcurrentHashMap<>());
        PlayerScore kept = perPlayer.merge(new Key(score.gameContext(), score.domain()), score,
                (old, fresh) -> fresh.scoredAt().isBefore(old.scoredAt()) ? old : fresh);
        if (kept != score) {
            return false;
        }
        for (ScoreListener listener : listeners) {
            dispatch(listener, score);
        }
        return true;
    }

    private void dispatch(ScoreListener listener, PlayerScore score) {
        try {
            executor.execute(() -> {
                try {
                    listener.onScore(score);
                } catch (Throwable t) {
                    listenerErrors.increment();
                    log.warn("A Corvus score listener threw " + t + " (" + listener.getClass().getName() + ")");
                }
            });
        } catch (RejectedExecutionException e) {
            dispatchRejected.increment();
            log.warn("Corvus score listener queue is full, dropped a score update");
        }
    }

    /** Forgets all scores of a player that went offline. */
    public void evict(UUID player) {
        latest.remove(player);
    }

    @Override
    public Optional<PlayerScore> latestScore(UUID player, String gameContext) {
        Objects.requireNonNull(gameContext, "gameContext");
        var perPlayer = latest.get(Objects.requireNonNull(player, "player"));
        if (perPlayer == null) {
            return Optional.empty();
        }
        return perPlayer.values().stream()
                .filter(s -> s.gameContext().equals(gameContext))
                .max(Comparator.comparing(PlayerScore::scoredAt));
    }

    @Override
    public Collection<PlayerScore> latestScores(UUID player) {
        var perPlayer = latest.get(Objects.requireNonNull(player, "player"));
        return perPlayer == null ? List.of() : List.copyOf(perPlayer.values());
    }

    @Override
    public Registration registerScoreListener(ScoreListener listener) {
        Objects.requireNonNull(listener, "listener");
        listeners.add(listener);
        AtomicBoolean done = new AtomicBoolean();
        return () -> {
            if (done.compareAndSet(false, true)) {
                listeners.remove(listener);
            }
        };
    }

    public long listenerErrors() {
        return listenerErrors.sum();
    }

    public long dispatchRejected() {
        return dispatchRejected.sum();
    }
}
