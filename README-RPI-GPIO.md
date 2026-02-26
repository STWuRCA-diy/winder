# Nawijarka – **tylko Raspberry Pi (bez Arduino)**

Sterowanie **w całości z RPi** przez GPIO: silniki krokowe, enkoder i krańcówka podłączone do pinów RPi. Arduino nie jest używane.

---

## Co jest potrzebne

- **Raspberry Pi** (Zero 2 W lub inny z 40-pinowym GPIO)
- **Sterowniki silników** (A4988 lub DRV8825) – **bez** płytki Arduino/CNC Shield
- Zasilanie silników (12–24 V)
- **Enkoder** wrzeciona (2 kanały A/B)
- **Krańcówka** Y (przycisk/styk do GND)
- Połączenie RPi z siecią (Wi‑Fi) – sterowanie z przeglądarki

---

## Pinout GPIO (numeracja BCM)

| Funkcja        | Pin BCM | Opis                          |
|----------------|---------|-------------------------------|
| X_STEP         | 17      | Krok silnika osi X (wrzeciono)|
| X_DIR          | 27      | Kierunek X                    |
| Y_STEP         | 22      | Krok silnika osi Y (wózek)    |
| Y_DIR          | 23      | Kierunek Y                    |
| EN             | 24      | Enable driverów (LOW = włączone) |
| ENC_A          | 5       | Enkoder kanał A (zbocze = tick) |
| ENC_B          | 6       | Enkoder kanał B (kierunek)    |
| PIN_Y_MIN      | 26      | Krańcówka Y (LOW = zwarta do GND) |

**Zasilanie:** 3,3 V i GND z RPi dla logiki. Większość sterowników A4988/DRV8825 przyjmuje 3,3 V na STEP/DIR/EN. Zasilacz silników (VMOT, GND) podłącz do modułów driverów, **nie** do RPi.

---

## Jak podłączyć

1. **Sterowniki (A4988/DRV8825):**
   - STEP, DIR, EN każdego drivera do odpowiednich pinów RPi (np. X: 17, 27, 24; Y: 22, 23, 24 – EN wspólne).
   - VMOT i GND drivera do zewnętrznego zasilacza (12–24 V).
   - Silniki do gniazd driverów.

2. **Enkoder:**
   - Kanał A → GPIO 5 (BCM), kanał B → GPIO 6.
   - VCC enkodera → 3,3 V RPi, GND → GND RPi.
   - W kodzie: `ENC_TICKS_PER_REV = 18` – ustaw po kalibracji (ticki na 1 obrót).

3. **Krańcówka Y:**
   - Jeden przewód do GPIO 26, drugi do GND.
   - Wciśnięcie = zwarcie do GND = wykrycie „Y=0”.

---

## Oprogramowanie na RPi

```bash
sudo apt update
sudo apt install -y python3 python3-pip
pip3 install flask
```

**Nie** instalujesz pyserial (brak Arduino).  
Skopiuj projekt na RPi, w katalogu projektu:

```bash
python3 winder_server_rpi.py
```

W przeglądarce (telefon/laptop w tej samej sieci): **http://&lt;IP-RPi&gt;:5000**

Interfejs jest taki sam jak w wersji z Arduino, ale **bez** wyboru portu – od razu „gotowe”.

---

## Zmiana pinów

Jeśli chcesz inne piny, edytuj na początku pliku **`winder_engine_rpi.py`**:

```python
X_STEP = 17
X_DIR = 27
Y_STEP = 22
Y_DIR = 23
EN_PIN = 24
ENC_A = 5
ENC_B = 6
PIN_Y_MIN = 26
```

---

## Uwagi

- **Kierunek enkodera:** jeśli liczba zwojów (real) rośnie w złą stronę, w `_enc_callback` zamień `direction = -1 if b else 1` na `direction = 1 if b else -1`.
- **Wysokie RPM:** przy bardzo wysokich obrotach precyzja timingu w Pythonie może być gorsza niż na Arduino; dla typowych nawijarek (np. do 300 RPM) jest zwykle OK.
- **Autostart:** tak jak w README-RPI możesz dodać usługę systemd, z `ExecStart=/usr/bin/python3 .../winder_server_rpi.py`.
