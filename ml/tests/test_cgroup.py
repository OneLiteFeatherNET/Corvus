import pytest

from corvus_ml.cgroup import cpu_limit, default_workers, parse_cpu_max


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
