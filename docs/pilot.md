# Corvus pilot runbook

The pilot answers, on a real proxy with real clients:

- real packet rates per player (connection state, direction, packet type, game context) and bytes/s,
- drop rates of the capture queue,
- whether frames are in the client's native protocol or the backend's (ViaVersion on Velocity),
- that no chat or command packets are captured,
- that chat signing still works with the plugin installed.

## Prerequisites

- Docker with Compose v2, a JDK for the Gradle build (the plugin build sets the toolchain), `uv`, `openssl`.
- A Minecraft Java account for every client you connect with (the proxy runs in online mode).
- Roughly 5.5 GB RAM and 4 CPUs free. Limits per container are set in `deploy/pilot/compose.yaml`
  (proxy 1 CPU / 1 GB, each Paper backend 1.5 CPU / 2 GB).

## Pinned versions

| Component | Version | Source |
| --- | --- | --- |
| `itzg/mc-proxy` | `2026.10.0-java25` | Docker Hub tags (Velocity 4.x needs Java 25) |
| `itzg/minecraft-server` | `2026.10.0-java25` | Docker Hub tags |
| Velocity | 4.2.0, build 30 (stable) | PaperMC Fill API |
| Paper (lobby, pvp) | 26.2, build 134 (stable) | PaperMC Fill API |
| PacketEvents | 2.14.0 (`packetevents-velocity-2.14.0.jar`, sha256 `0772f64b...271b`) | GitHub release v2.14.0; identical to the Modrinth file |
| ViaVersion | 5.12.1 | Modrinth CDN, release |
| ViaBackwards | 5.12.1 | Modrinth CDN, release |

Plugins are downloaded by the proxy container from the URLs in `PLUGINS` in `compose.yaml`.
To bump a version, replace the URL and update this table. ViaVersion lets newer clients than the
backend join, ViaBackwards lets older clients join.

## Setup

```sh
cd deploy/pilot
cp .env.example .env
# fill in the secrets:
openssl rand -base64 24 | tr -d '\n=+/'   # VELOCITY_FORWARDING_SECRET
openssl rand -base64 32                   # CORVUS_PSEUDONYM_KEY
# set EULA=TRUE after reading https://aka.ms/MinecraftEULA
```

Keep the pseudonym key outside the data you later share: it is what links a pseudonym to a player.

Build the plugin jar (done from the repository root; the `plugin/` module lives on its own branch):

```sh
./gradlew :plugin:shadowJar
```

The `stage-corvus` service copies exactly one `plugin/build/libs/corvus-velocity-*.jar` (never
`-sources`, `-plain`, `-javadoc`) into the proxy's `/plugins`. It fails with a clear message if there
are zero or several.

Pilot configuration of the plugin is `deploy/pilot/proxy/corvus-config.toml`, mounted read-only as
`plugins/corvus/config.toml` (Velocity's data directory for the plugin id `corvus`). Its keys are the
ones the plugin reads (`PilotConfigTest` fails if it contains a key the plugin ignores): game context
mapping `lobby -> lobby`, `pvp -> pvp`, pseudonym key id `pilot-1`, and the payload policy below. The
HMAC key itself is read from the environment variable `CORVUS_PSEUDONYM_KEY` (base64, at least 16
bytes). The plugin writes `plugins/corvus/captures/<yyyy-MM-dd>/<session-id>.cvc`, which is bind
mounted from `deploy/pilot/data/captures`; the one-shot `stage-corvus` service makes that directory
writable for the proxy user (uid 1000).

### Raw payload policy (`capture.raw_payloads`)

Clientbound packets such as scoreboard, teams, titles, spawn player, boss bars contain names and UUIDs
of other players, so payload bytes are restricted. The setting is `none`, `serverbound` (default) or
`all`; the old booleans still parse (`true` = `serverbound`, `false` = `none`).

| Packet group | `none` | `serverbound` | `all` |
| --- | --- | --- | --- |
| Chat, commands, signing, tab completion, player info (see `PrivacyFilter`) | frame dropped | frame dropped | frame dropped |
| Serverbound text-bearing play types (sign, book, anvil name, plugin message, command blocks, structure and jigsaw blocks, cookie response, test blocks, game rule, custom click action, spectate, inventory clicks) | no payload | no payload | no payload |
| Other serverbound play types in the vocabulary | no payload | payload | payload |
| Serverbound play types outside the vocabulary | no payload | no payload | payload |
| Clientbound entity relative move (and rotation), rotation, head look, teleport, velocity, position sync, player position and look | no payload | payload | payload |
| All other clientbound play types and all configuration packets | no payload | no payload | payload |

Frames are always recorded with their packet type; only the payload bytes differ. `all` logs a WARN
at startup and is for local debugging only: never feed such captures into the data lake.
`Frame.payload_original_size` holds the size before truncation to `max_payload_bytes`; the analyzer
reports the share of truncated payloads.

## Start

```sh
cd deploy/pilot
docker compose up -d
docker compose logs -f proxy
```

The proxy waits for both backends to become healthy (first start downloads Paper, a few minutes).
Connect to `127.0.0.1:25565` (change `PROXY_BIND`/`PROXY_PORT` in `.env` to expose it).
Captures appear in `deploy/pilot/data/captures/<yyyy-MM-dd>/<session-id>.cvc`.
Stop with `docker compose down` (add `-v` to also drop world data and the plugin volume).

## Clients

Connect with at least:

1. the current vanilla release (matches the backend, native frames expected),
2. an older vanilla version (for example 1.21.x or 1.20.x) to exercise ViaBackwards,
3. optionally a Fabric client (brand check in `SegmentStart.client_brand`).

## Checks on the proxy

1. Plugin load order. the plugin load order decides whether ViaVersion's `via-decoder` sits
   before PacketEvents' `pe-decoder-<id>` in the Netty pipeline. The Corvus plugin logs the pipeline handler names
   once per session together with a `via_before_pe` flag:

   ```sh
   docker compose logs proxy | grep -i via_before_pe
   ```

   Record the value per session. `via_before_pe=true` is a finding to report, because captured frames
   may then be in the translated rather than the client's native protocol (compare with the version
   table in the analyzer output). PacketEvents on Velocity only sees the client to proxy
   connection; there is no backend channel, which is expected.
2. `docker compose logs proxy | grep -i -E "error|exception|corvus"` shows no Corvus errors.

## Manual checklist

For each client:

- [ ] Join; you land on `lobby`.
- [ ] `/server pvp`, then `/server lobby` several times (each switch must start a new segment).
- [ ] Send normal chat messages (signed) on both servers, and run a few commands. Nobody is
      disconnected with "chat validation failed", "out of order chat" or similar.
- [ ] `/corvus stats` shows frames, queue state and drop counters that grow while you play.
- [ ] On `pvp`: a PvP session of N minutes (N >= 10; use two clients fighting each other, moving,
      swinging, placing and breaking blocks).
- [ ] Disconnect cleanly (a `SessionEnd` is written) for most sessions, and kill one client
      abruptly to check truncation handling.
- [ ] No kicks or disconnects caused by Corvus, no stack traces from Corvus in the proxy log.

## Analyze

Stop the stack first (or analyze only closed files; an open file may end in a truncated message,
which the analyzer counts and tolerates).

```sh
cd ml
uv sync
uv run corvus-pilot analyze ../deploy/pilot/data/captures --json /tmp/pilot-report.json
```

The pool size defaults to the cgroup CPU limit (`/sys/fs/cgroup/cpu.max`), else the usable CPU
count; override with `--workers N`. Exit code: 0 ok, 1 privacy check failed, 2 usage error.

The report contains, per session and per game context: duration, frames/s and bytes/s per direction
and connection state (bytes are payload bytes), top packet types by rate, decoded event rates
(movement, swing, interact_entity, dig, place, held_item, entity_motion), sequence gaps, drop
counters (queue full, privacy, listener errors), a client vs observed protocol version table
(`translated` is true when they differ), and the privacy check. Rates for a context are per
player-second; `per_player_frames_per_s` gives min/median/p95/max over player-segments.

### Privacy check

The analyzer loads `proto/corvus/capture/v1/vocabulary.json` (exported from the plugin, kept in sync
by `VocabularyJsonTest`) and fails when

- a frame has a privacy-dropped packet type (chat, commands, signing, player lists),
- a frame of a payload-stripped type (sign, book, ...) carries a payload,
- under the default policy a frame carries a payload outside the policy: any clientbound payload
  other than the motion allowlist, or a serverbound payload of a type that is not in the vocabulary,
- any string field looks like a UUID or an IP address (the finding reports only the field).

`--payload-policy none|serverbound|all` states which `capture.raw_payloads` the captures were
recorded with (default `serverbound`); only the `stripped` rule applies to `all`. `--vocabulary FILE`
overrides the file. `--denylist FILE` adds packet types or wire ids to the vocabulary's private
ids, for example version dependent wire ids:

```json
{"packet_types": [], "wire_ids": [{"id": 6, "direction": "serverbound", "state": "play"}]}
```

Payload bytes are not scanned for identifiers; the payload policy is what keeps them clean.

## Acceptance thresholds

| Criterion | Pass when |
| --- | --- |
| Corvus-caused kicks and errors | 0 |
| Chat signing | signed chat and commands work on both servers, all clients, across server switches |
| Drop rate (`queue_full / (captured + queue_full)`) | < 0.1 % under normal load, per context and overall |
| Privacy check | analyzer exits 0 (vocabulary denylist and payload policy are always active) |
| Native protocol | `via_before_pe` and the version table understood; any translated frames reported as a finding |

Also report: observed rates per player and context (these size the real pipeline), sequence gaps
(any gap with zero queue drops is a bug), and truncated or corrupt files.
