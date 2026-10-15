import json

from builders import frame, simple_session, write_capture
from corvus_ml.pilot import EXIT_OK, EXIT_PRIVACY, EXIT_USAGE, find_captures, main


def test_clean_capture_exits_zero_and_writes_json(tmp_path, capsys):
    write_capture(tmp_path / "2026-10-15" / "a.cvc", *simple_session([frame(1), frame(2)]))
    out = tmp_path / "report.json"

    code = main(["analyze", str(tmp_path), "--json", str(out), "--workers", "1"])

    assert code == EXIT_OK
    assert json.loads(out.read_text())["overall"]["frames"] == 2
    assert "== privacy: PASS" in capsys.readouterr().out


def test_chat_packet_in_capture_exits_non_zero(tmp_path):
    write_capture(tmp_path / "a.cvc", *simple_session([frame(1, packet_type=42)]))
    denylist = tmp_path / "deny.json"
    denylist.write_text(json.dumps({"packet_types": [42]}))

    code = main(["analyze", str(tmp_path), "--denylist", str(denylist), "--workers", "1"])

    assert code == EXIT_PRIVACY


def test_directory_without_captures_is_a_usage_error(tmp_path, capsys):
    code = main(["analyze", str(tmp_path)])

    assert code == EXIT_USAGE
    assert "no .cvc files" in capsys.readouterr().err


def test_zero_workers_is_a_usage_error(tmp_path):
    write_capture(tmp_path / "a.cvc", *simple_session([frame(1)]))

    assert main(["analyze", str(tmp_path), "--workers", "0"]) == EXIT_USAGE


def test_truncated_file_is_reported_but_does_not_fail(tmp_path):
    path = write_capture(tmp_path / "a.cvc", *simple_session([frame(1)]))
    path.write_bytes(path.read_bytes()[:-2])
    out = tmp_path / "r.json"

    code = main(["analyze", str(tmp_path), "--json", str(out), "--workers", "1"])

    assert code == EXIT_OK
    assert json.loads(out.read_text())["files"]["truncated"] == 1


def test_find_captures_ignores_other_files_and_recurses(tmp_path):
    write_capture(tmp_path / "d1" / "a.cvc", *simple_session([frame(1)]))
    (tmp_path / "d1" / "notes.txt").write_text("x")

    assert [p.name for p in find_captures(tmp_path)] == ["a.cvc"]
