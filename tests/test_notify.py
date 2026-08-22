from secretary import notify


def test_dispatch_skips_disabled_channels(capsys):
    delivered = notify.dispatch(
        {"console": {"enabled": True}, "slack": {"enabled": False}}, "件名", "本文"
    )
    assert delivered == ["console"]
    assert "本文" in capsys.readouterr().out


def test_dispatch_continues_after_a_failure(capsys):
    # webhook_url が無いので slack は失敗するが、console は届くこと
    delivered = notify.dispatch(
        {"slack": {"enabled": True}, "console": {"enabled": True}}, "件名", "本文"
    )
    assert delivered == ["console"]


def test_dispatch_ignores_unknown_channel():
    assert notify.dispatch({"telepathy": {"enabled": True}}, "件名", "本文") == []


def test_dispatch_with_empty_config():
    assert notify.dispatch({}, "件名", "本文") == []
