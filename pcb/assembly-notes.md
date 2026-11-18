# Assembly & first-article test

## Assembly

1. SMT all parts per [bom.csv](bom.csv); inspect USB-C alignment.  
2. Hand-solder `J_DISP` if not SMT; verify **pin 1 = RESET**.  
3. Clean flux around USB-C and 5-way.  
4. Do **not** fit a jumper on `DISP_RESET`.

## Continuity (before plugging a display)

| Check | Expect |
|---|---|
| USB-C VBUS → `J_DISP` pin **5** | Continuity via fuse |
| USB-C GND → `J_DISP` pin **2** | Continuity |
| `J_DISP` pin 4 → ESP32 GPIO16 | Continuity (`DISP_TX`) |
| `J_DISP` pin 3 → ESP32 GPIO17 | Continuity (`DISP_RX`) |
| `J_DISP` pin 1 → anything driven | **Open** (pad only) |
| `3V3` short to GND | None |

## Power-on (no display)

1. Plug USB-C 5 V supply (≥1.5 A capable).  
2. Power LED on; `3V3` ≈ 3.3 V.  
3. USB enumerates CP2102N / CH340.  
4. Flash [arduino/arduino.ino](../arduino/arduino.ino); set `ROOM_ID` / Wi‑Fi.

## Power-on (with display)

1. Confirm gen4 is **Rev 1.6+** with 5-way.  
2. Mate `J_DISP` — pin 1 to RESET.  
3. One USB-C: **screen backlight + gateway** both live.  
4. Serial: `Room <ID> listening`.  
5. Bell press → server log.  
6. RESET still floating; screen stays up.

## Failures to watch

| Symptom | Likely cause |
|---|---|
| Screen dark, ESP OK | Fuse / pin 5 not powered / wrong pin order |
| Smoke / hot regulator | **Pin order reversed** (5V on RESET) — stop |
| No Genie events | TX/RX not crossed; wrong GPIO |
| Upload fails | Auto-reset caps / bridge wiring |

## Packing

Ship with: short note “Pin 1 = RESET”, anti-static bag, no Dupont harness required for Rev 1.6 screens.
