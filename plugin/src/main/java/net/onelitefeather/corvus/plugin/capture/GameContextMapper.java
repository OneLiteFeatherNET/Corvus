package net.onelitefeather.corvus.plugin.capture;

import java.util.Locale;
import java.util.Map;

/** Maps backend server names to game-context tags. */
public final class GameContextMapper {

    private final Map<String, String> servers;
    private final String defaultContext;

    public GameContextMapper(Map<String, String> servers, String defaultContext) {
        this.servers = Map.copyOf(servers);
        this.defaultContext = defaultContext;
    }

    public String contextFor(String serverName) {
        if (serverName == null) {
            return defaultContext;
        }
        return servers.getOrDefault(serverName.toLowerCase(Locale.ROOT), defaultContext);
    }
}
