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
- [ ] `common/config.yaml` + `common/config.py`: bus ports, fusion weights, triage thresholds, timings, serial paths, target device IDs, hash salt
- [ ] `common/schema.py`: `WifiHit`, `BleHit`, `ThermalHit`, `RadarHit`, `HazardReading`, `Target`, `Alert`
- [ ] Device-ID hashing helper (salted SHA-256, truncated)
- [ ] `common/bus.py`: `Publisher`, `Subscriber`, proxy process
- [ ] Bus logger: every message appended to `logs/<run>.jsonl`
- [ ] `tools/tap.py`: print all bus traffic
- [ ] `tools/fake_detections.py`: scripted victim (device gets warmer → heat appears → breathing confirmed)
- [ ] `Procfile.node` and `Procfile.post`
- [ ] Tests: schema round-trips, `Alert` encodes under 100 bytes, hashing, bus pub/sub smoke test

## Phase 1: thin end-to-end path in sim

Fake scenario → fusion → relay queue → LoRa (sim) → receiver → web map and TAK, on one laptop with no hardware.

- [ ] `find/rssi_filter.py`: smoothing, log-distance range estimate, `e_rf` mapping (−60 dBm → 1, −90 dBm → 0) + tests
- [ ] `fuse/tracks.py`: merge hits by device hash, or bearing within 20° seen in the last 30 s + tests
- [ ] `fuse/fusion.py`: noisy-OR confidence, P1–P4 triage, send rules (new track, priority change, every 30 s for P1/P2) + tests
- [ ] `relay/codec.py`: `Alert` to and from compact bytes, under 100 bytes + tests
- [ ] `relay/priority_queue.py`: priority then age, newer alert replaces queued older one, back-off retry after 10 s without ack, P1 never blocked + tests
- [ ] `relay/lora_tx.py` and `relay/lora_rx.py` with a sim radio that can drop and restore the link
- [ ] `map/web/`: Flask + Leaflet, markers colored by priority, vitals popup, hazard ring, P1-first side list, "last received" timestamp, x/y floor-plan mode
- [ ] `map/cot_sender.py`: CoT via pytak, `uid` = `AG-<track id>`, stale after 2 min, searcher as a friendly marker
- [ ] `tools/replay.py`: replays a `.jsonl` run through the same pipeline
- [ ] Integration test: fake scenario ends with a P1 alert received at the command post
- [ ] Milestone: `honcho` starts node and post on one laptop and the full scenario plays out on the web map

## Hardware bring-up

Get each part talking to software standalone before wrapping it in a module.

**Pi**

- [ ] Raspberry Pi OS Bookworm 64-bit; I2C and serial enabled in `raspi-config`, serial login console off
- [ ] AI HAT+ detected by the Hailo runtime

**RF**

- [ ] USB Wi-Fi adapter in monitor mode on `wlan1`; scapy logs probe requests on channels 1/6/11
- [ ] bleak reports RSSI for our test phone and BLE tag

**Sensing**

- [ ] MLX90640 returns a 32×24 frame over I2C (0x33)
- [ ] LD2410 or Rd-03D frames read over UART
- [ ] A YOLO person model runs on the Hailo-8 (Hailo's Pi examples)

**Hazard**

- [ ] Arduino + Grove MQ sensor prints readings over USB serial

**Comms + map**

- [ ] Two Meshtastic boards flashed, region US, same preset (MediumFast indoors), passing text
- [ ] ATAK or WinTAK shows a test CoT marker over our own travel router

## Phase 2: real sensor modules

Each behind `--sim`, sim path written first, and never able to crash the node.

- [ ] `find/ble_scanner.py`: bleak, flags known target IDs from config
- [ ] `find/wifi_sniffer.py`: monitor mode, channel hop 1/6/11, MAC hashed on capture
- [ ] `tools/calibrate_rssi.py`: RSSI table at 1, 2, 4, 8 m; writes P₁ and n into config
- [ ] `confirm/thermal.py`: 30–37 °C threshold, blob filter, bearing from pixel column
- [ ] `confirm/radar.py`: frame parser; "still but present" is the key signal
- [ ] `hazard/arduino/gas_sensor.ino`: 10-reading average, one JSON line per 500 ms
- [ ] `hazard/gas_reader.py`: clean-air baseline, ×1.5 caution, ×2.5 danger
- [ ] `relay/lora_tx.py` and `relay/lora_rx.py` real path via `meshtastic` with want-ack
- [ ] Node starts every process on boot with no laptop or keyboard attached

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
