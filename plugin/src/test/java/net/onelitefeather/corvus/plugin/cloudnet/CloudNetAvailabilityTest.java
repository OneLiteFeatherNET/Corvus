package net.onelitefeather.corvus.plugin.cloudnet;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.net.URL;
import java.net.URLClassLoader;
import java.time.InstantSource;
import java.util.ArrayList;
import java.util.List;
import net.onelitefeather.corvus.plugin.capture.ServiceMetadataSource;
import org.junit.jupiter.api.Test;

class CloudNetAvailabilityTest {

    /** A loader that sees no classes at all, so CloudNet is never visible to it. */
    private static URLClassLoader isolated() {
        return new URLClassLoader(new URL[0], null);
    }

    @Test
    void cloudNetIsNotPresentWithoutItsClasses() throws IOException {
        try (var loader = isolated()) {
            assertFalse(CloudNetAvailability.present(loader));
        }
    }

    @Test
    void sourceFallsBackToNoneWithoutCloudNet() throws IOException {
        List<String> info = new ArrayList<>();
        List<String> warn = new ArrayList<>();
        try (var loader = isolated()) {
            ServiceMetadataSource source = CloudNetAvailability.load(loader, InstantSource.system(), info::add,
                    warn::add);

            assertFalse(source.lookup("Lobby-1").isPresent());
        }
        assertTrue(info.get(0).contains("not on the class path"), "the absence is logged as information");
        assertTrue(warn.isEmpty(), "a missing optional dependency is not a warning");
    }
}
