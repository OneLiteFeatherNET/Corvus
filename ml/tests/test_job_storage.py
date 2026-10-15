"""Object store behaviour on a local filesystem (the same code path as S3)."""

from corvus_ml.jobs.context import S3Settings
from corvus_ml.jobs.storage import local_store, s3_filesystem


def test_files_round_trip_through_a_prefix(tmp_path):
    store = local_store(tmp_path / "bucket")
    source = tmp_path / "src"
    (source / "windows").mkdir(parents=True)
    (source / "windows" / "part-0.parquet").write_bytes(b"w")
    (source / "feature_spec.json").write_bytes(b"{}")

    keys = store.upload_dir("features/abc", source)

    assert keys == ["features/abc/feature_spec.json", "features/abc/windows/part-0.parquet"]
    assert store.list_keys("features/abc") == keys
    assert store.get_bytes("features/abc/feature_spec.json") == b"{}"
    written = store.download_dir("features/abc", tmp_path / "dst")
    assert sorted(p.relative_to(tmp_path / "dst").as_posix() for p in written) == [
        "feature_spec.json",
        "windows/part-0.parquet",
    ]


def test_download_can_skip_parts(tmp_path):
    store = local_store(tmp_path / "bucket")
    store.put_bytes("p/window_tokens/part-0.parquet", b"t")
    store.put_bytes("p/windows/part-0.parquet", b"w")

    store.download_dir("p", tmp_path / "dst", skip=lambda rel: rel.startswith("window_tokens/"))

    assert not (tmp_path / "dst" / "window_tokens").exists()
    assert (tmp_path / "dst" / "windows" / "part-0.parquet").is_file()


def test_delete_prefix_removes_only_that_prefix_and_tolerates_a_missing_one(tmp_path):
    store = local_store(tmp_path / "bucket")
    store.put_bytes("features/a/x", b"1")
    store.put_bytes("features/b/x", b"2")

    store.delete_prefix("features/a")
    store.delete_prefix("features/never-existed")

    assert store.list_keys("features") == ["features/b/x"]


def test_exists_distinguishes_files_from_missing_keys(tmp_path):
    store = local_store(tmp_path / "bucket")
    store.put_bytes("a/b", b"1")

    assert store.exists("a/b")
    assert not store.exists("a/c")
    assert not store.exists("a")


def test_an_s3_filesystem_is_built_from_the_endpoint_url_without_connecting():
    fs = s3_filesystem(S3Settings("http://minio:9000", "access", "secret", "lake", "us-east-1"))

    assert type(fs).__name__ == "S3FileSystem"
