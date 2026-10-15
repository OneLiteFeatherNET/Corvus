package net.onelitefeather.corvus.plugin.score;

import net.onelitefeather.corvus.api.PlayerScore;
import net.onelitefeather.corvus.api.ScoreStage;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ScoreFormatterTest {

    private static final Instant NOW = Instant.parse("2026-03-01T12:00:10Z");

    @Test
    void emptyScoresSayThatNoneExist() {
        assertEquals(List.of("[corvus] no scores for Steve yet"), ScoreFormatter.lines("Steve", List.of(), NOW));
    }

    @Test
    void linesAreSortedByContextAndDomainAndShowAge() {
        UUID id = new UUID(1, 1);
        Instant at = Instant.parse("2026-03-01T12:00:00Z");
        var pvp = new PlayerScore(id, "pvp", "combat", 0.8f, 0.5f, 3, "gbm", "2", ScoreStage.ENFORCE, at);
        var lobby = new PlayerScore(id, "lobby", "movement", 0.1f, 0.1f, 1, "gbm", "2", ScoreStage.CANARY, at);

        List<String> lines = ScoreFormatter.lines("Steve", List.of(pvp, lobby), NOW);

        assertEquals(3, lines.size());
        assertTrue(lines.get(0).contains("never sanctions"), lines.get(0));
        assertEquals("lobby/movement: score 0.100, smoothed 0.100 over 1 windows, gbm 2, CANARY, 10s ago",
                lines.get(1));
        assertEquals("pvp/combat: score 0.800, smoothed 0.500 over 3 windows, gbm 2, ENFORCE, 10s ago",
                lines.get(2));
    }
}
