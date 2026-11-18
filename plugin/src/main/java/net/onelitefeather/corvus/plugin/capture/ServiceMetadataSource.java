package net.onelitefeather.corvus.plugin.capture;

import java.util.Optional;

/**
 * Looks up the orchestrator's view of a backend server by its Velocity server name. Implementations must never
 * throw and should answer from memory: this is called on the proxy's connect path.
 */
@FunctionalInterface
public interface ServiceMetadataSource {

    /** Used when no orchestrator is available: every server is unknown. */
    ServiceMetadataSource NONE = serverName -> Optional.empty();

    Optional<ServiceMetadata> lookup(String serverName);
}
