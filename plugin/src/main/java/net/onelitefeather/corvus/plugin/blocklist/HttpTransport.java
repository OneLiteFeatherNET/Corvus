package net.onelitefeather.corvus.plugin.blocklist;

import java.io.IOException;
import java.net.URI;
import java.time.Duration;
import java.util.Map;

/** Port for a blocking HTTP GET; the production adapter is {@link JdkHttpTransport}, tests use a fake. */
public interface HttpTransport {

    record Response(int status, String body) {
    }

    Response get(URI uri, Map<String, String> headers, Duration timeout) throws IOException;
}
