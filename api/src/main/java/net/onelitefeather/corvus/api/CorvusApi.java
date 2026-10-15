package net.onelitefeather.corvus.api;

import java.util.Collection;
import java.util.Optional;
import java.util.UUID;

/**
 * Read access to the player scores received by the Corvus plugin. Obtain it with {@link CorvusProvider#get()}.
 *
 * <p><strong>Scores are decision aids.</strong> Only {@link ScoreStage#ENFORCE} scores are meant for automated
 * actions; canary scores are informational and shadow scores are never exposed. Corvus never sanctions players.
 *
 * <p>Scores exist only for players that are currently online on this proxy and only after the scoring service
 * published one; they are forgotten when the player disconnects. All methods are thread-safe and non-blocking.
 */
public interface CorvusApi {

    /** The latest score of {@code player} in {@code gameContext}; if several domains exist, the most recent one. */
    Optional<PlayerScore> latestScore(UUID player, String gameContext);

    /** The latest score per (game context, domain) of {@code player}. Empty if none; the result is a snapshot. */
    Collection<PlayerScore> latestScores(UUID player);

    /**
     * Registers a listener for score updates. Listeners are invoked asynchronously, see {@link ScoreListener}.
     *
     * @return a handle to remove the listener again; plugins should do that when they are disabled
     */
    Registration registerScoreListener(ScoreListener listener);

    /** Handle returned by {@link #registerScoreListener(ScoreListener)}. */
    interface Registration {

        /** Removes the listener; calling it again has no effect. */
        void unregister();
    }
}
