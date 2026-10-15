package net.onelitefeather.corvus.plugin.blocklist;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Optional;
import java.util.function.Consumer;
import java.util.function.Function;

/** Loads the plugin API token from env var CORVUS_PLUGIN_API_TOKEN or a token file. The token is never logged. */
public final class TokenLoader {

    public static final String ENV_VAR = "CORVUS_PLUGIN_API_TOKEN";

    private TokenLoader() {
    }

    public static Optional<String> load(Function<String, String> env, String tokenFile, Path dataDir,
                                        Consumer<String> warn) {
        String token = env.apply(ENV_VAR);
        if (token == null || token.isBlank()) {
            if (tokenFile == null || tokenFile.isBlank()) {
                warn.accept("blocklist_sync.enabled but no token (set " + ENV_VAR
                        + " or blocklist_sync.token_file). Blocklist sync is DISABLED.");
                return Optional.empty();
            }
            try {
                token = Files.readString(dataDir.resolve(tokenFile), StandardCharsets.UTF_8);
            } catch (IOException e) {
                warn.accept("Cannot read blocklist_sync.token_file (" + e.getClass().getSimpleName()
                        + "). Blocklist sync is DISABLED.");
                return Optional.empty();
            }
        }
        token = token.trim();
        if (token.isEmpty()) {
            warn.accept("blocklist_sync token is empty. Blocklist sync is DISABLED.");
            return Optional.empty();
        }
        return Optional.of(token);
    }
}
