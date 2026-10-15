package net.onelitefeather.corvus.plugin.score;

import net.onelitefeather.corvus.api.PlayerScore;
import net.onelitefeather.corvus.api.ScoreStage;
import net.onelitefeather.corvus.plugin.score.ScoreProcessor.Outcome;
import net.onelitefeather.corvus.proto.score.v1.Score;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ScoreProcessorTest {

    private static final UUID PLAYER = new UUID(7, 8);

    private final ScoreFixture f = new ScoreFixture();

    @Test
    void enforceScoreOfAnOnlinePlayerIsDecodedAndMapped() {
        f.directory.add(PLAYER);

        Outcome outcome = f.processor.onMessage(f.message(PLAYER, Score.Stage.STAGE_ENFORCE, 1_000_000L, 0.8f));

        assertEquals(Outcome.ACCEPTED, outcome);
        PlayerScore s = f.registry.latestScore(PLAYER, "pvp").orElseThrow();
        assertEquals(new PlayerScore(PLAYER, "pvp", "combat", 0.8f, 0.4f, 4, "combat-gbm", "3",
                ScoreStage.ENFORCE, Instant.ofEpochMilli(1_000_000L)), s);
    }

    @Test
    void canaryStageIsExposedAsCanary() {
        f.directory.add(PLAYER);

        f.processor.onMessage(f.message(PLAYER, Score.Stage.STAGE_CANARY, 1_000L, 0.3f));

        assertEquals(ScoreStage.CANARY, f.registry.latestScore(PLAYER, "pvp").orElseThrow().stage());
    }

    @Test
    void shadowScoreIsIgnoredEvenForAnOnlinePlayer() {
        f.directory.add(PLAYER);

        Outcome outcome = f.processor.onMessage(f.message(PLAYER, Score.Stage.STAGE_SHADOW, 1_000L, 0.9f));

        assertEquals(Outcome.IGNORED_STAGE, outcome);
        assertTrue(f.registry.latestScores(PLAYER).isEmpty());
    }

    @Test
    void unspecifiedStageIsIgnored() {
        f.directory.add(PLAYER);

        assertEquals(Outcome.IGNORED_STAGE,
                f.processor.onMessage(f.message(PLAYER, Score.Stage.STAGE_UNSPECIFIED, 1_000L, 0.9f)));
    }

    @Test
    void scoreOfAPlayerWhoIsNotOnlineIsIgnored() {
        Outcome outcome = f.processor.onMessage(f.message(PLAYER, Score.Stage.STAGE_ENFORCE, 1_000L, 0.9f));

        assertEquals(Outcome.IGNORED_OFFLINE, outcome);
        assertTrue(f.registry.latestScores(PLAYER).isEmpty());
    }

    @Test
    void newerScoreWinsByTimestamp() {
        f.directory.add(PLAYER);
        f.processor.onMessage(f.message(PLAYER, Score.Stage.STAGE_ENFORCE, 2_000L, 0.2f));

        Outcome outcome = f.processor.onMessage(f.message(PLAYER, Score.Stage.STAGE_ENFORCE, 3_000L, 0.7f));

        assertEquals(Outcome.ACCEPTED, outcome);
        assertEquals(0.7f, f.registry.latestScore(PLAYER, "pvp").orElseThrow().score());
    }

    @Test
    void olderScoreArrivingLateDoesNotReplaceTheLatest() {
        f.directory.add(PLAYER);
        f.processor.onMessage(f.message(PLAYER, Score.Stage.STAGE_ENFORCE, 3_000L, 0.7f));

        Outcome outcome = f.processor.onMessage(f.message(PLAYER, Score.Stage.STAGE_ENFORCE, 2_000L, 0.2f));

        assertEquals(Outcome.STALE, outcome);
        assertEquals(0.7f, f.registry.latestScore(PLAYER, "pvp").orElseThrow().score());
    }

    @Test
    void missingTimestampFallsBackToTheInjectedClock() {
        f.directory.add(PLAYER);

        f.processor.onMessage(f.message(PLAYER, Score.Stage.STAGE_ENFORCE, 0L, 0.5f));

        assertEquals(ScoreFixture.NOW, f.registry.latestScore(PLAYER, "pvp").orElseThrow().scoredAt());
    }

    @Test
    void undecodableBytesAreInvalidAndWarnOnce() {
        Outcome first = f.processor.onMessage(new byte[] {(byte) 0xff, (byte) 0xff, (byte) 0xff});
        f.processor.onMessage(new byte[] {(byte) 0xff, (byte) 0xff, (byte) 0xff});

        assertEquals(Outcome.INVALID, first);
        assertEquals(1, f.warnings.size(), "warnings are rate limited");
        assertEquals(2, f.processor.count(Outcome.INVALID));
    }

    @Test
    void outOfRangeScoreIsInvalid() {
        f.directory.add(PLAYER);

        Outcome outcome = f.processor.onMessage(f.message(PLAYER, Score.Stage.STAGE_ENFORCE, 1_000L, 7f));

        assertEquals(Outcome.INVALID, outcome);
        assertTrue(f.registry.latestScores(PLAYER).isEmpty());
    }

    @Test
    void scoresForDifferentContextsAreKeptSeparately() throws Exception {
        f.directory.add(PLAYER);
        f.processor.onMessage(f.message(PLAYER, Score.Stage.STAGE_ENFORCE, 1_000L, 0.1f));
        byte[] lobby = Score.newBuilder().mergeFrom(f.message(PLAYER, Score.Stage.STAGE_ENFORCE, 1_000L, 0.6f))
                .setGameContext("lobby").build().toByteArray();

        f.processor.onMessage(lobby);

        assertEquals(2, f.registry.latestScores(PLAYER).size());
        assertEquals(0.6f, f.registry.latestScore(PLAYER, "lobby").orElseThrow().score());
        assertEquals(0.1f, f.registry.latestScore(PLAYER, "pvp").orElseThrow().score());
    }
}
