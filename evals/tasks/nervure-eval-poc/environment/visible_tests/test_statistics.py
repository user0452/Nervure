from calculator.statistics import mean, median


def test_median_odd_values() -> None:
    assert median([3, 1, 2]) == 2


def test_mean_accepts_non_empty_values() -> None:
    assert mean([2, 4]) == 3
