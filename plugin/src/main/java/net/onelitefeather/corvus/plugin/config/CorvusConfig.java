package net.onelitefeather.corvus.plugin.config;

import com.moandjiezana.toml.Toml;

import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** Immutable plugin configuration, parsed from {@code plugins/corvus/config.toml}. */
public record CorvusConfig(
        String proxyId,
        String keyFile,
        String keyId,
        ListMode listMode,
        Set<UUID> allowlist,
        Set<UUID> blocklist,
        boolean rawPayloads,
        int maxPayloadBytes,
        long queueMaxBytes,
        int batchSize,
        long flushIntervalMillis,
        String defaultGameContext,
        Map<String, String> serverContexts) {

    public enum ListMode { ALL, ALLOWLIST }

    public static final String DEFAULT_TOML = """
            # Corvus capture plugin. The HMAC pseudonym key is read from the environment variable
            # CORVUS_PSEUDONYM_KEY (base64) or from key_file. Without a key nothing is captured.
            proxy_id = "proxy-1"

            [pseudonym]
            # Path to a file containing the base64 key (used when the environment variable is unset).
            key_file = ""
            key_id = "k1"

            [players]
            # "all" captures everybody except the blocklist; "allowlist" only captures the allowlist.
            mode = "all"
            allowlist = []
            blocklist = []

            [capture]
            raw_payloads = true
            # Payloads longer than this are truncated (chunk data etc. are not useful and expensive).
            max_payload_bytes = 2048
            # Upper bound of the in-memory queue between the Netty threads and the writer thread.
            queue_max_bytes = 67108864
            batch_size = 200
            flush_interval_seconds = 5

            [game_context]
            default = "unknown"

            # backend server name = game context tag
            [game_context.servers]
            lobby = "lobby"
            pvp = "pvp"
            """;

    public CorvusConfig {
        allowlist = Set.copyOf(allowlist);
        blocklist = Set.copyOf(blocklist);
        serverContexts = Map.copyOf(serverContexts);
    }

    public static CorvusConfig parse(String tomlText) {
        Toml toml = new Toml().read(tomlText);
        String mode = toml.getString("players.mode", "all").toLowerCase(Locale.ROOT);
        ListMode listMode = switch (mode) {
            case "all" -> ListMode.ALL;
            case "allowlist" -> ListMode.ALLOWLIST;
            default -> throw new IllegalArgumentException("players.mode must be 'all' or 'allowlist', got: " + mode);
        };
        Map<String, String> contexts = new HashMap<>();
        Toml servers = toml.getTable("game_context.servers");
        if (servers != null) {
            for (Map.Entry<String, Object> e : servers.entrySet()) {
                contexts.put(e.getKey().toLowerCase(Locale.ROOT), String.valueOf(e.getValue()));
            }
        }
        int maxPayload = (int) Math.min(Integer.MAX_VALUE, toml.getLong("capture.max_payload_bytes", 2048L));
        return new CorvusConfig(
                toml.getString("proxy_id", "proxy-1"),
                toml.getString("pseudonym.key_file", ""),
                toml.getString("pseudonym.key_id", "k1"),
                listMode,
                uuids(toml.getList("players.allowlist")),
                uuids(toml.getList("players.blocklist")),
                toml.getBoolean("capture.raw_payloads", true),
                Math.max(0, maxPayload),
                Math.max(1024L, toml.getLong("capture.queue_max_bytes", 64L * 1024 * 1024)),
                (int) Math.max(1L, toml.getLong("capture.batch_size", 200L)),
                Math.max(100L, toml.getLong("capture.flush_interval_seconds", 5L) * 1000L),
                toml.getString("game_context.default", "unknown"),
                contexts);
    }

    private static Set<UUID> uuids(List<Object> raw) {
        Set<UUID> out = new HashSet<>();
        if (raw != null) {
            for (Object o : raw) {
                out.add(UUID.fromString(String.valueOf(o)));
            }
        }
        return out;
    }
}
