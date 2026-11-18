# Hayali Gateway PCB — Manufacturer Pack

Custom **USB-C ESP32 gateway** that mounts / plugs onto the back of the Hayali room screens.

**One USB-C cable** powers:

1. the ESP32 gateway, and  
2. the 4D gen4 display (`5V IN` on the 5-way header).

**Direct display mate:** a 5-pin 2.54 mm connector on the PCB edge plugs onto the gen4 **5-way programming / host header** (same electrical pinout on **7″** and **4.3″** gen4-uLCD modules).

| Document | Use |
|---|---|
| [manufacturer-brief.md](manufacturer-brief.md) | Share this first with the fab / assembler |
| [connectors/display-5way.md](connectors/display-5way.md) | Official 5-way pinout + 7″ / 4.3″ notes |
| [schematic.md](schematic.md) | Block + net-level schematic |
| [bom.csv](bom.csv) | Bill of materials |
| [mechanical/dimensions.md](mechanical/dimensions.md) | Outline, keep-outs, mounting |
| [mechanical/pcb-diagram.svg](mechanical/pcb-diagram.svg) | **PCB top-view diagram** (open in browser / Cursor) |
| [mechanical/outline.svg](mechanical/outline.svg) | Compact board outline |
| [assembly-notes.md](assembly-notes.md) | Build / test / first-article checks |
| [../docs/pcb-gateway-spec.md](../docs/pcb-gateway-spec.md) | Product context (system role) |

**Firmware alignment:** [arduino/arduino.ino](../arduino/arduino.ino) — UART2 **RX=GPIO16**, **TX=GPIO17**, 115200 baud Genie.

**Part number (proposed):** `HAYALI-GW-USB-C-R1`  
**Variants:** same PCB + firmware for 7″ and 4.3″; only mechanical mounting holes differ (see mechanical pack).
