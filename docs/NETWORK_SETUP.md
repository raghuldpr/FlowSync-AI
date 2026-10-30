# Network setup: Windows Mobile Hotspot

Plan: the laptop broadcasts the network; boards and judges' phones join it; the phone gives the laptop internet by USB tethering (the LLM needs it).

1. Settings > Network & internet > Mobile hotspot. Set the band to **2.4 GHz** (ESP32 cannot see 5 GHz). Set a WPA2 password.
2. Connect the phone by USB cable and enable USB Tethering so the laptop's Wi-Fi card is free for the hotspot.
3. Turn the hotspot on. Run `ipconfig` and confirm the laptop is `192.168.137.1` on the hotspot adapter. Windows normally uses this address; if it differs, use the one shown.
4. Set `BACKEND_HOST` to that address in each firmware `config.h` (git-ignored; copy from `config.example.h`).
5. Allow the backend port once, in an admin PowerShell:
   `New-NetFirewallRule -DisplayName "FlowSync 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Any`
6. Start the backend bound to `0.0.0.0` (see the README), then run `python -m app.joinqr` to print the URLs and the QR sheet.

## Checks before the day
- A second phone and an ESP32 can both reach `http://192.168.137.1:8000/`.
- Count devices. Windows Mobile Hotspot is believed to allow about 8 clients; verify with the real number (boards plus camera phone plus several judge phones). Bring a router as backup if it is too tight.
- Hotspot auto-off: Windows can switch it off when the upstream connection changes. Toggle it back on and recheck the address.
- The LLM fallback still answers with internet removed (`drill_llm_down`).
