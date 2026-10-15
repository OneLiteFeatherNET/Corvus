package net.onelitefeather.corvus.plugin.time;

/** Monotonic time source. All logic goes through this port; only {@link #system()} touches the JVM clock. */
@FunctionalInterface
public interface NanoClock {

    long nanoTime();

    static NanoClock system() {
        return System::nanoTime;
    }
}
