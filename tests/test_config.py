from collector.config import get_kaken_appid, load_env


def test_load_env(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# comment\nKAKEN_APPID = abc123 \n\nBROKEN_LINE\nX=1\n",
                 encoding="utf-8")
    env = load_env(str(p))
    assert env == {"KAKEN_APPID": "abc123", "X": "1"}


def test_load_env_missing_file(tmp_path):
    assert load_env(str(tmp_path / "nope.env")) == {}


def test_get_kaken_appid_prefers_environ(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    p.write_text("KAKEN_APPID=from_file\n", encoding="utf-8")
    monkeypatch.setenv("KAKEN_APPID", "from_env")
    assert get_kaken_appid(str(p)) == "from_env"
    monkeypatch.delenv("KAKEN_APPID")
    assert get_kaken_appid(str(p)) == "from_file"
    assert get_kaken_appid(str(tmp_path / "nope.env")) is None
