import pytest

from corvus_ml.cgroup import (
    available_cpus,
    cpu_limit,
    cpu_limit_v1,
    default_workers,
    parse_cpu_max,
    resolve_threads,
)


def test_parse_unlimited_returns_none():
    assert parse_cpu_max("max 100000") is None


def test_parse_two_cpus():
    assert parse_cpu_max("200000 100000\n") == 2.0


def test_parse_fractional_cpu():
    assert parse_cpu_max("50000 100000") == 0.5


@pytest.mark.parametrize("text", ["", "garbage", "abc def", "0 100000", "100000 0"])
def test_parse_unusable_content_returns_none(text):
    assert parse_cpu_max(text) is None


def test_missing_file_means_no_limit(tmp_path):
    assert cpu_limit(tmp_path / "absent") is None


def test_workers_follow_cgroup_limit_when_below_cpu_count(tmp_path):
    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("200000 100000")

    assert default_workers(cpu_max, process_cpu_count=16) == 2


def test_workers_round_fractional_limit_up(tmp_path):
    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("150000 100000")

    assert default_workers(cpu_max, process_cpu_count=16) == 2


def test_workers_never_exceed_cpu_count(tmp_path):
    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("800000 100000")

    assert default_workers(cpu_max, process_cpu_count=4) == 4


def test_workers_fall_back_to_cpu_count_when_unlimited(tmp_path):
    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("max 100000")

    assert default_workers(cpu_max, process_cpu_count=6) == 6


def test_workers_fall_back_to_cpu_count_when_file_missing(tmp_path):
    assert default_workers(tmp_path / "absent", process_cpu_count=3) == 3


def test_v1_quota_and_period_give_cpu_count(tmp_path):
    (tmp_path / "quota").write_text("400000\n")
    (tmp_path / "period").write_text("100000\n")

    assert cpu_limit_v1(tmp_path / "quota", tmp_path / "period") == 4.0


def test_v1_negative_quota_means_no_limit(tmp_path):
    (tmp_path / "quota").write_text("-1\n")
    (tmp_path / "period").write_text("100000\n")

    assert cpu_limit_v1(tmp_path / "quota", tmp_path / "period") is None


def test_v1_missing_files_mean_no_limit(tmp_path):
    assert cpu_limit_v1(tmp_path / "q", tmp_path / "p") is None


def _paths(tmp_path):
    return {
        "path": tmp_path / "cpu.max",
        "v1_quota": tmp_path / "quota",
        "v1_period": tmp_path / "period",
    }


def test_available_cpus_prefers_cgroup_v2(tmp_path):
    kw = _paths(tmp_path)
    kw["path"].write_text("300000 100000")
    (tmp_path / "quota").write_text("100000")
    (tmp_path / "period").write_text("100000")

    assert available_cpus(**kw, process_cpu_count=16) == 3


def test_available_cpus_falls_back_to_cgroup_v1(tmp_path):
    kw = _paths(tmp_path)
    (tmp_path / "quota").write_text("200000")
    (tmp_path / "period").write_text("100000")

    assert available_cpus(**kw, process_cpu_count=16) == 2


def test_available_cpus_without_any_limit_uses_process_cpu_count(tmp_path):
    assert available_cpus(**_paths(tmp_path), process_cpu_count=6) == 6


def test_available_cpus_never_exceeds_process_cpu_count(tmp_path):
    kw = _paths(tmp_path)
    kw["path"].write_text("800000 100000")

    assert available_cpus(**kw, process_cpu_count=2) == 2


def test_available_cpus_is_at_least_one(tmp_path):
    kw = _paths(tmp_path)
    kw["path"].write_text("10000 100000")

    assert available_cpus(**kw, process_cpu_count=4) == 1


def test_resolve_threads_auto_uses_all_available():
    assert resolve_threads("auto", available=4) == 4


def test_resolve_threads_keeps_explicit_value_within_limit():
    assert resolve_threads(2, available=4) == 2


def test_resolve_threads_clamps_to_available_with_warning(caplog):
    with caplog.at_level("WARNING", logger="corvus_ml.cgroup"):
        assert resolve_threads(16, available=4) == 4

    assert "16" in caplog.text
    assert "4 available" in caplog.text
