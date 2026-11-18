# Mechanical — HAYALI-GW-USB-C-R1

## Goal

Board sits **behind** the gen4 module (application PCB style).  
`J_DISP` plugs **directly** onto the module’s **5-way male** header.  
USB-C hangs at a free edge for the wall cable.

---

## Board outline (R1 target)

| Parameter | Value |
|---|---|
| Size | **48 × 32 mm** (±1 mm OK after fit check) |
| Thickness | **1.6 mm** FR-4 |
| Corners | 2 mm radius |
| Finish | ENIG preferred (or HASL lead-free) |
| Silkscreen | Pin 1 marks, `HAYALI-GW-R1`, USB-C orientation |

See [outline.svg](outline.svg).

---

## Connector placement

| Ref | Edge | Notes |
|---|---|---|
| `J_DISP` | **Top edge**, centered on gen4 5-way | Female 1×5; pin 1 marked **RESET** |
| `J_USB` | **Bottom edge** (opposite display) | Cable exits away from glass |
| `U_ESP` | Center-right | Antenna toward board edge; keep-out clear of metal |
| `U_USB` / `U_REG` | Near USB-C | Short D+/D− |

### Direct mate

1. Align gateway so `J_DISP` pin 1 matches display pin 1 (**RESET**).  
2. Press until fully seated; board should lie roughly parallel to the display back.  
3. MicroSD of the gen4 must remain accessible — cutout below.

```text
        gen4 display (rear view)
   ┌────────────────────────────┐
   │  [microSD]     [5-way ↑]   │
   │                 ||||||     │
   │                 J_DISP     │  ← gateway plugs here
   │            ┌──────────┐    │
   │            │  ESP32   │    │
   │            │  gateway │    │
   │            │  USB-C ▭ │    │
   │            └──────────┘    │
   └────────────────────────────┘
```

---

## Mounting holes

Provide **both** hole sets on the same PCB so one design fits 7″ and 4.3″ frames (populate screws that match the installed screen).

| Hole set | Qty | ID | Use |
|---|---:|---|---|
| H_70_* | 4 | 2.7 mm (M2.5 clear) | Align with gen4-uLCD-**70** rear bosses / standoffs |
| H_43_* | 4 | 2.7 mm | Align with gen4-uLCD-**43** rear bosses / standoffs |

**Action for fab:** before tooling, customer will confirm exact hole coordinates against physical samples of both screens (measure from 5-way pin 1). Until then, place holes as **NPTH** pads on a 2.54 mm grid near corners; first-article may be file-to-fit.

Standoff height: **keep ≥ plastic rear recess**; do not crush the 5-way pins (header height typically ~6–8 mm including mate).

---

## Keep-outs

1. **ESP32 PCB antenna** — Espressif WROOM keep-out (no copper/ground pour under antenna region; no metal under that zone behind the screen if possible).  
2. **microSD** — rectangular cutout or notch ≥ card insertion path.  
3. **5-way** — clearance so female housing does not hit gen4 components.  
4. No tall parts under the gen4 plastic lip.

---

## Variants

| SKU | Difference |
|---|---|
| `HAYALI-GW-USB-C-R1` | Common PCB for 7″ and 4.3″ |
| (optional later) `-70` / `-43` | Outline trimmed if one hole set interferes |

Electrical BOM is identical for both screen sizes.
