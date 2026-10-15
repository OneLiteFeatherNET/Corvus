"""Configuration objects shared by the reader, the extractor and the window builder."""

from __future__ import annotations

from dataclasses import dataclass, fields

# Upper edges (milliseconds) of the log-spaced inter-frame gap buckets.
DEFAULT_DT_EDGES_MS: tuple[float, ...] = (
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
    20.0,
    35.0,
    50.0,
    75.0,
    100.0,
    200.0,
    500.0,
    1000.0,
    5000.0,
)


@dataclass(frozen=True)
class FeaturizerConfig:
    """Everything that influences the featurizer output (windows and tokens).

    Part of the :class:`~corvus_ml.features.spec.FeatureSpec` stored next to an exported model.
    """

    window: int = 256
    overlap: float = 0.5
    max_duration_s: float = 30.0
    min_frames: int = 16
    dt_edges_ms: tuple[float, ...] = DEFAULT_DT_EDGES_MS
    entity_cache: int = 256

    def __post_init__(self) -> None:
        if self.window < 1:
            raise ValueError("window must be >= 1")
        if not 0.0 <= self.overlap < 1.0:
            raise ValueError("overlap must be in [0, 1)")
        if self.max_duration_s <= 0:
            raise ValueError("max_duration_s must be > 0")
        if self.entity_cache < 1:
            raise ValueError("entity_cache must be >= 1")
        if list(self.dt_edges_ms) != sorted(set(self.dt_edges_ms)):
            raise ValueError("dt_edges_ms must be strictly increasing")

    def featurizer(self) -> FeaturizerConfig:
        """The featurizer-relevant part (drops the batch build knobs of a subclass)."""
        return FeaturizerConfig(**{f.name: getattr(self, f.name) for f in fields(FeaturizerConfig)})


@dataclass(frozen=True)
class FeatureConfig(FeaturizerConfig):
    """Offline build configuration: the featurizer config plus I/O knobs."""

    # Rows per Arrow batch while scanning the lake (memory knob, no effect on output).
    batch_rows: int = 65_536
    # Windows buffered before a Parquet row group is written (memory knob, no effect on output).
    flush_windows: int = 1024


@dataclass(frozen=True)
class S3Config:
    """Optional S3/MinIO settings; credentials come from the standard AWS environment."""

    endpoint_override: str | None = None
    region: str | None = None
    scheme: str = "https"
