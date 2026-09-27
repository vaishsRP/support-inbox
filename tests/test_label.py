import numpy as np

from inbox.label import pick_threshold, split_of


def test_threshold_is_the_deepest_point_meeting_precision():
    sims = np.array([0.99, 0.97, 0.95, 0.93, 0.91, 0.89, 0.87])
    ys = np.array([1, 1, 1, 1, 0, 0, 0], dtype=float)
    assert pick_threshold(sims, ys, 0.85) == 0.93
    # One miss high up still allows a lower threshold if precision holds.
    ys2 = np.array([1, 0, 1, 1, 1, 1, 0], dtype=float)
    assert pick_threshold(sims, ys2, 0.8) == 0.89


def test_no_threshold_when_nothing_is_reusable():
    assert pick_threshold(np.array([0.99, 0.9]), np.array([0.0, 0.0]), 0.85) is None


def test_split_is_stable_and_roughly_two_thirds_tune():
    items = [f"{i}-{i + 7}" for i in range(3000)]
    assert [split_of(x) for x in items[:50]] == [split_of(x) for x in items[:50]]
    share = np.mean([split_of(x) == "tune" for x in items])
    assert 0.6 < share < 0.73
