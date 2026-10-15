package net.onelitefeather.corvus.plugin.velocity;

import com.velocitypowered.api.command.SimpleCommand;
import net.kyori.adventure.text.Component;
import net.onelitefeather.corvus.plugin.capture.StatsReporter;

/** {@code /corvus stats}, permission {@code corvus.admin}. */
final class CorvusCommand implements SimpleCommand {

    static final String PERMISSION = "corvus.admin";

    private final StatsReporter reporter;

    CorvusCommand(StatsReporter reporter) {
        this.reporter = reporter;
    }

    @Override
    public void execute(Invocation invocation) {
        String[] args = invocation.arguments();
        if (args.length == 1 && args[0].equalsIgnoreCase("stats")) {
            invocation.source().sendMessage(Component.text("[corvus] " + reporter.sample()));
        } else {
            invocation.source().sendMessage(Component.text("Usage: /corvus stats"));
        }
    }

    @Override
    public boolean hasPermission(Invocation invocation) {
        return invocation.source().hasPermission(PERMISSION);
    }
}
