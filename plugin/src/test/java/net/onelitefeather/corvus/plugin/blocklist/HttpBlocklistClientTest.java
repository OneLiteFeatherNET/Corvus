package net.onelitefeather.corvus.plugin.blocklist;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.URI;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class HttpBlocklistClientTest {

    /** Records requests and answers with a canned response; no network. */
    private static final class FakeTransport implements HttpTransport {
        final List<URI> uris = new ArrayList<>();
        final List<Map<String, String>> headers = new ArrayList<>();
        Response response = new Response(200, "{\"version\":\"v\",\"blocked\":[],\"allowed\":[]}");

        @Override
        public Response get(URI uri, Map<String, String> headers, Duration timeout) {
            uris.add(uri);
            this.headers.add(headers);
            return response;
        }
    }

    private final FakeTransport transport = new FakeTransport();

    private HttpBlocklistClient client(String base) {
        return new HttpBlocklistClient(base, "secret-token", Duration.ofSeconds(3), transport);
    }

    @Test
    void sendsTheBearerTokenToTheFeedEndpoint() throws IOException {
        client("https://api.example/").fetchBlocked();

        assertEquals(URI.create("https://api.example/api/v1/plugin/blocklist"), transport.uris.get(0));
        assertEquals("Bearer secret-token", transport.headers.get(0).get("Authorization"));
    }

    @Test
    void parsesTheBlockedArrayAndIgnoresAllowed() throws IOException {
        transport.response = new HttpTransport.Response(200,
                "{\"version\":\"v\",\"blocked\":[\"AB12\",\"cd34\"],\"allowed\":[\"ef56\"]}");

        Set<String> blocked = client("https://api.example").fetchBlocked();

        assertEquals(Set.of("ab12", "cd34"), blocked, "entries are lower-cased, allowed is not used");
    }

    @Test
    void anErrorStatusIsAFailure() {
        transport.response = new HttpTransport.Response(401, "{}");

        IOException e = assertThrows(IOException.class, () -> client("https://api.example").fetchBlocked());

        assertEquals("blocklist feed answered HTTP 401", e.getMessage());
    }

    @Test
    void aMalformedBodyIsAFailure() {
        transport.response = new HttpTransport.Response(200, "<html>proxy error</html>");

        assertThrows(IOException.class, () -> client("https://api.example").fetchBlocked());
    }

    @Test
    void aBodyWithoutBlockedArrayIsAFailure() {
        transport.response = new HttpTransport.Response(200, "{\"version\":\"v\"}");

        assertThrows(IOException.class, () -> client("https://api.example").fetchBlocked());
    }
}
