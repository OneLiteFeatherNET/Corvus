package net.onelitefeather.corvus.plugin.capture;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import net.onelitefeather.corvus.plugin.support.FakeServiceMetadataSource;
import org.junit.jupiter.api.Test;

class GameContextResolutionTest {

    private static GameContextMapper mapper(Map<String, String> servers) {
        return new GameContextMapper(servers, "unknown");
    }

    private static Optional<ServiceMetadata> cloudNet(String task) {
        return new FakeServiceMetadataSource().service(task + "-1", task, List.of()).lookup(task + "-1");
    }

    @Test
    void serverEntryWinsOverTaskEntry() {
        var m = mapper(Map.of("lobby-1", "server-entry", "lobby", "task-entry"));
        assertEquals("server-entry", m.resolve("Lobby-1", cloudNet("Lobby")).gameContext());
    }

    @Test
    void strippedServerEntryWinsOverTaskEntry() {
        var m = mapper(Map.of("lobby", "server-entry", "team-build", "task-entry"));
        assertEquals("server-entry", m.resolve("Lobby-2", cloudNet("Lobby")).gameContext());
    }

    @Test
    void taskEntryUsedWhenServerNameHasNoEntry() {
        var m = mapper(Map.of("slender", "list-slender", "slender-staging", "list-slender-staging"));
        assertEquals("list-slender", m.resolve("Slender-Live-3", cloudNet("Slender")).gameContext());
    }

    @Test
    void explicitEntryForStagingTaskIsUsedInsteadOfSuffixRemoval() {
        var m = mapper(Map.of("slender-staging", "list-staging"));
        assertEquals("list-staging", m.resolve("Slender-Staging-1", cloudNet("Slender-Staging")).gameContext());
    }

    @Test
    void stagingSuffixIsRemovedFromTaskWithoutListEntry() {
        var m = mapper(Map.of());
        assertEquals("slender", m.resolve("Slender-Staging-1", cloudNet("Slender-Staging")).gameContext());
    }

    @Test
    void stagingSuffixRemovalIsCaseInsensitive() {
        var m = mapper(Map.of());
        assertEquals("slender", m.resolve("Slender-Staging-1", cloudNet("SLENDER-staging")).gameContext());
    }

    @Test
    void taskNameIsLowerCasedWhenNoEntryExists() {
        var m = mapper(Map.of());
        assertEquals("lobby", m.resolve("Lobby-1", cloudNet("Lobby")).gameContext());
    }

    @Test
    void defaultUsedWithoutListEntryAndWithoutMetadata() {
        var m = mapper(Map.of());
        assertEquals(new BackendContext("unknown", ""), m.resolve("Tamias-Staging-1", Optional.empty()));
    }

    @Test
    void stagingTaskNameWithoutMetadataStaysUnknown() {
        var m = mapper(Map.of());
        assertEquals("unknown", m.resolve("Tamias-Staging-1", Optional.empty()).gameContext());
    }

    @Test
    void stagingSuffixAloneDoesNotProduceAnEmptyContext() {
        var m = mapper(Map.of());
        assertEquals("unknown", m.resolve("x", cloudNet("-staging")).gameContext());
    }

    @Test
    void contextForStillIgnoresMetadataAndUsesDefault() {
        var m = mapper(Map.of("lobby", "lobby"));
        assertEquals("unknown", m.contextFor("Slender-Staging-1"));
    }
}
