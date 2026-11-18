package net.onelitefeather.corvus.plugin.capture;

/**
 * The game context and stage of one backend connection. {@code stage} is "production", "staging" or empty when the
 * orchestrator does not know the server.
 */
public record BackendContext(String gameContext, String stage) {
}
