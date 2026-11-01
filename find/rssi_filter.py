"""Turn raw RSSI readings into a smoothed signal, a rough range and RF evidence.

Pure logic, no I/O: the Wi-Fi and BLE scanners smooth and range each reading
before publishing it, and fusion turns the smoothed RSSI into e_rf.
"""

from collections import OrderedDict, deque

from common.config import RfConfig


def est_range_m(rssi_dbm: float, rf: RfConfig) -> float:
    """Distance by the log-distance model, d = 10^((P1m - RSSI) / (10 n)).
    Rough: a wall or a body costs about as much as doubling the distance."""
    return 10 ** ((rf.p1m_dbm - rssi_dbm) / (10 * rf.path_loss_n))


def e_rf(rssi_dbm: float, rf: RfConfig) -> float:
    """RF evidence: 1 at or above e_full_dbm, 0 at or below e_zero_dbm,
    linear between."""
    frac = (rssi_dbm - rf.e_zero_dbm) / (rf.e_full_dbm - rf.e_zero_dbm)
    return min(max(frac, 0.0), 1.0)


class Smoother:
    """Moving average of each device's RSSI over the last rf.smooth_window_s.

    Bounded however crowded the area: at most rf.max_devices devices (the
    longest-quiet is forgotten first) and rf.smooth_max_samples readings each.
    """

    def __init__(self, rf: RfConfig) -> None:
        self._rf = rf
        # Device hash -> (t, rssi) readings, oldest first; longest-quiet device first.
        self._devs: OrderedDict[str, deque[tuple[float, float]]] = OrderedDict()

    def add(self, dev: str, t: float, rssi_dbm: float) -> float:
        """Record one reading; return the device's smoothed RSSI."""
        samples = self._devs.pop(dev, None)
        if samples is None or t < samples[-1][0]:
            # New device, or the clock jumped back: averaging across the jump
            # would mix in readings from an unknown time, so start over.
            samples = deque(maxlen=self._rf.smooth_max_samples)
        samples.append((t, rssi_dbm))
        while samples[0][0] < t - self._rf.smooth_window_s:
            samples.popleft()
        self._devs[dev] = samples
        if len(self._devs) > self._rf.max_devices:
            self._devs.popitem(last=False)
        return sum(rssi for _, rssi in samples) / len(samples)

    def __len__(self) -> int:
        return len(self._devs)
