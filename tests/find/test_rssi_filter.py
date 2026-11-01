import pytest

from common.config import RfConfig, get_config
from find.rssi_filter import Smoother, e_rf, est_range_m


def _rf(**changes: object) -> RfConfig:
    return get_config().rf.model_copy(update=changes)


RF = _rf(
    p1m_dbm=-45,
    path_loss_n=3.0,
    smooth_window_s=2.5,
    smooth_max_samples=100,
    max_devices=500,
    e_full_dbm=-60,
    e_zero_dbm=-90,
)


def test_range_is_one_meter_at_the_calibration_point() -> None:
    assert est_range_m(-45, RF) == pytest.approx(1.0)


def test_range_grows_tenfold_every_10n_db() -> None:
    assert est_range_m(-75, RF) == pytest.approx(10.0)
    assert est_range_m(-105, RF) == pytest.approx(100.0)
    assert est_range_m(-35, RF) == pytest.approx(10 ** (-1 / 3))  # closer than 1 m


def test_range_is_finite_across_every_valid_input() -> None:
    # The config bounds (n >= 1, RSSI within the schema's range) keep the
    # estimate from overflowing whatever a scanner reports.
    worst = _rf(p1m_dbm=-1, path_loss_n=1)
    assert 0 < est_range_m(-120, worst) < 1e13
    assert 0 < est_range_m(-0.1, _rf(p1m_dbm=-120, path_loss_n=1))


@pytest.mark.parametrize(
    ("rssi", "e"),
    [(-40, 1.0), (-60, 1.0), (-75, 0.5), (-90, 0.0), (-115, 0.0)],
)
def test_e_rf_is_linear_between_the_set_points_and_clamped(
    rssi: float, e: float
) -> None:
    assert e_rf(rssi, RF) == pytest.approx(e)


def test_smoother_averages_over_the_window() -> None:
    s = Smoother(RF)
    assert s.add("a" * 16, 0.0, -70) == -70
    assert s.add("a" * 16, 1.0, -60) == -65
    assert s.add("a" * 16, 2.0, -80) == pytest.approx(-70)
    # At t=3.0 the reading from t=0.0 is older than 2.5 s and drops out.
    assert s.add("a" * 16, 3.0, -60) == pytest.approx(-200 / 3)


def test_devices_are_smoothed_separately() -> None:
    s = Smoother(RF)
    s.add("a" * 16, 0.0, -50)
    assert s.add("b" * 16, 0.1, -90) == -90


def test_clock_jumping_back_starts_the_device_over() -> None:
    s = Smoother(RF)
    s.add("a" * 16, 100.0, -50)
    assert s.add("a" * 16, 40.0, -80) == -80


def test_device_count_is_bounded_and_the_longest_quiet_goes_first() -> None:
    s = Smoother(_rf(max_devices=3))
    for i, dev in enumerate(["a", "b", "c"]):
        s.add(dev * 16, float(i), -70)
    s.add("a" * 16, 3.0, -50)  # a heard again: b is now the longest quiet
    s.add("d" * 16, 4.0, -60)
    assert len(s) == 3
    # b was forgotten, so its next reading starts fresh; a's history survives.
    assert s.add("b" * 16, 4.1, -90) == -90
    assert s.add("a" * 16, 4.2, -70) == -60 and len(s) == 3


def test_readings_per_device_are_bounded() -> None:
    s = Smoother(_rf(smooth_max_samples=4))
    for _ in range(1000):  # a flood inside one window
        s.add("a" * 16, 0.0, -90)
    assert s.add("a" * 16, 0.0, -50) == pytest.approx((3 * -90 + -50) / 4)
