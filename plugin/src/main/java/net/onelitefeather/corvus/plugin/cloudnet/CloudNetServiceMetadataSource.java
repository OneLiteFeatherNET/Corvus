package net.onelitefeather.corvus.plugin.cloudnet;

import eu.cloudnetservice.driver.inject.InjectionLayer;
import eu.cloudnetservice.driver.provider.CloudServiceProvider;
import eu.cloudnetservice.driver.registry.ServiceRegistry;
import eu.cloudnetservice.driver.service.ServiceId;
import eu.cloudnetservice.driver.service.ServiceInfoSnapshot;
import net.onelitefeather.corvus.plugin.capture.ServiceMetadata;
import net.onelitefeather.corvus.plugin.capture.ServiceMetadataSource;

import java.util.List;
import java.util.Optional;

/**
 * Reads service metadata from the CloudNet v4 driver. Only loaded through {@link CloudNetAvailability}, which checks
 * that the CloudNet classes exist first; nothing else in the plugin refers to this class.
 */
final class CloudNetServiceMetadataSource implements ServiceMetadataSource {

    private final CloudServiceProvider provider;

    CloudNetServiceMetadataSource() {
        // CloudNet 4.0.0-RC17 exposes driver services through the injection layer; the service registry is a fallback.
        CloudServiceProvider found = fromInjectionLayer();
        if (found == null) {
            found = ServiceRegistry.registry().defaultInstance(CloudServiceProvider.class);
        }
        if (found == null) {
            throw new IllegalStateException("no CloudServiceProvider registered");
        }
        this.provider = found;
    }

    private static CloudServiceProvider fromInjectionLayer() {
        try {
            return InjectionLayer.ext().instance(CloudServiceProvider.class);
        } catch (RuntimeException e) {
            // not bound in this layer; the registry lookup in the constructor decides
            return null;
        }
    }

    @Override
    public Optional<ServiceMetadata> lookup(String serverName) {
        // Wrapped by CachedServiceMetadataSource, so the connect path calls the driver once per server and interval.
        ServiceInfoSnapshot service = provider.serviceByName(serverName);
        if (service == null) {
            return Optional.empty();
        }
        ServiceId id = service.serviceId();
        List<String> groups = List.copyOf(service.configuration().groups());
        return Optional.of(new ServiceMetadata(id.taskName(), groups, id.nodeUniqueId(), id.environmentName()));
    }
}
