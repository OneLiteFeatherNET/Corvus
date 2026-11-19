package net.onelitefeather.corvus.plugin.cloudnet;

import net.onelitefeather.corvus.plugin.capture.CachedServiceMetadataSource;
import net.onelitefeather.corvus.plugin.capture.ServiceMetadataSource;

import java.lang.reflect.InvocationTargetException;
import java.time.Duration;
import java.time.InstantSource;
import java.util.function.Consumer;

/**
 * Chooses the service metadata source at startup. CloudNet is optional: without its classes on the class path (the
 * pilot runs without it) the source is {@link ServiceMetadataSource#NONE}. The CloudNet classes are compiled against
 * but never shaded into the plugin jar, so the implementation is only instantiated by reflection here.
 */
public final class CloudNetAvailability {

    /** Answers are reused this long; a server that CloudNet does not know is looked up once per interval. */
    public static final Duration CACHE_TTL = Duration.ofSeconds(30);

    static final String DRIVER_MARKER = "eu.cloudnetservice.driver.registry.ServiceRegistry";
    static final String SOURCE_CLASS = "net.onelitefeather.corvus.plugin.cloudnet.CloudNetServiceMetadataSource";

    private CloudNetAvailability() {
    }

    /** @return {@code true} when the CloudNet driver classes are visible to the given class loader */
    public static boolean present(ClassLoader loader) {
        try {
            Class.forName(DRIVER_MARKER, false, loader);
            return true;
        } catch (ClassNotFoundException | LinkageError e) {
            return false;
        }
    }

    /**
     * @return a cached CloudNet source when CloudNet is present and usable, otherwise {@link ServiceMetadataSource#NONE}.
     * Never throws.
     */
    public static ServiceMetadataSource load(ClassLoader loader, InstantSource clock, Consumer<String> info,
                                             Consumer<String> warn) {
        if (!present(loader)) {
            info.accept("CloudNet is not on the class path: game context comes from [game_context.servers] only");
            return ServiceMetadataSource.NONE;
        }
        try {
            Class<?> type = Class.forName(SOURCE_CLASS, true, loader);
            ServiceMetadataSource source = (ServiceMetadataSource) type.getDeclaredConstructor().newInstance();
            info.accept("CloudNet detected: game context and stage fall back to CloudNet service metadata");
            return new CachedServiceMetadataSource(source, clock, CACHE_TTL, warn);
        } catch (Throwable t) {
            warn.accept("CloudNet is on the class path but unusable (" + describe(t)
                    + "); game context comes from [game_context.servers] only");
            return ServiceMetadataSource.NONE;
        }
    }

    /**
     * Names the failure for the log. Reflection wraps constructor failures in an {@link InvocationTargetException}, so
     * the reason is the root cause of the thrown exception: its class name and message.
     */
    static String describe(Throwable t) {
        Throwable cause = t;
        if (t instanceof InvocationTargetException ite && ite.getTargetException() != null) {
            cause = ite.getTargetException();
        }
        while (cause.getCause() != null && cause.getCause() != cause) {
            cause = cause.getCause();
        }
        return cause.getMessage() == null
                ? cause.getClass().getName()
                : cause.getClass().getName() + ": " + cause.getMessage();
    }
}
