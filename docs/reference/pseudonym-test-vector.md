# Reference: pseudonym test vector

The player pseudonym is computed twice: by the capture plugin (`Pseudonymizer.java`) and by the control-plane API
(`corvus_services/api/pseudonym.py`, used for UUID lookup and erasure). Both must produce identical bytes, otherwise an
erasure by UUID would silently miss the player.

## Algorithm

```
pseudonym = HMAC-SHA-256(key, uuid_bytes)
uuid_bytes = most significant 8 bytes || least significant 8 bytes, big-endian (16 bytes, RFC 4122 byte order)
key        = base64-decoded value of CORVUS_PSEUDONYM_KEY (at least 16 bytes)
```

The UUID is hashed as its 16 raw bytes, not as text. The result is 32 bytes; the API shows it as 64 lowercase hex
characters. The key identifier (`CORVUS_PSEUDONYM_KEY_ID`) is not part of the computation; it is stored next to the
pseudonym so that a key rotation can be detected.

## Vectors

| # | Key (hex of the raw bytes) | UUID | Pseudonym (hex) |
|---|---|---|---|
| 1 | `30313233343536373839616263646566` (`0123456789abcdef`, the key of `PseudonymizerTest`) | `11111111-2222-3333-4444-555555555555` (`PLAYER` in `PseudonymizerTest`) | `40642e55746eeaffde2e9de80d506b79cd3dbbc299a84da87a3812759417da84` |
| 2 | `000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f` (bytes 0 to 31) | `00000000-0000-0000-0000-000000000000` | `ff921230177d146ee8401eb82173128410002ba6f997e8e837e804407c8cdd38` |

For `CORVUS_PSEUDONYM_KEY` the keys are the base64 of the raw bytes: vector 1 is `MDEyMzQ1Njc4OWFiY2RlZg==`, vector 2 is
`AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=`.

Independent check with OpenSSL:

```sh
printf '11111111222233334444555555555555' | xxd -r -p | openssl dgst -sha256 -hmac '0123456789abcdef' -hex
printf '00000000000000000000000000000000' | xxd -r -p \
  | openssl dgst -sha256 -mac HMAC -macopt hexkey:000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f -hex
```

## Where it is tested

- API: `services/tests/api/test_api_players.py` (`test_pseudonym_matches_the_documented_vector` and the second vector).
- Plugin: **not yet.** `PseudonymizerTest` only checks properties (determinism, length, no raw UUID bytes), and the
  Java test has no fixed expected value. The plugin should get a test that asserts the two pseudonyms above for
  `new PseudonymKey("0123456789abcdef".getBytes(), "k1")` with `UUID.fromString("11111111-2222-3333-4444-555555555555")`,
  and for the 32-byte key of vector 2. Plugin changes are outside the control-plane API change.
