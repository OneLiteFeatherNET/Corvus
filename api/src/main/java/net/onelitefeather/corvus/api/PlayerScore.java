package net.onelitefeather.corvus.api;

import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

/**
 * The latest judgement of one Corvus model about one online player. Immutable.
 *
 * <p><strong>Decision aid, not a verdict.</strong> A score is a probability-like model output in [0, 1]; higher
 * means more likely cheating. Only scores whose {@link #stage()} is {@link ScoreStage#ENFORCE} are meant for
 * automated actions; {@link ScoreStage#CANARY} scores are informational. Corvus never sanctions players, any
 * consequence is the decision of the plugin that reads the score.
 *
 * @param player        the online player the score is about
 * @param gameContext   game context tag of the backend server, e.g. {@code lobby} or {@code pvp}
 * @param domain        model domain, e.g. {@code movement}, {@code combat}
 * @param score         score of the latest window, in [0, 1]
 * @param smoothedScore score smoothed over the player's recent windows, in [0, 1]
 * @param windows       number of windows behind {@code smoothedScore}
 * @param modelName     name of the model that produced the score
 * @param modelVersion  version of that model
 * @param stage         rollout stage of the model, never shadow
 * @param scoredAt      when the score was produced
 */
public record PlayerScore(
        UUID player,
        String gameContext,
        String domain,
        float score,
        float smoothedScore,
        int windows,
        String modelName,
        String modelVersion,
        ScoreStage stage,
        Instant scoredAt) {

    public PlayerScore {
        Objects.requireNonNull(player, "player");
        Objects.requireNonNull(gameContext, "gameContext");
        Objects.requireNonNull(domain, "domain");
        Objects.requireNonNull(modelName, "modelName");
        Objects.requireNonNull(modelVersion, "modelVersion");
        Objects.requireNonNull(stage, "stage");
        Objects.requireNonNull(scoredAt, "scoredAt");
        requireUnit(score, "score");
        requireUnit(smoothedScore, "smoothedScore");
        if (windows < 0) {
            throw new IllegalArgumentException("windows must not be negative: " + windows);
        }
    }

    private static void requireUnit(float value, String name) {
        if (!(value >= 0f && value <= 1f)) {
            throw new IllegalArgumentException(name + " must be in [0, 1]: " + value);
        }
    }
}
