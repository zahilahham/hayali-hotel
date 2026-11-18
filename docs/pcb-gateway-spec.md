# Hayali USB-C ESP32 Gateway PCB — Spec

**Purpose:** One small board per room that replaces jumper-wire chaos between the 4D display programming header, a 5 V supply, and an ESP32. USB-C powers the gateway; the board feeds the display and speaks Genie UART to the Hayali server over Wi-Fi.

**Manufacturer pack (share with fab):** [../pcb/README.md](../pcb/README.md) — schematic nets, BOM, 5-way connector (7″ + 4.3″), mechanical outline, assembly tests.

**Firmware alignment:** Current sketch in [arduino/arduino.ino](../arduino/arduino.ino) uses `HardwareSerial(2)` with **RX = GPIO16**, **TX = GPIO17**, baud **115200**.

---

## 1. Role in the system

```text
USB-C (5 V) ---> Gateway PCB ---> Display +5V IN / GND
                      |
                      +-- ESP32 Wi-Fi ---> Hayali server (room ID + button)
                      |
                      +-- UART <---> Display TX / RX
```

Shelly is **not** soldered on this PCB. The server maps `ROOM_ID` to a Shelly HTTP URL and rings the relay on the LAN.

---

## 2. Connectors

### 2.1 USB-C (power + optional programming)

- USB-C receptacle, **CC resistors** for default 5 V sink (no PD negotiation required for v1).
- 5 V to onboard regulator chain and to display **+5V IN**.
- USB D+/D− to USB–UART bridge (CP2102N or CH340C) for flashing the ESP32 in the field.
- ESD protection on USB lines recommended.

### 2.2 Display header (direct mate to gen4 5-way)

**Official 4D gen4-uLCD Rev 1.6+ 5-way** (same on **7″** and **4.3″**). Gateway uses a **female 1×5 2.54 mm** that plugs onto the display male pins — see [../pcb/connectors/display-5way.md](../pcb/connectors/display-5way.md).

| Pin | Display name | Board net | Notes |
|---:|---|---|---|
| 1 | RESET | `DISP_RESET` | **Leave floating** in normal use; test pad only |
| 2 | GND | `GND` | Common ground |
| 3 | RX0 | `DISP_RX` | ESP32 **GPIO17** (TX2) → display receive |
| 4 | TX0 | `DISP_TX` | Display transmit → ESP32 **GPIO16** (RX2) |
| 5 | 5V IN | `DISP_5V` | From USB-C 5 V after polyfuse |

**Critical:** pin 1 is RESET, pin 5 is 5 V. Reversing the plug can damage the module. Silk-mark pin 1 on the PCB.

### 2.3 Optional debug

- 2.54 mm pads: `3V3`, `GND`, `EN`, `GPIO0` (boot), `TX0`, `RX0` if the USB bridge uses a different UART.
- LED: power.
- LED: Wi-Fi / activity (GPIO-driven).

---

## 3. Power

| Rail | Source | Use |
|---|---|---|
| 5 V | USB-C VBUS | Display backlight / module 5 V input |
| 3.3 V | Buck or LDO from 5 V (≥600 mA peak headroom) | ESP32-WROOM |

Requirements:

- Polyfuse on VBUS (~1–1.5 A).
- Reverse / TVS protection where practical.
- Capacitance for ESP32 Wi-Fi TX peaks.
- **Do not** feed display 5 V from ESP32 3V3.
- Common **GND** between USB-C, ESP32, and display header.

Estimated draw: ESP32 active Wi-Fi ~200–400 mA peaks; 7″ display backlight often several hundred mA. Design for **≥1 A** at 5 V continuous budget.

---

## 4. ESP32 and UART

- Module: **ESP32-WROOM-32E** (or equivalent), PCB antenna keep-out observed.
- Flash: 4 MB minimum.
- Genie link: UART2

| Function | GPIO | Direction |
|---|---:|---|
| Display data in | 16 | ESP32 RX ← display TX |
| Display data out | 17 | ESP32 TX → display RX |
| Boot | 0 | Strap / button |
| EN | EN | Reset |

**Level notes:** 4D programming headers are typically 3.3 V logic on TX/RX for these modules; confirm with a scope on first article. If 5 V TX is ever measured from an adapter path, add a **TXS0102** (or divider on DISP_TX only). Production intent: direct 3.3 V UART, short cable.

Baud: **115200 8N1**, Genie checksum as in current firmware.

---

## 5. Mechanical

- Target outline: roughly credit-card half or smaller (~50 × 40 mm class); finalize in layout.
- Mounting holes: M2.5 or M3, 4 corners.
- USB-C at board edge for wall-box or behind-display access.
- Display cable exits opposite or adjacent edge so the board can sit behind the panel.

---

## 6. Firmware provisioning (32 rooms)

Each board is flashed with:

- Wi-Fi SSID / password (2.4 GHz)
- `API_BASE` (hotel server URL)
- Unique `ROOM_ID` / device ID matching the desk and Shelly map

No PCB change per room — only firmware constants (or NVS config) differ.

Desk already stores Shelly links per room via `/api/bell-links`. After install, set each room’s Shelly URL once in the Hayali UI.

---

## 7. Out of scope for this PCB

- Shelly relay (separate module on mains)
- 4D microSD graphics (stays in the display)
- 220 V routing
- Zigbee / Matter (ESP32 Wi-Fi only for v1)

---

## 8. Acceptance tests (first articles)

1. USB-C powers board; power LED on; display backlight on with header connected.
2. Serial monitor shows `Room <ID> listening` and Wi-Fi join.
3. Press Bell on screen → server log line → mapped Shelly receives HTTP turn-on.
4. RESET pin open: screen stays up. Momentary GND on RESET pad: display resets.
5. Programming over USB-C succeeds without external CP2102 dongle.
