package net.onelitefeather.corvus.plugin.velocity;

import com.github.retrooper.packetevents.PacketEvents;
import com.google.inject.Inject;
import com.velocitypowered.api.command.CommandMeta;
import com.velocitypowered.api.event.Subscribe;
import com.velocitypowered.api.event.connection.DisconnectEvent;
import com.velocitypowered.api.event.player.ServerPostConnectEvent;
import com.velocitypowered.api.event.proxy.ProxyInitializeEvent;
import com.velocitypowered.api.event.proxy.ProxyShutdownEvent;
import com.velocitypowered.api.plugin.Dependency;
import com.velocitypowered.api.plugin.Plugin;
import com.velocitypowered.api.plugin.annotation.DataDirectory;
import com.velocitypowered.api.proxy.Player;
import com.velocitypowered.api.proxy.ProxyServer;
import net.onelitefeather.corvus.plugin.BuildConstants;
import net.onelitefeather.corvus.plugin.capture.ByteBoundedQueue;
import net.onelitefeather.corvus.plugin.capture.CaptureEngine;
import net.onelitefeather.corvus.plugin.capture.CaptureWriter;
import net.onelitefeather.corvus.plugin.capture.GameContextMapper;
import net.onelitefeather.corvus.plugin.capture.Metrics;
import net.onelitefeather.corvus.plugin.capture.StatsReporter;
import net.onelitefeather.corvus.plugin.capture.WriterThread;
import net.onelitefeather.corvus.plugin.config.CorvusConfig;
import net.onelitefeather.corvus.plugin.pe.CorvusPacketListener;
import net.onelitefeather.corvus.plugin.privacy.KeyLoader;
import net.onelitefeather.corvus.plugin.privacy.PayloadMode;
import net.onelitefeather.corvus.plugin.privacy.PlayerFilter;
import net.onelitefeather.corvus.plugin.privacy.PseudonymKey;
import net.onelitefeather.corvus.plugin.privacy.Pseudonymizer;
import net.onelitefeather.corvus.plugin.time.NanoClock;
import org.slf4j.Logger;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.time.Duration;
import java.time.InstantSource;
import java.util.Optional;
import java.util.concurrent.TimeUnit;

@Plugin(
        id = "corvus",
        name = "Corvus",
        version = BuildConstants.VERSION,
        description = "Passive packet capture for ML anti-cheat research",
        authors = {"OneLiteFeather"},
        dependencies = {@Dependency(id = "packetevents")})
public final class CorvusPlugin {

    private static final int REGISTER_ATTEMPTS = 120;

    private final ProxyServer proxy;
    private final Logger logger;
    private final Path dataDirectory;

    private CaptureEngine engine;
    private WriterThread writerThread;
    private CorvusPacketListener listener;

    @Inject
    public CorvusPlugin(ProxyServer proxy, Logger logger, @DataDirectory Path dataDirectory) {
        this.proxy = proxy;
        this.logger = logger;
        this.dataDirectory = dataDirectory;
    }

    @Subscribe
    public void onProxyInitialize(ProxyInitializeEvent event) {
        try {
            start();
        } catch (Throwable t) {
            // never let Corvus break the proxy start
            logger.error("Corvus failed to start, capture is DISABLED", t);
        }
    }

    private void start() throws IOException {
        Files.createDirectories(dataDirectory);
        Path configFile = dataDirectory.resolve("config.toml");
        if (!Files.exists(configFile)) {
            Files.writeString(configFile, CorvusConfig.DEFAULT_TOML, StandardCharsets.UTF_8);
            logger.info("Wrote default config to {}", configFile);
        }
        CorvusConfig config = CorvusConfig.parse(Files.readString(configFile, StandardCharsets.UTF_8));

        if (config.payloadMode() == PayloadMode.ALL) {
            logger.warn("capture.raw_payloads = \"all\": payloads of clientbound packets that may contain names "
                    + "and UUIDs of other players are recorded. Use for local debugging only, never for data "
                    + "that reaches the data lake.");
        }

        Optional<PseudonymKey> key = KeyLoader.load(System::getenv, config.keyFile(), dataDirectory,
                config.keyId(), logger::warn);

        NanoClock nano = NanoClock.system();
        Metrics metrics = new Metrics();
        ByteBoundedQueue queue = new ByteBoundedQueue(config.queueMaxBytes());
        SecureRandom random = new SecureRandom();
        engine = new CaptureEngine(
                new CaptureEngine.Settings(config.payloadMode(), config.maxPayloadBytes(), config.proxyId(),
                        BuildConstants.VERSION),
                key.map(Pseudonymizer::new).orElse(null),
                new PlayerFilter(config.listMode(), config.allowlist(), config.blocklist()),
                new GameContextMapper(config.serverContexts(), config.defaultGameContext()),
                queue, nano, InstantSource.system(),
                () -> {
                    byte[] id = new byte[16];
                    random.nextBytes(id);
                    return id;
                },
                metrics, logger::info);

        StatsReporter commandStats = new StatsReporter(metrics, queue, engine, nano);
        CommandMeta meta = proxy.getCommandManager().metaBuilder("corvus").plugin(this).build();
        proxy.getCommandManager().register(meta, new CorvusCommand(commandStats));

        if (!engine.isCapturing()) {
            logger.warn("Corvus is loaded but NOT capturing (no valid pseudonym key). Players are unaffected.");
            return;
        }

        CaptureWriter writer = new CaptureWriter(dataDirectory.resolve("captures"), queue, config.batchSize(),
                config.flushIntervalMillis(), nano, metrics, logger::warn);
        writerThread = new WriterThread(writer);
        writerThread.start();

        StatsReporter logStats = new StatsReporter(metrics, queue, engine, nano);
        proxy.getScheduler().buildTask(this, () -> logger.info("stats {}", logStats.sample()))
                .delay(Duration.ofSeconds(60)).repeat(Duration.ofSeconds(60)).schedule();

        listener = new CorvusPacketListener(engine);
        registerListener(0);
    }

    /** PacketEvents initialises itself in its own ProxyInitializeEvent handler; wait until it is ready. */
    private void registerListener(int attempt) {
        try {
            if (PacketEvents.getAPI() != null && PacketEvents.getAPI().isInitialized()) {
                PacketEvents.getAPI().getEventManager().registerListener(listener);
                logger.info("Corvus capture listener registered (MONITOR, read-only)");
                return;
            }
        } catch (Throwable t) {
            logger.debug("PacketEvents not ready yet", t);
        }
        if (attempt >= REGISTER_ATTEMPTS) {
            logger.error("PacketEvents did not initialise in time, capture is DISABLED");
            return;
        }
        proxy.getScheduler().buildTask(this, () -> registerListener(attempt + 1))
                .delay(500, TimeUnit.MILLISECONDS).schedule();
    }

    @Subscribe
    public void onServerPostConnect(ServerPostConnectEvent event) {
        try {
            Player player = event.getPlayer();
            String server = player.getCurrentServer().map(c -> c.getServerInfo().getName()).orElse(null);
            if (engine != null) {
                engine.onBackendConnected(player.getUniqueId(), server, player.getClientBrand());
            }
        } catch (Throwable t) {
            logger.debug("Corvus ignored a segment event failure", t);
        }
    }

    @Subscribe
    public void onDisconnect(DisconnectEvent event) {
        try {
            if (engine != null) {
                engine.onDisconnect(event.getPlayer().getUniqueId());
            }
        } catch (Throwable t) {
            logger.debug("Corvus ignored a disconnect failure", t);
        }
    }

    @Subscribe
    public void onProxyShutdown(ProxyShutdownEvent event) {
        try {
            if (listener != null) {
                PacketEvents.getAPI().getEventManager().unregisterListener(listener);
            }
        } catch (Throwable ignored) {
            // PacketEvents may already be terminated
        }
        if (engine != null) {
            engine.shutdown();
        }
        if (writerThread != null) {
            writerThread.stop();
        }
    }
}
