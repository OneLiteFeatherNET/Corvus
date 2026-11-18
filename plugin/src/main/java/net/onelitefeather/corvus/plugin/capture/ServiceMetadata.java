package net.onelitefeather.corvus.plugin.capture;

import java.util.List;
import java.util.Objects;

/**
 * What the orchestrator knows about a backend server: task, groups, node and environment of the service. Built by a
 * {@link ServiceMetadataSource}; the plugin never depends on the orchestrator's classes outside that source.
 */
public record ServiceMetadata(String task, List<String> groups, String node, String environment) {

    public ServiceMetadata {
        task = Objects.requireNonNullElse(task, "");
        groups = groups == null ? List.of() : groups.stream().filter(Objects::nonNull).toList();
        node = Objects.requireNonNullElse(node, "");
        environment = Objects.requireNonNullElse(environment, "");
    }
}
