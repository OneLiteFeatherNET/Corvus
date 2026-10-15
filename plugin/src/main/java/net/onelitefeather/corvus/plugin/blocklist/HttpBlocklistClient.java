package net.onelitefeather.corvus.plugin.blocklist;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonParseException;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.net.URI;
import java.time.Duration;
import java.util.HashSet;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * {@code GET <base>/api/v1/plugin/blocklist} with {@code Authorization: Bearer <token>}. The feed is one document
 * {@code {"version", "blocked": [hex...], "allowed": [hex...]}} and is not paged. Only {@code blocked} is used.
 */
public final class HttpBlocklistClient implements BlocklistClient {

    public static final String PATH = "/api/v1/plugin/blocklist";

    private final URI uri;
    private final String token;
    private final Duration timeout;
    private final HttpTransport transport;

    public HttpBlocklistClient(String baseUrl, String token, Duration timeout, HttpTransport transport) {
        String base = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.uri = URI.create(base + PATH);
        this.token = token;
        this.timeout = timeout;
        this.transport = transport;
    }

    @Override
    public Set<String> fetchBlocked() throws IOException {
        HttpTransport.Response response = transport.get(uri,
                Map.of("Authorization", "Bearer " + token, "Accept", "application/json"), timeout);
        if (response.status() != 200) {
            throw new IOException("blocklist feed answered HTTP " + response.status());
        }
        JsonElement blocked;
        try {
            blocked = JsonParser.parseString(response.body()).getAsJsonObject().get("blocked");
        } catch (JsonParseException | IllegalStateException e) {
            throw new IOException("blocklist feed is not a JSON object", e);
        }
        if (blocked == null || !blocked.isJsonArray()) {
            throw new IOException("blocklist feed has no 'blocked' array");
        }
        JsonArray array = blocked.getAsJsonArray();
        Set<String> out = new HashSet<>(Math.max(16, array.size() * 2));
        for (JsonElement e : array) {
            if (!e.isJsonPrimitive()) {
                throw new IOException("blocklist feed has a non-string entry");
            }
            String hex = e.getAsString().trim().toLowerCase(Locale.ROOT);
            if (!hex.isEmpty()) {
                out.add(hex);
            }
        }
        return out;
    }
}
