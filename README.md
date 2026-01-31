# ESP32 Sensor Projekt

Kompakte Wetterstation auf dem ESP32 mit MicroPython: liest einen DHT22 aus, erkennt Anomalien, zeigt Werte auf einem SSD1306-OLED, schickt Anomaliefenster per MQTT und loggt parallel auf SD-Karte. Für CPU-Infos wird eine MicroPython-Firmware mit `esp32.idf_task_info` benötigt (siehe [MicroPython Doku](https://docs.micropython.org/en/latest/library/esp32.html#esp32.idf_task_info) und CustomFirmware.MD).

## Hardware / Pinbelegung
- DHT22: Daten an GPIO 25 (Pin als Eingang).
- OLED (SSD1306, 128x64) via SoftI2C: SCL an GPIO 32, SDA an GPIO 33.
- SD-Karte (SPI): CS=12, SCK=14, MOSI=27, MISO=26; gemountet unter `/sd`.
- Shutdown-Taster: GPIO 13 auf GND (Pull-up aktiv).
- WiFi/MQTT: Standard ohne TLS auf Port 1883.

## Ablauf beim Start (`boot.py`)
1) SD-Karte auf `/sd` einbinden (SPI-Pins siehe oben); weiterlaufen, falls keine Karte steckt.
2) WLAN-Interface aktivieren und Hostname setzen (Verbindung wird von `mqtt_as` in `main.py` aufgebaut).
3) Konfiguration laden und für das Hauptprogramm exportieren.

## Laufzeitlogik (`main.py`)
- Liest den DHT22 etwa alle 2 Sekunden und testet auf Anomalien.
- Zeigt Temperatur, Luftfeuchte sowie CPU- und RAM-Auslastung (MicroPython- und IDF-Daten aus `lib/resources.py`) auf dem OLED an.
- Sendet Telemetrie als JSON an `sensors/esp32/ESP32-Sensor/telemetry` in Anomaliefällen.
- Schreibt Messungen zusätzlich als CSV (pro Tag eine Datei) nach `/sd/telemetry_YYYY-MM-DD.csv`.
- MQTT/WiFi läuft asynchron via `mqtt_as` (asyncio), inkl. automatischer Reconnects und Keepalive.
- Uhrzeit wird nach MQTT-Connect per NTP synchronisiert.
- Shutdown: bei Tasterdruck OLED löschen, SD sauber aushängen, verbindung trennen und danach sicheres Abschalten ermöglichen.

### MQTT-Nachrichten
- Status (`.../status`, retained): `{"device_id":"ESP32-Sensor","status":"online|offline"}`
- Telemetrie (`.../telemetry`): `{"device_id":"ESP32-Sensor","ts":[Y,M,D,h,m,s,wday,yday],"temp_c":<float>,"hum_pct":<float>}`

## Dateien
- `src/boot.py`: SD-Mount, Config-Load, WLAN-Hostname, MQTT-IDs/Topics.
- `src/main.py`: Sensorauslese, Anzeige, MQTT-Publish via `mqtt_as`, SD-Logging, Shutdown-Handling.
- `src/lib/resources.py`: Helfer für CPU-/Speicherstatistiken (benötigt Custom-Firmware).
- `src/lib/detectors.py`: Implementierung von Anomalieerkennungsverfahren (Z-Score, EWMA, Adaptive Treshholding, Rule-Based)
- `src/lib/logger.py`: Zentraler Logger für Konsolen und SD-Logging.
- `src/lib/sdd1306.py`: SSD1306-Treiber (externe Bibliothek).
- `src/lib/mqtt_as`: MQTT-Client (externe Bibliothek).
- `evaluation.ipynb`: Skript zur Auswertung der Anomalieerkennung.
