package net.onelitefeather.corvus.plugin.privacy;

import java.util.Locale;

/** Configured scope of raw payload recording ({@code capture.raw_payloads}). */
public enum PayloadMode {
    /** No payload bytes at all. */
    NONE,
    /** Payloads of serverbound play packets (minus text-bearing types) and of allowlisted motion packets. */
    SERVERBOUND,
    /** Every payload except privacy-dropped and always-stripped types. Local debugging only. */
    ALL;

    public static final PayloadMode DEFAULT = SERVERBOUND;

    /**
     * Parses the raw TOML value. Accepts the strings {@code none|serverbound|all} (case-insensitive) and, for
     * backward compatibility, the booleans {@code true} (serverbound) and {@code false} (none). {@code null}
     * yields the default.
     */
    public static PayloadMode parse(Object raw) {
        if (raw == null) {
            return DEFAULT;
        }
        if (raw instanceof Boolean b) {
            return b ? SERVERBOUND : NONE;
        }
        String text = String.valueOf(raw).trim().toLowerCase(Locale.ROOT);
        return switch (text) {
            case "none" -> NONE;
            case "serverbound" -> SERVERBOUND;
            case "all" -> ALL;
            default -> throw new IllegalArgumentException(
                    "capture.raw_payloads must be 'none', 'serverbound' or 'all' (or true/false), got: " + raw);
        };
    }
}
