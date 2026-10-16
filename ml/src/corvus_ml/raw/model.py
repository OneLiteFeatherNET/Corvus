"""MEGABYTE-style hierarchy over packet bytes (PyTorch, CPU, ONNX-exportable).

    tokens of one packet --local encoder--> packet embedding (+ header token embeddings, + time)
    packets of one window --global transformer--> [CLS] --> head
    windows of one macro window --second-level transformer + pooling--> head   (optional)

The input of every model is one float32 tensor ``(batch, [macro,] packets, tokens + 1)``: token
ids (``0`` = padding) and, in the last channel, the packet time in seconds since the window start.
Attention is written out (matmul / softmax with an additive mask) instead of using
``nn.TransformerEncoder``: it exports to plain ONNX operators and padded packets cannot produce
NaN. The packet axis is dynamic, there is no index-based positional encoding: the position of a
packet in the global transformer comes from its time.

Optional variants (all off by default, see :class:`ModelConfig`): a position embedding conditioned
on the packet type, byte-span / whole-packet masking, an entropy-weighted byte loss, a JEPA latent
objective with an EMA target encoder, and a kNN memory bank that is combined with the score.
"""

from __future__ import annotations

import copy
import math
from dataclasses import asdict, dataclass

import numpy as np
import torch
import torch.nn.functional as F  # noqa: N812
from torch import nn

from corvus_ml.raw.layout import HEADER_LEN, MASK_ID, PAD_ID, PROTO_OTHER_ID, TokenLayout

_NEG = -1e4
OBJECTIVES = ("reconstruct", "jepa", "both")
JEPA_LOSSES = ("smooth_l1", "cosine")
COMBINES = ("none", "rank", "zsum")
PACKET_MASKS = ("partial", "full")


@dataclass(frozen=True)
class ModelConfig:
    vocab_size: int
    max_tokens: int = 48
    n_special: int = 0  # ids below are reserved (never masked as "bytes")
    proto_lo: int = 0  # ids in [proto_lo, proto_hi) or PROTO_OTHER are protocol tokens
    proto_hi: int = 0
    local_dim: int = 32
    local_layers: int = 1
    local_type: str = "transformer"  # or "cnn"
    local_heads: int = 2
    d_model: int = 64
    layers: int = 2
    heads: int = 4
    ffn_mult: int = 2
    dropout: float = 0.1
    time_freqs: int = 16
    time_min_s: float = 0.01
    time_max_s: float = 600.0
    macro_windows: int = 1
    macro_layers: int = 1
    macro_pool: str = "attention"  # or "mean"
    # masked-prediction objectives
    p_token: float = 0.15
    p_packet: float = 0.1
    first_tokens: int = 2
    max_masked: int = 2048  # cap on masked byte tokens per training batch (bounds the logits)
    score_period: int = 8  # anomaly score: every ``period``-th position is masked ...
    score_passes: int = 2  # ... in this many shifted passes
    # --- variants of the tokenizer ablation (defaults reproduce the original model) ---
    pkt_lo: int = 0  # ids in [pkt_lo, pkt_hi) are the [PKT_<n>] tokens
    pkt_hi: int = 0
    type_pos: bool = False  # byte position embedding conditioned on the packet type
    type_buckets: int = 64  # bucket 0 = unknown / masked type, 1.. = (type - 1) % (buckets - 1) + 1
    span_mean: float = 1.0  # mean length of masked byte spans (1 = independent positions)
    packet_mask: str = "partial"  # "full": masked packets lose all byte tokens, not only the first
    entropy_weight: bool = False  # weight the masked-byte loss by inverse empirical entropy
    entropy_score: bool = False  # ... and the anomaly score too (default: training loss only)
    objective: str = "reconstruct"  # "jepa": latent prediction of masked packets; "both": sum
    jepa_loss: str = "smooth_l1"  # or "cosine", on L2-normalised vectors
    ema_momentum: float = 0.99  # target encoder = EMA of the online encoder
    combine: str = "none"  # "rank" / "zsum": reconstruction score + kNN distance to a memory bank
    knn_bank: int = 256  # windows in the memory bank (fixed size, seeded subsample of training)
    knn_k: int = 5
    knn_weight: float = 0.5  # share of the kNN term in the combined score

    def __post_init__(self) -> None:
        if self.d_model % self.heads or self.local_dim % self.local_heads:
            raise ValueError("d_model must divide by heads and local_dim by local_heads")
        if self.local_type not in ("transformer", "cnn"):
            raise ValueError("local_type must be 'transformer' or 'cnn'")
        if self.macro_pool not in ("attention", "mean"):
            raise ValueError("macro_pool must be 'attention' or 'mean'")
        if self.max_tokens < HEADER_LEN + 2:
            raise ValueError("max_tokens is too small for a packet header")
        if not 0.0 <= self.p_token < 1.0 or not 0.0 <= self.p_packet <= 0.95:
            raise ValueError("p_token must be in [0, 1) and p_packet in [0, 0.95] (p_packet)")
        if self.span_mean < 1.0:
            raise ValueError("span_mean must be >= 1")
        if self.type_buckets < 2:
            raise ValueError("type_buckets must be >= 2")
        for name, allowed in (
            ("packet_mask", PACKET_MASKS), ("objective", OBJECTIVES),
            ("jepa_loss", JEPA_LOSSES), ("combine", COMBINES),
        ):  # fmt: skip
            if getattr(self, name) not in allowed:
                raise ValueError(f"{name} must be one of {allowed}")
        if not 0.0 <= self.ema_momentum < 1.0 or not 0.0 <= self.knn_weight <= 1.0:
            raise ValueError("ema_momentum must be in [0, 1) and knn_weight in [0, 1]")
        if self.knn_bank < 2 or self.knn_k < 1:
            raise ValueError("knn_bank must be >= 2 and knn_k >= 1")

    @property
    def full_packet_mask(self) -> bool:
        """Masked packets lose every byte token (the latent target needs it)."""
        return self.packet_mask == "full" or self.objective != "reconstruct"

    @classmethod
    def for_layout(cls, layout: TokenLayout, vocab_size: int, max_tokens: int, **kw) -> ModelConfig:
        return cls(
            vocab_size=vocab_size,
            max_tokens=max_tokens,
            n_special=layout.n_special,
            proto_lo=layout.proto_base,
            proto_hi=layout.proto_base + len(layout.protocols),
            pkt_lo=layout.pkt_base,
            pkt_hi=layout.pkt_base + layout.packet_type_max,
            **kw,
        )

    def to_dict(self) -> dict:
        return asdict(self)


def type_bucket(ids: torch.Tensor, cfg: ModelConfig) -> torch.Tensor:
    """Bucket of the packet type token (``ids[..., 1]``): 0 for unknown or masked types."""
    tid = ids[..., 1]
    known = (tid >= cfg.pkt_lo) & (tid < cfg.pkt_hi)
    bucket = 1 + torch.remainder(tid - cfg.pkt_lo, cfg.type_buckets - 1)
    return torch.where(known, bucket, torch.zeros_like(bucket))


class Attention(nn.Module):
    def __init__(self, dim: int, heads: int, dropout: float) -> None:
        super().__init__()
        self.heads, self.dh = heads, dim // heads
        self.qkv = nn.Linear(dim, 3 * dim)
        self.out = nn.Linear(dim, dim)
        self.drop = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
        b, length, dim = x.shape
        qkv = self.qkv(x).reshape(b, length, 3, self.heads, self.dh).permute(2, 0, 3, 1, 4)
        q, k, v = qkv[0], qkv[1], qkv[2]
        scores = (q @ k.transpose(-1, -2)) / math.sqrt(self.dh)
        bias = (1.0 - valid.to(scores.dtype))[:, None, None, :] * _NEG
        att = self.drop(torch.softmax(scores + bias, dim=-1))
        return self.out((att @ v).transpose(1, 2).reshape(b, length, dim))


class Block(nn.Module):
    """Pre-norm transformer block."""

    def __init__(self, dim: int, heads: int, mult: int, dropout: float) -> None:
        super().__init__()
        self.n1, self.n2 = nn.LayerNorm(dim), nn.LayerNorm(dim)
        self.attn = Attention(dim, heads, dropout)
        self.ff = nn.Sequential(
            nn.Linear(dim, mult * dim), nn.GELU(), nn.Dropout(dropout), nn.Linear(mult * dim, dim)
        )
        self.drop = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
        x = x + self.drop(self.attn(self.n1(x), valid))
        return x + self.drop(self.ff(self.n2(x)))


class LocalCnn(nn.Module):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.c1 = nn.Conv1d(dim, dim, 3, padding=1)
        self.c2 = nn.Conv1d(dim, dim, 3, padding=2, dilation=2)

    def forward(self, x: torch.Tensor, valid: torch.Tensor) -> torch.Tensor:
        m = valid.to(x.dtype)[..., None]
        h = F.gelu(self.c1((x * m).transpose(1, 2)))
        h = F.gelu(self.c2(h)).transpose(1, 2)
        return x + h * m


class MicroNet(nn.Module):
    """Packets of one window -> window embedding. Also returns what the objectives need."""

    def __init__(self, cfg: ModelConfig) -> None:
        super().__init__()
        self.cfg = cfg
        dl, d = cfg.local_dim, cfg.d_model
        self.embed = nn.Embedding(cfg.vocab_size, dl, padding_idx=PAD_ID)
        self.pos = nn.Parameter(torch.zeros(cfg.max_tokens, dl))
        nn.init.normal_(self.pos, std=0.02)
        if cfg.type_pos:
            # one row per (type bucket, byte offset); a gather on a computed index
            self.type_pos = nn.Embedding(cfg.type_buckets * cfg.max_tokens, dl)
            nn.init.normal_(self.type_pos.weight, std=0.02)
        if cfg.local_type == "transformer":
            self.local = nn.ModuleList(
                Block(dl, cfg.local_heads, cfg.ffn_mult, cfg.dropout)
                for _ in range(cfg.local_layers)
            )
        else:
            self.local = nn.ModuleList(LocalCnn(dl) for _ in range(cfg.local_layers))
        self.local_norm = nn.LayerNorm(dl)
        self.fuse = nn.Sequential(nn.Linear((1 + HEADER_LEN) * dl, d), nn.GELU(), nn.Linear(d, d))
        self.time_proj = nn.Linear(2 * cfg.time_freqs, d)
        periods = torch.logspace(
            math.log10(cfg.time_min_s), math.log10(cfg.time_max_s), cfg.time_freqs
        )
        self.register_buffer("omega", 2 * math.pi / periods)
        self.cls = nn.Parameter(torch.zeros(1, 1, d))
        self.blocks = nn.ModuleList(
            Block(d, cfg.heads, cfg.ffn_mult, cfg.dropout) for _ in range(cfg.layers)
        )
        self.out_norm = nn.LayerNorm(d)

    def position_embedding(self, ids: torch.Tensor) -> torch.Tensor:
        """``(N, P, T, local_dim)``: offset embedding, plus one per (packet type, offset)."""
        n, p, length = ids.shape
        pos = self.pos[:length].expand(n, p, length, -1)
        if not self.cfg.type_pos:
            return pos
        index = type_bucket(ids, self.cfg)[..., None] * self.cfg.max_tokens + torch.arange(length)
        return pos + self.type_pos(index)

    def time_encoding(self, t: torch.Tensor) -> torch.Tensor:
        ang = t[..., None] * self.omega
        return self.time_proj(torch.cat([torch.sin(ang), torch.cos(ang)], dim=-1))

    def forward(self, ids: torch.Tensor, t: torch.Tensor):
        """``ids`` (N, P, T) long, ``t`` (N, P) -> (cls (N, d), per-packet (N, P, d),
        per-token local (N, P, T, dl), packet validity (N, P))."""
        n, p, length = ids.shape
        tok_valid = ids != PAD_ID
        valid = tok_valid[..., 0]
        h = (self.embed(ids) + self.position_embedding(ids)).reshape(n * p, length, -1)
        flat_valid = tok_valid.reshape(n * p, length)
        for layer in self.local:
            h = layer(h, flat_valid)
        h = self.local_norm(h).reshape(n, p, length, -1)
        m = tok_valid.to(h.dtype)[..., None]
        pooled = (h * m).sum(2) / m.sum(2).clamp(min=1.0)
        header = self.embed(ids[..., :HEADER_LEN]).reshape(n, p, -1)
        pk = self.fuse(torch.cat([pooled, header], dim=-1)) + self.time_encoding(t)
        seq = torch.cat([self.cls.expand(n, 1, -1), pk], dim=1)
        seq_valid = torch.cat([torch.ones_like(valid[:, :1]), valid], dim=1)
        for block in self.blocks:
            seq = block(seq, seq_valid)
        seq = self.out_norm(seq)
        return seq[:, 0], seq[:, 1:], h, valid


def nll(logits: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    """Cross entropy per row (Gather based, so it also exports for zero rows)."""
    return -torch.log_softmax(logits, dim=-1).gather(1, target[:, None]).squeeze(1)


def segment_sum(values: torch.Tensor, index: torch.Tensor, n: int) -> torch.Tensor:
    """Sum of ``values`` (K,) per window index (K,) into (n,); a matmul, so it exports cleanly."""
    onehot = (index[:, None] == torch.arange(n)[None, :]).to(values.dtype)
    return values @ onehot


def split_input(x: torch.Tensor, max_tokens: int) -> tuple[torch.Tensor, torch.Tensor]:
    return x[..., :max_tokens].round().long(), x[..., max_tokens]


class RawHierNet(nn.Module):
    """Supervised model: window (or macro window) -> probability of cheating."""

    def __init__(self, cfg: ModelConfig) -> None:
        super().__init__()
        self.cfg = cfg
        d = cfg.d_model
        self.micro = MicroNet(cfg)
        if cfg.macro_windows > 1:
            self.win_pos = nn.Parameter(torch.zeros(cfg.macro_windows, d))
            nn.init.normal_(self.win_pos, std=0.02)
            self.macro = nn.ModuleList(
                Block(d, cfg.heads, cfg.ffn_mult, cfg.dropout) for _ in range(cfg.macro_layers)
            )
            self.macro_norm = nn.LayerNorm(d)
            self.pool_q = nn.Linear(d, 1)
        self.head = nn.Sequential(nn.Dropout(cfg.dropout), nn.Linear(d, 1))

    def embed(self, ids: torch.Tensor, t: torch.Tensor) -> torch.Tensor:
        if self.cfg.macro_windows == 1:
            return self.micro(ids, t)[0]
        n, m, p, length = ids.shape
        cls, _, _, valid = self.micro(ids.reshape(n * m, p, length), t.reshape(n * m, p))
        win = cls.reshape(n, m, -1) + self.win_pos
        win_valid = valid.any(dim=-1).reshape(n, m)
        for block in self.macro:
            win = block(win, win_valid)
        win = self.macro_norm(win)
        mask = win_valid.to(win.dtype)
        if self.cfg.macro_pool == "mean":
            weights = mask
        else:
            weights = torch.softmax(self.pool_q(win).squeeze(-1) + (1.0 - mask) * _NEG, dim=-1)
            weights = weights * mask
        weights = weights / weights.sum(-1, keepdim=True).clamp(min=1e-6)
        return (win * weights[..., None]).sum(1)

    def logits_ids(self, ids: torch.Tensor, t: torch.Tensor) -> torch.Tensor:
        return self.head(self.embed(ids, t)).squeeze(-1)

    def logits(self, x: torch.Tensor) -> torch.Tensor:
        return self.logits_ids(*split_input(x, self.cfg.max_tokens))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return torch.sigmoid(self.logits(x))


def _std(v: torch.Tensor) -> torch.Tensor:
    return v.std(unbiased=False).clamp(min=1e-6)


def _cdf(score: torch.Tensor, ref: torch.Tensor, scale: torch.Tensor) -> torch.Tensor:
    """Smoothed share of the reference scores below ``score`` (a sigmoid step of width
    ``0.1 * scale`` per reference): rank-like, but float noise cannot flip a whole step."""
    return torch.sigmoid((score[:, None] - ref[None, :]) / (0.1 * scale)).mean(-1)


def _smooth_l1(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    """Huber loss (beta 1) summed over the last axis; ``smooth_l1_loss`` has no ONNX operator."""
    d = (a - b).abs()
    return torch.where(d < 1.0, 0.5 * d * d, d - 0.5).sum(-1)


class RawAnomalyNet(nn.Module):
    """Self-supervised model: masked byte-token and masked packet prediction.

    Training masks randomly. The exported score masks fixed positions (every ``score_period``-th
    packet / token, ``score_passes`` shifted passes) and returns the mean prediction loss of the
    window, so a window the model cannot reconstruct scores high.

    Variants (``ModelConfig``): ``objective`` ``"jepa"`` predicts the embedding of a masked packet
    from an EMA copy of the encoder instead of its bytes (``"both"`` adds the two); ``combine``
    adds the kNN distance of the window embedding to a memory bank (:meth:`fit_memory`);
    ``entropy_weight`` weights the masked-byte loss per (packet type bucket, offset)
    (:meth:`set_byte_weights`).
    """

    def __init__(self, cfg: ModelConfig) -> None:
        super().__init__()
        if cfg.macro_windows != 1:
            raise ValueError("the anomaly model works on single windows (macro_windows = 1)")
        self.cfg = cfg
        d, dl, v = cfg.d_model, cfg.local_dim, cfg.vocab_size
        self.micro = MicroNet(cfg)
        if cfg.objective != "jepa":
            self.to_local = nn.Linear(d, dl)
            self.tok_head = nn.Linear(dl, v)
            self.pkt_head = nn.Linear(d, v)
            self.first_head = nn.Linear(d, cfg.first_tokens * v)
        if cfg.objective != "reconstruct":
            self.target = copy.deepcopy(self.micro).requires_grad_(False).eval()
            hidden = cfg.ffn_mult * d
            self.predictor = nn.Sequential(nn.Linear(d, hidden), nn.GELU(), nn.Linear(hidden, d))
        if cfg.entropy_weight:
            self.register_buffer("byte_w", torch.ones(cfg.type_buckets, cfg.max_tokens))
        if cfg.combine != "none":
            self.register_buffer("memory_ready", torch.zeros(()))
            self.register_buffer("bank", torch.zeros(cfg.knn_bank, d))
            self.register_buffer("ref_recon", torch.zeros(cfg.knn_bank))
            self.register_buffer("ref_knn", torch.zeros(cfg.knn_bank))
            self.register_buffer("ref_stats", torch.tensor([0.0, 1.0, 0.0, 1.0]))

    def train(self, mode: bool = True):
        super().train(mode)
        if hasattr(self, "target"):
            self.target.eval()  # the target encoder never uses dropout
        return self

    def _load_from_state_dict(self, state_dict, prefix, *args, **kwargs):
        # The memory bank holds min(knn_bank, windows) rows: take the size of the saved one.
        for name in ("bank", "ref_recon", "ref_knn"):
            if prefix + name in state_dict and name in self._buffers:
                self._buffers[name] = torch.zeros_like(state_dict[prefix + name])
        super()._load_from_state_dict(state_dict, prefix, *args, **kwargs)

    # --- stored statistics ---------------------------------------------------------------------

    def set_byte_weights(self, weights: np.ndarray) -> None:
        """Per (type bucket, offset) loss weights computed on the training set."""
        w = torch.as_tensor(np.asarray(weights), dtype=torch.float32)
        if "byte_w" not in self._buffers or w.shape != self.byte_w.shape:
            raise ValueError("entropy_weight must be enabled and the weights must be (buckets, T)")
        self.byte_w = w.clone()

    @torch.no_grad()
    def sync_target(self) -> None:
        """Copy the online encoder into the target encoder (after loading a checkpoint)."""
        if hasattr(self, "target"):
            self.target.load_state_dict(self.micro.state_dict())

    @torch.no_grad()
    def _ema_update(self) -> None:
        m = self.cfg.ema_momentum
        for pt, po in zip(self.target.parameters(), self.micro.parameters(), strict=True):
            pt.mul_(m).add_(po.detach(), alpha=1.0 - m)

    # --- masking -------------------------------------------------------------------------------

    def _token_mask(self, shape: torch.Size) -> torch.Tensor:
        """Random byte mask: independent positions, or spans of mean length ``span_mean``."""
        cfg = self.cfg
        if cfg.span_mean <= 1.0:
            return torch.rand(shape) < cfg.p_token
        # Two-state chain: a span starts with probability q and continues with 1 - 1/span_mean;
        # q is chosen so that the stationary share of masked positions equals p_token.
        span = cfg.span_mean
        q = min(1.0, cfg.p_token / (span * (1.0 - cfg.p_token)))
        start = torch.rand(shape) < q
        cont = torch.rand(shape) < 1.0 - 1.0 / span
        on = torch.zeros(shape[:-1], dtype=torch.bool)
        cols = []
        for j in range(shape[-1]):
            on = (on & cont[..., j]) | (~on & start[..., j])
            cols.append(on)
        return torch.stack(cols, dim=-1)

    def _masks(self, ids: torch.Tensor, valid: torch.Tensor, train: bool, shift: int):
        cfg = self.cfg
        n, p, length = ids.shape
        is_byte = ids >= cfg.n_special
        if train:
            tok = is_byte & self._token_mask(ids.shape)
            pkt = valid & (torch.rand(n, p) < cfg.p_packet)
        else:
            j = torch.arange(length).view(1, 1, length)
            i = torch.arange(p).view(1, p, 1)
            tok = is_byte & ((j + 3 * i + shift) % cfg.score_period == 0)
            pkt = valid & ((torch.arange(p).view(1, p) + shift) % cfg.score_period == 0)
        if cfg.full_packet_mask:
            tok = tok | (is_byte & pkt[..., None])
        return tok, pkt

    def _first_positions(self, ids: torch.Tensor) -> torch.Tensor:
        """Index of the first payload token per packet (after an optional protocol token)."""
        cfg = self.cfg
        h = ids[..., HEADER_LEN]
        has_proto = ((h >= cfg.proto_lo) & (h < cfg.proto_hi)) | (h == PROTO_OTHER_ID)
        return HEADER_LEN + has_proto.long()

    def _byte_loss_terms(self, ids, tok_m, hg, lo, train: bool):
        """Masked byte-token loss per window: sum and weight (count, or sum of the weights)."""
        cfg = self.cfg
        n, p, length = ids.shape
        dl = cfg.local_dim
        tok_idx = tok_m.reshape(-1).nonzero().squeeze(1)
        if train and tok_idx.shape[0] > cfg.max_masked:
            tok_idx = tok_idx[torch.randperm(tok_idx.shape[0])[: cfg.max_masked]]
        pk_of_tok = torch.div(tok_idx, length, rounding_mode="floor")
        win_of_tok = torch.div(pk_of_tok, p, rounding_mode="floor")
        local = lo.reshape(n * p * length, dl).index_select(0, tok_idx)
        glob = self.to_local(hg).reshape(n * p, dl).index_select(0, pk_of_tok)
        target = ids.reshape(-1).index_select(0, tok_idx)
        tok_loss = nll(self.tok_head(local + glob), target)
        weight = torch.ones_like(tok_loss)
        if cfg.entropy_weight and (train or cfg.entropy_score):
            bucket = type_bucket(ids, cfg).reshape(-1).index_select(0, pk_of_tok)
            cell = bucket * cfg.max_tokens + (tok_idx - pk_of_tok * length)
            weight = self.byte_w.reshape(-1).index_select(0, cell)
        return segment_sum(tok_loss * weight, win_of_tok, n), segment_sum(weight, win_of_tok, n)

    def _terms(self, ids, t, train: bool, shift: int):
        """Per-window sums and weights of the prediction losses (one pair per loss term)."""
        cfg = self.cfg
        n, p, length = ids.shape
        valid = ids[..., 0] != PAD_ID
        tok_m, pkt_m = self._masks(ids, valid, train, shift)
        first_pos = self._first_positions(ids)  # (N, P)
        k = cfg.first_tokens
        offs = torch.arange(k).view(1, 1, k)
        fpos = (first_pos[..., None] + offs).clamp(max=length - 1)  # (N, P, k)
        ftgt = torch.gather(ids, 2, fpos)
        fvalid = (ftgt >= cfg.n_special) & pkt_m[..., None]
        in_first = torch.zeros_like(ids, dtype=torch.bool)
        in_first = in_first.scatter(2, fpos, fvalid)
        masked = torch.where(
            tok_m | in_first | ((torch.arange(length) == 1).view(1, 1, length) & pkt_m[..., None]),
            torch.full_like(ids, MASK_ID),
            ids,
        )
        _, hg, lo, _ = self.micro(masked, t)

        # Everything below picks rows by flat index (Gather) instead of advanced indexing: the
        # exported graph must also run when nothing is masked (a window without byte tokens).
        d = cfg.d_model
        pk_idx = pkt_m.reshape(-1).nonzero().squeeze(1)
        win_of_pkt = torch.div(pk_idx, p, rounding_mode="floor")
        feats = hg.reshape(n * p, d).index_select(0, pk_idx)
        terms = []
        if cfg.objective != "jepa":
            # masked byte tokens, predicted from the local state plus the packet's global state
            terms.append(self._byte_loss_terms(ids, tok_m, hg, lo, train))
            # masked packets: packet type and the first payload tokens
            pkt_loss = nll(self.pkt_head(feats), ids[..., 1].reshape(-1).index_select(0, pk_idx))
            terms.append(
                (
                    segment_sum(pkt_loss, win_of_pkt, n),
                    segment_sum(torch.ones_like(pkt_loss), win_of_pkt, n),
                )
            )
            flogits = self.first_head(feats).reshape(-1, cfg.vocab_size)
            ft = ftgt.reshape(n * p, k).index_select(0, pk_idx)
            fv = fvalid.reshape(n * p, k).index_select(0, pk_idx).to(feats.dtype)
            floss = nll(flogits, ft.reshape(-1)).reshape(-1, k) * fv
            terms.append(
                (segment_sum(floss.sum(1), win_of_pkt, n), segment_sum(fv.sum(1), win_of_pkt, n))
            )
        if cfg.objective != "reconstruct":
            # latent prediction: the target is the embedding of the unmasked packet (stop-gradient)
            with torch.no_grad():
                _, ht, _, _ = self.target(ids, t)
                tgt = F.normalize(ht.reshape(n * p, d).index_select(0, pk_idx), dim=-1)
            pred = F.normalize(self.predictor(feats), dim=-1)
            cosine = 1.0 - (pred * tgt).sum(-1)
            err = cosine if cfg.jepa_loss == "cosine" else _smooth_l1(pred, tgt)
            terms.append(
                (segment_sum(err, win_of_pkt, n), segment_sum(torch.ones_like(err), win_of_pkt, n))
            )
        return terms

    def pretrain_loss(self, ids: torch.Tensor, t: torch.Tensor) -> torch.Tensor:
        """Scalar loss: random masks while training, the fixed score masks in eval mode."""
        if self.training and self.cfg.objective != "reconstruct":
            self._ema_update()  # target = EMA of the weights the previous optimiser step produced
        terms = self._terms(ids, t, self.training, 0)
        total = ids.new_zeros(()).float()
        for s, c in terms:
            total = total + s.sum() / c.sum().clamp(min=1.0)
        return total

    def recon_score_ids(self, ids: torch.Tensor, t: torch.Tensor) -> torch.Tensor:
        cfg = self.cfg
        score = torch.zeros(ids.shape[0])
        for i in range(cfg.score_passes):
            shift = (i * cfg.score_period) // cfg.score_passes
            for s, c in self._terms(ids, t, False, shift):
                score = score + s / c.clamp(min=1.0)
        return score / cfg.score_passes

    # --- kNN memory ----------------------------------------------------------------------------

    def window_embedding(self, ids: torch.Tensor, t: torch.Tensor) -> torch.Tensor:
        """L2-normalised mean of the packet embeddings of the (unmasked) window."""
        _, hg, _, valid = self.micro(ids, t)
        m = valid.to(hg.dtype)[..., None]
        return F.normalize((hg * m).sum(1) / m.sum(1).clamp(min=1.0), dim=-1)

    def _knn_distance(self, sims: torch.Tensor, usable: int) -> torch.Tensor:
        """Mean cosine distance to the ``knn_k`` most similar of ``usable`` bank entries."""
        k = max(1, min(self.cfg.knn_k, usable))
        return (1.0 - sims.topk(k, dim=-1).values).mean(-1)

    @torch.no_grad()
    def fit_memory(self, ids: np.ndarray, time_s: np.ndarray, seed: int = 0, chunk: int = 32):
        """Fill the memory bank with a seeded subsample of training windows and store the score
        distributions (reconstruction and leave-one-out kNN) the combination is scaled with."""
        if self.cfg.combine == "none":
            raise ValueError("set combine to 'rank' or 'zsum' to use a memory bank")
        was_training = self.training
        self.eval()
        n = len(ids)
        pick = np.sort(np.random.default_rng(seed).choice(n, min(self.cfg.knn_bank, n), False))
        embs, recon = [], []
        for i in range(0, len(pick), chunk):
            sel = pick[i : i + chunk]
            x = torch.from_numpy(ids[sel].astype(np.int64))
            tt = torch.from_numpy(time_s[sel].astype(np.float32))
            embs.append(self.window_embedding(x, tt))
            recon.append(self.recon_score_ids(x, tt))
        emb, recon_s = torch.cat(embs), torch.cat(recon)
        sims = emb @ emb.T
        sims.fill_diagonal_(-2.0)  # leave one out: a window is not its own neighbour
        knn_s = self._knn_distance(sims, len(emb) - 1) if len(emb) > 1 else torch.zeros(1)
        self._buffers["bank"] = emb
        self._buffers["ref_recon"] = recon_s.sort().values
        self._buffers["ref_knn"] = knn_s.sort().values
        self._buffers["ref_stats"] = torch.stack(
            [recon_s.mean(), _std(recon_s), knn_s.mean(), _std(knn_s)]
        )
        self._buffers["memory_ready"] = torch.ones(())
        self.train(was_training)

    def score_ids(self, ids: torch.Tensor, t: torch.Tensor) -> torch.Tensor:
        score = self.recon_score_ids(ids, t)
        if self.cfg.combine == "none" or not bool(self.memory_ready):
            return score
        knn = self._knn_distance(self.window_embedding(ids, t) @ self.bank.T, len(self.bank))
        w = self.cfg.knn_weight
        if self.cfg.combine == "rank":  # smoothed empirical CDF against the training windows
            sd_r, sd_k = self.ref_stats[1], self.ref_stats[3]
            return (1.0 - w) * _cdf(score, self.ref_recon, sd_r) + w * _cdf(knn, self.ref_knn, sd_k)
        mu_r, sd_r, mu_k, sd_k = self.ref_stats.unbind()
        return (1.0 - w) * (score - mu_r) / sd_r + w * (knn - mu_k) / sd_k

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.score_ids(*split_input(x, self.cfg.max_tokens))
