# ESP32 Sensor Projekt

Kompakte Wetterstation auf dem ESP32 mit MicroPython: liest einen DHT22 aus, zeigt Werte auf einem SSD1306-OLED, schickt Messungen per MQTT (TLS/Port 8883) und loggt parallel auf SD-Karte. Für CPU- und RAM-Infos wird eine MicroPython-Firmware mit `esp32.idf_task_info` benötigt (siehe [MicroPython Doku](https://docs.micropython.org/en/latest/library/esp32.html#esp32.idf_task_info) und CustomFirmware.MD).

## Hardware / Pinbelegung
- DHT22: Daten an GPIO 25 (Pin als Eingang).
- OLED (SSD1306, 128x64) via SoftI2C: SCL an GPIO 32, SDA an GPIO 33.
- SD-Karte (SPI): CS=12, SCK=14, MOSI=27, MISO=26; gemountet unter `/sd`.
- Shutdown-Taster: GPIO 13 auf GND (Pull-up aktiv).
- WiFi/MQTT: nutzt TLS-Verbindung zum Broker `janlieder.de` auf Port 8883.

## Ablauf beim Start (`boot.py`)
1) SD-Karte auf `/sd` einbinden (SPI-Pins siehe oben); weiterlaufen, falls keine Karte steckt.
2) WLAN verbinden (SSID/Passwort in `boot.py` hinterlegt) und Hostname setzen.
3) Uhrzeit via NTP (`pool.ntp.org`) synchronisieren.
4) MQTT-Client mit TLS initialisieren, Status-Topic (`.../status`) mit Last-Will registrieren und verbinden.
5) Globale Objekte/Topics für die Hauptschleife exportieren.

## Laufzeitlogik (`main.py`)
- Liest den DHT22 etwa alle 2 Sekunden, formatiert Timestamp aus RTC.
- Zeigt Temperatur, Luftfeuchte sowie CPU- und RAM-Auslastung (MicroPython- und IDF-Daten aus `lib/resources.py`) auf dem OLED an.
- Sendet Telemetrie als JSON an `sensors/esp32/ESP32-Sensor/telemetry`.
- Schreibt Messungen zusätzlich als CSV (pro Tag eine Datei) nach `/sd/telemetry_YYYY-MM-DD.csv`.
- Fehlerbehandlung: bei MQTT-/Netzwerkfehler wird die Verbindung neu aufgebaut.
- Shutdown: bei Tasterdruck OLED löschen, SD sauber aushängen und danach sicheres Abschalten ermöglichen.

### MQTT-Nachrichten
- Status (`.../status`, retained): `{"device_id":"ESP32-Sensor","status":"online|offline"}`
- Telemetrie (`.../telemetry`): `{"device_id":"ESP32-Sensor","ts":[Y,M,D,h,m,s,wday,yday],"temp_c":<float>,"hum_pct":<float>}`

## Dateien
- `boot.py`: SD-Mount, WLAN, NTP, MQTT-Setup.
- `main.py`: Sensorauslese, Anzeige, MQTT-Publish, SD-Logging, Shutdown-Handling.
- `lib/resources.py`: Helfer für CPU-/Speicherstatistiken (benötigt Custom-Firmware).

