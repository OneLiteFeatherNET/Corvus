package net.onelitefeather.corvus.plugin.capture;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import net.onelitefeather.corvus.plugin.support.FakeServiceMetadataSource;
import org.junit.jupiter.api.Test;

class StageDerivationTest {

    private static String stageOf(String task, List<String> groups) {
        Optional<ServiceMetadata> meta = new FakeServiceMetadataSource()
                .service("server-1", task, groups)
                .lookup("server-1");
        return new GameContextMapper(Map.of(), "unknown").resolve("server-1", meta).stage();
    }

    @Test
    void taskEndingWithStagingIsStaging() {
        assertEquals("staging", stageOf("Slender-Staging", List.of()));
    }

    @Test
    void stagingSuffixMatchesCaseInsensitively() {
        assertEquals("staging", stageOf("Slender-staging", List.of()));
        assertEquals("staging", stageOf("SLENDER-STAGING", List.of()));
    }

    @Test
    void stagingGroupMakesStagingEvenWithoutTaskSuffix() {
        assertEquals("staging", stageOf("Tamias", List.of("Proxy", "Staging")));
    }

    @Test
    void stagingGroupMatchesCaseInsensitively() {
        assertEquals("staging", stageOf("Tamias", List.of("staging")));
    }

    @Test
    void knownServiceWithoutStagingMarkersIsProduction() {
        assertEquals("production", stageOf("Lobby", List.of("Lobby", "Production")));
    }

    @Test
    void stagingInTheMiddleOfTaskNameIsNotStaging() {
        assertEquals("production", stageOf("Staging-Lobby", List.of()));
    }

    @Test
    void unknownServerHasEmptyStage() {
        assertEquals("", new GameContextMapper(Map.of(), "unknown").resolve("Lobby-1", Optional.empty()).stage());
    }
}
