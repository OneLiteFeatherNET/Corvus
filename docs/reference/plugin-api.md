# Reference: Corvus plugin API (v1)

The Corvus Velocity plugin exposes the latest model scores of online players to other Velocity plugins through the
`corvus-api` module (package `net.onelitefeather.corvus.api`, plain Java 21+, no dependencies).

## Decision aids, not verdicts

- A score is a probability-like model output in [0, 1]; higher means more likely cheating.
- **Only `ENFORCE` scores are meant to drive automated actions** in other plugins. `CANARY` scores are
  informational. `SHADOW` scores are never exposed: the scoring service does not publish them and the plugin
  drops them if they arrive anyway.
- Corvus itself **never sanctions players**. What happens with a score is the decision of the plugin that reads
  it. Prefer the smoothed score and a minimum `windows()` over a single window score, and keep a human in the loop
  for anything irreversible.

## Depending on `corvus-api`

The API classes are packaged unrelocated inside the Corvus plugin jar, so other plugins compile against the API
and use the classes of the running Corvus plugin at runtime. Do not shade `corvus-api` into your plugin.

```kotlin
dependencies {
    compileOnly("net.onelitefeather.corvus:corvus-api:<version>")
}
```

Publishing the artifact is not set up yet (open point); until then use `./gradlew :api:jar` and depend on
`api/build/libs/corvus-api-<version>.jar` as a file, or on the Corvus plugin jar with `compileOnly(files(...))`.

Declare the dependency so Velocity loads Corvus first. The plugin id is `corvus`:

```java
@Plugin(id = "my-plugin", name = "My Plugin", version = "1.0.0",
        dependencies = {@Dependency(id = "corvus")})
public final class MyPlugin { ... }
```

Use `optional = true` if your plugin also works without Corvus and guard every access with
`CorvusProvider.find()`.

## Lookup

Velocity has no service registry, so the API is looked up through a static holder:

```java
CorvusApi api = CorvusProvider.get();            // throws IllegalStateException if Corvus is not started
Optional<CorvusApi> maybe = CorvusProvider.find(); // empty instead of throwing
```

The provider is filled when Corvus handles `ProxyInitializeEvent` (so read it in your own
`ProxyInitializeEvent` handler or later, with the `@Dependency` above) and cleared on shutdown. The API object is
available even when scoring is disabled; it simply never has scores.

## Reading scores

```java
Optional<PlayerScore> latest = api.latestScore(player.getUniqueId(), "pvp");
Collection<PlayerScore> all = api.latestScores(player.getUniqueId());

latest.filter(s -> s.stage() == ScoreStage.ENFORCE)
      .filter(s -> s.windows() >= 5 && s.smoothedScore() > 0.9f)
      .ifPresent(s -> notifyStaff(player, s));   // a decision aid for staff, not an automatic ban
```

| Method | Result |
|---|---|
| `latestScore(UUID, String gameContext)` | the most recent score of the player in that game context (any domain) |
| `latestScores(UUID)` | snapshot with the latest score per (game context, domain); empty if none |
| `registerScoreListener(ScoreListener)` | `Registration`; call `unregister()` when your plugin shuts down |

`PlayerScore` is an immutable record: `player`, `gameContext`, `domain`, `score`, `smoothedScore`, `windows`
(number of windows behind the smoothed score), `modelName`, `modelVersion`, `stage` (`CANARY` or `ENFORCE`),
`scoredAt`.

Scores only exist for players that are online on this proxy and are forgotten when they disconnect. The latest
score per (player, game context, domain) is decided by `scoredAt`; a late older score never replaces a newer one.

### Listeners

```java
CorvusApi.Registration reg = api.registerScoreListener(score -> { /* light work */ });
```

- Listeners run asynchronously on Corvus worker threads (`scores.listener_threads`, default 2), never on Netty
  threads, Velocity event threads or the NATS dispatcher.
- A bounded queue (`scores.listener_queue`, default 1024) sits in front of the workers. When it is full, updates
  are dropped (the latest value is still readable through `latestScore`).
- An exception in one listener is caught and logged (at most once a minute) and does not affect other listeners.
  Do not block in a listener; hand long work to your own executor.

## How scores reach the plugin

When `sink.type = "nats"` (or `scores.enabled = true`) the plugin subscribes (core NATS) to `corvus.score.v1.>`
(`scores.subject`), decodes `corvus.score.v1.Score` and maps its `player_pseudonym` to an online player. For that
it computes the HMAC pseudonym (same key as capture) of every player on login and caches it until they
disconnect. Scores for players that are not online on this proxy and scores of stage `SHADOW` are ignored. Without
the pseudonym key, scoring is disabled with a warning. See `docs/reference/data-lake.md` section 6 for the
producer side and `deploy/pilot/proxy/corvus-config.toml` for the config keys.

The command `/corvus score <player>` (permission `corvus.admin`) prints the latest scores of an online player.

## Partitioned capture subjects

Capture envelopes are published to `<nats.subject_prefix>.<proxy_id>.<partition>`. `partition` is the first four
bytes of the session id as a big-endian unsigned integer modulo `nats.partitions` (default 16), written as decimal
with at least two digits (`00`..`15`). With more than 100 partitions the width grows to the number of digits of
the highest partition (128 partitions give `000`..`127`). The count must be identical on all proxies and match the
scorers' filter subjects.

## Compatibility

The API follows the plugin version. Additions are made append-only (new methods have defaults); a breaking change
bumps the major version.
