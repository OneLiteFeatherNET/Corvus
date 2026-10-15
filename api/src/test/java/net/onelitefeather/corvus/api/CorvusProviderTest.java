package net.onelitefeather.corvus.api;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.util.Collection;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class CorvusProviderTest {

    private final CorvusApi api = new CorvusApi() {
        @Override
        public Optional<PlayerScore> latestScore(UUID player, String gameContext) {
            return Optional.empty();
        }

        @Override
        public Collection<PlayerScore> latestScores(UUID player) {
            return List.of();
        }

        @Override
        public Registration registerScoreListener(ScoreListener listener) {
            return () -> { };
        }
    };

    @AfterEach
    void reset() {
        CorvusProvider.unregister(api);
    }

    @Test
    void getFailsBeforeRegistration() {
        assertThrows(IllegalStateException.class, CorvusProvider::get);
        assertTrue(CorvusProvider.find().isEmpty());
    }

    @Test
    void getReturnsTheRegisteredApi() {
        CorvusProvider.register(api);

        assertSame(api, CorvusProvider.get());
    }

    @Test
    void unregisterOnlyRemovesTheSameInstance() {
        CorvusProvider.register(api);
        CorvusProvider.unregister(new CorvusApi() {
            @Override
            public Optional<PlayerScore> latestScore(UUID player, String gameContext) {
                return Optional.empty();
            }

            @Override
            public Collection<PlayerScore> latestScores(UUID player) {
                return List.of();
            }

            @Override
            public Registration registerScoreListener(ScoreListener listener) {
                return () -> { };
            }
        });

        assertSame(api, CorvusProvider.get());
    }
}
