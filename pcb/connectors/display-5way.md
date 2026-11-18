# Display connector — gen4 5-way (7″ and 4.3″)

## Compatibility

| Screen | 4D part class | 5-way header |
|---|---|---|
| Hayali 7″ room panel | **gen4-uLCD-70DT** (Diablo16, Rev **1.6+**) | Yes — same pinout |
| Hayali 4.3″ panel | **gen4-uLCD-43DT** (same series, Rev **1.6+**) | Yes — same pinout |

Both use a **2.54 mm (0.1″) pitch, 5-pin** host/programming interface on the back of the module.

**Rev ≤1.5** boards have **no** onboard 5-way; they need gen4-IB + FFC. This PCB targets **Rev 1.6+** only.

---

## Official pinout (mate this exactly)

Viewed on the **display** 5-way male pins, **Pin 1 = RESET** (per 4D gen4-uLCD-(43-70)D datasheet):

| Pin | Display signal | Gateway net | Connection |
|---:|---|---|---|
| 1 | **RESET** | `DISP_RESET` | Test pad only — **leave floating** in normal use (do not drive hard) |
| 2 | **GND** | `GND` | Common ground |
| 3 | **RX0** | `DISP_RX` | ← ESP32 **GPIO17** (TX2) |
| 4 | **TX0** | `DISP_TX` | → ESP32 **GPIO16** (RX2) |
| 5 | **5V IN** | `DISP_5V` | USB-C VBUS after polyfuse |

```text
Display male header (top view, typical silk "1" at RESET):

   1      2      3      4      5
 RESET   GND   RX0   TX0   5V IN
   |      |      |      |      |
   |      |      |      |      |
 Gateway J_DISP female (plugs onto pins above)
```

### UART cross (required)

| From | To |
|---|---|
| Display **TX0** (pin 4) | ESP32 **RX** GPIO16 |
| ESP32 **TX** GPIO17 | Display **RX0** (pin 3) |

This matches firmware in `arduino/arduino.ino`.

---

## Connector on the gateway PCB (`J_DISP`)

| Property | Requirement |
|---|---|
| Gender | **Female** 1×5 receptacle (mates male pins on gen4) |
| Pitch | **2.54 mm** |
| Style | Prefer **right-angle** so the PCB lies flat behind the screen; vertical OK if mechanical clear |
| Keying | Silk mark **Pin 1 = RESET**; optional keyed housing |
| Current | Pin 5 must carry display + backlight (≥1 A continuous rating preferred) |

**Direct fit:** the PCB edge is placed so `J_DISP` seats fully on the gen4 5-way with the board parallel to the display back (application-PCB style per 4D mounting guidance).

Alternative (prototype only): solder the five gen4 pins straight into plated holes `H1–H5` with the same net assignment (no receptacle). Production prefers a removable female header.

---

## One PCB, two screen sizes

Electrically **identical** for 7″ and 4.3″.

Mechanically:

- Place `J_DISP` so it mates the 5-way on **both** modules (header location is in the same relative corner family on gen4 43/70).
- Provide mounting holes / outline clearance for **both** plastic frames — see [../mechanical/dimensions.md](../mechanical/dimensions.md).
- Keep **microSD cutout** so cards can be swapped without removing the gateway.

If first-article fit fails on one size, fab a outline-only respin; do **not** change the 5-way pin order.

---

## Power from one USB-C

```text
USB-C VBUS (5 V)
    |
   F1 polyfuse ~1.5 A
    |
    +---- DISP_5V ----> J_DISP pin 5 (powers screen)
    |
    +---- U_REG 3V3 --> ESP32
```

No second PSU. Do **not** power the display from the ESP32 3.3 V pin.

---

## Installer checklist

1. Confirm display silk / pin 1 before first plug-in.  
2. Seat `J_DISP` fully; do not bend gen4 pins.  
3. Plug USB-C — screen backlight + gateway power LED should turn on together.  
4. RESET must remain open (no jumper to GND).
