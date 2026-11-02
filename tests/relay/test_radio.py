import socket

import pytest

from common.config import Config
from relay.radio import Ack, Packet, SimRadio, link_flag, open_radio


def test_a_packet_arrives_and_is_acked(radios: tuple[SimRadio, SimRadio]) -> None:
    node, post = radios
    packet_id = node.send(b"hello", want_ack=True)
    assert post.recv(1) == Packet(b"hello")
    assert node.recv(1) == Ack(packet_id)


def test_no_ack_unless_asked(radios: tuple[SimRadio, SimRadio]) -> None:
    node, post = radios
    node.send(b"beacon", want_ack=False)
    assert post.recv(1) == Packet(b"beacon")
    assert node.recv(0.1) is None


def test_packet_ids_differ(radios: tuple[SimRadio, SimRadio]) -> None:
    node, _ = radios
    assert len({node.send(b"x", want_ack=True) for _ in range(5)}) == 5


def test_a_cut_link_loses_everything_until_restored(
    cfg: Config, radios: tuple[SimRadio, SimRadio]
) -> None:
    node, post = radios
    link_flag(cfg).touch()
    node.send(b"lost", want_ack=True)
    assert post.recv(0.1) is None and node.lost == 1
    link_flag(cfg).unlink()
    node.send(b"found", want_ack=True)
    assert post.recv(1) == Packet(b"found")


def test_an_ack_can_be_lost_too(cfg: Config, radios: tuple[SimRadio, SimRadio]) -> None:
    node, post = radios
    node.send(b"data", want_ack=True)
    link_flag(cfg).touch()  # cut after the packet went, before the ack
    assert post.recv(1) == Packet(b"data")
    assert node.recv(0.1) is None and post.lost == 1


def test_random_loss(cfg: Config) -> None:
    lossy = cfg.model_copy(
        update={"relay": cfg.relay.model_copy(update={"sim_loss": 1.0})}
    )
    radio = SimRadio("node", lossy, port=1)
    try:
        for _ in range(5):
            radio.send(b"x", want_ack=True)
        assert radio.lost == 5
    finally:
        radio.close()


def test_frames_that_arent_ours_are_counted_and_skipped(
    radios: tuple[SimRadio, SimRadio],
) -> None:
    node, post = radios
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stranger:
        for junk in (b"", b"Z123", b"D\x01", b"K\x00\x00\x00\x01"):  # last: an ack
            stranger.sendto(junk, post._post_addr)
    assert [post.recv(1) for _ in range(4)] == [None] * 4
    assert post.bad == 4


def test_only_the_node_end_sends(radios: tuple[SimRadio, SimRadio]) -> None:
    _, post = radios
    with pytest.raises(RuntimeError):
        post.send(b"x", want_ack=False)


def test_recv_without_waiting(radios: tuple[SimRadio, SimRadio]) -> None:
    node, _ = radios
    assert node.recv(0) is None


def test_the_real_radio_is_not_built_yet(cfg: Config) -> None:
    with pytest.raises(ValueError, match="sim"):
        open_radio("node", cfg, sim=False)
