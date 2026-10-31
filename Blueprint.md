# Afterglow: Technical Blueprint

Oct 27, 2026 · @Dillon

## What we are building

Afterglow is a backpack-sized sensing node that finds a missing person's devices, confirms the person is alive, and sends a prioritized alert to a rescue map with no GPS, cell or Wi-Fi infrastructure.

It works in three stages, all computed on a Raspberry Pi 5 at the edge:

1. **Find.** Listen passively for the radio chatter every phone, watch and earbud emits (Wi-Fi probe requests, Bluetooth Low Energy adverts). Signal strength tells you "warmer / colder."
2. **Confirm.** When the searcher gets close, a thermal camera looks for a human heat shape and an mmWave radar looks for chest motion from breathing. Device + heat + breathing = a living person.
3. **Relay.** A small alert (who, where, how confident, alive or not, any hazard) is sent over a LoRa mesh radio to the command post, where it appears as a marker on a TAK map.

It hits all four challenge objectives: Locate (RF detection and ranging), Detect life and hazards (thermal, radar, gas), Map changing conditions (live markers and hazard rings), Deliver information (LoRa mesh with priority queuing).

New to a term? Every technical word in this blueprint is explained in the **Glossary** tab. The **Build Order** tab says what to build in what sequence, and **Additions** lists stretch goals.

## System architecture

The system has two halves joined only by a LoRa radio link: a field node that does all sensing and thinking, and a command post that only decodes and displays.

&#91;embedded content: System architecture · 5 sensors, 5 software modules, 1 radio link, 2 map outputs\]

Read it top to bottom. Hardware in the first row feeds the three sensing modules. All modules talk through one message bus, the fusion step turns evidence into a scored alert, and only that alert crosses the dashed radio link to the command post.

Three design choices matter most:

- **Edge first.** Raw data (thermal frames, packet captures) never leaves the Pi. Only a short alert is transmitted, which is what makes a \~1 kbps radio enough.
- **One bus, many small processes.** Each teammate owns a module, modules can crash and restart independently, and every message is logged so a good run can be replayed.
- **Two map outputs.** TAK is what operators use; the web map is insurance if ATAK misbehaves on demo day.

## End-to-end flow: from a victim's phone to a marker on the map

The system has three checkpoints: is the signal getting closer, is there a living person, and did the alert actually arrive. Each failure loops back instead of being dropped.

&#91;embedded content: Detection flow · 3 decisions, 3 loops\]

The right-hand column is where the system protects itself. A device with no confirmed person is logged and searched for, never shown as a confirmed victim. An alert with no acknowledgement is held and retried, so a radio dropout delays the alert but never loses it.

Typical timings to aim for: device detection within seconds of coming in range (phones scan every few seconds to minutes depending on screen state), confirmation within 5 s of the sensors pointing at the person, and under 5 s from score to map marker.

## Hardware bill of materials and wiring

The whole node is one Pi 5 with five sensors attached, plus one LoRa radio on each end of the link. Models named are examples of what is common and well-documented; use whatever equivalent the shared library has.

**Sensing node (carried by the searcher, or mounted on a robot)**

| # | Part | Example model | What it does | Connects to Pi via | Source |
| --- | --- | --- | --- | --- | --- |
| 1 | Compute | Raspberry Pi 5, 16 GB | Runs every module, the fusion score and the radio link | n/a | Team kit |
| 2 | AI accelerator | Raspberry Pi AI HAT+ 26 TOPS (Hailo-8) | Runs the person-detection neural network fast and at low power | PCIe ribbon (sits on top of the Pi) | Team kit |
| 3 | Wi-Fi sniffer | USB Wi-Fi adapter that supports monitor mode (e.g. Alfa AWUS036ACM, MT7612U chipset) | Hears phones' probe requests | USB 3 | Shared library |
| 4 | BLE scanner | Pi 5 built-in Bluetooth (or a USB BLE dongle) | Hears watches, earbuds, phones advertising | Built in | Team kit |
| 5 | Thermal camera | MLX90640 (32×24 px, I2C) or a FLIR Lepton board | Sees body heat in the dark or through smoke | I2C (GPIO 2/3) or USB | Shared library |
| 6 | Visual camera (optional) | Pi Camera Module 3 or NoIR | Person detection by eye when there is light | CSI ribbon | Shared library |
| 7 | mmWave radar | 24 GHz module (e.g. HLK-LD2410 or Rd-03D) | Detects presence and tiny chest motion from breathing | UART (GPIO 14/15) or USB-serial | Shared library |
| 8 | Hazard sensors | Grove gas sensor (MQ-2 / MQ-9 style) + temp/humidity | Smoke, CO, combustible gas warnings | Grove shield → Arduino → USB serial | Team kit |
| 9 | LoRa radio (field) | Meshtastic-capable board (e.g. LILYGO T-Beam or Heltec V3, 915 MHz) | Sends alerts off-grid | USB serial | Shared library |
| 10 | IMU / GPS (optional) | Grove IMU; any GNSS module | Heading for bearing estimates; position when GPS exists | I2C / UART | Shared library |
| 11 | Power | USB-C PD power bank, 27 W+ (Pi 5 wants 5 V 5 A) | Runs the node untethered for the demo | USB-C | Bring your own |
| 12 | Directional antenna (optional) | Small 2.4 GHz panel or Yagi for the Wi-Fi adapter | Makes "which direction?" much clearer | RP-SMA on adapter | Shared library |

**Command post**

| # | Part | What it does |
| --- | --- | --- |
| 13 | Second LoRa radio | Receives alerts from the mesh |
| 14 | Laptop | Runs the receiver script, the TAK client (WinTAK) or the web-map fallback |
| 15 | Android phone (optional) | Runs ATAK so judges can see markers on a phone like operators use |

**Wiring summary (Pi 5 40-pin header)**

| Signal | Pi 5 pin | Goes to |
| --- | --- | --- |
| 3.3 V | pin 1 | MLX90640 VCC |
| 5 V | pin 2 / 4 | mmWave radar VCC (check your module: LD2410 wants 5 V) |
| GND | pin 6, 9, 14 | All grounds tied together |
| I2C SDA / SCL | GPIO 2 / 3 (pins 3 / 5) | MLX90640 (address 0x33), IMU |
| UART TX / RX | GPIO 14 / 15 (pins 8 / 10) | Radar RX / TX (crossed: Pi TX → radar RX) |
| USB 3 | blue ports | Wi-Fi adapter (needs the bandwidth and power) |
| USB 2 | black ports | Arduino (gas), LoRa radio |

The AI HAT+ uses PCIe and passes the GPIO header through a stacking header, so the sensors above still wire to the same pins. Enable I2C and the serial port with `raspi-config` and disable the serial login console.

## Software stack and repo layout

Everything is Python 3 on Raspberry Pi OS (64-bit), with each sensor as its own small process that publishes JSON to one shared message bus. If one sensor crashes, the rest keep running, and each teammate owns one process.

| Layer | Tool | Why this one |
| --- | --- | --- |
| OS | Raspberry Pi OS Bookworm 64-bit | Native support for the AI HAT+ and Pi camera stack |
| Internal message bus | ZeroMQ (pyzmq) pub/sub, or MQTT (Mosquitto) on localhost | Decouples modules; any process can listen to any topic; easy to log and replay |
| Wi-Fi sniffing | scapy + iw (monitor mode) | Parses 802.11 probe requests in a few lines |
| BLE scanning | bleak | Async BLE scanner, gives RSSI per advert |
| Thermal | adafruit-circuitpython-mlx90640 (or pylepton / OpenCV for USB thermal) | Reads the 32×24 temperature grid |
| Person detection | Hailo runtime (HailoRT, hailo-apps / rpicam-apps examples) with a YOLO model | Pre-compiled models for the Hailo-8 already exist |
| Radar | pyserial + a small parser for the module's frame format | Radars speak simple binary frames over UART |
| Gas | Arduino sketch printing JSON lines; pyserial on the Pi | Arduino handles analog reads; Pi just parses lines |
| Fusion | Plain Python (numpy) | A transparent, explainable score; no black box |
| LoRa link | meshtastic Python library over USB serial | Send and receive text/data packets in a few lines |
| Map | pytak (Cursor-on-Target to ATAK/WinTAK); Leaflet + Flask web page as fallback | PyTAK sends to the ATAK multicast default `udp://239.2.3.1:6969` with no server needed |
| Logging and replay | Every bus message appended to a .jsonl file | Lets you replay a good run if the live demo misbehaves |

Repo layout:

```
afterglow/
  common/        bus.py (pub/sub helper), schema.py (the Detection message), config.yaml
  find/          wifi_sniffer.py, ble_scanner.py, rssi_filter.py
  confirm/       thermal.py, person_detect.py, radar.py
  hazard/        arduino/gas_sensor.ino, gas_reader.py
  fuse/          fusion.py (score + triage), tracks.py (merge hits into targets)
  relay/         lora_tx.py (field), lora_rx.py (command post), priority_queue.py
  map/           cot_sender.py (TAK), web/ (Flask + Leaflet fallback)
  tools/         replay.py, calibrate_rssi.py, fake_detections.py
  run_node.sh    starts every field process
  run_post.sh    starts receiver + map on the laptop
```

Bus topics: `find.wifi`, `find.ble`, `confirm.thermal`, `confirm.radar`, `hazard.gas`, `fuse.target`, `relay.out`, `relay.in`.

## Module 1, Find: hearing the victim's devices

Phones, watches and earbuds constantly announce themselves over radio, even when nobody is using them. We listen passively, receive only, and use signal strength to steer the searcher toward the source.

**1a. Wi-Fi probe requests**

- Put the USB Wi-Fi adapter in monitor mode (`iw dev wlan1 set type monitor`) and hop across 2.4 GHz channels 1, 6 and 11, where most probes appear.
- With scapy, capture packets of type 802.11 management / subtype probe request. Keep: source MAC (hashed immediately), RSSI, channel, timestamp.
- Phones probe more often when the screen is on and less when idle, so expect bursts every few seconds to every few minutes. Research from NUS found phones detectable from the air by these background probes alone.
- Modern phones randomize MAC addresses while probing. Treat each as "a device is present" and group by timing and signal rather than relying on the MAC as an identity.

**1b. Bluetooth Low Energy adverts**

- `bleak` scans continuously and reports each advertisement with RSSI. Watches, earbuds and trackers advertise several times a second, giving a much faster update rate than Wi-Fi.
- For the demo, register the victim's known BLE tag or phone ID as a "target of interest" (as if a family member gave it to rescuers), and highlight it over other devices.

**1c. From RSSI to distance and direction**

- Smooth each device's RSSI with a moving average over the last 2–3 s, or a simple 1-D Kalman filter, to remove most of the jitter.
- Estimate distance with the log-distance model, where P₁ is the RSSI at 1 m and n is the path-loss exponent:

```latex
d = 10^{(P_{1m} - RSSI) / (10 n)}
```

&#91;embedded content: Modeled, not measured · log-distance path loss with an assumed −45 dBm at 1 m; replace with your venue calibration\]

The chart shows why distance estimates are rough: indoors, every doubling of distance costs about 9 dB, and a body or wall can cost just as much. Calibrate P₁ and n in the demo room with the actual victim phone.

- **Direction:** watch RSSI while the searcher turns slowly through 360° (strongest = facing the victim, especially with a directional antenna), or compare readings as the searcher walks: rising means warmer.
- **Output** on `find.wifi` / `find.ble`: `{dev: hash, rssi_dbm, rssi_smooth, est_range_m, bearing_deg (if known), t}`.

## Module 2, Confirm: is there a living person?

A phone is not a person. This module turns "a device is nearby" into "a human is here and breathing" using two independent sensors, so one false alarm cannot fool the system.

**2a. Thermal person detection**

- The MLX90640 returns a 32×24 grid of temperatures (up to 32 frames per second) across a 55° or 110° field of view.
- Simple, reliable method: threshold for skin-surface temperatures (about 30–37 °C against a cooler background), find connected blobs, and keep blobs whose size and shape fit a person at that distance.
- Better method if a higher-resolution thermal or the Pi camera is available: run a YOLO person detector on the Hailo-8. Pre-compiled YOLO models for Hailo run at real-time frame rates on the Pi 5.
- Output per frame: `{person: true/false, conf: 0–1, bearing_deg, max_temp_c}`. Bearing comes from which column of pixels the blob sits in.

**2b. mmWave breathing and presence**

- A 24 GHz radar sends out radio chirps and measures the reflections. Anything moving shifts the reflection slightly (the Doppler effect), even a chest rising 0.5 cm.
- Off-the-shelf modules (LD2410, Rd-03D) already report "moving target" and "still target" with distance; the Rd-03D tracks people up to about 8 m across 120°.
- "Still but present" is the key signal: a person lying motionless still breathes, and the module reports a stationary target with energy at their range.
- Stretch: estimate breathing rate by taking the target's range or energy over 20–30 s and finding the dominant frequency between 0.1 and 0.5 Hz (6–30 breaths per minute) with an FFT.
- Output: `{present: true, moving: false, range_m, breaths_per_min (optional), conf}`.

**Why both:** thermal is fooled by hot objects (a radiator, a laptop, a heat pack) but sees through darkness and smoke. Radar is fooled by fans and moving curtains but ignores temperature. Agreement between them is strong evidence. In the demo, show the judges a case where only one fires and the score stays low.

## Module 3, Hazards: keep the rescuer safe

The cheapest win in the whole project: an Arduino reads the Grove gas and environment sensors and the Pi raises a hazard flag that rides along on every alert.

- **Sensors:** Grove gas sensor (MQ-family: smoke, CO, combustible gas), temperature/humidity, optionally air quality. They output an analog voltage that rises with gas concentration.
- **Arduino sketch:** read each sensor every 500 ms, average the last 10 readings, print one JSON line over USB serial, e.g. `{"gas":412,"temp_c":22.4,"rh":41}`.
- **Warm-up and baseline:** MQ sensors need a few minutes to heat up. Record a baseline in clean air at the venue, then flag readings above baseline × 1.5 as `caution` and × 2.5 as `danger`. These multipliers are starting points to tune on site, not calibrated ppm.
- **Output:** `{hazard: "none"|"caution"|"danger", gas_raw, temp_c}` on topic `hazard.gas`.
- **On the map:** a hazard becomes a colored ring around the searcher's position, and in the alert it raises the triage priority (a victim in a gas zone is more urgent).
- **Demo trick:** a puff from an unlit butane lighter near the sensor reliably triggers it. Ask organizers first and never use a flame indoors.

## Module 4, Fuse: one confidence number and a triage level

Fusion merges every sensor's evidence about one target into a single confidence score and a triage priority. Keep it a weighted sum anyone can explain on a whiteboard; judges trust what they can follow.

**Step 1: build a target track.** Group hits that refer to the same person: same device ID (hashed MAC) or same bearing within about 20° and seen within the last 30 s. Each track keeps its latest value from each sensor.

**Step 2: score it.** Each sensor gives evidence from 0 to 1, and the score combines them:

```latex
C = 1 - (1 - w_{rf} e_{rf})(1 - w_{th} e_{th})(1 - w_{mm} e_{mm})(1 - w_{au} e_{au})
```

This "noisy-OR" form means each independent sensor can only raise the confidence, two agreeing sensors push it high quickly, and one sensor alone stays moderate. Starting weights (tune on site):

| Evidence | Symbol | How e is computed | Starting weight w |
| --- | --- | --- | --- |
| RF device nearby | e\_rf | 1 when RSSI > −60 dBm, scaling to 0 at −90 dBm | 0.5 |
| Thermal person | e\_th | Detector confidence | 0.7 |
| Radar presence / breathing | e\_mm | Module confidence; 1 if breathing rate in 6–30 /min | 0.7 |
| Audio (if added) | e\_au | Voice / whistle classifier confidence | 0.4 |

Worked example: a strong phone signal (e\_rf = 1) alone gives C = 0.5. Add a thermal person at 0.9 confidence and C = 1 − 0.5 × 0.37 = 0.82. Add radar presence at 0.8 and C rises to about 0.92.

**Step 3: triage level.**

| Priority | Rule | Map color |
| --- | --- | --- |
| P1 Urgent | C ≥ 0.8 and (not moving, or hazard = danger) | Red |
| P2 Confirmed | C ≥ 0.8 | Orange |
| P3 Possible | 0.5 ≤ C < 0.8 | Yellow |
| P4 Device only | C < 0.5 | Gray (logged, not pushed unless link is idle) |

**Step 4: decide what to send.** Send a new alert when a track is created, when its priority changes, or every 30 s for P1/P2. That keeps radio traffic tiny.

## Module 5, Relay: getting the alert out with no infrastructure

LoRa trades speed for range: kilobits per second instead of megabits, but kilometers instead of tens of meters, on a few milliamps. So the design rule is to send tiny, prioritized messages, never raw data.

**Radio setup**

- Two (or more) Meshtastic boards, region set to US (902–928 MHz), all on the same modem preset and channel. Mismatched presets cannot hear each other.
- Default preset LongFast (250 kHz bandwidth, spreading factor 11) gives roughly 1 kbps with long range. Indoors at the venue, MediumFast is a good choice: 3–4× the data rate and lower latency.
- Every extra node you place automatically relays (a "mesh"), hop limit 3 by default. Dropping an extra node partway is the "breadcrumb" trick for reaching into a building.
- The Pi talks to its board over USB serial using the `meshtastic` Python library; the board handles radio, encryption, retries and mesh routing.

**Alert message (designed to stay well under 100 bytes)**

| Field | Example | Bytes (approx.) | Meaning |
| --- | --- | --- | --- |
| v | 1 | 1 | Message format version |
| id | 7 | 2 | Track number |
| p | 1 | 1 | Triage priority 1–4 |
| c | 92 | 1 | Confidence, percent |
| lat, lon or x, y | 42.36030, -71.05880 | 8 | Position (GPS) or meters from the start point (no GPS) |
| brg, rng | 135, 6 | 3 | Bearing (deg) and estimated range (m) from the searcher |
| alive | 1 | 1 | Thermal or radar confirms life |
| br | 14 | 1 | Breaths per minute (0 = unknown) |
| hz | 2 | 1 | Hazard: 0 none, 1 caution, 2 danger |
| t | 1762531200 | 4 | Unix time |

Send it as compact JSON for the hackathon (simple to debug, about 90 bytes) or pack it with `struct` into about 25 bytes if airtime gets tight.

**Priority queue with store-and-forward**

1. Every alert goes into a queue ordered by priority, then age.
2. The sender pops the top item, transmits, and waits for the mesh acknowledgement (Meshtastic supports want-ack).
3. No ack in 10 s: keep it in the queue and retry with back-off. Never block a P1 behind a stuck P3.
4. Newer alerts for the same track replace older queued ones, so a backlog never sends stale data.
5. When the link comes back after an outage, the queue drains P1 first. This is the moment to show judges.

## Module 6, Map: show it where operators already look

Operators use TAK (ATAK on Android, WinTAK on Windows) as their shared map, so the strongest finish is a marker appearing in TAK. A simple web map is the fallback that always works.

**Primary: TAK via Cursor on Target**

- The command-post laptop runs `lora_rx.py`, which turns each received alert into a Cursor-on-Target (CoT) XML event.
- `pytak` sends it to the ATAK multicast default, `udp://239.2.3.1:6969`. Any ATAK/WinTAK device on the same local network shows it instantly with no TAK server.
- Event fields: `uid` = `AG-<track id>`; `type` = a ground-unit or point type (pick one that renders as a clear icon); `point` = lat/lon; `stale` = now + 2 minutes so old markers fade; `detail/remarks` = "P1 alive, 14 br/min, conf 92%, gas: danger".
- Searcher position goes out the same way as a separate friendly marker, so the map shows both the rescuer and the victim.
- Local network for the demo: a travel router or a phone hotspot that the laptop and the ATAK phone join. Multicast often fails on venue Wi-Fi, so don't rely on it.

**Fallback: web map**

- Flask serves a Leaflet page on the laptop; alerts arrive over a WebSocket.
- Markers colored by priority, a popup with vitals, a ring for hazards, and a side list sorted P1 first.
- No GPS indoors? Use a floor-plan image as the map background and plot x/y in meters from the entry point.

**What judges should see on the map:** the searcher's dot moving, a gray device hit appearing, turning orange then red as confirmation arrives, a hazard ring, and a clear timestamp showing when the last message arrived over LoRa.

## Demo layout and test plan

The demo is a scripted two-minute search across the demo area, rehearsed at least three times on Sunday morning in the actual space.

**Setup**

- **Victim:** a teammate lying still behind a partition or table, carrying a phone (Wi-Fi on, not connected) and a known BLE tag. A heat pack is the fallback if they can't lie in place.
- **Searcher:** a teammate with the sensing node in a backpack and the thermal/radar on a handheld bracket, starting about 10 m away.
- **Command post:** a laptop and an ATAK phone facing the judges, joined to your own travel router; the LoRa receiver plugged into the laptop.
- **Degraded-comms moment:** the field node has no IP network at all; turn off the laptop's venue Wi-Fi so it's visible that only LoRa delivers.

**Test plan (run each before demo day)**

| Test | Pass when | Why it matters |
| --- | --- | --- |
| RSSI calibration | You have a table of RSSI vs distance for the test phone at 1, 2, 4, 8 m in the demo area | Range estimates on screen are believable |
| Victim found from start position | Device hit within 30 s; correct direction within 45° | Core claim |
| False positive: hot object only | Heat pack alone with no breathing → score stays below 0.8 | Proves fusion isn't naive |
| False positive: phone on a table, no person | Score stays P3/P4 | Answers "a phone isn't a person" |
| Link drop | Unplug the receiver 30 s, plug back: queued P1 arrives first | Store-and-forward works |
| End-to-end latency | Measure time from detection on Pi to marker on map; target < 5 s | A number for the deployability slide |
| Battery runtime | Node runs 2+ hours on the power bank | Deployability slide |
| Replay mode | `replay.py` reproduces a recorded good run end to end | Insurance if live fails |

## Sources

- [Raspberry Pi AI HAT+ documentation](https://www.raspberrypi.com/documentation/accessories/ai-hat-plus.html)
- [Raspberry Pi AI HAT+ 26 TOPS (Elektor product page)](https://www.elektor.com/products/raspberry-pi-ai-hat-plus-26-tops)
- [Meshtastic LoRa configuration](https://meshtastic.org/docs/settings/config/lora)
- [Meshtastic radio settings and presets](https://meshtastic.org/docs/overview/radio-settings/)
- [Meshtastic blog: why to switch from LongFast](https://meshtastic.org/blog/why-your-mesh-should-switch-from-longfast/)
- [PyTAK (Python Team Awareness Kit)](https://pypi.org/project/pytak/5.6.0)
- [MLX90640 thermal camera (Waveshare)](https://www.waveshare.com/product/mlx90640-d110-thermal-camera.htm)
- [Rd-03D mmWave radar with Raspberry Pi (Core Electronics)](https://core-electronics.com.au/videos/how-to-use-an-mmwave-radar-to-track-humans-rd-03d-and-raspberry-pi-pico)
- [NUS: Mobile phone Wi-Fi detection in aerial SAR](https://www.comp.nus.edu.sg/~bleong/publications/apsys13-sos.pdf)
- [Parsons BlueFly](https://www.parsons.com/products/bluefly/)
- [MIT Lincoln Laboratory MURMUR](https://www.ll.mit.edu/r-d/projects/motion-under-rubble-measured-using-radar)
- [Meshtastic + TAK hardware guide for SAR](https://www.sdrstore.eu/meshtastic-tak-hardware-guide-search-rescue-field-teams/)
