package net.onelitefeather.corvus.plugin.score;

import com.google.protobuf.ByteString;
import net.onelitefeather.corvus.plugin.privacy.PseudonymKey;
import net.onelitefeather.corvus.plugin.privacy.Pseudonymizer;
import net.onelitefeather.corvus.proto.score.v1.Score;

import java.time.Instant;
import java.time.InstantSource;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicLong;

/** Fresh per-test wiring of directory, registry and processor with a direct executor and fake clocks. */
final class ScoreFixture {

    static final Instant NOW = Instant.parse("2026-03-01T12:00:00Z");

    final Pseudonymizer pseudonymizer = new Pseudonymizer(new PseudonymKey("0123456789abcdef".getBytes(), "k1"));
    final PseudonymDirectory directory = new PseudonymDirectory(pseudonymizer::pseudonym);
    final AtomicLong nanos = new AtomicLong();
    final List<String> warnings = new ArrayList<>();
    final RateLimitedLog log = new RateLimitedLog(nanos::get, warnings::add, 60_000_000_000L);
    final ScoreRegistry registry = new ScoreRegistry(Runnable::run, log);
    final ScoreProcessor processor = new ScoreProcessor(directory, registry, InstantSource.fixed(NOW), log);

    byte[] message(UUID player, Score.Stage stage, long scoredAtMillis, float score) {
        return Score.newBuilder()
                .setPlayerPseudonym(ByteString.copyFrom(pseudonymizer.pseudonym(player)))
                .setPseudonymKeyId("k1")
                .setGameContext("pvp")
                .setDomain("combat")
                .setModelName("combat-gbm")
                .setModelVersion("3")
                .setStage(stage)
                .setScore(score)
                .setScoreSmoothed(score / 2)
                .setWindows(4)
                .setScoredAtUnixMillis(scoredAtMillis)
                .build().toByteArray();
    }
}
