# Schematic — HAYALI-GW-USB-C-R1

Net-level design for manufacturer recreation (KiCad / Altium / EasyEDA).  
All nets named below are authoritative.

---

## 1. Block diagram

```text
┌─────────────┐   VBUS     ┌──────────┐   3V3    ┌─────────────────┐
│  J_USB      │───────────►│ F1+TVS   │────────►│ ESP32-WROOM-32E  │
│  USB-C      │            │ + 3V3    │         │  GPIO16 RX2      │
│             │ D+/D-      │ LDO/buck │         │  GPIO17 TX2      │
│             │───────────►│          │         └────────┬──────────┘
└─────────────┘            │ CP2102N  │◄──USB───┐        │
                           └────┬─────┘         │        │
                                │ DISP_5V       │        │
                                ▼               │        ▼
                           ┌─────────┐          │   DISP_TX / DISP_RX
                           │ J_DISP  │◄─────────┴───────────────────
                           │ 1x5     │  (see pin map)
                           └─────────┘
```

---

## 2. USB-C power + data (`J_USB`)

| Pin (USB-C) | Net | Notes |
|---|---|---|
| A4/B4/A9/B9 VBUS | `VBUS` | Tie together |
| A1/B1/A12/B12 GND | `GND` | Tie together |
| A5 CC1 | via **R_CC1 = 5.1 kΩ** to GND | Default 5 V sink |
| B5 CC2 | via **R_CC2 = 5.1 kΩ** to GND | |
| A6/B6 D+ | `USB_DP` | To CP2102N |
| A7/B7 D− | `USB_DN` | To CP2102N |
| SBU / other | NC | |

**Protection**

| Ref | Part idea | Net |
|---|---|---|
| F1 | PTC 1.5 A / 6 V | `VBUS` → `VBUS_F` |
| D1 | TVS USB 5 V bidirectional | `VBUS_F`–`GND` |
| U_ESD | USBLC6-2 or equiv. | `USB_DP`, `USB_DN` |

Rename after fuse: **`VBUS_F`** feeds display and regulator.

`DISP_5V` = `VBUS_F` (direct).

---

## 3. 3.3 V regulator

| Ref | Requirement |
|---|---|
| U_REG | 5 V → 3.3 V, ≥600 mA (e.g. AMS1117-3.3 *or better* low-dropout / buck for heat) |
| C_IN | ≥10 µF on `VBUS_F` |
| C_OUT | ≥22 µF on `3V3` + 100 nF at ESP32 |
| Bulk | Extra 47–100 µF near ESP32 for Wi‑Fi TX peaks |

Net: `3V3` → ESP32 `3V3`, CP2102N `VDD`, pull-ups.

---

## 4. ESP32-WROOM-32E (`U_ESP`)

| Module pin | Net |
|---|---|
| 3V3 | `3V3` |
| GND | `GND` |
| EN | `EN` (10 kΩ pull-up to 3V3; optional reset button to GND) |
| GPIO0 | `GPIO0` (10 kΩ pull-up; BOOT button to GND) |
| GPIO16 | `DISP_TX` (UART2 RX ← display TX0) |
| GPIO17 | `DISP_RX` (UART2 TX → display RX0) |
| TXD0 | `ESP_TX0` → CP2102N RXD |
| RXD0 | `ESP_RX0` ← CP2102N TXD |
| Antenna | PCB antenna keep-out per Espressif |

Optional: IPEX module variant later — not R1.

---

## 5. USB–UART bridge (`U_USB`)

| Bridge | Net |
|---|---|
| CP2102N (preferred) or CH340C | |
| VDD | `3V3` |
| GND | `GND` |
| D+/D− | `USB_DP` / `USB_DN` |
| TXD | `ESP_RX0` |
| RXD | `ESP_TX0` |
| ~RTS | optional auto-reset to `EN` via 100 nF (classic ESP auto-program) |
| ~DTR | optional to `GPIO0` via 100 nF |

Implement Espressif-recommended auto-reset capacitor network so Arduino IDE upload works over USB-C.

---

## 6. Display header (`J_DISP`)

| Pin | Net | ESP32 |
|---:|---|---|
| 1 | `DISP_RESET` | Test pad only (no driver) |
| 2 | `GND` | |
| 3 | `DISP_RX` | GPIO17 |
| 4 | `DISP_TX` | GPIO16 |
| 5 | `DISP_5V` (`VBUS_F`) | |

Optional series 22–33 Ω on `DISP_TX` / `DISP_RX` for edge rate / ESD.  
Optional TVS array to GND on UART lines.

**Do not** hard-tie `DISP_RESET` to GPIO unless open-drain; 4D requires open-collector style if driven.

---

## 7. Indicators / debug

| Ref | Net |
|---|---|
| LED_PWR | `VBUS_F` via 1 kΩ (or `3V3`) |
| LED_ACT | GPIO2 (or free GPIO) via 1 kΩ — firmware optional |
| J_DBG | 2.54 mm pads: `3V3`, `GND`, `EN`, `GPIO0`, `ESP_TX0`, `ESP_RX0` |

---

## 8. Netlist (summary)

```
VBUS, VBUS_F, DISP_5V, 3V3, GND,
USB_DP, USB_DN,
EN, GPIO0, ESP_TX0, ESP_RX0,
DISP_TX, DISP_RX, DISP_RESET
```

Equivalence: `DISP_5V` ≡ `VBUS_F`.

---

## 9. Design rules (suggested)

| Rule | Value |
|---|---|
| Trace VBUS / DISP_5V | ≥0.5 mm (20 mil), prefer pour |
| UART | ≥0.2 mm |
| USB D+/D− | 90 Ω differential if length >30 mm; keep short |
| Copper | 1 oz min; 2 oz on power if available |
| Layers | 2 OK; 4 better for GND under ESP32 |

---

## 10. Out of scope nets

No Ethernet, no PoE, no Shelly, no 220 V, no battery.
