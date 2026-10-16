package net.onelitefeather.corvus.plugin.config;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.function.Function;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ProxyIdResolverTest {

    private static final String SECRET = "s3cr3t-connection-key-0123456789";

    private static final String WRAPPER_JSON = """
            {
              "connectionKey": "%s",
              "serviceConfiguration": {
                "serviceId": {
                  "uniqueId": "7d1f1f6e-6a3c-4a3e-9d2b-0c7b8a6f1e11",
                  "taskName": "Proxy-Corvus",
                  "nameSplitter": "-",
                  "taskServiceId": 1,
                  "nodeUniqueId": "Node-1",
                  "environmentName": "VELOCITY"
                },
                "port": 25565
              },
              "serviceInfoSnapshot": {
                "configuration": {
                  "serviceId": {
                    "taskName": "Other",
                    "nameSplitter": "-",
                    "taskServiceId": 9
                  }
                }
              }
            }
            """.formatted(SECRET);

    @TempDir
    Path dir;

    private Path wrapper(String json) throws IOException {
        Path file = dir.resolve(".wrapper").resolve("wrapper.json");
        Files.createDirectories(file.getParent());
        Files.writeString(file, json, StandardCharsets.UTF_8);
        return file;
    }

    private static Function<String, String> env(Map<String, String> values) {
        return values::get;
    }

    private ProxyIdResolver resolver(Map<String, String> env) throws IOException {
        return new ProxyIdResolver(wrapper(WRAPPER_JSON), env(env));
    }

    private ProxyIdResolver resolverWithoutWrapper(Map<String, String> env) {
        return new ProxyIdResolver(dir.resolve("missing").resolve("wrapper.json"), env(env));
    }

    @Test
    void servicePlaceholderJoinsTaskNameSplitterAndServiceId() throws IOException {
        String id = resolver(Map.of()).resolve("${cloudnet.service}");

        assertEquals("Proxy-Corvus-1", id, "service placeholder");
        assertFalse(id.contains(SECRET), "connectionKey must never reach the result");
    }

    @Test
    void taskPlaceholderIsTheTaskName() throws IOException {
        assertEquals("Proxy-Corvus", resolver(Map.of()).resolve("${cloudnet.task}"), "task placeholder");
    }

    @Test
    void envPlaceholderIsTheEnvironmentVariable() throws IOException {
        assertEquals("edge-7", resolver(Map.of("PROXY_NAME", "edge-7")).resolve("${env:PROXY_NAME}"),
                "env placeholder");
    }

    @Test
    void literalIdIsLeftUnchanged() {
        assertEquals("pilot-proxy", resolverWithoutWrapper(Map.of()).resolve("pilot-proxy"),
                "literal id must not need the wrapper file");
    }

    @Test
    void placeholderCanBeMixedWithText() throws IOException {
        assertEquals("olf-Proxy-Corvus-1", resolver(Map.of()).resolve("olf-${cloudnet.service}"),
                "mixed text");
    }

    @Test
    void severalPlaceholdersAreAllResolved() throws IOException {
        assertEquals("eu_Proxy-Corvus",
                resolver(Map.of("REGION", "eu")).resolve("${env:REGION}_${cloudnet.task}"), "two placeholders");
    }

    @Test
    void missingWrapperFileNamesThePlaceholderAndFails() {
        ProxyIdException e = assertThrows(ProxyIdException.class,
                () -> resolverWithoutWrapper(Map.of()).resolve("${cloudnet.service}"));

        assertTrue(e.getMessage().contains("${cloudnet.service}"), "message names the placeholder: " + e.getMessage());
    }

    @Test
    void malformedWrapperFileFailsWithoutLeakingItsContent() throws IOException {
        Path file = wrapper("{\"connectionKey\": \"" + SECRET + "\", \"serviceConfiguration\": [oops");
        ProxyIdException e = assertThrows(ProxyIdException.class,
                () -> new ProxyIdResolver(file, env(Map.of())).resolve("${cloudnet.task}"));

        assertTrue(e.getMessage().contains("${cloudnet.task}"), "message names the placeholder: " + e.getMessage());
        assertFalse(e.getMessage().contains(SECRET), "message must not leak the connectionKey");
        assertEquals(null, e.getCause(), "no cause that could carry file content");
    }

    @Test
    void wrapperFileWithoutServiceIdFails() throws IOException {
        Path file = wrapper("{\"connectionKey\": \"" + SECRET + "\", \"serviceConfiguration\": {}}");
        ProxyIdException e = assertThrows(ProxyIdException.class,
                () -> new ProxyIdResolver(file, env(Map.of())).resolve("${cloudnet.service}"));

        assertTrue(e.getMessage().contains("${cloudnet.service}"), e.getMessage());
        assertFalse(e.getMessage().contains(SECRET), "message must not leak the connectionKey");
    }

    @Test
    void rejectsNonIntegerTaskServiceIdWithoutLeakingConnectionKey() throws IOException {
        Path file = wrapper(WRAPPER_JSON.replace("\"taskServiceId\": 1,", "\"taskServiceId\": 1.5,"));
        ProxyIdException e = assertThrows(ProxyIdException.class,
                () -> new ProxyIdResolver(file, env(Map.of())).resolve("${cloudnet.service}"));

        assertTrue(e.getMessage().contains("${cloudnet.service}"), "message names the placeholder: " + e.getMessage());
        assertFalse(e.getMessage().contains(SECRET), "message must not leak the connectionKey");
        assertEquals(null, e.getCause(), "no cause that could carry file content");
    }

    @Test
    void missingEnvironmentVariableNamesThePlaceholderAndFails() throws IOException {
        ProxyIdException e = assertThrows(ProxyIdException.class,
                () -> resolver(Map.of()).resolve("${env:PROXY_NAME}"));

        assertTrue(e.getMessage().contains("${env:PROXY_NAME}"), "message names the placeholder: " + e.getMessage());
    }

    @Test
    void blankEnvironmentVariableCountsAsMissing() throws IOException {
        assertThrows(ProxyIdException.class, () -> resolver(Map.of("PROXY_NAME", "  ")).resolve("${env:PROXY_NAME}"));
    }

    @Test
    void unknownPlaceholderFails() throws IOException {
        ProxyIdException e = assertThrows(ProxyIdException.class, () -> resolver(Map.of()).resolve("${nope}"));

        assertTrue(e.getMessage().contains("${nope}"), e.getMessage());
    }

    @Test
    void unterminatedPlaceholderFails() throws IOException {
        assertThrows(ProxyIdException.class, () -> resolver(Map.of()).resolve("olf-${cloudnet.service"));
    }

    @Test
    void emptyIdFails() {
        assertThrows(ProxyIdException.class, () -> resolverWithoutWrapper(Map.of()).resolve(""));
    }

    @Test
    void dotInTheResolvedIdFailsBecauseItSplitsTheSubject() {
        ProxyIdException e = assertThrows(ProxyIdException.class,
                () -> resolverWithoutWrapper(Map.of("N", "a.b")).resolve("${env:N}"));

        assertTrue(e.getMessage().contains("${env:N}"), "message names the source: " + e.getMessage());
    }

    @Test
    void wildcardWhitespaceAndControlCharactersAreRejected() {
        for (String bad : new String[] {"a*b", "a>b", "a b", "a\tb", "a\nb"}) {
            assertThrows(ProxyIdException.class, () -> resolverWithoutWrapper(Map.of()).resolve(bad),
                    "invalid id must be rejected: " + bad.replace("\n", "\\n").replace("\t", "\\t"));
        }
    }

    @Test
    void configParseResolvesTheProxyId() throws IOException {
        CorvusConfig config = CorvusConfig.parse("proxy_id = \"olf-${cloudnet.service}\"\n", resolver(Map.of()));

        assertEquals("olf-Proxy-Corvus-1", config.proxyId(), "resolved at config load");
    }
}
