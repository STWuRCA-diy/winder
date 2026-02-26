# Sterownik nawijarki – wersja serwer WWW (bez RPi)

Serwer możesz uruchomić na **dowolnym komputerze**: Windows, Mac lub Linux (np. laptop, stary PC). **Raspberry Pi nie jest potrzebne.**

- Arduino łączysz **USB do tego komputera**.
- Sterowanie z **przeglądarki**: na tym samym komputerze (`localhost`) albo z telefonu/tabletu w sieci.

---

## Wymagania

- Python 3 (z pip)
- Arduino UNO podłączone przez USB
- Na Windows: zwykle port **COM3**, **COM4** itd. (pojazą w liście w interfejsie)

---

## Instalacja (Windows / Mac / Linux)

1. Zainstaluj Pythona 3, jeśli go nie ma: [python.org](https://www.python.org/downloads/).

2. W katalogu projektu:
   ```bash
   pip install -r requirements.txt
   ```
   Zainstalują się: `pyserial`, `flask`.

3. Uruchom serwer:
   ```bash
   python winder_server.py
   ```
   (Na Macu/Linuxie: `python3 winder_server.py`.)

4. W przeglądarce otwórz:
   - **Na tym samym komputerze:**  
     **http://127.0.0.1:5000** lub **http://localhost:5000**
   - **Z telefonu/tabletu (ta sama sieć Wi‑Fi):**  
     **http://&lt;IP-komputera&gt;:5000**  
     (IP sprawdzisz: Windows → `ipconfig`, Mac/Linux → `ifconfig` lub `ip addr`.)

5. W stronie wybierz port Arduino (np. **COM4** na Windows, **/dev/ttyACM0** na Linux/Mac), kliknij **Połącz** i steruj jak zwykle.

---

## Podsumowanie

| Gdzie działa serwer | Arduino podłączone do | Sterowanie z przeglądarki |
|---------------------|------------------------|----------------------------|
| Laptop / PC (Windows, Mac, Linux) | USB tego samego komputera | localhost lub IP w sieci |
| Raspberry Pi | USB RPi | IP RPi w sieci |

**Bez RPi:** wystarczy komputer z Pythonem i USB – uruchamiasz `winder_server.py` i otwierasz w przeglądarce `http://localhost:5000`.
