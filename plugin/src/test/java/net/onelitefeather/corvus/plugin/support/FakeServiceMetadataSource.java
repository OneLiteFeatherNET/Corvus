package net.onelitefeather.corvus.plugin.support;

import net.onelitefeather.corvus.plugin.capture.ServiceMetadata;
import net.onelitefeather.corvus.plugin.capture.ServiceMetadataSource;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/** Map-backed orchestrator source for tests; records every lookup. Instances are per test, never shared. */
public final class FakeServiceMetadataSource implements ServiceMetadataSource {

    private final Map<String, ServiceMetadata> services = new HashMap<>();
    public final List<String> lookups = new ArrayList<>();
    private RuntimeException failure;

    public FakeServiceMetadataSource service(String serverName, String task, List<String> groups) {
        services.put(serverName, new ServiceMetadata(task, groups, "node-1", "MINECRAFT_SERVER"));
        return this;
    }

    public FakeServiceMetadataSource failingWith(RuntimeException e) {
        this.failure = e;
        return this;
    }

    @Override
    public Optional<ServiceMetadata> lookup(String serverName) {
        lookups.add(serverName);
        if (failure != null) {
            throw failure;
        }
        return Optional.ofNullable(services.get(serverName));
    }
}
