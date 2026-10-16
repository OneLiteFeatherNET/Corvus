package net.onelitefeather.corvus.plugin.tls;

import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;
import javax.net.ssl.TrustManagerFactory;
import javax.net.ssl.X509TrustManager;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.GeneralSecurityException;
import java.security.KeyStore;
import java.security.cert.CertificateException;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/**
 * Builds an {@link SSLContext} that trusts the JVM default CAs <em>and</em> an extra PEM bundle (for example a
 * private step-ca). The JVM-wide truststore is never touched; the context is only handed to the NATS and HTTP
 * clients of this plugin.
 */
public final class TlsTrust {

    /** The configured CA file is unreadable or holds no valid certificate. */
    public static final class TlsSetupException extends Exception {
        public TlsSetupException(String message, Throwable cause) {
            super(message, cause);
        }

        public TlsSetupException(String message) {
            super(message);
        }
    }

    private TlsTrust() {
    }

    /** Relative paths are resolved against the plugin data directory. */
    public static Path resolve(String caFile, Path dataDirectory) {
        Path path = Path.of(caFile);
        return path.isAbsolute() ? path : dataDirectory.resolve(path).normalize();
    }

    /** Empty {@code caFile} means JVM default trust only: no context is returned. */
    public static Optional<SSLContext> sslContext(String caFile, Path dataDirectory) throws TlsSetupException {
        if (caFile.isBlank()) {
            return Optional.empty();
        }
        Path path = resolve(caFile.trim(), dataDirectory);
        try {
            return Optional.of(sslContext(jvmDefaultTrustManager(), readCertificates(path)));
        } catch (GeneralSecurityException e) {
            throw new TlsSetupException("tls.ca_file " + path + " could not be used: " + e.getMessage(), e);
        }
    }

    /** Context that accepts what {@code defaultTrust} accepts plus chains anchored in {@code extra}. */
    public static SSLContext sslContext(X509TrustManager defaultTrust, List<X509Certificate> extra)
            throws GeneralSecurityException {
        X509TrustManager composite = composite(defaultTrust, trustManagerOf(extra));
        SSLContext context = SSLContext.getInstance("TLS");
        context.init(null, new TrustManager[]{composite}, null);
        return context;
    }

    public static X509TrustManager composite(X509TrustManager first, X509TrustManager second) {
        return new CompositeTrustManager(first, second);
    }

    static List<X509Certificate> readCertificates(Path path) throws TlsSetupException {
        List<X509Certificate> certificates = new ArrayList<>();
        try (InputStream in = Files.newInputStream(path)) {
            for (java.security.cert.Certificate c : CertificateFactory.getInstance("X.509").generateCertificates(in)) {
                if (c instanceof X509Certificate x509) {
                    certificates.add(x509);
                }
            }
        } catch (IOException | CertificateException e) {
            throw new TlsSetupException("tls.ca_file " + path + " is unreadable or not a PEM certificate bundle: "
                    + e.getMessage(), e);
        }
        if (certificates.isEmpty()) {
            throw new TlsSetupException("tls.ca_file " + path + " contains no PEM certificate");
        }
        return certificates;
    }

    private static X509TrustManager trustManagerOf(List<X509Certificate> certificates) throws GeneralSecurityException {
        KeyStore store = KeyStore.getInstance(KeyStore.getDefaultType());
        try {
            store.load(null, null);
        } catch (IOException e) {
            throw new GeneralSecurityException(e);
        }
        int index = 0;
        for (X509Certificate certificate : certificates) {
            store.setCertificateEntry("corvus-ca-" + index++, certificate);
        }
        return x509(store);
    }

    private static X509TrustManager jvmDefaultTrustManager() throws GeneralSecurityException {
        return x509(null);
    }

    private static X509TrustManager x509(KeyStore store) throws GeneralSecurityException {
        TrustManagerFactory factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        factory.init(store);
        for (TrustManager tm : factory.getTrustManagers()) {
            if (tm instanceof X509TrustManager x509) {
                return x509;
            }
        }
        throw new GeneralSecurityException("no X509TrustManager available");
    }

    private record CompositeTrustManager(X509TrustManager first, X509TrustManager second)
            implements X509TrustManager {

        @Override
        public void checkServerTrusted(X509Certificate[] chain, String authType) throws CertificateException {
            try {
                first.checkServerTrusted(chain, authType);
            } catch (CertificateException primary) {
                try {
                    second.checkServerTrusted(chain, authType);
                } catch (CertificateException secondary) {
                    primary.addSuppressed(secondary);
                    throw primary;
                }
            }
        }

        @Override
        public void checkClientTrusted(X509Certificate[] chain, String authType) throws CertificateException {
            try {
                first.checkClientTrusted(chain, authType);
            } catch (CertificateException primary) {
                try {
                    second.checkClientTrusted(chain, authType);
                } catch (CertificateException secondary) {
                    primary.addSuppressed(secondary);
                    throw primary;
                }
            }
        }

        @Override
        public X509Certificate[] getAcceptedIssuers() {
            X509Certificate[] a = first.getAcceptedIssuers();
            X509Certificate[] b = second.getAcceptedIssuers();
            X509Certificate[] all = java.util.Arrays.copyOf(a, a.length + b.length);
            System.arraycopy(b, 0, all, a.length, b.length);
            return all;
        }
    }
}
