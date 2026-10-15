package net.onelitefeather.corvus.plugin.blocklist;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;

/** {@link HttpTransport} on {@code java.net.http.HttpClient}. Only call it from the dedicated sync thread. */
public final class JdkHttpTransport implements HttpTransport {

    private final HttpClient client;

    public JdkHttpTransport(Duration connectTimeout) {
        this.client = HttpClient.newBuilder().connectTimeout(connectTimeout)
                .followRedirects(HttpClient.Redirect.NEVER).build();
    }

    @Override
    public Response get(URI uri, Map<String, String> headers, Duration timeout) throws IOException {
        HttpRequest.Builder request = HttpRequest.newBuilder(uri).timeout(timeout).GET();
        headers.forEach(request::header);
        try {
            HttpResponse<String> response = client.send(request.build(), HttpResponse.BodyHandlers.ofString());
            return new Response(response.statusCode(), response.body());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("interrupted", e);
        }
    }
}
