package net.onelitefeather.corvus.plugin.vocab;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.reflect.TypeToken;
import net.onelitefeather.corvus.plugin.privacy.PayloadPolicy;
import net.onelitefeather.corvus.plugin.privacy.PrivacyFilter;
import net.onelitefeather.corvus.proto.capture.v1.Direction;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Keeps {@code proto/corvus/capture/v1/vocabulary.json} (read by the analyzer as its privacy denylist) in sync
 * with {@link CorvusPacket}, {@link PrivacyFilter} and {@link PayloadPolicy}. To regenerate after an intended
 * change run {@code ./gradlew :plugin:test --tests '*VocabularyJsonTest' -PupdateVocabulary}.
 */
class VocabularyJsonTest {

    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();

    record Entry(int id, String name, String direction, String state, boolean isPrivate, String payloadPolicy) {
    }

    private static Path file;

    @BeforeAll
    static void locateFile() throws IOException {
        file = Path.of(System.getProperty("corvus.vocabulary.json"));
        if (Boolean.parseBoolean(System.getProperty("corvus.vocabulary.update"))) {
            Files.createDirectories(file.getParent());
            Files.writeString(file, render(fromJava()) + "\n", StandardCharsets.UTF_8);
        }
    }

    private static List<Entry> fromJava() {
        List<Entry> out = new ArrayList<>();
        for (CorvusPacket p : CorvusPacket.values()) {
            PayloadPolicy.Level level = PayloadPolicy.levelOf(p.packetEventsType(), p.direction(), p.state());
            out.add(new Entry(p.id(), p.name(),
                    p.direction() == Direction.DIRECTION_SERVERBOUND ? "serverbound" : "clientbound",
                    p.state().name().replace("CONNECTION_STATE_", "").toLowerCase(java.util.Locale.ROOT),
                    level == PayloadPolicy.Level.FRAME_DROPPED,
                    level.json()));
        }
        out.sort(Comparator.comparingInt(Entry::id));
        return out;
    }

    private static String render(List<Entry> entries) {
        List<java.util.Map<String, Object>> rows = new ArrayList<>();
        for (Entry e : entries) {
            java.util.Map<String, Object> row = new java.util.LinkedHashMap<>();
            row.put("id", e.id());
            row.put("name", e.name());
            row.put("direction", e.direction());
            row.put("state", e.state());
            row.put("private", e.isPrivate());
            row.put("payload_policy", e.payloadPolicy());
            rows.add(row);
        }
        return GSON.toJson(rows);
    }

    private static List<Entry> fromJson() throws IOException {
        List<java.util.Map<String, Object>> rows = GSON.fromJson(Files.readString(file, StandardCharsets.UTF_8),
                new TypeToken<List<java.util.Map<String, Object>>>() { }.getType());
        List<Entry> out = new ArrayList<>();
        for (java.util.Map<String, Object> r : rows) {
            out.add(new Entry(((Number) r.get("id")).intValue(), (String) r.get("name"),
                    (String) r.get("direction"), (String) r.get("state"), (Boolean) r.get("private"),
                    (String) r.get("payload_policy")));
        }
        return out;
    }

    @Test
    void committedJsonMatchesJavaVocabularyAndPolicies() throws IOException {
        assertEquals(fromJava(), fromJson(),
                "vocabulary.json is out of sync with CorvusPacket/PrivacyFilter/PayloadPolicy; regenerate with "
                        + "./gradlew :plugin:test --tests '*VocabularyJsonTest' -PupdateVocabulary");
    }

    @Test
    void everyPrivacyDroppedTypeIsMarkedPrivateInTheJson() throws IOException {
        List<Entry> json = fromJson();
        for (var type : PrivacyFilter.privateTypes()) {
            int id = CorvusPacket.idOf(type);
            Entry entry = json.stream().filter(e -> e.id() == id).findFirst().orElse(null);
            assertTrue(entry != null && entry.isPrivate(), type.getName() + " must be private in vocabulary.json");
        }
    }

    @Test
    void everyPayloadStrippedTypeIsMarkedStrippedInTheJson() throws IOException {
        List<Entry> json = fromJson();
        for (var type : PayloadPolicy.strippedServerboundTypes()) {
            int id = CorvusPacket.idOf(type);
            Entry entry = json.stream().filter(e -> e.id() == id).findFirst().orElse(null);
            assertTrue(entry != null && entry.payloadPolicy().equals("stripped"),
                    type.getName() + " must be stripped in vocabulary.json");
        }
    }

    @Test
    void jsonHasNoDuplicateIds() throws IOException {
        List<Entry> json = fromJson();
        assertEquals(json.size(), json.stream().map(Entry::id).distinct().count());
    }
}
