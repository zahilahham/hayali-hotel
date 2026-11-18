# Manufacturer brief — Hayali USB-C ESP32 Gateway

**Customer:** Hayali Boutique Hotel (room screens)  
**Project:** Room gateway PCB  
**Revision:** R1  
**Qty (first run):** 5 prototypes, then ~32 production  

Please quote: bare PCB + SMT assembly + through-hole connectors + functional test.

---

## What to build

A small 2-layer (prefer **4-layer** if RF budget allows) PCB that:

1. Accepts **USB-C** as the only external power/data cable.
2. Derives **5 V** for the display and **3.3 V** for an **ESP32-WROOM-32E**.
3. Plugs **directly** onto the 4D Systems gen4 **5-way** header (2.54 mm pitch) used on:
   - **gen4-uLCD-70DT** (7″)
   - **gen4-uLCD-43DT** (4.3″)
4. Crosses UART so ESP32 GPIO16/17 speak Genie to the display.
5. Exposes USB D+/D− to a USB–UART bridge for field flashing (no external dongle).

```text
         USB-C 5 V
             |
             +-- polyfuse / TVS
             |
             +---> DISP_5V  ----+
             |                  |
             +---> 3V3 LDO ---> ESP32-WROOM-32E
             |                  |  GPIO16 = RX2
             |                  |  GPIO17 = TX2
             |                  |
             +-- CP2102N <------+ USB D+/D-
                                |
        5-pin female 2.54 mm <--+ mates gen4 5-way male pins
        (board edge)
```

Shelly relays, 220 V, and the microSD graphics are **out of scope** for this PCB.

---

## Connectors (critical)

| Ref | Type | Purpose |
|---|---|---|
| `J_USB` | USB-C receptacle (16-pin mid-mount preferred) | Power + programming |
| `J_DISP` | **1×5 female**, 2.54 mm, **right-angle or vertical** (see mechanical) | Direct mate to gen4 5-way |

**Do not reverse pin 1.** Use the official 4D order in [connectors/display-5way.md](connectors/display-5way.md).

Optional: second identical footprint `J_DISP_ALT` not populated — only if mechanical review needs a second mating position. Default is **one** `J_DISP`.

---

## Electrical targets

| Item | Spec |
|---|---|
| Input | USB-C VBUS 5 V (CC pull-downs 5.1 kΩ for default sink) |
| Display rail | 5.0 V, shared VBUS after **1.5 A** polyfuse |
| ESP32 rail | 3.3 V, ≥600 mA continuous capability |
| UART | 3.3 V TTL, 115200 8N1 |
| RESET to display | **NC / floating** in normal use (pad only) |
| ESD | USB lines + display UART recommended |

Current budget: design for **≥1.5 A** at 5 V (7″ backlight + ESP32 Wi‑Fi peaks).

---

## Deliverables from manufacturer

1. Gerbers (RS-274X) + drill + IPC-356 netlist  
2. Pick-and-place / centroid  
3. Assembled & tested boards  
4. Photo of first article top/bottom  
5. Continuity report: USB-C VBUS → pin 5 of `J_DISP`; GND continuity; UART cross-check  

Customer will flash firmware and run Genie/Wi‑Fi acceptance.

---

## Files in this folder

See [README.md](README.md). Schematic is net-level (markdown). If you prefer KiCad/Altium, recreate from [schematic.md](schematic.md) + [bom.csv](bom.csv); we can accept your native project as long as nets and mechanical constraints match.
