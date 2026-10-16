package net.onelitefeather.corvus.plugin.tls;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import javax.net.ssl.X509TrustManager;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.cert.CertificateException;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class TlsTrustTest {

    @TempDir
    Path dir;

    private static String resource(String name) throws IOException {
        try (InputStream in = TlsTrustTest.class.getResourceAsStream("/tls/" + name)) {
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    private static X509Certificate cert(String name) throws Exception {
        try (InputStream in = TlsTrustTest.class.getResourceAsStream("/tls/" + name)) {
            return (X509Certificate) CertificateFactory.getInstance("X.509").generateCertificate(in);
        }
    }

    /** Stands in for the JVM default trust: accepts exactly the chains whose first certificate it knows. */
    private static final class FakeTrust implements X509TrustManager {
        private final List<X509Certificate> accepted;

        FakeTrust(X509Certificate... accepted) {
            this.accepted = List.of(accepted);
        }

        @Override
        public void checkClientTrusted(X509Certificate[] chain, String authType) throws CertificateException {
            checkServerTrusted(chain, authType);
        }

        @Override
        public void checkServerTrusted(X509Certificate[] chain, String authType) throws CertificateException {
            if (!accepted.contains(chain[0])) {
                throw new CertificateException("not trusted by fake default");
            }
        }

        @Override
        public X509Certificate[] getAcceptedIssuers() {
            return accepted.toArray(new X509Certificate[0]);
        }
    }

    private static X509TrustManager compositeOf(X509TrustManager defaults, String caResource) throws Exception {
        Path ca = Path.of(TlsTrustTest.class.getResource("/tls/" + caResource).toURI());
        var context = TlsTrust.sslContext(defaults, TlsTrust.readCertificates(ca));
        // The context hides its trust manager; rebuild the same composite for direct chain checks.
        return TlsTrust.composite(defaults, trustOf(TlsTrust.readCertificates(ca)));
    }

    private static X509TrustManager trustOf(List<X509Certificate> certs) throws Exception {
        var store = java.security.KeyStore.getInstance(java.security.KeyStore.getDefaultType());
        store.load(null, null);
        for (int i = 0; i < certs.size(); i++) {
            store.setCertificateEntry("c" + i, certs.get(i));
        }
        var factory = javax.net.ssl.TrustManagerFactory.getInstance(
                javax.net.ssl.TrustManagerFactory.getDefaultAlgorithm());
        factory.init(store);
        return (X509TrustManager) factory.getTrustManagers()[0];
    }

    @Test
    void emptyCaFileMeansJvmDefaultOnly() throws Exception {
        assertEquals(Optional.empty(), TlsTrust.sslContext("", dir));
        assertEquals(Optional.empty(), TlsTrust.sslContext("  ", dir));
    }

    @Test
    void relativePathResolvesAgainstDataDirectory() {
        assertEquals(dir.resolve("certs/ca.pem"), TlsTrust.resolve("certs/ca.pem", dir));
    }

    @Test
    void absolutePathIsKept() {
        Path absolute = dir.resolve("elsewhere/ca.pem").toAbsolutePath();
        assertEquals(absolute, TlsTrust.resolve(absolute.toString(), Path.of("/unused")));
    }

    @Test
    void validBundleYieldsContext() throws Exception {
        Files.writeString(dir.resolve("ca.pem"), resource("ca.pem") + resource("other-ca.pem"));

        Optional<javax.net.ssl.SSLContext> context = TlsTrust.sslContext("ca.pem", dir);

        assertTrue(context.isPresent(), "a readable bundle must produce an SSLContext");
    }

    @Test
    void missingFileIsReported() {
        var e = assertThrows(TlsTrust.TlsSetupException.class, () -> TlsTrust.sslContext("nope.pem", dir));
        assertTrue(e.getMessage().contains("nope.pem"), e.getMessage());
    }

    @Test
    void fileWithoutCertificateIsReported() throws Exception {
        Files.writeString(dir.resolve("junk.pem"), "this is not a certificate\n");

        assertThrows(TlsTrust.TlsSetupException.class, () -> TlsTrust.sslContext("junk.pem", dir));
    }

    @Test
    void chainSignedByExtraCaIsAcceptedWhenDefaultRejectsIt() throws Exception {
        X509TrustManager trust = compositeOf(new FakeTrust(), "ca.pem");

        assertDoesNotThrow(() -> trust.checkServerTrusted(new X509Certificate[]{cert("leaf.pem")}, "RSA"));
    }

    @Test
    void chainTrustedByDefaultIsStillAccepted() throws Exception {
        X509Certificate leaf = cert("leaf.pem");
        X509TrustManager trust = compositeOf(new FakeTrust(leaf), "other-ca.pem");

        assertDoesNotThrow(() -> trust.checkServerTrusted(new X509Certificate[]{leaf}, "RSA"));
    }

    @Test
    void chainUnknownToBothIsRejected() throws Exception {
        X509TrustManager trust = compositeOf(new FakeTrust(), "other-ca.pem");

        assertThrows(CertificateException.class,
                () -> trust.checkServerTrusted(new X509Certificate[]{cert("leaf.pem")}, "RSA"));
    }

    @Test
    void acceptedIssuersCombineBoth() throws Exception {
        X509TrustManager trust = compositeOf(new FakeTrust(cert("other-ca.pem")), "ca.pem");

        assertEquals(2, trust.getAcceptedIssuers().length);
        assertFalse(List.of(trust.getAcceptedIssuers()).isEmpty());
    }
}
