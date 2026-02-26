# Nawijarka na Raspberry Pi – **tryb serwer**

RPi działa jako **serwer**: nie potrzebujesz monitora ani klawiatury. Sterujesz nawijką z **telefonu lub laptopa** przez przeglądarkę (Wi‑Fi/LAN).

---

## Co musisz mieć na RPi

### Sprzęt

- **Raspberry Pi Zero 2 W** (albo inny model z WiFi)
- Karta microSD z systemem
- Zasilanie USB
- **Arduino UNO** podłączone do RPi **przez USB** (kabel danych)
- RPi w tej samej sieci co telefon/laptop (Wi‑Fi lub Ethernet)

### System na RPi

Wystarczy **Raspberry Pi OS Lite** (bez pulpitu) – lżejszy i szybszy. Jeśli wolisz pulpitu, zadziała też **Raspberry Pi OS with desktop**.

---

## 1. Instalacja systemu

1. Zainstaluj **Raspberry Pi OS** na karcie (np. **Raspberry Pi Imager**).
2. W Imagerze włącz **SSH** i ustaw hasło (żeby móc się później zalogować).
3. Włóż kartę do RPi, podłącz zasilanie i sieć (Wi‑Fi ustaw przy pierwszym uruchomieniu lub przez plik `wpa_supplicant.conf` na karcie).

---

## 2. Co zainstalować na RPi (tylko to)

Zaloguj się przez SSH (np. `ssh pi@192.168.1.xxx`) i wykonaj:

```bash
sudo apt update
sudo apt install -y python3 python3-pip
sudo usermod -aG dialout $USER
```

**Wyloguj się i zaloguj ponownie** (albo zrestartuj: `sudo reboot`), żeby grupa `dialout` zadziałała (dostęp do portu USB Arduino).

**Nie instalujesz:** pulpitu, VNC, `python3-tk` – tryb serwer ich nie potrzebuje.

---

## 3. Skopiowanie projektu i zależności

Skopiuj folder `WINDER_V4` na RPi (SCP, pendrive, git itd.). Na RPi:

```bash
cd ~/WINDER_V4
pip3 install -r requirements.txt
```

Zainstalują się: `pyserial`, `flask`.

---

## 4. Uruchomienie serwera

```bash
cd ~/WINDER_V4
python3 winder_server.py
```

W terminalu zobaczysz coś w stylu:

```text
Serwer nawijarki: http://<adres-RPi>:5000
```

Adres RPi sprawdzisz np. przez `hostname -I` (pierwszy to IP w LAN).

---

## 5. Sterowanie z przeglądarki

Na **telefonie lub laptopie** (w tej samej sieci co RPi) otwórz w przeglądarce:

```text
http://192.168.1.XXX:5000
```

( Zamień `192.168.1.XXX` na adres IP Twojego RPi. )

W interfejsie:

- Wybierz **port** (np. `/dev/ttyACM0`) i kliknij **Połącz**.
- **START** – start z zadaną liczbą zwojów (i opcjonalnie sekcjami).
- **STOP** – zatrzymanie.
- **WZNÓW** – wznowienie (w trybie sekcji: następna sekcja).
- **Y=0** – zerowanie pozycji Y.
- Pola **RPM**, **Skok [mm]**, **Szer. [mm]** – ustawienia; zmiany wysyłane po wyjściu z pola (change).
- **Zwoje (całość)** i **Sekcji** – jak w desktopowej wersji.
- **Auto następna sekcja** – po zakończeniu sekcji automatycznie start kolejnej.

Status (stan, zwoje, Y, RPM) odświeża się co ok. 1,5 s.

---

## 6. Autostart serwera po włączeniu RPi (opcja)

Żeby serwer uruchamiał się sam po starcie systemu (bez logowania):

```bash
sudo nano /etc/systemd/system/winder.service
```

Wklej (ścieżkę dostosuj do swojego użytkownika i katalogu):

```ini
[Unit]
Description=Sterownik nawijarki (WWW)
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/WINDER_V4
ExecStart=/usr/bin/python3 winder_server.py
Restart=always
RestartSec=5
Environment=PORT=5000

[Install]
WantedBy=multi-user.target
```

Zapisz (Ctrl+O, Enter, Ctrl+X). Włącz usługę:

```bash
sudo systemctl daemon-reload
sudo systemctl enable winder.service
sudo systemctl start winder.service
```

Sprawdzenie: `sudo systemctl status winder.service`.  
Strona: `http://<IP-RPi>:5000`.

---

## 7. Rozwiązywanie problemów

| Problem | Co zrobić |
|--------|-----------|
| Brak portu w liście | Na RPi: `ls /dev/ttyACM* /dev/ttyUSB*`. Arduino podłączone? Kabel USB (dane)? |
| Permission denied (port) | `sudo usermod -aG dialout $USER`, wyloguj/zaloguj. |
| Brak modułu `flask` / `serial` | `pip3 install -r requirements.txt` w katalogu `WINDER_V4`. |
| Nie mogę wejść na stronę | Sprawdź IP: `hostname -I`. Firewall: `sudo ufw allow 5000` (jeśli używasz ufw). |
| Serwer się wyłącza | Uruchom przez systemd (jak wyżej) – będzie się restartował. |

---

## Podsumowanie – tryb serwer

| Co | Wartość |
|----|--------|
| **System** | Raspberry Pi OS (Lite lub z pulpitem) |
| **Na RPi instalujesz** | `python3`, `python3-pip`, użytkownik w grupie `dialout` |
| **Projekt** | `pip3 install -r requirements.txt` (pyserial, flask) |
| **Uruchomienie** | `python3 winder_server.py` w `WINDER_V4` |
| **Adres** | `http://<IP-RPi>:5000` z przeglądarki (telefon, laptop) |
| **Port Arduino** | Na RPi zwykle `/dev/ttyACM0` lub `/dev/ttyUSB0` |

---

## Jeśli chcesz GUI na RPi (winder4.py)

Jeśli wolisz **program z oknem** na samym RPi (z monitorem lub VNC):

- Zainstaluj pulpitu i: `sudo apt install python3-tk`
- Uruchom: `python3 winder4.py`
- Reszta jak wcześniej (port, dialout itd.) – patrz pierwotna wersja tego README w historii, jeśli potrzebujesz kroków pod GUI.
