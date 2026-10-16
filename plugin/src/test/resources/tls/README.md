Test fixtures for TlsTrustTest. Generated once with openssl (valid ~100 years, private keys discarded):

- ca.pem: self-signed test CA (CN=Corvus Test CA)
- other-ca.pem: unrelated self-signed CA (CN=Other Test CA)
- leaf.pem: server certificate (CN=nats.test) signed by ca.pem

```
openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.pem -days 36500 -subj "/CN=Corvus Test CA" \
  -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign,cRLSign
openssl req -newkey rsa:2048 -nodes -keyout leaf.key -out leaf.csr -subj "/CN=nats.test"
openssl x509 -req -in leaf.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out leaf.pem -days 36500 -extfile ext
```
