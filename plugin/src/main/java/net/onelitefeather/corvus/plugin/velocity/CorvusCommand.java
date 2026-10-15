package net.onelitefeather.corvus.plugin.velocity;

import com.velocitypowered.api.command.SimpleCommand;
import com.velocitypowered.api.proxy.Player;
import com.velocitypowered.api.proxy.ProxyServer;
import net.kyori.adventure.text.Component;
import net.onelitefeather.corvus.api.CorvusApi;
import net.onelitefeather.corvus.plugin.capture.StatsReporter;
import net.onelitefeather.corvus.plugin.score.ScoreFormatter;

import java.time.Instant;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import java.util.function.Supplier;

/** {@code /corvus stats} and {@code /corvus score <player>}, permission {@code corvus.admin}. */
final class CorvusCommand implements SimpleCommand {

    static final String PERMISSION = "corvus.admin";

    private final StatsReporter reporter;

    private final ProxyServer proxy;
    private final CorvusApi api;
    private final boolean scoresEnabled;
    private final Supplier<String> blocklistStatus;

    CorvusCommand(StatsReporter reporter, ProxyServer proxy, CorvusApi api, boolean scoresEnabled,
                  Supplier<String> blocklistStatus) {
        this.blocklistStatus = blocklistStatus;
        this.reporter = reporter;
        this.proxy = proxy;
        this.api = api;
        this.scoresEnabled = scoresEnabled;
    }

    @Override
    public void execute(Invocation invocation) {
        String[] args = invocation.arguments();
        if (args.length == 1 && args[0].equalsIgnoreCase("stats")) {
            invocation.source().sendMessage(Component.text("[corvus] " + reporter.sample() + " " + blocklistStatus.get()));
        } else if (args.length == 2 && args[0].equalsIgnoreCase("score")) {
            score(invocation, args[1]);
        } else {
            invocation.source().sendMessage(Component.text("Usage: /corvus stats | /corvus score <player>"));
        }
    }

    private void score(Invocation invocation, String name) {
        if (!scoresEnabled) {
            invocation.source().sendMessage(Component.text("[corvus] scores are not enabled (scores.enabled)"));
            return;
        }
        Optional<Player> player = proxy.getPlayer(name);
        if (player.isEmpty()) {
            invocation.source().sendMessage(Component.text("[corvus] " + name + " is not online"));
            return;
        }
        for (String line : ScoreFormatter.lines(player.get().getUsername(),
                api.latestScores(player.get().getUniqueId()), Instant.now())) {
            invocation.source().sendMessage(Component.text(line));
        }
    }

    @Override
    public List<String> suggest(Invocation invocation) {
        String[] args = invocation.arguments();
        if (args.length <= 1) {
            return List.of("stats", "score");
        }
        if (args.length == 2 && args[0].equalsIgnoreCase("score")) {
            String prefix = args[1].toLowerCase(Locale.ROOT);
            return proxy.getAllPlayers().stream().map(Player::getUsername)
                    .filter(n -> n.toLowerCase(Locale.ROOT).startsWith(prefix)).toList();
        }
        return List.of();
    }

    @Override
    public boolean hasPermission(Invocation invocation) {
        return invocation.source().hasPermission(PERMISSION);
    }
}
