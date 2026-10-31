from common.config import get_config


def test_shipped_config_has_every_section() -> None:
    cfg = get_config()
    expected = {"bus", "log", "privacy", "targets", "rf", "fusion", "triage", "tracks"}
    assert expected <= cfg.keys()


def test_node_and_post_buses_use_different_ports() -> None:
    # Both run on one laptop in sim mode, so no port can be shared.
    bus = get_config()["bus"]
    ports = [
        bus[name][kind]
        for name in ("node", "post")
        for kind in ("pub_port", "sub_port")
    ]
    assert len(set(ports)) == 4
