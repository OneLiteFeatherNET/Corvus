from __future__ import annotations

import pytest

from corvus_services.scoring.nats_io import (
    configured_filters,
    consumer_config,
    consumer_name,
    needs_update,
    score_stream_config,
)
from corvus_services.scoring.partitions import (
    filter_subjects,
    owned_partitions,
    partition_of,
    partition_token,
    replica_index,
)
from corvus_services.scoring.settings import ScoreSettings


def test_tokens_have_two_digits_for_the_default_partition_count():
    assert [partition_token(p, 16) for p in (0, 9, 15)] == ["00", "09", "15"]


def test_tokens_widen_when_more_than_100_partitions_exist():
    assert partition_token(5, 128) == "005"
    assert partition_token(127, 128) == "127"
    assert partition_token(99, 100) == "99"
    assert partition_token(100, 101) == "100"


def test_token_rejects_a_partition_outside_the_range():
    with pytest.raises(ValueError, match="outside"):
        partition_token(16, 16)


def test_partition_is_the_big_endian_head_of_the_session_id_modulo_the_count():
    assert partition_of(bytes([0, 0, 1, 2]) + bytes(12), 16) == 258 % 16
    assert partition_of(bytes([0xFF] * 16), 16) == 0xFFFFFFFF % 16


def test_replica_owns_the_partitions_congruent_to_its_index():
    assert owned_partitions(1, 4, 16) == [1, 5, 9, 13]
    assert owned_partitions(0, 1, 4) == [0, 1, 2, 3]


def test_replicas_cover_every_partition_exactly_once():
    owned = [p for i in range(3) for p in owned_partitions(i, 3, 16)]

    assert sorted(owned) == list(range(16))


def test_filter_subjects_use_the_partition_token_of_the_plugin():
    assert filter_subjects(1, 8, 16) == ["corvus.capture.v1.*.01", "corvus.capture.v1.*.09"]


def test_filter_subjects_for_128_partitions_use_three_digits():
    subjects = filter_subjects(3, 32, 128)

    assert subjects == [f"corvus.capture.v1.*.{p:03d}" for p in (3, 35, 67, 99)]


def test_replica_index_prefers_the_configuration():
    assert replica_index(2, 4, hostname="corvus-score-0") == 2


def test_replica_index_is_parsed_from_a_statefulset_hostname():
    assert replica_index(None, 4, hostname="corvus-score-3") == 3


def test_a_standalone_scorer_without_index_is_replica_zero():
    assert replica_index(None, 1, hostname="my-laptop") == 0


def test_several_replicas_without_a_determinable_index_fail():
    with pytest.raises(ValueError, match="cannot determine"):
        replica_index(None, 2, hostname="my-laptop")


def test_an_index_beyond_the_replica_count_fails():
    with pytest.raises(ValueError, match="below the replica count"):
        replica_index(None, 2, hostname="corvus-score-5")


def test_settings_reject_more_replicas_than_partitions():
    with pytest.raises(ValueError, match="must not exceed"):
        ScoreSettings(score_replicas=32, capture_partitions=16)


def test_consumer_is_durable_per_replica_with_deliver_new_and_its_filters():
    s = ScoreSettings(score_replicas=4, capture_partitions=16)

    config = consumer_config(s, 2)

    assert consumer_name(s, 2) == "corvus-score-2"
    assert config.durable_name == "corvus-score-2"
    assert config.deliver_policy.value == "new"
    assert config.ack_policy.value == "explicit"
    assert config.filter_subjects == [f"corvus.capture.v1.*.{p:02d}" for p in (2, 6, 10, 14)]


def test_consumer_with_changed_filters_needs_an_update():
    old = consumer_config(ScoreSettings(score_replicas=2, capture_partitions=16), 0)
    new = consumer_config(ScoreSettings(score_replicas=4, capture_partitions=16), 0)

    assert needs_update(old, new)
    assert not needs_update(new, new)


def test_a_single_filter_subject_is_compared_like_a_filter_list():
    class Legacy:
        filter_subjects = None
        filter_subject = "corvus.capture.v1.*.00"

    wanted = consumer_config(ScoreSettings(score_replicas=16, capture_partitions=16), 0)

    assert configured_filters(Legacy) == {"corvus.capture.v1.*.00"}
    assert not needs_update(Legacy, wanted)


def test_score_stream_has_the_documented_subjects_and_a_seven_day_age():
    config = score_stream_config(ScoreSettings())

    assert config.name == "CORVUS_SCORE"
    assert config.subjects == ["corvus.score.v1.>"]
    assert config.max_age == 7 * 24 * 3600
