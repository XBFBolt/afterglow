# Afterglow

Search-and-rescue sensing node for the Code Metal Forge Hackathon 2026. A Raspberry Pi 5 finds a missing person's devices over Wi-Fi and BLE, confirms they're alive with a thermal camera and mmWave radar, and relays a prioritized alert over LoRa to a TAK map. Every module also runs in sim mode, so the whole pipeline works on a laptop with no hardware.

## Setup

Python 3.11 or newer.

**With uv (recommended)**

```
uv sync
uv run pytest
```

**With pip**

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
```

`requirements.txt` is generated from `uv.lock`, so don't edit it by hand. To add a package, run `uv add <package>`, then regenerate it:

```
uv export --format requirements-txt --no-hashes -o requirements.txt
```

## Where to look

- [CLAUDE.md](CLAUDE.md): architecture, message schema, coding rules, commands
- [Blueprint.md](Blueprint.md): the full technical design
- [TODO.md](TODO.md): what's done and what's next
