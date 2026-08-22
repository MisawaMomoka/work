import pytest

from secretary.config import ConfigError, load_config


def test_env_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_ICS", "https://example.com/a.ics")
    path = tmp_path / "config.yaml"
    path.write_text(
        "profile:\n  timezone: Asia/Tokyo\n"
        "calendar:\n  ics_urls:\n    - \"${MY_ICS}\"\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.calendar["ics_urls"] == ["https://example.com/a.ics"]
    assert str(config.tz) == "Asia/Tokyo"


def test_missing_env_becomes_empty_string(tmp_path, monkeypatch):
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("notify:\n  slack:\n    webhook_url: \"${NOT_SET_ANYWHERE}\"\n", encoding="utf-8")
    assert load_config(path).notify["slack"]["webhook_url"] == ""


def test_work_hours_parsing(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("profile:\n  work_hours: [\"08:30\", \"17:45\"]\n", encoding="utf-8")
    start, end = load_config(path).work_hours
    assert (start.hour, start.minute, end.hour, end.minute) == (8, 30, 17, 45)


def test_defaults_when_sections_absent(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("{}\n", encoding="utf-8")
    config = load_config(path)
    assert str(config.tz) == "Asia/Tokyo"
    assert config.money == {}


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


def test_unknown_timezone_suggests_tzdata(tmp_path):
    """Windowsでは tzdata が無いと Asia/Tokyo すら引けないので、直し方を出す。"""
    path = tmp_path / "config.yaml"
    path.write_text("profile:\n  timezone: Asia/Nowhere\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="pip install tzdata"):
        _ = load_config(path).tz
