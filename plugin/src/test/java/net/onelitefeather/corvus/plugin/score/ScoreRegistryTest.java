package net.onelitefeather.corvus.plugin.score;

import net.onelitefeather.corvus.api.CorvusApi;
import net.onelitefeather.corvus.api.PlayerScore;
import net.onelitefeather.corvus.api.ScoreStage;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.RejectedExecutionException;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ScoreRegistryTest {

    private static final UUID PLAYER = new UUID(1, 1);

    private final ScoreFixture f = new ScoreFixture();

    private static PlayerScore score(String context, String domain, float value, long millis) {
        return new PlayerScore(PLAYER, context, domain, value, value, 1, "m", "1", ScoreStage.ENFORCE,
                Instant.ofEpochMilli(millis));
    }

    @Test
    void throwingListenerDoesNotAffectOtherListeners() {
        List<PlayerScore> received = new ArrayList<>();
        f.registry.registerScoreListener(s -> {
            throw new IllegalStateException("boom");
        });
        f.registry.registerScoreListener(received::add);

        f.registry.update(score("pvp", "combat", 0.5f, 1_000));

        assertEquals(1, received.size());
        assertEquals(1, f.registry.listenerErrors());
        assertEquals(1, f.warnings.size(), "the failure is logged");
    }

    @Test
    void listenerFailuresAreLoggedRateLimited() {
        f.registry.registerScoreListener(s -> {
            throw new IllegalStateException("boom");
        });

        f.registry.update(score("pvp", "combat", 0.1f, 1_000));
        f.registry.update(score("pvp", "combat", 0.2f, 2_000));
        f.registry.update(score("pvp", "combat", 0.3f, 3_000));
        f.nanos.set(61_000_000_000L);
        f.registry.update(score("pvp", "combat", 0.4f, 4_000));

        assertEquals(4, f.registry.listenerErrors());
        assertEquals(2, f.warnings.size());
        assertTrue(f.warnings.get(1).contains("2 more"), f.warnings.get(1));
    }

    @Test
    void listenersRunOnTheExecutorNotOnTheCaller() {
        ArrayDeque<Runnable> queued = new ArrayDeque<>();
        ScoreRegistry registry = new ScoreRegistry(queued::add, f.log);
        List<PlayerScore> received = new ArrayList<>();
        registry.registerScoreListener(received::add);

        registry.update(score("pvp", "combat", 0.5f, 1_000));

        assertTrue(received.isEmpty(), "update returned without running the listener");
        queued.poll().run();
        assertEquals(1, received.size());
    }

    @Test
    void fullExecutorQueueDropsTheUpdateWithoutThrowing() {
        ScoreRegistry registry = new ScoreRegistry(r -> {
            throw new RejectedExecutionException("queue full");
        }, f.log);
        registry.registerScoreListener(s -> { });

        registry.update(score("pvp", "combat", 0.5f, 1_000));

        assertEquals(1, registry.dispatchRejected());
        assertEquals(1, registry.latestScores(PLAYER).size(), "the score is still stored");
    }

    @Test
    void staleUpdateDoesNotNotifyListeners() {
        List<PlayerScore> received = new ArrayList<>();
        f.registry.registerScoreListener(received::add);
        f.registry.update(score("pvp", "combat", 0.5f, 2_000));

        boolean stored = f.registry.update(score("pvp", "combat", 0.9f, 1_000));

        assertEquals(false, stored);
        assertEquals(1, received.size());
    }

    @Test
    void unregisteredListenerReceivesNothingMore() {
        List<PlayerScore> received = new ArrayList<>();
        CorvusApi.Registration registration = f.registry.registerScoreListener(received::add);
        f.registry.update(score("pvp", "combat", 0.5f, 1_000));

        registration.unregister();
        registration.unregister();
        f.registry.update(score("pvp", "combat", 0.6f, 2_000));

        assertEquals(1, received.size());
    }

    @Test
    void latestScoreForAContextPicksTheMostRecentDomain() {
        f.registry.update(score("pvp", "combat", 0.1f, 1_000));
        f.registry.update(score("pvp", "movement", 0.2f, 5_000));
        f.registry.update(score("lobby", "movement", 0.3f, 9_000));

        assertEquals("movement", f.registry.latestScore(PLAYER, "pvp").orElseThrow().domain());
        assertEquals(3, f.registry.latestScores(PLAYER).size());
    }

    @Test
    void evictForgetsAllScoresOfThePlayer() {
        f.registry.update(score("pvp", "combat", 0.1f, 1_000));

        f.registry.evict(PLAYER);

        assertTrue(f.registry.latestScores(PLAYER).isEmpty());
        assertTrue(f.registry.latestScore(PLAYER, "pvp").isEmpty());
    }

    @Test
    void returnedCollectionIsASnapshotThatCannotBeModified() {
        f.registry.update(score("pvp", "combat", 0.1f, 1_000));
        var snapshot = f.registry.latestScores(PLAYER);

        f.registry.update(score("pvp", "movement", 0.2f, 2_000));

        assertEquals(1, snapshot.size());
        assertThrows(UnsupportedOperationException.class, snapshot::clear);
    }
}
