import json

from common.config import get_config
from common.schema import Alert, Beacon, Heartbeat
from map.board import Board

CFG = get_config()
NAMES = CFG.health.node_modules
DEAD_S = CFG.relay.link_dead_s


def _alert(track: int, p: int, t: float = 100.0, **more: object) -> Alert:
    return Alert(t=t, id=track, p=p, c=90, alive=p <= 2, **more)  # type: ignore[arg-type]


def _beacon(seq: int, up: int = 0b11, bad: int = 0) -> Beacon:
    return Beacon(seq=seq, up=up, bad=bad, sim=True)


def test_markers_are_urgent_first_then_most_recent() -> None:
    board = Board(CFG)
    for now, (track, p) in enumerate([(1, 3), (2, 1), (3, 3), (4, 1)]):
        board.heard(_alert(track, p), now, now)
    assert [v["id"] for v in board.snapshot(10)["victims"]] == [4, 2, 3, 1]


def test_the_newest_arrival_wins_even_with_an_older_clock() -> None:
    # A node rebooted without a working clock must still move its markers.
    board = Board(CFG)
    board.heard(_alert(7, 2, t=5000), 0, 0)
    board.heard(_alert(7, 1, t=10, br=14), 1, 1)
    [victim] = board.snapshot(2)["victims"]
    assert victim["p"] == 1 and victim["br"] == 14 and victim["heard_s"] == 1


def test_unknown_is_shown_as_unknown() -> None:
    board = Board(CFG)
    board.heard(_alert(7, 3), 0, 0)
    [victim] = board.snapshot(0)["victims"]
    assert victim["br"] is None and victim["hz"] is None and victim["rng"] is None


def test_link_is_waiting_then_up_then_down() -> None:
    board = Board(CFG)
    assert board.snapshot(0)["link"] == "waiting"
    assert board.snapshot(0)["sim"] is None  # not known yet
    board.heard(_beacon(0), 0, 0)
    assert board.snapshot(DEAD_S)["link"] == "up"
    assert board.snapshot(DEAD_S + 1)["link"] == "down"


def test_node_modules_are_unknown_while_the_link_is_down() -> None:
    board = Board(CFG)
    assert {m["state"] for m in board.snapshot(0)["node"]} == {"unknown"}
    board.heard(_beacon(0, up=0b01, bad=0b01), 0, 0)
    states = {m["name"]: m["state"] for m in board.snapshot(1)["node"]}
    assert states == {NAMES[0]: "degraded", NAMES[1]: "down"}
    assert {m["state"] for m in board.snapshot(DEAD_S + 1)["node"]} == {"unknown"}


def test_lost_beacons_are_counted_but_a_restart_is_not_a_loss() -> None:
    board = Board(CFG)
    for now, seq in enumerate([0, 1, 4, 5, 0, 1]):  # 2 and 3 lost; then a restart
        board.heard(_beacon(seq), now, now)
    assert board.snapshot(6)["missed"] == 2


def test_post_modules_come_from_post_heartbeats() -> None:
    board = Board(CFG)
    board.heard(Heartbeat(src="lora_rx", status="ok", sim=True), 0, 0)
    states = {m["name"]: m["state"] for m in board.snapshot(1)["post"]}
    assert states["lora_rx"] == "ok" and states["recorder"] == "down"


def test_markers_are_bounded_dropping_the_least_urgent() -> None:
    tracks = CFG.tracks.model_copy(update={"max_tracks": 2})
    board = Board(CFG.model_copy(update={"tracks": tracks}))
    board.heard(_alert(1, 1), 0, 0)
    board.heard(_alert(2, 4), 1, 1)
    board.heard(_alert(3, 3), 2, 2)
    assert set(board.markers) == {1, 3}


def test_snapshot_is_plain_json() -> None:
    board = Board(CFG)
    board.heard(_alert(1, 1, br=12, hz=2, rng=3), 0, 0)
    board.heard(_beacon(0), 0, 0)
    assert json.loads(json.dumps(board.snapshot(1)))["victims"][0]["hz"] == "danger"
