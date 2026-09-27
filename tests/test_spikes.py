from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from inbox.categories import SpikeConfig, complaints, detect_spikes, raise_spikes
from inbox.store import connect

NOW = datetime(2017, 11, 8, 18, 0, tzinfo=timezone.utc)


def quiet_week(cat="bags", per_day=4):
    rows = []
    for d in range(1, 8):
        for i in range(per_day):
            rows.append((NOW - timedelta(days=d, hours=i * 5), f"{cat}-{d}-{i}", cat))
    return rows


def frame(rows):
    return pd.DataFrame(rows, columns=["ts", "customer_id", "category"])


def test_burst_of_distinct_customers_is_a_spike():
    burst = [(NOW - timedelta(minutes=10 * i), f"new-{i}", "login") for i in range(20)]
    spikes = detect_spikes(frame(quiet_week() + quiet_week("login", 1) + burst), NOW)
    assert [s["category"] for s in spikes] == ["login"]
    assert spikes[0]["customers"] == 20


def test_one_angry_customer_is_not_a_spike():
    same = [(NOW - timedelta(minutes=5 * i), "angry-1", "login") for i in range(25)]
    assert detect_spikes(frame(quiet_week("login", 1) + same), NOW) == []


def test_normal_busy_category_is_not_a_spike():
    rows = quiet_week("bags", per_day=40)
    today = [(NOW - timedelta(minutes=30 * i), f"b-{i}", "bags") for i in range(10)]
    assert detect_spikes(frame(rows + today), NOW) == []


def test_one_row_per_spike_and_it_closes_when_calm(tmp_path):
    conn = connect(tmp_path / "s.db")
    spike = [{"category": 3, "customers": 20, "normal": 1.0, "since": NOW}]
    names = {3: "login failures"}
    first = raise_spikes(conn, spike, names, NOW)
    again = raise_spikes(conn, spike, names, NOW + timedelta(hours=1))
    assert len(first) == 1 and again == []
    assert "login failures" in conn.execute("SELECT what FROM actions").fetchone()[0]
    raise_spikes(conn, [], names, NOW + timedelta(hours=8))
    assert conn.execute("SELECT status FROM actions").fetchone()[0] == "done"


def test_complaints_counts_distinct_customers_and_change():
    rows = quiet_week("bags", 2) + [(NOW - timedelta(hours=1), "x", "bags")] * 3
    out = complaints(frame(rows), {"bags": "Bags"}, NOW, days=3)
    # Days 1 and 2 back: 2 distinct each (day 3 sits exactly on the window edge), plus "x" once.
    assert out.loc["bags", "customers"] == 5
