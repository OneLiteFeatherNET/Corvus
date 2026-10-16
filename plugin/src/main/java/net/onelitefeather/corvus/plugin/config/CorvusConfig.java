package net.onelitefeather.corvus.plugin.config;

import com.moandjiezana.toml.Toml;
import net.onelitefeather.corvus.plugin.privacy.PayloadMode;

import java.util.ArrayList;
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
        PayloadMode payloadMode,
        int maxPayloadBytes,
        long queueMaxBytes,
        int batchSize,
        long flushIntervalMillis,
        String defaultGameContext,
        Map<String, String> serverContexts,
        boolean stripInstanceSuffix,
        SinkType sinkType,
        Nats nats,
        Scores scores,
        BlocklistSync blocklistSync,
        Tls tls) {

    public enum ListMode { ALL, ALLOWLIST }

    public enum SinkType { FILE, NATS }

    /** Settings of the NATS JetStream sink; only used when {@code sink.type = "nats"}. */
    public record Nats(
            List<String> urls,
            String credsFile,
            String subjectPrefix,
            long maxInFlightBytes,
            long publishTimeoutMillis,
            long reconnectBufferBytes,
            int partitions) {

        public static final String DEFAULT_SUBJECT_PREFIX = "corvus.capture.v1";
        public static final int DEFAULT_PARTITIONS = 16;

        public Nats {
            urls = List.copyOf(urls);
        }
    }

    /** Settings of the online score subscription; connection settings come from {@link Nats}. */
    public record Scores(boolean enabled, String subject, int listenerThreads, int listenerQueueSize) {

        public static final String DEFAULT_SUBJECT = "corvus.score.v1.>";
    }

    /**
     * Optional periodic fetch of the blocked pseudonyms from the control plane
     * ({@code GET <url>/api/v1/plugin/blocklist}). The token comes from CORVUS_PLUGIN_API_TOKEN or token_file.
     */
    public record BlocklistSync(boolean enabled, String url, String tokenFile, int intervalSeconds,
                                int timeoutSeconds) {

        public static final int DEFAULT_INTERVAL_SECONDS = 300;
        public static final int DEFAULT_TIMEOUT_SECONDS = 10;
    }

    /**
     * Extra trust for the NATS and control-plane connections: a PEM CA bundle that is trusted in addition to the
     * JVM default CAs. Empty = JVM default only. Relative paths are resolved against the plugin directory.
     */
    public record Tls(String caFile) {
    }

    public static final String DEFAULT_TOML = """
            # Corvus capture plugin. The HMAC pseudonym key is read from the environment variable
            # CORVUS_PSEUDONYM_KEY (base64) or from key_file. Without a key nothing is captured.
            #
            # proxy_id names this proxy in the NATS subject corvus.capture.v1.<proxy_id>.<partition>, so it must be one
            # subject token: not empty, no ".", "*", ">" or whitespace. It may contain placeholders, resolved once
            # at startup:
            #   ${cloudnet.service}  CloudNet service name, <task><splitter><number> (Proxy-1), read from
            #                        .wrapper/wrapper.json in the service working directory
            #   ${cloudnet.task}     CloudNet task name (Proxy)
            #   ${env:NAME}          value of the environment variable NAME
            # Placeholders may be mixed with text, e.g. "olf-${cloudnet.service}". All proxies of a CloudNet task share
            # one template, so use ${cloudnet.service} there. If a placeholder cannot be resolved or the result is
            # invalid, the plugin logs an ERROR and does not capture.
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
            # Raw packet bytes to store: "none", "serverbound" or "all" (true = serverbound, false = none).
            # "serverbound": serverbound play packets except text-bearing ones (signs, books, anvil names,
            # plugin messages, command blocks...) plus a few clientbound motion packets. Other clientbound
            # packets carry names and UUIDs of other players and never keep their payload.
            # "all" is for local debugging only and logs a warning at startup.
            raw_payloads = "serverbound"
            # Payloads longer than this are truncated (chunk data etc. are not useful and expensive).
            max_payload_bytes = 2048
            # Upper bound of the in-memory queue between the Netty threads and the writer thread.
            queue_max_bytes = 67108864
            batch_size = 200
            flush_interval_seconds = 5

            [sink]
            # "file" writes captures/<date>/<session>.cvc below the plugin directory (pilot).
            # "nats" publishes every envelope to NATS JetStream (see [nats]); the stream is owned by ops.
            type = "file"

            [nats]
            # Required when sink.type = "nats": one URL or a list. Connects in the background; an outage never
            # delays the proxy.
            url = ["nats://localhost:4222"]
            # Optional NATS credentials file (.creds); empty = no authentication.
            creds_file = ""
            # Envelopes go to <subject_prefix>.<proxy_id>.<partition>.
            subject_prefix = "corvus.capture.v1"
            # Number of partitions: first 4 bytes of the session id (big-endian, unsigned) mod partitions, written
            # with at least two digits (00..15). Must be identical on all proxies and match the scorers.
            partitions = 16
            # Unacknowledged bytes allowed in flight; further batches are dropped (dropped_transport).
            max_in_flight_bytes = 33554432
            # A publish without an acknowledgement after this long counts as failed.
            publish_timeout_seconds = 5
            # Bytes jnats buffers while reconnecting; beyond this publishes fail.
            reconnect_buffer_bytes = 8388608

            [scores]
            # Subscribe to the scores of the scoring service and expose them through the corvus-api. Defaults to
            # true when sink.type = "nats" and false otherwise; set it explicitly to override. Needs [nats] url
            # and the pseudonym key (scores are mapped to online players through their pseudonym).
            # enabled = true
            subject = "corvus.score.v1.>"
            # Worker threads and queue size (updates) for score listeners of other plugins; a full queue drops updates.
            listener_threads = 2
            listener_queue = 1024

            [blocklist_sync]
            # Fetch the blocklist of the control plane (GET <url>/api/v1/plugin/blocklist, proxy API token) and
            # never capture the listed pseudonyms. On failure the last good list is kept.
            enabled = false
            # API base URL, for example "https://corvus-api.example". Required when enabled.
            url = ""
            # Token: environment variable CORVUS_PLUGIN_API_TOKEN, or this file (relative to the plugin directory).
            token_file = ""
            interval_seconds = 300
            timeout_seconds = 10

            [tls]
            # Optional PEM file with extra CA certificates (for example a private step-ca) trusted IN ADDITION to
            # the JVM default CAs for [nats] (tls:// and wss://) and [blocklist_sync] (https://). Relative to the
            # plugin directory, or absolute. Empty = JVM default CAs only. The JVM truststore is not changed.
            # If the file cannot be read, the NATS sink, scores and blocklist sync are not started (ERROR logged).
            ca_file = ""

            [game_context]
            default = "unknown"
            # Backends that are CloudNet services are named <Task>-<n> (Lobby-1, Lobby-2, Team-Build-1). Lookup order:
            # 1. the exact server name, 2. the name without ONE trailing "-<digits>" (Lobby-2 -> lobby,
            # Team-Build-1 -> team-build), 3. default. Names are matched case-insensitively. Set to false to
            # match exact server names only.
            strip_instance_suffix = true

            # server name (or CloudNet task name) = game context tag
            [game_context.servers]
            lobby = "lobby"
            pvp = "pvp"
            """;

    public CorvusConfig {
        allowlist = Set.copyOf(allowlist);
        blocklist = Set.copyOf(blocklist);
        serverContexts = Map.copyOf(serverContexts);
    }

    /** Parses with the real environment and {@code .wrapper/wrapper.json} for {@code proxy_id} placeholders. */
    public static CorvusConfig parse(String tomlText) {
        return parse(tomlText, ProxyIdResolver.system());
    }

    /**
     * @throws ProxyIdException when {@code proxy_id} has a placeholder that cannot be resolved or the result is not
     *                          a valid NATS subject token
     */
    public static CorvusConfig parse(String tomlText, ProxyIdResolver proxyIds) {
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
        String sink = toml.getString("sink.type", "file").toLowerCase(Locale.ROOT);
        SinkType sinkType = switch (sink) {
            case "file" -> SinkType.FILE;
            case "nats" -> SinkType.NATS;
            default -> throw new IllegalArgumentException("sink.type must be 'file' or 'nats', got: " + sink);
        };
        int maxPayload = (int) Math.min(Integer.MAX_VALUE, toml.getLong("capture.max_payload_bytes", 2048L));
        return new CorvusConfig(
                proxyIds.resolve(toml.getString("proxy_id", "proxy-1")),
                toml.getString("pseudonym.key_file", ""),
                toml.getString("pseudonym.key_id", "k1"),
                listMode,
                uuids(toml.getList("players.allowlist")),
                uuids(toml.getList("players.blocklist")),
                PayloadMode.parse(rawPayloadsValue(toml)),
                Math.max(0, maxPayload),
                Math.max(1024L, toml.getLong("capture.queue_max_bytes", 64L * 1024 * 1024)),
                (int) Math.max(1L, toml.getLong("capture.batch_size", 200L)),
                Math.max(100L, toml.getLong("capture.flush_interval_seconds", 5L) * 1000L),
                toml.getString("game_context.default", "unknown"),
                contexts,
                toml.getBoolean("game_context.strip_instance_suffix", true),
                sinkType,
                parseNats(toml, sinkType),
                parseScores(toml, sinkType),
                parseBlocklistSync(toml),
                new Tls(toml.getString("tls.ca_file", "").trim()));
    }

    private static BlocklistSync parseBlocklistSync(Toml toml) {
        boolean enabled = toml.getBoolean("blocklist_sync.enabled", false);
        String url = toml.getString("blocklist_sync.url", "").trim();
        if (enabled && url.isEmpty()) {
            throw new IllegalArgumentException("blocklist_sync.enabled = true requires blocklist_sync.url");
        }
        return new BlocklistSync(
                enabled,
                url,
                toml.getString("blocklist_sync.token_file", "").trim(),
                (int) Math.min(86_400L, Math.max(5L, toml.getLong("blocklist_sync.interval_seconds",
                        (long) BlocklistSync.DEFAULT_INTERVAL_SECONDS))),
                (int) Math.min(120L, Math.max(1L, toml.getLong("blocklist_sync.timeout_seconds",
                        (long) BlocklistSync.DEFAULT_TIMEOUT_SECONDS))));
    }

    private static Scores parseScores(Toml toml, SinkType sinkType) {
        Toml scores = toml.getTable("scores");
        Object rawEnabled = scores == null ? null : scores.toMap().get("enabled");
        boolean enabled = rawEnabled instanceof Boolean b ? b : sinkType == SinkType.NATS;
        String subject = toml.getString("scores.subject", Scores.DEFAULT_SUBJECT).trim();
        return new Scores(
                enabled,
                subject.isEmpty() ? Scores.DEFAULT_SUBJECT : subject,
                (int) Math.min(16L, Math.max(1L, toml.getLong("scores.listener_threads", 2L))),
                (int) Math.min(1_000_000L, Math.max(1L, toml.getLong("scores.listener_queue", 1024L))));
    }

    private static Nats parseNats(Toml toml, SinkType sinkType) {
        List<String> urls = new ArrayList<>();
        Toml nats = toml.getTable("nats");
        Object rawUrl = nats == null ? null : nats.toMap().get("url");
        if (rawUrl instanceof List<?> list) {
            for (Object o : list) {
                urls.add(String.valueOf(o).trim());
            }
        } else if (rawUrl != null) {
            urls.add(String.valueOf(rawUrl).trim());
        }
        urls.removeIf(String::isEmpty);
        Object rawScores = toml.getTable("scores") == null ? null : toml.getTable("scores").toMap().get("enabled");
        boolean scoresOn = rawScores instanceof Boolean b && b;
        if (urls.isEmpty() && (sinkType == SinkType.NATS || scoresOn)) {
            throw new IllegalArgumentException(
                    "sink.type = \"nats\" or scores.enabled = true requires nats.url (one URL or a list)");
        }
        String prefix = toml.getString("nats.subject_prefix", Nats.DEFAULT_SUBJECT_PREFIX).trim();
        return new Nats(
                urls,
                toml.getString("nats.creds_file", "").trim(),
                prefix.isEmpty() ? Nats.DEFAULT_SUBJECT_PREFIX : prefix,
                Math.max(1024L, toml.getLong("nats.max_in_flight_bytes", 32L * 1024 * 1024)),
                Math.max(1L, toml.getLong("nats.publish_timeout_seconds", 5L)) * 1000L,
                Math.max(64L * 1024, toml.getLong("nats.reconnect_buffer_bytes", 8L * 1024 * 1024)),
                (int) Math.min(100_000L, Math.max(1L, toml.getLong("nats.partitions", (long) Nats.DEFAULT_PARTITIONS))));
    }

    private static Object rawPayloadsValue(Toml toml) {
        Toml capture = toml.getTable("capture");
        return capture == null ? null : capture.toMap().get("raw_payloads");
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
