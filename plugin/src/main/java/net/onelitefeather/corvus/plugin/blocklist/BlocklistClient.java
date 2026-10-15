package net.onelitefeather.corvus.plugin.blocklist;

import java.io.IOException;
import java.util.Set;

/** Fetches the blocked pseudonyms (lower-case hex) from the control plane. Blocking. */
public interface BlocklistClient {

    Set<String> fetchBlocked() throws IOException;
}
