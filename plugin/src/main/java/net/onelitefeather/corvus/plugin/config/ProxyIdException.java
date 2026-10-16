package net.onelitefeather.corvus.plugin.config;

/**
 * {@code proxy_id} could not be resolved or is not usable in a NATS subject. The message names the placeholder (or the
 * value problem) and never carries file contents; there is deliberately no cause.
 */
public final class ProxyIdException extends IllegalArgumentException {

    public ProxyIdException(String message) {
        super(message);
    }
}
