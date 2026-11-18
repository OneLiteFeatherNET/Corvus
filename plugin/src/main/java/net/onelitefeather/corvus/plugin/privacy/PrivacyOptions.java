package net.onelitefeather.corvus.plugin.privacy;

import java.util.Locale;

/**
 * Operator choices about text and name bearing packets ({@code capture.text_packets} and
 * {@code capture.redact_names}). The defaults keep the complete raw stream: the data lake stores it in plaintext.
 *
 * @param textPackets {@link TextPackets#FULL} captures chat, commands, signing, player lists and chat session
 *                    frames; {@link TextPackets#DROP} drops them before any byte is copied
 * @param redactNames {@code true} strips the payload of packets that carry names, UUIDs or free text (the frame is
 *                    still recorded); {@code false} records it
 */
public record PrivacyOptions(TextPackets textPackets, boolean redactNames) {

    /** Capture default: the complete raw stream, as the plugin records it. */
    public static final PrivacyOptions DEFAULT = new PrivacyOptions(TextPackets.FULL, false);

    /**
     * Policy of the committed vocabulary export and of the analyzers (what models may train on). It does not depend
     * on what the plugin captures: text packets are private and names are stripped.
     */
    public static final PrivacyOptions ANALYSIS = new PrivacyOptions(TextPackets.DROP, true);

    public enum TextPackets {
        FULL, DROP;

        /** Parses {@code full|drop} (case-insensitive). {@code null} yields {@link #FULL}. */
        public static TextPackets parse(Object raw) {
            if (raw == null) {
                return FULL;
            }
            String text = String.valueOf(raw).trim().toLowerCase(Locale.ROOT);
            return switch (text) {
                case "full" -> FULL;
                case "drop" -> DROP;
                default -> throw new IllegalArgumentException(
                        "capture.text_packets must be 'full' or 'drop', got: " + raw);
            };
        }
    }
}
