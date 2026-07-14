from grok_account_manager.updater import parse_version, version_gt


def test_parse_version():
    assert parse_version("0.1.0")[:3] == (0, 1, 0)
    assert parse_version("v1.2.3")[:3] == (1, 2, 3)
    assert parse_version("release-2.0.1-beta")[:3] == (2, 0, 1)


def test_version_gt():
    assert version_gt("0.2.0", "0.1.0")
    assert version_gt("1.0.0", "0.9.9")
    assert not version_gt("0.1.0", "0.1.0")
    assert not version_gt("0.1.0", "0.2.0")
    assert version_gt("1.0.0", "1.0.0-beta")
