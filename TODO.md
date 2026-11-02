# Afterglow TODO

The software task list. Check items off as they land. The how and why live in [CLAUDE.md](CLAUDE.md) and [Blueprint.md](Blueprint.md); this file only tracks what's done.

## Key dates

| When | What |
| --- | --- |
| Sun Nov 1 | Hardware bring-up done: every part reads or sends from software |
| Thu Nov 5 | Target: Phase 0 and Phase 1 done, whole pipeline runs in sim on a laptop |
| Sat Nov 7, 10:00 | Hard start |
| Sat Nov 7, 22:00 | Crude end-to-end demo on real hardware |
| Sun Nov 8, 09:00 | Feature freeze |
| Sun Nov 8, 12:00 | Submissions close |

## Setup

- [x] GitHub repo created and pushed
- [x] `uv` installed and `uv sync` works; `requirements.txt` + README setup steps for pip users

## Phase 0: foundation

- [x] `pyproject.toml` (package `afterglow`, Python 3.11, ruff, pytest), `uv.lock`, `requirements.txt`
- [x] `common/config.yaml` + `common/config.py`: bus ports, fusion weights, triage thresholds, timings, serial paths, target device IDs, hash salt
- [x] `common/schema.py`: `WifiHit`, `BleHit`, `ThermalHit`, `RadarHit`, `HazardReading`, `Target`, `Alert`
- [x] `common/ids.py`: device-ID hashing (salted SHA-256, truncated)
- [x] `common/bus.py`: `Publisher`, `Subscriber`, proxy process; separate node and post buses
- [x] `common/recorder.py`: every message appended to `logs/<start time>-<bus>.jsonl`
- [x] `tools/tap.py`: print all bus traffic
- [x] `tools/fake_detections.py`: scripted victim (device gets warmer → heat appears → breathing confirmed)
- [x] `Procfile.node` and `Procfile.post`
- [x] Tests: schema validation and round-trips, hashing, bus pub/sub, recorder lines, fake scenario story

## Phase 1: thin end-to-end path in sim

Fake scenario → fusion → relay queue → LoRa (sim) → receiver → web map and TAK, on one laptop with no hardware.

- [x] `find/rssi_filter.py`: smoothing, log-distance range estimate, `e_rf` mapping (−60 dBm → 1, −90 dBm → 0) + tests
- [x] `fuse/tracks.py`: merge hits by device hash, or bearing within 20° seen in the last 30 s + tests
- [x] `fuse/fusion.py`: noisy-OR confidence, P1–P4 triage, send rules (new track, priority change, every 30 s for P1/P2) + tests
- [x] Decide the bearing reference: camera-relative (no IMU; a compass is unreliable indoors near metal). Bearing merge window down to `tracks.bearing_s` (5 s), and `brg` not sent over the air
- [x] Decide victim position: no GPS, so the node sends none; the command post puts each marker at the searcher's position (fixed in config for now), labelled with range from the searcher
- [x] Fusion also publishes when a breathing rate first becomes known (the first P1 used to go out 5 s before breathing and wait 30 s for the resend)
- [x] A person first seen while their phone still reads past `tracks.confirm_range_m` (body loss: a phone under the victim can read 9 m away) takes the phone once it reads closer
- [x] Status beacon over LoRa: sequence number + modules-up and degraded bitmasks every 10 s, no ack, doubling as the link keepalive (link down after 30 s silent); a module is down after `health.down_after_s` (6 s, over 2 × `beat_s`). Compact JSON came out at 40–55 bytes, not the ~10 a packed format would give
- [x] `relay/codec.py`: `Alert` and `Beacon` to and from compact JSON with unknown fields left off; worst-case alert 91 bytes (no position or bearing any more) + tests
- [x] `relay/priority_queue.py`: priority then age, newer alert replaces queued older one, back-off retry after 10 s without ack, P1 never blocked + tests
- [x] `relay/lora_tx.py` and `relay/lora_rx.py` with a sim radio (`relay/radio.py`, UDP on loopback) that can drop and restore the link (`python -m relay.radio down|up`)
- [x] `map/web/`: standard-library server and one page with nothing loaded from outside (the post has no internet; Flask and Leaflet dropped): P1-first list colored by priority, vitals, hazard, "heard N s ago", link and module status, a loud banner when the map server stops answering
- [x] Web map: each victim's history (P4 → P3 → P2 → P1, first breathing rate, with times) and text that scales up on a projector
- [ ] Web map: a floor-plan view with the searcher and markers, and the searcher's position settable by clicking it instead of fixed in config
- [x] `map/cot_sender.py`: CoT over UDP (standard library, no pytak), `uid` = `AG-<track id>`, stale after 2 min, searcher as a friendly marker; untested against a real TAK client
- [x] `tools/replay.py`: replays a `.jsonl` run's sensor messages through the live pipeline
- [x] `Heartbeat` model on `health.beat` and the `common.health.Heart` helper; fusion beats
- [x] Health: recorder (degraded when disk is low), relay, receiver, map and TAK output publish heartbeats; the map flags any module or link that has gone quiet. Each sensor module adds its own in Phase 2, and its name to `health.node_modules`
- [x] Integration test: fake scenario ends with a P1 alert received at the command post; after a link drop the queued P1 arrives first (`tests/relay/test_end_to_end.py`)
- [ ] Milestone: `honcho` starts node and post on one laptop and the full scenario plays out on the web map (runs, and `/state` shows the P1 and a link drop; still needs a look in a browser)
- [ ] Phase 1 review against the engineering standard, then fixes

## Hardware bring-up

Get each part talking to software standalone before wrapping it in a module.

**Pi**

- [ ] Raspberry Pi OS Bookworm 64-bit; I2C and serial enabled in `raspi-config`, serial login console off
- [ ] AI HAT+ detected by the Hailo runtime
- [ ] Pi clock is right with no network (RTC battery fitted, or set from the laptop before deploying); every timestamp depends on it

**RF**

- [ ] USB Wi-Fi adapter in monitor mode on `wlan1`; scapy logs probe requests on channels 1/6/11
- [ ] bleak reports RSSI for our test phone and BLE tag
- [ ] Demo victim device is a BLE tag with a fixed address (phones randomize theirs, and `known_only` would then ignore it): put its hash in `targets.devices` (`uv run python -c "from common.ids import hash_device; print(hash_device('AA:BB:CC:DD:EE:FF'))"`) and check that hash shows up in `uv run python -m tools.tap find.`

**Sensing**

- [ ] MLX90640 returns a 32×24 frame over I2C (0x33)
- [ ] LD2410 or Rd-03D frames read over UART
- [ ] A YOLO person model runs on the Hailo-8 (Hailo's Pi examples)

**Hazard**

- [ ] Arduino + Grove MQ sensor prints readings over USB serial

**Comms + map**

- [ ] Two Meshtastic boards flashed, region US, same preset (MediumFast indoors), passing text
- [x] iTAK shows a test CoT marker (`python -m tools.cot_test`) over home Wi-Fi: multicast works with no TAK server, once iOS Settings → Privacy & Security → Local Network → iTAK is on
- [ ] Same over our own travel router, then the full scenario's victim marker in iTAK (set `tak.send_from` to the laptop's address on the router)

## Phase 2: real sensor modules

Each behind `--sim`, sim path written first, and never able to crash the node. A reading that fails schema validation (bad frame, out-of-range value) is logged and dropped on its own, not allowed to restart the module.

- [ ] `find/ble_scanner.py`: bleak, flags known target IDs from config
- [ ] `find/wifi_sniffer.py`: monitor mode, channel hop 1/6/11, MAC hashed on capture
- [ ] `tools/calibrate_rssi.py`: RSSI table at 1, 2, 4, 8 m; writes P₁ and n into config
- [ ] `confirm/thermal.py`: 30–37 °C threshold, blob filter, bearing from pixel column
- [ ] `confirm/radar.py`: frame parser; "still but present" is the key signal
- [ ] `hazard/arduino/gas_sensor.ino`: 10-reading average, one JSON line per 500 ms
- [ ] `hazard/gas_reader.py`: clean-air baseline, ×1.5 caution, ×2.5 danger
- [ ] `relay/lora_tx.py` and `relay/lora_rx.py` real path via `meshtastic` with want-ack
- [ ] systemd unit per process on the Pi: starts on boot with no laptop or keyboard, restarts any process that dies (honcho stops everything when one process exits: fine for dev, not for the field)

## Phase 3: stretch (only once everything above is solid)

- [ ] Hailo person detection in `confirm/person_detect.py`
- [ ] Breathing-rate FFT (0.1–0.5 Hz over 20–30 s)
- [ ] Directional bearing (directional antenna or 360° sweep)
- [ ] Audio evidence `e_au` (voice, whistle, tapping)
- [ ] Struct-packed alert (~25 bytes) if airtime gets tight
- [ ] Multi-node triangulation or SDR spectrum survey

## Test plan

- [ ] RSSI calibration: RSSI vs distance table at 1, 2, 4, 8 m in the demo area
- [ ] Victim found from the start position: device hit within 30 s, direction within 45°
- [ ] Hot object only: heat pack with no breathing stays below 0.8
- [ ] Phone on a table, no person: stays P3/P4
- [ ] Link drop: unplug the receiver for 30 s, plug it back, queued P1 arrives first
- [ ] End-to-end latency from detection to marker measured, under 5 s
- [ ] Replay: `tools/replay.py` reproduces a recorded good run end to end
- [ ] Every rehearsal and the demo start with `uv run python -m fuse.fusion --new-search` (node stopped), or the last run's P1 comes back if it was under 15 min ago
- [ ] Walk away from the victim for over 30 s and come back: expect a second P1 marker for the same person (a duplicate beats a hidden victim); rehearse it so it's no surprise in front of the judges

## If we had more time: security

Known gaps, left for after the demo. The node bus is localhost-only, every message is validated, and MACs are hashed on capture; these are what's still open.

- [ ] Private Meshtastic channel with a random key on both boards (a settings change when flashing); the default channel's key is public, so anyone nearby with a Meshtastic radio can read alerts or send fake ones
- [ ] Signed alerts, so the receiver can tell our board from anyone else holding the channel key
- [ ] Secret hash salt per deployment, kept out of git; the committed salt only protects sim data, and anyone holding it can brute-force a hashed MAC
- [ ] TAK traffic encrypted (TAK server over TLS) instead of plain UDP multicast; until then, run it only on our own password-protected router
