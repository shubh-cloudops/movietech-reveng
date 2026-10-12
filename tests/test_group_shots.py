from itertools import pairwise

from movietech.ingest import group_shots


def test_short_shots_merge_until_min_length():
    shots = [(0, 5), (5, 12), (12, 21), (21, 25)]
    assert group_shots(shots, min_s=20, max_s=90) == [(0, 21, 3), (21, 25, 1)]


def test_long_shot_is_cut_at_max_length():
    assert group_shots([(0, 200)], min_s=20, max_s=90) == [(0, 90, 1), (90, 180, 1), (180, 200, 1)]


def test_segment_never_exceeds_max_length():
    shots = [(0, 15), (15, 100)]
    for start, end, _ in group_shots(shots, min_s=20, max_s=90):
        assert end - start <= 90


def test_every_second_is_covered_once():
    shots = [(0, 3), (3, 50), (50, 52), (52, 140), (140, 141)]
    segments = group_shots(shots, min_s=20, max_s=90)
    assert segments[0][0] == 0 and segments[-1][1] == 141
    for (_, end, _), (start, _, _) in pairwise(segments):
        assert end == start
