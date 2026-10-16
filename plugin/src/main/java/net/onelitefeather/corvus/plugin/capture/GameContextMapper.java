package net.onelitefeather.corvus.plugin.capture;

import java.util.Locale;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * Maps backend server names to game-context tags. Lookup order: exact (lower-cased) name, then the name without one
 * trailing CloudNet instance suffix {@code -<digits>} (when enabled), then the default.
 */
public final class GameContextMapper {

    private static final Pattern INSTANCE_SUFFIX = Pattern.compile("-\\d+$");

    private final Map<String, String> servers;
    private final String defaultContext;
    private final boolean stripInstanceSuffix;

    public GameContextMapper(Map<String, String> servers, String defaultContext) {
        this(servers, defaultContext, true);
    }

    public GameContextMapper(Map<String, String> servers, String defaultContext, boolean stripInstanceSuffix) {
        this.servers = Map.copyOf(servers);
        this.defaultContext = defaultContext;
        this.stripInstanceSuffix = stripInstanceSuffix;
    }

    public String contextFor(String serverName) {
        if (serverName == null) {
            return defaultContext;
        }
        String name = serverName.toLowerCase(Locale.ROOT);
        String exact = servers.get(name);
        if (exact != null) {
            return exact;
        }
        if (stripInstanceSuffix) {
            String task = INSTANCE_SUFFIX.matcher(name).replaceFirst("");
            if (!task.equals(name) && !task.isEmpty()) {
                return servers.getOrDefault(task, defaultContext);
            }
        }
        return defaultContext;
    }
}
