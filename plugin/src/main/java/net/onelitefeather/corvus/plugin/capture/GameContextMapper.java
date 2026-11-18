package net.onelitefeather.corvus.plugin.capture;

import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.regex.Pattern;

/**
 * Maps backend servers to game-context tags and stages. Resolution order:
 * <ol>
 *   <li>the explicit entry for the server name: exact (lower-cased) name, then the name without one trailing
 *       CloudNet instance suffix {@code -<digits>} (when enabled)</li>
 *   <li>the explicit entry for the CloudNet task name</li>
 *   <li>the CloudNet task name, lower-cased, without a trailing {@value #STAGING_SUFFIX}</li>
 *   <li>the configured default</li>
 * </ol>
 * Steps 2 and 3 need CloudNet metadata; without it the mapper is the plain server-name list.
 */
public final class GameContextMapper {

    private static final Pattern INSTANCE_SUFFIX = Pattern.compile("-\\d+$");

    /** Task names ending with this (case-insensitive) are staging services. Fixed; not configurable. */
    static final String STAGING_SUFFIX = "-staging";
    /** A CloudNet group with this name (case-insensitive) marks staging services. Fixed; not configurable. */
    static final String STAGING_GROUP = "Staging";

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

    /** The list-only resolution (no orchestrator metadata). */
    public String contextFor(String serverName) {
        String context = explicitContext(serverName);
        return context == null ? defaultContext : context;
    }

    /**
     * Resolves the game context and stage of one backend connection.
     *
     * @param metadata the orchestrator's view of the server, empty when unknown
     */
    public BackendContext resolve(String serverName, Optional<ServiceMetadata> metadata) {
        ServiceMetadata meta = metadata == null ? null : metadata.orElse(null);
        String context = explicitContext(serverName);
        if (context == null && meta != null) {
            context = contextForTask(meta.task());
        }
        return new BackendContext(context == null ? defaultContext : context, stageOf(meta));
    }

    /** @return the explicit entry for the server name, or {@code null} when the list has none */
    private String explicitContext(String serverName) {
        if (serverName == null) {
            return null;
        }
        String name = serverName.toLowerCase(Locale.ROOT);
        String exact = servers.get(name);
        if (exact != null) {
            return exact;
        }
        if (stripInstanceSuffix) {
            String task = INSTANCE_SUFFIX.matcher(name).replaceFirst("");
            if (!task.equals(name) && !task.isEmpty()) {
                return servers.get(task);
            }
        }
        return null;
    }

    /** @return the explicit entry for the task, else the task without a staging suffix, or {@code null} */
    private String contextForTask(String task) {
        if (task == null || task.isEmpty()) {
            return null;
        }
        String name = task.toLowerCase(Locale.ROOT);
        String explicit = servers.get(name);
        if (explicit != null) {
            return explicit;
        }
        String base = name.endsWith(STAGING_SUFFIX) ? name.substring(0, name.length() - STAGING_SUFFIX.length()) : name;
        return base.isEmpty() ? null : base;
    }

    /** "staging" for a staging task or group, "production" for any other known service, empty when unknown. */
    static String stageOf(ServiceMetadata meta) {
        if (meta == null) {
            return "";
        }
        boolean staging = meta.task().toLowerCase(Locale.ROOT).endsWith(STAGING_SUFFIX)
                || meta.groups().stream().anyMatch(STAGING_GROUP::equalsIgnoreCase);
        return staging ? "staging" : "production";
    }
}
