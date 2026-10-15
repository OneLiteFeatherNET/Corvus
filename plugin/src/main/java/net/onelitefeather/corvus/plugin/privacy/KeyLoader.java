package net.onelitefeather.corvus.plugin.privacy;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Base64;
import java.util.Optional;
import java.util.function.Consumer;
import java.util.function.Function;

/** Loads the pseudonym key from env var CORVUS_PSEUDONYM_KEY (base64) or a key file. Fails closed. */
public final class KeyLoader {

    public static final String ENV_VAR = "CORVUS_PSEUDONYM_KEY";

    private KeyLoader() {
    }

    /** @return the key, or empty if none is configured or usable; the reason goes to {@code warn}. */
    public static Optional<PseudonymKey> load(
            Function<String, String> env, String keyFile, Path dataDir, String keyId, Consumer<String> warn) {
        String encoded = env.apply(ENV_VAR);
        String source = "environment variable " + ENV_VAR;
        if (encoded == null || encoded.isBlank()) {
            if (keyFile == null || keyFile.isBlank()) {
                warn.accept("No pseudonym key configured (set " + ENV_VAR + " or pseudonym.key_file). Capture is DISABLED.");
                return Optional.empty();
            }
            Path path = dataDir.resolve(keyFile);
            source = "key file " + path;
            try {
                encoded = Files.readString(path, StandardCharsets.UTF_8);
            } catch (IOException e) {
                warn.accept("Cannot read pseudonym key file (" + e.getClass().getSimpleName() + "). Capture is DISABLED.");
                return Optional.empty();
            }
        }
        try {
            return Optional.of(new PseudonymKey(Base64.getMimeDecoder().decode(encoded.trim()), keyId));
        } catch (IllegalArgumentException e) {
            warn.accept("Pseudonym key from " + source + " is invalid (base64, at least "
                    + PseudonymKey.MIN_KEY_BYTES + " bytes). Capture is DISABLED.");
            return Optional.empty();
        }
    }
}
