package net.onelitefeather.corvus.plugin.score;

import com.google.protobuf.InvalidProtocolBufferException;
import net.onelitefeather.corvus.api.PlayerScore;
import net.onelitefeather.corvus.api.ScoreStage;
import net.onelitefeather.corvus.proto.score.v1.Score;

import java.time.Instant;
import java.time.InstantSource;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicLongArray;

/**
 * Decodes a {@code corvus.score.v1.Score} message and maps it to an online player. Cheap and non-blocking, so
 * it can run directly on the NATS dispatcher thread: listeners are fired by {@link ScoreRegistry} on its own
 * executor.
 */
public final class ScoreProcessor {

    public enum Outcome {
        /** Stored as the latest score and listeners were notified. */
        ACCEPTED,
        /** A newer score for the same (player, game context, domain) was already known. */
        STALE,
        /** Shadow (or unspecified) stage; never exposed. */
        IGNORED_STAGE,
        /** The pseudonym belongs to nobody online on this proxy. */
        IGNORED_OFFLINE,
        /** Undecodable message or out-of-range values. */
        INVALID
    }

    private final PseudonymDirectory directory;
    private final ScoreRegistry registry;
    private final InstantSource clock;
    private final RateLimitedLog log;
    private final AtomicLongArray counts = new AtomicLongArray(Outcome.values().length);

    public ScoreProcessor(PseudonymDirectory directory, ScoreRegistry registry, InstantSource clock,
                          RateLimitedLog log) {
        this.directory = directory;
        this.registry = registry;
        this.clock = clock;
        this.log = log;
    }

    public Outcome onMessage(byte[] body) {
        Outcome outcome = process(body);
        counts.incrementAndGet(outcome.ordinal());
        return outcome;
    }

    public long count(Outcome outcome) {
        return counts.get(outcome.ordinal());
    }

    private Outcome process(byte[] body) {
        Score message;
        try {
            message = Score.parseFrom(body);
        } catch (InvalidProtocolBufferException e) {
            log.warn("Ignored an undecodable Corvus score message: " + e.getMessage());
            return Outcome.INVALID;
        }
        Optional<ScoreStage> stage = stage(message.getStage());
        if (stage.isEmpty()) {
            return Outcome.IGNORED_STAGE;
        }
        Optional<UUID> player = directory.lookup(message.getPlayerPseudonym().toByteArray());
        if (player.isEmpty()) {
            return Outcome.IGNORED_OFFLINE;
        }
        long scoredAtMillis = message.getScoredAtUnixMillis();
        PlayerScore score;
        try {
            score = new PlayerScore(player.get(), requireText(message.getGameContext()),
                    requireText(message.getDomain()), message.getScore(), message.getScoreSmoothed(),
                    (int) Math.min(Integer.MAX_VALUE, Integer.toUnsignedLong(message.getWindows())),
                    message.getModelName(), message.getModelVersion(), stage.get(),
                    scoredAtMillis > 0 ? Instant.ofEpochMilli(scoredAtMillis) : clock.instant());
        } catch (IllegalArgumentException e) {
            log.warn("Ignored an invalid Corvus score message: " + e.getMessage());
            return Outcome.INVALID;
        }
        return registry.update(score) ? Outcome.ACCEPTED : Outcome.STALE;
    }

    private static String requireText(String value) {
        if (value.isBlank()) {
            throw new IllegalArgumentException("game_context and domain must not be blank");
        }
        return value;
    }

    /** Shadow, unspecified and unknown stages are not exposed. */
    static Optional<ScoreStage> stage(Score.Stage stage) {
        return switch (stage) {
            case STAGE_CANARY -> Optional.of(ScoreStage.CANARY);
            case STAGE_ENFORCE -> Optional.of(ScoreStage.ENFORCE);
            default -> Optional.empty();
        };
    }
}
