import os

from app.config import Settings
from app.logs.service import LogsService
from app.paths import build_paths, prepare_data_dirs


def test_latest_download_is_exact_full_file_not_combined_history(tmp_path):
    paths = build_paths(Settings(data_root=tmp_path, host="127.0.0.1", port=3000))
    prepare_data_dirs(paths)
    old = paths.studio_log_dir / "app.log.1"
    old.write_text("previous file\n")
    latest = paths.studio_log_dir / "app.log"
    content = "\r\n".join(f"line {number}" for number in range(1200)) + "\r\n\r\n"
    latest.write_bytes(content.encode())
    os.utime(old, (100, 100))
    os.utime(latest, (200, 200))
    service = LogsService(paths)
    source, body = service.download("application", latest=True)
    assert source.file_count == 2
    assert body == content
    assert "previous file" in service.download("application")[1]


def test_latest_profile_file_and_empty_source(tmp_path):
    paths = build_paths(Settings(data_root=tmp_path, host="127.0.0.1", port=3000))
    prepare_data_dirs(paths)
    service = LogsService(paths)
    assert service.download("application", latest=True)[1] == ""
    folder = paths.profiles_log_dir / "demo"
    folder.mkdir()
    first = folder / "10.log"
    second = folder / "20.log"
    first.write_text("old run\n")
    second.write_text("new run without newline")
    os.utime(first, (100, 100))
    os.utime(second, (200, 200))
    assert service.download("profile:demo", latest=True)[1] == "new run without newline"
