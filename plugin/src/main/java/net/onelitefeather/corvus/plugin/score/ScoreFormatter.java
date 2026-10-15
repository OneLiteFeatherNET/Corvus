package net.onelitefeather.corvus.plugin.score;

import net.onelitefeather.corvus.api.PlayerScore;

import java.time.Duration;
import java.time.Instant;
import java.util.Collection;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;

/** Text lines for {@code /corvus score <player>}. */
public final class ScoreFormatter {

    private ScoreFormatter() {
    }

    public static List<String> lines(String playerName, Collection<PlayerScore> scores, Instant now) {
        if (scores.isEmpty()) {
            return List.of("[corvus] no scores for " + playerName + " yet");
        }
        List<String> out = new java.util.ArrayList<>();
        out.add("[corvus] scores for " + playerName + " (decision aids, Corvus never sanctions players)");
        scores.stream()
                .sorted(Comparator.comparing(PlayerScore::gameContext).thenComparing(PlayerScore::domain))
                .forEach(s -> out.add(line(s, now)));
        return out;
    }

    static String line(PlayerScore s, Instant now) {
        long age = Math.max(0, Duration.between(s.scoredAt(), now).toSeconds());
        return String.format(Locale.ROOT, "%s/%s: score %.3f, smoothed %.3f over %d windows, %s %s, %s, %ds ago",
                s.gameContext(), s.domain(), s.score(), s.smoothedScore(), s.windows(), s.modelName(),
                s.modelVersion(), s.stage(), age);
    }
}
