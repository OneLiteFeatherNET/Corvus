package net.onelitefeather.corvus.api;

import org.junit.jupiter.api.Test;

import java.lang.reflect.Modifier;
import java.time.Instant;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class PlayerScoreTest {

    private static final UUID PLAYER = new UUID(1, 2);
    private static final Instant AT = Instant.parse("2026-01-01T00:00:00Z");

    private static PlayerScore score(float score, float smoothed, int windows) {
        return new PlayerScore(PLAYER, "pvp", "combat", score, smoothed, windows, "m", "1", ScoreStage.ENFORCE, AT);
    }

    @Test
    void equalComponentsGiveEqualScores() {
        assertEquals(score(0.5f, 0.4f, 3), score(0.5f, 0.4f, 3));
    }

    @Test
    void allFieldsAreFinalSoTheRecordCannotBeMutated() {
        for (var field : PlayerScore.class.getDeclaredFields()) {
            assertTrue(Modifier.isFinal(field.getModifiers()), field.getName() + " must be final");
        }
        assertTrue(PlayerScore.class.isRecord());
    }

    @Test
    void scoreOutsideUnitIntervalIsRejected() {
        assertThrows(IllegalArgumentException.class, () -> score(1.01f, 0.5f, 1));
        assertThrows(IllegalArgumentException.class, () -> score(0.5f, -0.1f, 1));
        assertThrows(IllegalArgumentException.class, () -> score(Float.NaN, 0.5f, 1));
    }

    @Test
    void negativeWindowsAreRejected() {
        assertThrows(IllegalArgumentException.class, () -> score(0.5f, 0.5f, -1));
    }

    @Test
    void nullComponentsAreRejected() {
        assertThrows(NullPointerException.class, () -> new PlayerScore(null, "pvp", "combat", 0.5f, 0.5f, 1,
                "m", "1", ScoreStage.ENFORCE, AT));
        assertThrows(NullPointerException.class, () -> new PlayerScore(PLAYER, "pvp", "combat", 0.5f, 0.5f, 1,
                "m", "1", null, AT));
    }

    @Test
    void shadowIsNotAnExposedStage() {
        for (ScoreStage stage : ScoreStage.values()) {
            assertFalse(stage.name().contains("SHADOW"), "shadow must never be exposed");
        }
    }

    @Test
    void onlyEnforceMayDriveAutomatedActions() {
        assertTrue(ScoreStage.ENFORCE.mayDriveAutomatedActions());
        assertFalse(ScoreStage.CANARY.mayDriveAutomatedActions());
    }
}
