# CLAUDE.md

Project brief for AI coding assistants working in this repo. Read this before writing or changing code.

## What this project is

Afterglow is a search-and-rescue sensing node built for the Code Metal Forge Hackathon (Nov 7–8, 2026). A Raspberry Pi 5 carried by a searcher:

1. **Finds** a missing person's devices by passively listening for Wi-Fi probe requests and BLE adverts (signal strength = warmer/colder).
2. **Confirms** a living person with a thermal camera (body heat) and a 24 GHz mmWave radar (breathing/presence).
3. **Relays** a small, prioritized alert over a LoRa mesh (Meshtastic) to a command-post laptop, which shows it as a marker on a TAK map (ATAK/WinTAK) or a web-map fallback.

There is no GPS, cell or Wi-Fi infrastructure in the scenario. All sensing and scoring happens on the Pi. Only short alerts cross the radio.

We are judged on a **working live demo** and **deployability**. Reliability beats features. Every change should keep the end-to-end demo working.

## Engineering standard

Build close to military grade. Assume a tired operator, flaky hardware and a link that drops. The system must still never crash, never go quiet without saying so, and never report something it doesn't know. In practice:

- **Validate at the edges.** The whole config is checked when a process starts (`common/config.py`), so a typo stops it at launch, not mid-search. Every message is checked when it is built, published and received (`common/schema.py`): no NaN, no physically impossible values, no unknown fields. A bad message is logged and dropped, never passed on.
- **Unknown is not zero.** A value nobody measured (no breathing rate yet, no recent gas reading, no one in front of the radar) is `None`, never a default that looks like a real reading.
- **Nothing takes the node down.** Long-running modules run their loop under `common.service.run_forever`, which logs any crash with its traceback and restarts with back-off. Hardware and serial errors are caught where they happen.
- **Everything is bounded.** Queues, buffers, retries, back-off, disk use and log volume have limits set in `config.yaml`, so a long search can't exhaust memory or storage. Repeated warnings are counted and summarized, not printed one per event.
- **Privacy by construction.** Message and config schemas accept only hashed device IDs (16+ hex characters, longer than any MAC), so a raw MAC can't be published or configured, even by mistake. Validation errors never echo the rejected value, so refusing a raw MAC can't leak it into a log either.
- **Test the failures, not just the demo.** Bad input, a restarted bus, a crashed module, a dropped link and a dead sensor each get a test.
- **Measure every claim.** Latency, range, battery life and false-positive numbers come from tests we ran.

## Architecture in one paragraph

Each module is its own small Python process. Sensor modules publish typed JSON messages to a local ZeroMQ pub/sub bus. The fusion module subscribes to sensor topics, groups hits into target tracks, computes a confidence score and triage priority, and publishes targets. The relay module turns targets into compact alerts, queues them by priority, and sends them over LoRa with store-and-forward. At the command post, a receiver decodes alerts and feeds the TAK output and the web map. Every bus message is logged to `.jsonl` so runs can be replayed.

## Repo layout

```
common/        bus.py (pub/sub helper + proxy), schema.py (all message models), config.py, config.yaml,
               ids.py (device-ID hashing), recorder.py (bus → logs/*.jsonl), service.py (crash restart)
find/          wifi_sniffer.py, ble_scanner.py, rssi_filter.py
confirm/       thermal.py, person_detect.py, radar.py
hazard/        arduino/gas_sensor.ino, gas_reader.py
fuse/          fusion.py (score + triage), tracks.py (merge hits into targets)
relay/         lora_tx.py (field), lora_rx.py (command post), priority_queue.py, codec.py
map/           cot_sender.py (TAK), web/ (Flask + Leaflet fallback)
tools/         replay.py, fake_detections.py, calibrate_rssi.py, tap.py (print bus traffic)
tests/         mirrors the package layout
Procfile.node  starts every field process
Procfile.post  starts receiver + map on the laptop
TODO.md        team task list; check items off as they land
```

Do not add new top-level folders without a reason. New code goes in the module it belongs to.

## The contract: schema and bus

`common/schema.py` is the single source of truth for every message. All modules import from it. Never hand-build message dicts elsewhere.

- Models are `pydantic` v2 `BaseModel`s, serialized as JSON.
- Every message has `t` (Unix time, float seconds) and `src` (module name).
- Changing a field in `schema.py` is a breaking change: update every producer, consumer and test in the same commit.

Bus topics and the model each one carries:

| Topic | Model | Producer |
| --- | --- | --- |
| `find.wifi` | `WifiHit` | find/wifi_sniffer.py |
| `find.ble` | `BleHit` | find/ble_scanner.py |
| `confirm.thermal` | `ThermalHit` | confirm/thermal.py |
| `confirm.radar` | `RadarHit` | confirm/radar.py |
| `hazard.gas` | `HazardReading` | hazard/gas_reader.py |
| `fuse.target` | `Target` | fuse/fusion.py |
| `relay.out` | `Alert` | relay/lora_tx.py (what was sent) |
| `relay.in` | `Alert` | relay/lora_rx.py (what was received) |

Key fields (see `schema.py` for the full definitions):

- `WifiHit` / `BleHit`: `dev` (hashed ID), `rssi_dbm`, `rssi_smooth`, `est_range_m`, `bearing_deg | None`, `channel` (Wi-Fi only)
- `ThermalHit`: `person`, `conf` (0–1), `bearing_deg`, `max_temp_c`
- `RadarHit`: `present`, `moving | None`, `range_m | None`, `breaths_per_min | None`, `conf` (when nobody is present, all three are `None`, never `False` or 0)
- `HazardReading`: `hazard` ("none" | "caution" | "danger"), `gas_raw` (0–1023), `temp_c | None`
- `Target`: `id`, `confidence`, `priority` (1–4), `alive`, `bearing_deg`, `range_m`, `breaths_per_min`, `hazard`, `x`, `y` (meters from the start point, if known), `evidence` (`Evidence`: per-sensor e values, `None` for a sensor with nothing on the track)
- `Alert`: compact keys `v, id, p, c, x, y, brg, rng, alive, br, hz, t`; must encode to at most `relay.max_alert_bytes` (100); unknown values (`br`, `hz`, position) are `None` and left off the air, never sent as 0

Bus usage: always go through `common/bus.py` (`Publisher(src)`, `Subscriber(topics)`). Do not open raw ZeroMQ sockets in modules. The proxy runs as its own process and binds local ports defined in `config.yaml`. `Publisher` stamps its `src` on any message that leaves `src` empty. The node and the command post each run their own bus on separate ports (`bus="post"` in code, `--bus post` on the command line), so both can run on one laptop in sim mode.

## Core logic (keep it explainable)

Fusion is a noisy-OR, not a black box:

```
C = 1 - Π (1 - w_i * e_i)   over rf, thermal, radar, audio
```

Starting weights: rf 0.5, thermal 0.7, radar 0.7, audio 0.4. `e_rf` is 1 at RSSI ≥ −60 dBm, scaling linearly to 0 at −90 dBm.

Triage:

- P1: C ≥ 0.8 and (not moving or hazard = danger)
- P2: C ≥ 0.8
- P3: 0.5 ≤ C < 0.8
- P4: C < 0.5 (logged, sent only when the link is idle)

Tracks merge hits with the same device hash, or bearing within 20° seen in the last 30 s. Send an alert when a track is created, when its priority changes, or every 30 s for P1/P2.

Relay queue: ordered by priority then age; retry with back-off if no ack within 10 s; a newer alert for a track replaces any queued older one; a stuck low-priority alert must never block a P1.

All weights, thresholds and timings live in `config.yaml`, never as magic numbers in code.

## Sim mode is mandatory

Every sensor and radio module must run without hardware:

- Each module accepts `--sim` (or `sim: true` in config) and then produces realistic fake data or uses a fake radio.
- `tools/fake_detections.py` plays a scripted victim scenario (device gets warmer → heat appears → breathing confirmed).
- The whole pipeline must run end-to-end on a laptop with no Pi and no sensors attached.

When writing a hardware module, write the sim path first, then the real path behind the same interface.

## Commands

```
uv sync                                   # install dependencies
uv run pytest                             # run tests
uv run ruff check . && uv run ruff format .
uv run honcho -f Procfile.node start      # start the field node
uv run honcho -f Procfile.post start      # start the command post
uv run python -m tools.fake_detections    # play the fake victim scenario
uv run python -m tools.tap                # print all bus traffic
uv run python -m tools.replay logs/<run>.jsonl
```

Run modules as packages (`python -m find.ble_scanner`), not as loose scripts.

`requirements.txt` is a pip fallback for teammates without uv, generated from `uv.lock`. Never edit it by hand: after any `uv add` or `uv remove`, regenerate it with `uv export --format requirements-txt --no-hashes -o requirements.txt` and commit both files together.

## Coding rules

- Python 3.11. Type hints on all functions. Formatting and linting with `ruff`.
- Small modules, plain functions, few classes. Prefer clarity over cleverness; a teammate should follow it at 3 a.m.
- Use `asyncio` only where a library requires it (`bleak`, `pytak`). Plain loops elsewhere.
- Logging through the standard `logging` module, one logger per module. No bare `print` outside tools.
- A module must never crash the node: catch hardware and serial errors, log them, back off and retry. Run every long-running loop under `common.service.run_forever`.
- Pure logic (RSSI filter, fusion, triage, priority queue, codec) must be separated from I/O so it can be unit-tested without hardware.
- Every new piece of pure logic gets a pytest test. Use the fake scenario for integration tests.
- Keep dependencies minimal. Ask before adding a new package.
- Don't rewrite working modules unprompted. Make the smallest change that does the job.

## Hard rules (legal, safety, privacy)

- **Receive only.** Never write code that transmits on Wi-Fi, cellular or any licensed band, or that impersonates an access point or cell tower. The only transmitter is the Meshtastic LoRa board in the 915 MHz ISM band.
- **Hash device identifiers immediately** on capture (salted SHA-256, truncated to `privacy.hash_chars`). Never log or transmit raw MAC addresses or packet payloads.
- Test only against our own devices. Known target device IDs live in `config.yaml`.

## Hardware reference (for the real paths)

- Pi 5 (16 GB), Raspberry Pi OS Bookworm 64-bit, AI HAT+ (Hailo-8)
- Wi-Fi sniffer: USB adapter in monitor mode (`wlan1`), channels 1/6/11
- BLE: built-in Bluetooth via `bleak`
- Thermal: MLX90640 on I2C (address 0x33), 32×24 grid
- Radar: LD2410 or Rd-03D on UART (`/dev/ttyAMA0`) or USB-serial
- Gas: Arduino + Grove MQ sensor, prints one JSON line per 500 ms over USB serial
- LoRa: Meshtastic board over USB serial, region US, same preset on both ends
- TAK: CoT via `pytak` to `udp://239.2.3.1:6969`; marker `uid` = `AG-<track id>`, stale after 2 min

Serial device paths and ports belong in `config.yaml`.

## Current priorities

1. ~~Phase 0: schema, bus, logger, fake scenario.~~ Done.
2. Thin end-to-end path: fake scenario → fusion → relay queue → LoRa (sim) → receiver → web map and TAK.
3. Real sensor modules, one at a time, each behind `--sim`.
4. Stretch only after the above is solid: Hailo person detection, breathing-rate FFT, directional bearing.

Update this section as phases complete. Task-level tracking lives in `TODO.md`; check items off there as they land.
