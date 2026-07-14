from grok_account_manager.app import _fmt_time
from grok_account_manager.models import QuotaInfo


def test_fmt_time_iso_z():
    # UTC noon → local; just ensure no crash and no T/+noise
    out = _fmt_time("2026-07-12T07:22:01.352965+00:00")
    assert "T" not in out
    assert "+" not in out
    assert out.startswith("2026-07-12 ")
    assert len(out) == len("2026-07-12 15:22")


def test_fmt_time_empty():
    assert _fmt_time(None) == "—"
    assert _fmt_time("") == "—"


def test_quota_short_label_remaining():
    q = QuotaInfo(
        credits_used=745,
        credits_limit=15000,
        credits_remaining=14255,
        credit_usage_percent=5.0,
    )
    label = q.short_label()
    assert "剩" in label
    assert "14255" in label.replace(",", "") or "14,255" in label
    assert "95%" in label
    assert abs(q.remaining_percent() - (14255 / 15000 * 100)) < 0.01

    # percent-only accounts: invert used% → remaining%
    q2 = QuotaInfo(credit_usage_percent=19.0)
    assert abs(q2.remaining_percent() - 81.0) < 0.01
    assert "剩余 81%" in q2.short_label()
