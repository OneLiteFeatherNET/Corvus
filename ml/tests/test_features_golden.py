"""Regression: the feature stage still produces what the pre-refactor implementation produced.

The fixtures in ``tests/fixtures/golden`` were generated from ``golden_lake.py`` with the
implementation that predates the shared incremental featurizer (see ``golden_lake_cli.py``).
"""

import pyarrow.dataset as ds
import pyarrow.parquet as pq
import pytest

from corvus_ml.features.pipeline import TOKENS_DIR, WINDOWS_DIR, build
from golden_lake import GOLDEN_DIR, write_golden_events
from golden_lake_cli import GOLDEN_CONFIG, golden_segmap


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("golden")
    out = tmp / "out"
    build(write_golden_events(tmp), golden_segmap(), out, GOLDEN_CONFIG, group_size=1)
    return out


@pytest.mark.parametrize(
    ("directory", "fixture"), [(WINDOWS_DIR, "windows"), (TOKENS_DIR, "window_tokens")]
)
def test_output_equals_the_golden_table(built, directory, fixture):
    actual = ds.dataset(built / directory, format="parquet").to_table()
    expected = pq.read_table(GOLDEN_DIR / f"{fixture}.parquet")
    assert actual.schema == expected.schema, "column names or types changed"
    assert actual.num_rows == expected.num_rows
    assert actual.equals(expected), f"{fixture} differs from the golden fixture"


def test_golden_input_exercises_every_window_cut_and_both_tick_end_cases():
    windows = pq.read_table(GOLDEN_DIR / "windows.parquet").to_pylist()
    assert {w["cut"] for w in windows} == {"full", "duration", "end"}
    assert any(w["tick_end_rate"] is None for w in windows)
    assert any(w["tick_end_rate"] == 0.0 for w in windows)
    assert any(w["window_index"] > 0 and w["segment"] == 1 for w in windows)
