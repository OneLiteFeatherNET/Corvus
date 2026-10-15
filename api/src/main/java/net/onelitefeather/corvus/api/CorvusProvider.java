package net.onelitefeather.corvus.api;

import java.util.Objects;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Static access point to the {@link CorvusApi} of the running Corvus plugin. Velocity has no service registry,
 * so this holder is the lookup. Declare a dependency on the plugin id {@code corvus} so it is loaded first.
 *
 * <p>Corvus scores are decision aids; see {@link CorvusApi}.
 */
public final class CorvusProvider {

    private static final AtomicReference<CorvusApi> INSTANCE = new AtomicReference<>();

    private CorvusProvider() {
    }

    /**
     * The API of the running Corvus plugin.
     *
     * @throws IllegalStateException if Corvus is not loaded or not started yet
     */
    public static CorvusApi get() {
        CorvusApi api = INSTANCE.get();
        if (api == null) {
            throw new IllegalStateException("Corvus is not available (plugin not loaded or not started yet)");
        }
        return api;
    }

    /** Like {@link #get()} but empty instead of throwing. */
    public static Optional<CorvusApi> find() {
        return Optional.ofNullable(INSTANCE.get());
    }

    /** Called by the Corvus plugin on start. Other plugins must not call this. */
    public static void register(CorvusApi api) {
        INSTANCE.set(Objects.requireNonNull(api, "api"));
    }

    /** Called by the Corvus plugin on shutdown (and by tests); only removes {@code api} if it is registered. */
    public static void unregister(CorvusApi api) {
        INSTANCE.compareAndSet(api, null);
    }
}
