"""Synthetic raw packets with plausible Minecraft movement layouts.

SYNTHETIC DATA: for tests and smoke runs of the whole raw-byte pipeline. The payloads follow the
shape of the serverbound movement packets (big-endian doubles / floats, flag byte, varints) so the
tokenizer has real structure to learn, and the cheat variants differ the way the usual cheats do.
Numbers measured on it say that the code works, never how well a model does on real players.

Protocol differences: sessions below protocol 768 send a plain on-ground flag and no
``CLIENT_TICK_END`` (empty payload, so no raw row); from 768 on the flag byte may carry the
"pushing against wall" bit and a tick-end frame follows every client tick.

Cheat variants (one per cheating player):

* ``aimbot``: rotation snaps to exact, repeated yaw / pitch values, almost no rotation noise;
* ``killaura``: swings and entity attacks at a constant cadence, attack packets without the usual
  rotation jitter;
* ``timer``: movement packets at a constant, faster rate.

``cheat_strength`` (0..1) blends towards the legit behaviour.
"""

from __future__ import annotations

import datetime as dt
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from corvus_ml.features.schema import SESSION_ID_TYPE
from corvus_ml.raw.crypto import payload_aad
from corvus_ml.raw.reader import SessionPackets

# Corvus vocabulary ids (proto/corvus/capture/v1/vocabulary.json; checked by a test).
C_PLUGIN_MESSAGE = 8
C_INTERACT_ENTITY = 9
C_KEEP_ALIVE = 10
C_PLAYER_POSITION = 11
C_PLAYER_POSITION_AND_ROTATION = 12
C_PLAYER_ROTATION = 13
C_PLAYER_FLYING = 14
C_ANIMATION = 24
C_CLIENT_TICK_END = 27
S_ENTITY_RELATIVE_MOVE = 100
S_CHUNK_DATA = 60  # no payload in the lake (allowlist)

DIR_SB, DIR_CB = 1, 2
STATE_PLAY = 5
VARIANTS = ("aimbot", "killaura", "timer")
_BASE_TIME = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


@dataclass(frozen=True)
class SyntheticRawConfig:
    seed: int = 0
    n_legit: int = 20
    n_cheat: int = 10
    n_unlabelled: int = 0
    sessions_per_player: int = 2
    seconds_per_session: float = 40.0
    contexts: tuple[str, ...] = ("pvp",)
    protocols: tuple[int, ...] = (767, 768, 770)
    cheat_strength: float = 0.8


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


class _Player:
    """State of one simulated player."""

    def __init__(self, rng: np.random.Generator, variant: str | None, strength: float):
        self.rng, self.variant, self.s = rng, variant, strength
        self.x, self.y, self.z = rng.uniform(-200, 200), 64.0, rng.uniform(-200, 200)
        self.yaw, self.pitch = rng.uniform(-180, 180), rng.uniform(-20, 20)
        self.sens = float(np.exp(rng.normal(0.0, 0.3)))
        self.heading = rng.uniform(0, 2 * np.pi)

    def tick_dt_ms(self, i: int) -> float:
        if self.variant == "timer":
            base = 50.0 / (1.0 + 0.5 * self.s)
            return self.s * base + (1 - self.s) * 50.0 * float(np.exp(self.rng.normal(0, 0.25)))
        return 50.0 * float(np.exp(self.rng.normal(0.0, 0.25)))

    def rotate(self) -> None:
        r = self.rng
        sigma = 1.5 * self.sens
        dyaw, dpitch = r.normal(0, sigma), r.normal(0, sigma / 2)
        if r.random() < 0.04:
            dyaw += r.normal(0, 8 * sigma)
        if self.variant == "aimbot":
            if r.random() < 0.2 * self.s:
                step = 0.0879  # a fixed sensitivity grid: snaps land on exact multiples
                dyaw = round(float(r.choice([-1.0, 1.0]) * r.uniform(40, 150)) / step) * step
                dpitch = round(float(r.normal(0, 12)) / step) * step
            else:
                dyaw, dpitch = (1 - self.s) * dyaw, (1 - self.s) * dpitch
        self.yaw = (self.yaw + dyaw + 180.0) % 360.0 - 180.0
        self.pitch = float(np.clip(self.pitch + dpitch, -90.0, 90.0))

    def walk(self, dt_s: float) -> None:
        r = self.rng
        self.heading += r.normal(0, 0.05)
        speed = 4.3 * float(np.exp(r.normal(0, 0.05)))
        self.x += np.cos(self.heading) * speed * dt_s
        self.z += np.sin(self.heading) * speed * dt_s


def _flags(on_ground: bool, protocol: int, rng: np.random.Generator) -> int:
    flag = 1 if on_ground else 0
    if protocol >= 768 and rng.random() < 0.05:
        flag |= 2  # pushing against a wall (1.21.2+)
    return flag


def session_packets(
    rng: np.random.Generator, player: _Player, session_id: bytes, protocol: int, seconds: float
) -> SessionPackets:
    """One simulated session as the frames the reader would produce."""
    rows: list[
        tuple[int, int, int, bytes | None, int]
    ] = []  # (offset_ns, dir, ptype, payload, size)
    t_ms, tick, last_swing_ms, next_alive = 0.0, 0, -1e9, 15000.0
    cps_period = float(rng.uniform(110, 190))  # ms between clicks of a legit player
    ground = True
    while t_ms < seconds * 1000.0:
        t_ms += player.tick_dt_ms(tick)
        tick += 1
        ns = int(t_ms * 1e6)
        player.walk(0.05)
        rotate = rng.random() < 0.8
        if rotate:
            player.rotate()
        if rng.random() < 0.02:
            ground = not ground
        flags = _flags(ground, protocol, rng)
        kind = rng.random()
        if rotate and kind < 0.8:
            body = struct.pack(
                ">dddffB", player.x, player.y, player.z, player.yaw, player.pitch, flags
            )
            rows.append((ns, DIR_SB, C_PLAYER_POSITION_AND_ROTATION, body, len(body)))
        elif rotate:
            body = struct.pack(">ffB", player.yaw, player.pitch, flags)
            rows.append((ns, DIR_SB, C_PLAYER_ROTATION, body, len(body)))
        elif kind < 0.7:
            body = struct.pack(">dddB", player.x, player.y, player.z, flags)
            rows.append((ns, DIR_SB, C_PLAYER_POSITION, body, len(body)))
        else:
            rows.append((ns, DIR_SB, C_PLAYER_FLYING, bytes([flags]), 1))
        clicking = (
            tick % max(2, round(6 - 3 * player.s)) == 0
            if player.variant == "killaura"
            else t_ms - last_swing_ms > cps_period * float(np.exp(rng.normal(0, 0.35)))
            and rng.random() < 0.3
        )
        if clicking:
            last_swing_ms = t_ms
            rows.append((ns + 1000, DIR_SB, C_ANIMATION, _varint(0), 1))
            if rng.random() < (0.9 if player.variant == "killaura" else 0.3):
                target = int(rng.integers(1, 40))
                body = _varint(target) + _varint(1) + bytes([int(rng.random() < 0.1)])
                rows.append((ns + 2000, DIR_SB, C_INTERACT_ENTITY, body, len(body)))
        if protocol >= 768:
            rows.append((ns + 3000, DIR_SB, C_CLIENT_TICK_END, None, 0))
        if t_ms >= next_alive:
            next_alive += 15000.0
            body = struct.pack(">q", int(rng.integers(0, 2**62)))
            rows.append((ns + 4000, DIR_SB, C_KEEP_ALIVE, body, 8))
        if rng.random() < 0.06:
            body = _varint(int(rng.integers(1, 60))) + struct.pack(
                ">hhhB", *(int(v) for v in rng.integers(-300, 300, 3)), 1
            )
            rows.append((ns + 5000, DIR_CB, S_ENTITY_RELATIVE_MOVE, body, len(body)))
        if rng.random() < 0.02:
            rows.append((ns + 6000, DIR_CB, S_CHUNK_DATA, None, 0))
        if rng.random() < 0.003:
            rows.append((ns + 7000, DIR_SB, C_PLUGIN_MESSAGE, None, 0))  # privacy-stripped
    rows.sort(key=lambda r: r[0])
    n = len(rows)
    return SessionPackets(
        session_id=session_id,
        protocol=protocol,
        segment=np.zeros(n, dtype=np.uint32),
        seq=np.arange(n, dtype=np.uint64),
        offset_nanos=np.array([r[0] for r in rows], dtype=np.uint64),
        direction=np.array([r[1] for r in rows], dtype=np.uint8),
        state=np.full(n, STATE_PLAY, dtype=np.uint8),
        packet_type=np.array([r[2] for r in rows], dtype=np.uint32),
        payload_original_size=np.array([r[4] for r in rows], dtype=np.uint32),
        payloads=[r[3] for r in rows],
    )


@dataclass
class SyntheticRaw:
    sessions: list[SessionPackets]
    pseudonym: dict[bytes, bytes]  # session id -> pseudonym
    kind: dict[bytes, str]  # pseudonym -> legit | cheat | none
    variant: dict[bytes, str | None]  # pseudonym -> cheat variant
    started_at: dict[bytes, dt.datetime]
    contexts: dict[bytes, dict[int, str]]  # session id -> segment -> game context
    deks: dict[bytes, bytes]  # pseudonym -> DEK

    def sessions_table(self) -> pa.Table:
        ids = [s.session_id for s in self.sessions]
        return pa.table(
            {
                "session_id": pa.array(ids, pa.binary(16)),
                "pseudonym": pa.array([self.pseudonym[i] for i in ids], pa.binary(32)),
                "started_at": pa.array(
                    [self.started_at[i] for i in ids], pa.timestamp("us", tz="UTC")
                ),
                "client_protocol": pa.array([s.protocol for s in self.sessions], pa.int32()),
            }
        )

    def labels_table(self) -> pa.Table:
        labelled = [(p, k) for p, k in self.kind.items() if k != "none"]
        n = len(labelled)
        return pa.table(
            {
                "pseudonym": pa.array([p for p, _ in labelled], pa.binary(32)),
                "label": [k for _, k in labelled],
                "source": ["synthetic"] * n,
                "confidence": pa.array([1.0] * n, pa.float32()),
                "valid_from": pa.array([None] * n, pa.timestamp("us", tz="UTC")),
                "valid_to": pa.array([None] * n, pa.timestamp("us", tz="UTC")),
            }
        )

    def write_lake(self, root: Path, erased: frozenset[bytes] = frozenset()) -> dict[str, Path]:
        """Write ``events`` and (encrypted) ``raw`` Parquet files the way ingest would.

        Players in ``erased`` are written like any other; the caller drops their DEK to simulate
        crypto-shredding.
        """
        events_dir, raw_dir = root / "lake" / "events", root / "lake" / "raw"
        events_dir.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)
        ev_rows: dict[str, list] = {
            k: []
            for k in (
                "session_id", "segment", "seq", "offset_nanos", "direction", "state",
                "packet_type", "wire_packet_id", "payload_original_size", "event_kind",
            )
        }  # fmt: skip
        raw_rows: dict[str, list] = {
            k: []
            for k in (
                "session_id",
                "segment",
                "seq",
                "payload_original_size",
                "nonce",
                "ciphertext",
            )
        }
        rng = np.random.default_rng(12345)
        for sp in self.sessions:
            aead = AESGCM(self.deks[self.pseudonym[sp.session_id]])
            for i in range(len(sp)):
                ev_rows["session_id"].append(sp.session_id)
                ev_rows["segment"].append(int(sp.segment[i]))
                ev_rows["seq"].append(int(sp.seq[i]))
                ev_rows["offset_nanos"].append(int(sp.offset_nanos[i]))
                ev_rows["direction"].append(int(sp.direction[i]))
                ev_rows["state"].append(int(sp.state[i]))
                ev_rows["packet_type"].append(int(sp.packet_type[i]))
                ev_rows["wire_packet_id"].append(0)
                ev_rows["payload_original_size"].append(int(sp.payload_original_size[i]))
                ev_rows["event_kind"].append(0)
                payload = sp.payloads[i]
                if payload is not None:
                    nonce = rng.bytes(12)
                    raw_rows["session_id"].append(sp.session_id)
                    raw_rows["segment"].append(int(sp.segment[i]))
                    raw_rows["seq"].append(int(sp.seq[i]))
                    raw_rows["payload_original_size"].append(int(sp.payload_original_size[i]))
                    raw_rows["nonce"].append(nonce)
                    raw_rows["ciphertext"].append(
                        aead.encrypt(nonce, payload, payload_aad(sp.session_id, int(sp.seq[i])))
                    )
        events_schema = pa.schema(
            [
                ("session_id", SESSION_ID_TYPE), ("segment", pa.uint32()), ("seq", pa.uint64()),
                ("offset_nanos", pa.uint64()), ("direction", pa.uint8()), ("state", pa.uint8()),
                ("packet_type", pa.uint32()), ("wire_packet_id", pa.int32()),
                ("payload_original_size", pa.uint32()), ("event_kind", pa.uint8()),
            ]
        )  # fmt: skip
        raw_schema = pa.schema(
            [
                ("session_id", SESSION_ID_TYPE), ("segment", pa.uint32()), ("seq", pa.uint64()),
                ("payload_original_size", pa.uint32()), ("nonce", pa.binary(12)),
                ("ciphertext", pa.binary()),
            ]
        )  # fmt: skip
        paths = {"events": events_dir / "part-0.parquet", "raw": raw_dir / "part-0.parquet"}
        pq.write_table(pa.table(ev_rows, schema=events_schema), paths["events"])
        pq.write_table(pa.table(raw_rows, schema=raw_schema), paths["raw"])
        return paths


def generate(cfg: SyntheticRawConfig | None = None) -> SyntheticRaw:
    cfg = cfg or SyntheticRawConfig()
    rng = np.random.default_rng(cfg.seed)
    players = []
    for kind, count in (("legit", cfg.n_legit), ("cheat", cfg.n_cheat), ("none", cfg.n_unlabelled)):
        for i in range(count):
            variant = VARIANTS[i % len(VARIANTS)] if kind == "cheat" else None
            players.append((rng.bytes(32), kind, variant))
    out = SyntheticRaw([], {}, {}, {}, {}, {}, {})
    for p_i, (pseudonym, kind, variant) in enumerate(players):
        out.kind[pseudonym], out.variant[pseudonym] = kind, variant
        out.deks[pseudonym] = rng.bytes(32)
        player = _Player(np.random.default_rng(rng.integers(2**62)), variant, cfg.cheat_strength)
        protocol = int(cfg.protocols[p_i % len(cfg.protocols)])
        for s_i in range(cfg.sessions_per_player):
            sid = rng.bytes(16)
            sp = session_packets(player.rng, player, sid, protocol, cfg.seconds_per_session)
            out.sessions.append(sp)
            out.pseudonym[sid] = pseudonym
            out.started_at[sid] = _BASE_TIME + dt.timedelta(days=p_i % 20, hours=3 * s_i)
            out.contexts[sid] = {0: cfg.contexts[p_i % len(cfg.contexts)]}
    return out
