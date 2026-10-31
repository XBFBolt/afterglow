# Forge Hackathon 2026: Win Playbook

Oct 26, 2026 · @Dillon

## Event at a glance

The real build window is 26 hours (Sat 10:00 to Sun 12:00), and you are judged on a live demo, so everything here is optimized for one thing: a reliable, end-to-end working demo by Sunday noon.

| Item | Detail |
| --- | --- |
| Name | [Code Metal Forge Hackathon 2026, "Operation Lost and Found"](https://www.codemetal.ai/forge-hackathon) |
| Dates | Fri Nov 6 (reception 17:00–20:00), Sat Nov 7 (check-in 08:00, hard start 10:00, build overnight), Sun Nov 8 (submissions close 12:00, demos, awards \~16:30) |
| Venue | BASE-X, 60 State Street, Suite 1400, Boston. 25k sq ft, 200+ workstations, demo area, catering, open overnight |
| Sponsor / host | Sponsored by USSOCOM; hosted by Code Metal |
| Prizes | $10,000 / $5,000 / $3,000. Winning team gets a meeting with Code Metal leadership; university members on the winning team get an automatic internship interview |
| Judged on | Mission relevance, a working live demonstration, deployability |
| Judges | Experts from defense, emergency response, academia, deep tech, against a common mission and technical rubric |
| Team size | Up to 4. Solo applicants are matched Saturday morning |
| Who's in the room | Special Forces operators as mentors (mission realism, user feedback), technical mentors (sensing, comms, autonomy, embedded, ML) |
| Contact | forge@codemetal.ai |

The four challenge objectives (your solution should hit at least two, ideally all four in one pipeline):

1. **Locate isolated people** without GPS, cell network, or fixed comms.
2. **Detect life and hazards**: heat, motion, sound; gas, structural risk.
3. **Map changing conditions**: a shared operating picture as fire, weather, water, terrain shift.
4. **Deliver critical information** over degraded or overloaded channels.

The page's four theme tags are RF Communications, Sensing, Resilient Info-Sharing, and Edge Compute. Read those as the hosts telling you what they want to see.

## What the judges and hosts care about

The hosts are an edge-compute company that just bought an RF shop, judging alongside SOF operators. A project that finds a person with RF sensing, runs entirely on edge hardware, and pushes alerts over a degraded link is aimed squarely at all of them.

**Code Metal.** Boston startup founded 2023 by Peter Morales (CEO) and Alex Showalter-Bucher. Core product: AI code translation with formal verification, getting algorithms onto real chips (CPU, GPU, FPGA, DSP, MCU). Raised a $125M Series B in Feb 2026 and won an $80M Department of War agreement for WarMatrix in Aug 2026. Their recurring theme is closing the gap between an algorithm that works in a model and a capability that works in the field. Say "deployable on the edge, measured, tested" and show it.

**The Advanced RF Group (new, June 2026).** Code Metal acquired Signal Processing Technologies and its founders, Joe Farkas and Dr. Brandon Hombs, now lead an RF group focused on software-defined radio, spectrum sensing, RF machine learning, resilient comms, and Position, Navigation and Timing (PNT). Expect RF experts among mentors or judges. An SDR or RF-ML component that actually works will get noticed; a hand-wavy one will get picked apart.

**USSOCOM / operators.** They value mission realism: low size, weight and power; works with no infrastructure; simple for a tired operator; output in tools they already use (TAK / ATAK maps). Talk to operator mentors early Saturday and quote them in your pitch.

**Lessons from Code Metal's last hackathon (Metal Ops, March 2025, 14 teams, same city, same sponsor).**

| Place | Team | Project | Why it likely won |
| --- | --- | --- | --- |
| 1st ($10k) | Team Medarus (RF/Cyber R&D PM + BU and Harvard students) | LANTERN: passive sensor nodes sniffing tire-pressure RF signals, low-power mesh back to a command center | Passive RF sensing + mesh relay + cheap + scalable; a mixed team with a real RF practitioner |
| 2nd | Team C:4 (MIT students) | Code Three: bodycam to briefing in seconds | Clear operator workflow, edge AI that saves time |
| 3rd | DOD Special Forces team | Unsanitized Inputs | Operators compete too: realism is the bar |

2025 projects were judged on impact, function, feasibility, and scalability, and the winner was described as a concept. This year's rubric explicitly demands a working live demo and deployability, so the bar has moved from "cool idea" to "it works in front of us".

Three takeaways: passive RF sensing plus low-power mesh relay already won once with this crowd; cross-disciplinary teams with real RF skills win; and the demo must actually run.

## Hardware available and what it enables

Every team gets a Raspberry Pi 5 (16 GB) with a 26 TOPS AI accelerator (very likely the Hailo-8 based AI HAT+), an Arduino with a Grove shield, and a Grove 40-in-1 sensor kit. Everything else comes from a shared library, so grab contested items (SDRs, thermal cameras, mmWave radar, UWB, LoRa) the moment the clock starts.

| Hardware | Use in a SAR system | Objective | Grab priority |
| --- | --- | --- | --- |
| Wi-Fi adapter (monitor mode) / Bluetooth | Passively detect a victim's phone, watch, earbuds by probe requests and BLE adverts; RSSI for direction and range | Locate | High |
| SDR | Spectrum survey, detect other emitters (e.g. 2.4 GHz, 433/915 MHz beacons, PLBs), RF-ML classification. Receive-only | Locate, Detect | High |
| LoRa modules | Long-range, low-power mesh to carry alerts when Wi-Fi and cell are down (Meshtastic-style) | Deliver | High |
| UWB ranging | GPS-free relative positioning of searchers and beacons, \~10 cm class ranging | Locate | Medium-high |
| Thermal camera | Find body heat through darkness or smoke; person detection on the accelerator | Detect | High |
| mmWave / Doppler radar | Detect presence and breathing through light obstruction; tells living from not | Detect | High |
| Microphones | Detect calls for help, whistles, tapping; direction finding with a mic array | Detect | Medium |
| Gas / environmental sensors | Responder hazard alerts (CO, smoke, VOC) | Detect hazards | Medium (cheap win) |
| IMU, GPS/GNSS | Dead reckoning for searchers when GPS drops; geotag detections | Locate, Map | Medium |
| Microdrones, ground robots | Carry the sensor payload into areas too dangerous for people | All | Optional, high risk |
| Jetson, ESP32, Pi Zero | Extra nodes: ESP32s make cheap BLE/Wi-Fi sniffer or mesh nodes | Locate, Deliver | Medium |

Rule of thumb: only use a component in the demo if someone on your team has made it work before the event, or if it is plug-and-play. Drones are the classic hackathon trap; a robot or a handheld "wand" carrying the same payload demos far more reliably indoors.

## Project concepts, ranked

Recommended pick: **Concept A, an RF + life-sign "find, confirm, relay" kit**. It covers all four objectives in one pipeline, leans into the Advanced RF Group's specialties, and every piece can be built and rehearsed indoors.

| # | Concept | Objectives hit | Demo reliability | Fit with judges | Verdict |
| --- | --- | --- | --- | --- | --- |
| A | **Find, confirm, relay.** Passive Wi-Fi/BLE detection of a victim's devices (phone, watch, earbuds) gives a bearing and range; thermal + mmWave confirm a living person; alert travels over a LoRa mesh to a TAK-style map | All 4 | High (all indoor-friendly) | RF sensing, edge AI, resilient comms, PNT | **Build this** |
| B | Survivor-under-rubble sensor: mmWave breathing detection + audio tap detection, edge classifier, LoRa alert | Detect, Deliver | High | Sensing, edge | Strong fallback; fold into A as the "confirm" stage |
| C | GPS-denied searcher tracking: UWB anchors + IMU dead reckoning, search-coverage heat map shared over mesh | Locate, Map | Medium | PNT (an RF Group focus) | Great add-on if you have a UWB person |
| D | Drone thermal sweep with onboard person detection on the accelerator | Detect, Map | Low (flight risk indoors, FAA/venue limits outdoors) | Edge AI | Only with a drone expert on the team |
| E | Degraded-comms message prioritizer: compress and rank SAR traffic (distress > positions > chat) over a narrow LoRa link, with store-and-forward | Deliver | High | Resilient info-sharing | Build the core idea into A's relay layer |
| F | RF-ML spectrum classifier on SDR that flags beacons and phones vs. noise | Locate | Medium | RF Group bullseye | Stretch goal for A if you have an SDR/DSP person |

Why A beats the alternatives:

- **It mirrors a real fielded product.** Parsons/QRC's BlueFly detects BLE and Wi-Fi from phones, watches, AirTags and medical devices for SAR, weighs under a pound, and claims about 200 m range. Academic work (NUS, 2013) showed phones can be detected from the air by their background Wi-Fi probe requests. You are building a cheap, open, edge-verified version and adding life confirmation, which BlueFly does not do.
- **The confirm stage answers the obvious judge question:** "a phone isn't a person." Thermal plus breathing radar (the idea behind MIT Lincoln Lab's MURMUR rubble radar) turns a device hit into "person, alive, breathing \~14/min."
- **The relay stage hits the operator workflow.** The Meshtastic/ATAK pattern is already used by SAR teams; pushing Cursor-on-Target markers into a TAK map is exactly where operators want the data.

Differentiators to add if time allows: a triage priority score per detection (alive + not moving + hazard nearby = top); a hazard ring from the gas sensor; a confidence number on every alert; measured latency and power on the Pi, shown on a slide.

## Recommended architecture

One Pi 5 sensing node does all detection and scoring at the edge; only a small, prioritized alert crosses the radio link.

&#91;embedded content: Find, confirm, relay · one Pi node, a LoRa link, a TAK map\]

Three sensor stages feed one fusion step on the Pi, and only the scored alert crosses the LoRa link to the map.

Suggested stack (all open source, all runs on the Pi):

- **Find:** USB Wi-Fi adapter in monitor mode + scapy for probe requests; bleak for BLE adverts; RSSI smoothed with a Kalman or moving-average filter; bearing by rotating a directional antenna or comparing two nodes
- **Confirm:** thermal camera + person detector on the AI accelerator; 24 GHz mmWave module over UART for presence and breathing rate
- **Hazards:** Grove gas sensor on the Arduino, read by the Pi over serial
- **Fuse:** a simple, explainable score (device hit + heat + breathing + hazard), never a black box; judges will ask how it decides
- **Relay:** Meshtastic-flashed LoRa boards; compact JSON or protobuf alert, highest priority first, store-and-forward if the link drops
- **Map:** Cursor-on-Target markers into ATAK/WinTAK, or a Leaflet web map on the command laptop as the fallback

## Team roles

Four people, four lanes, one integrator. Assign by who has already touched the hardware, not by title.

| Role | Owns | Prep before the event |
| --- | --- | --- |
| RF lead | Wi-Fi monitor-mode / BLE scanning, RSSI bearing and range, SDR stretch goal | Get probe-request and BLE sniffing running on a Pi 5; know RSSI-to-distance math and its limits |
| Sensing + edge ML lead | Thermal person detection on the AI accelerator, mmWave presence/breathing, fusion into a confidence score | Run a YOLO person model on a Hailo-class accelerator; read a UART mmWave sensor |
| Comms + map lead | LoRa mesh relay, message format and prioritization, TAK/map display | Two LoRa nodes passing a message; push a Cursor-on-Target marker to ATAK/WinTAK or a simple web map |
| Integrator + pitch lead | Demo scenario, end-to-end test runs, slides, talking to operator mentors, timekeeping | Pitch skeleton, demo script, mission research; this person keeps everyone on the clock |

If you have fewer than four, merge the integrator role into the comms lead and drop the SDR stretch goal.

## Two-week prep plan (Oct 26 to Nov 6)

The goal of prep is skills and tooling, not a finished project: arrive able to make each sensor work in under an hour. First, email the organizers to confirm what pre-existing code is allowed (see Risks).

**Week 1 (Oct 26 to Nov 1): de-risk each lane**

- [ ] Email forge@codemetal.ai with the questions in the Risks section
- [ ] Confirm team roster and roles; set up a shared repo, chat channel, and this doc
- [ ] RF lead: Pi 5 + USB Wi-Fi adapter in monitor mode, log probe requests with scapy; BLE scan with bleak; plot RSSI vs. distance across a room
- [ ] Sensing lead: run a person-detection model on a Hailo-8 class accelerator (Hailo's Raspberry Pi examples); read a cheap 24 GHz mmWave module over UART
- [ ] Comms lead: flash two LoRa boards with Meshtastic, send text and position between them; install ATAK (Android) or WinTAK and get a test marker on the map
- [ ] Integrator: read up on SAR basics (lost-person behavior, probability of area, search segments) and on TAK / Cursor on Target; draft the pitch skeleton

**Week 2 (Nov 2 to Nov 6): integrate the skeleton**

- [ ] Define one JSON detection message (id, type, bearing/range, confidence, vitals, hazard, timestamp, node position) everyone codes against
- [ ] Dry run: fake detection on the Pi → LoRa → marker on the map, end to end
- [ ] Write the demo scenario and rehearse a 3-minute pitch once with a timer
- [ ] Pack list: laptops, chargers, phones with ATAK, own LoRa boards if you have them, USB-C cables, power banks, extension cord, tape, a cardboard "wall" for the demo, a hand warmer or heat pack as a thermal stand-in
- [ ] Sleep plan: decide who sleeps when on Saturday night (see weekend plan)

## Weekend plan

The one rule: a crude end-to-end demo by Saturday 22:00, then only improvements. Feature freeze Sunday 09:00.

| When | Goal | Notes |
| --- | --- | --- |
| Fri 17:00–20:00 | Reception: listen to the USSOCOM framing, meet mentors and judges | Ask operators: "What's the first thing you'd want to know when you arrive at a search area?" Note exact phrases for the pitch |
| Sat 08:00–10:00 | Check-in, challenge brief, adjust the plan to any surprise constraints | Scope check against the brief; claim shared hardware the second it is allowed |
| Sat 10:00–13:00 | Each lane gets its piece working standalone | Grab the integrator's attention the moment anything is blocked >30 min |
| Sat 13:00–15:00 | Operator mentor session: show the plan, get feedback | Adjust; this is your realism check |
| Sat 15:00–22:00 | First end-to-end run: detect → confirm → relay → map | **Milestone: crude but complete demo by 22:00** |
| Sat 22:00–04:00 | Robustness: confidence scoring, hazard sensor, map polish; two people sleep in shifts | Record a backup video of the working demo before anyone sleeps |
| Sun 04:00–09:00 | Stretch goals only if core is solid; slides; measure latency and power | Feature freeze 09:00 |
| Sun 09:00–12:00 | Rehearse the live demo 3+ times on the demo-area setup; submit early | Fix only demo-breaking bugs; have the backup video ready |
| Sun 12:00–16:30 | Live demo and judging | One person narrates, one runs hardware, one plays the victim, one handles Q&A |

## Pitch and demo script

Lead with the mission, then let the live demo do most of the talking, then close on deployability. Map every minute to the three judging criteria.

1. **Mission (30 s, mission relevance).** A hiker is lost in a dead zone, or a building has collapsed: no GPS, no cell. Quote what an operator mentor told you on Saturday.
2. **Live demo (2 min, working demonstration).**
   1. A teammate (the "victim") hides behind a partition with a phone and a heat pack.
   2. The searcher walks the room with the sensing node; the screen shows a device detection and a bearing.
   3. Closer in, thermal and mmWave confirm "person, alive, breathing." Show the confidence climb.
   4. Unplug venue Wi-Fi or turn off the laptop's network: the alert still arrives on the map over LoRa. Make the degraded link visible.
   5. The marker appears on the TAK-style map with priority, vitals, and a gas-hazard ring.
3. **Deployability (45 s).** Bill of materials and cost, weight, battery life, measured end-to-end latency, everything runs on the Pi at the edge, no cloud. What it would take to mount on a drone or robot.
4. **What's next (15 s).** Multi-node triangulation, RF-ML classification on SDR, integration with existing TAK servers.

Q&A prep, the questions you will get:

- "What if the victim has no phone or it is dead?" Thermal + radar + audio still work; device detection is the long-range cue, not the only one.
- "Privacy?" Hash MAC addresses, keep no payloads, and allow matching against a known device ID from family or the trip plan.
- "Phone MAC randomization?" Detecting presence still works; you rank by signal behavior, not identity.
- "Range?" Give your measured numbers, plus the published \~200 m for comparable drone-mounted systems.
- "Why trust the detection?" Show the confidence model and the false-positive test you ran.

## Risks, constraints, and questions for the organizers

The biggest risk is not technical: it is a demo that half-works on stage. Everything below protects the live demo.

| Risk | Mitigation |
| --- | --- |
| Demo-area RF is crowded (200 laptops, everyone's Wi-Fi/BLE) | Filter to a known test device ID; calibrate RSSI in the demo area Sunday morning |
| Shared hardware runs out | Bring your own LoRa boards, ESP32s, USB Wi-Fi adapter if you own them (confirm it's allowed) |
| Integration hell on Saturday night | Agree on the JSON message format before the event; hit the 22:00 end-to-end milestone |
| Live demo fails | Backup video recorded Saturday night; a "replay" mode that feeds logged sensor data through the same pipeline |
| Scope creep | Drones and SDR are stretch goals only; feature freeze 09:00 Sunday |
| Sleep deprivation | Two sleep shifts; the pitch lead sleeps before the demo |

Legal and safety constraints:

- **Receive, don't transmit, on licensed bands.** Never transmit on cellular frequencies or build anything that impersonates a cell tower. Keep SDR work receive-only; LoRa in the 915 MHz ISM band is fine at normal power.
- **No outdoor drone flights downtown** without explicit organizer approval; downtown Boston sits near Logan Airport's controlled airspace. Plan indoor-only.
- **Privacy.** Sniff only your own devices in testing; hash identifiers in logs.

Questions to email forge@codemetal.ai this week:

- [ ] Can teams bring pre-written code, libraries, or their own hardware?
- [ ] Is the challenge brief on Saturday more specific than the website (a scenario, a test course, specific data)?
- [ ] What does the demo area look like (space, power, network, can a person hide behind something)?
- [ ] How long is each team's demo and Q&A, and are slides expected?
- [ ] Can shared-library hardware be reserved, and how are contested items allocated?
- [ ] Is there a submission format (repo, video, write-up) due at 12:00?

## Sources

- [Code Metal Forge Hackathon 2026 event page](https://www.codemetal.ai/forge-hackathon)
- Hardware list (PDF provided by the organizers)
- [Code Metal newsroom](https://www.codemetal.ai/news)
- [Introducing Code Metal's Advanced RF Group](https://www.codemetal.ai/news/introducing-code-metals-advanced-rf-group)
- [Code Metal acquires Signal Processing Technologies (01net)](https://www.01net.it/code-metal-acquires-signal-processing-technologies-expanding-its-provable-ai-platform-and-establishing-the-advanced-rf-group/)
- [Metal Ops 2025 results (Business Wire)](https://www.businesswire.com/news/home/20250325591815/en/Code-Metals-Metal-Ops-Hackathon-Unveils-Cutting-Edge-Smart-City-Concepts-for-USSOCOM)
- [Code Metal company profile (yespress)](https://yespress.io/code-metal.md)
- [Parsons BlueFly SAR detector](https://www.parsons.com/products/bluefly/)
- [NUS: Mobile phone Wi-Fi detection in aerial SAR (2013)](https://www.comp.nus.edu.sg/~bleong/publications/apsys13-sos.pdf)
- [MIT Lincoln Laboratory MURMUR](https://www.ll.mit.edu/r-d/projects/motion-under-rubble-measured-using-radar)
- [Meshtastic + TAK hardware guide for SAR](https://www.sdrstore.eu/meshtastic-tak-hardware-guide-search-rescue-field-teams/)
- [Akita MeshTAK ATAK plugin](https://hamradio.my/akita-meshtak-an-atak-plugin-for-off-grid-mesh-communication/)
