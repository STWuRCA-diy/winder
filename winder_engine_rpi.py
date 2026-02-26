#!/usr/bin/env python3
"""
Silnik sterownika nawijarki – **tylko Raspberry Pi, bez Arduino**.
Sterowanie silnikami krokowymi i odczyt enkodera/krańcówki przez GPIO.
Użyj: winder_server_rpi.py (serwer WWW) lub zaimportuj tę klasę.
"""
import threading
import time
import math

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO_AVAILABLE = False
    GPIO = None

# --- Piny BCM (RPi 40-pin) – dostosuj do swojego okablowania ---
X_STEP = 17
X_DIR = 27
Y_STEP = 22
Y_DIR = 23
EN_PIN = 24
ENC_A = 5   # kanał A enkodera (zbocze → tick)
ENC_B = 6   # kanał B (kierunek)
PIN_Y_MIN = 26   # krańcówka Y (LOW = zwarta do GND)

ENC_TICKS_PER_REV = 18
STEP_PULSE_US = 2e-6  # 2 µs impuls kroku


class WinderEngineRPi:
    def __init__(self):
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._job = "IDLE"  # IDLE, RUN, PAUSE

        # Kalibracja (jak w firmware)
        self._x_steps_per_rev = 6400
        self._y_steps_per_mm = 800.0
        self._pitch_mm = 0.0
        self._eff_w_mm = 21.85
        self._x_dir_sign = 1
        self._rpm = 200

        # Stan ruchu
        self._y_acc = 0.0
        self._y_dir_sign = 1
        self._y_pos_steps = 0
        self._x_steps_mod = 0
        self._turns_x = 0
        self._goal_turns = -1

        # Encoder
        self._enc_ticks = 0
        self._enc_prev_a = True
        self._enc_lock = threading.Lock()

        # Krańcówka Y (one-shot zero)
        self._y_home_done = False
        self._y_home_armed = True

        # Sekcje (dla serwera)
        self.sections_mode = False
        self.section_plan = []
        self.section_ptr = 0
        self.last_goal = None
        self.auto_next_section = False

        self._y_steps_per_turn = 0.0
        self._y_step_per_xstep = 0.0
        self._x_interval_sec = 0.00005
        self._recalc_derived()
        self._gpio_ready = False
        if GPIO_AVAILABLE:
            self._setup_gpio()

    def _setup_gpio(self):
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        for pin in (X_STEP, X_DIR, Y_STEP, Y_DIR, EN_PIN):
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)
        GPIO.setup(PIN_Y_MIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(ENC_A, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(ENC_B, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self._enc_prev_a = GPIO.input(ENC_A)
        try:
            GPIO.add_event_detect(ENC_A, GPIO.BOTH, callback=self._enc_callback, bouncetime=1)
        except Exception:
            pass
        GPIO.output(EN_PIN, GPIO.HIGH)  # na start silniki wyłączone
        self._gpio_ready = True

    def _enc_callback(self, channel):
        a = GPIO.input(ENC_A)
        with self._enc_lock:
            if a != self._enc_prev_a:
                self._enc_prev_a = a
                if not a:  # zbocze opadające A
                    b = GPIO.input(ENC_B)
                    direction = -1 if b else 1  # dostosuj jeśli kierunek odwrotny
                    self._enc_ticks += direction

    def _recalc_derived(self):
        self._y_steps_per_turn = self._y_steps_per_mm * self._pitch_mm
        if self._x_steps_per_rev > 0:
            self._y_step_per_xstep = self._y_steps_per_turn / self._x_steps_per_rev
        else:
            self._y_step_per_xstep = 0.0
        sps = self._rpm * self._x_steps_per_rev / 60.0
        if sps < 1.0:
            sps = 1.0
        self._x_interval_sec = 1.0 / sps

    def _y_mm(self):
        return self._y_pos_steps / self._y_steps_per_mm

    def _step_pulse(self, pin):
        if not self._gpio_ready or not GPIO_AVAILABLE:
            return
        GPIO.output(pin, GPIO.HIGH)
        time.sleep(STEP_PULSE_US)
        GPIO.output(pin, GPIO.LOW)

    def _endstop_y(self):
        if not self._gpio_ready or not GPIO_AVAILABLE:
            return False
        return GPIO.input(PIN_Y_MIN) == GPIO.LOW

    def set_rpm(self, v):
        with self._lock:
            v = max(1, min(5000, int(v)))
            self._rpm = v
            self._recalc_derived()

    def set_pitch(self, v):
        with self._lock:
            if v <= 0:
                return
            self._pitch_mm = float(v)
            self._recalc_derived()

    def set_bwidth(self, v):
        with self._lock:
            if v <= 0:
                return
            self._eff_w_mm = float(v)

    def set_xrev(self, v):
        with self._lock:
            v = int(v)
            if v < 0:
                self._x_dir_sign = -1
                v = abs(v)
            else:
                self._x_dir_sign = 1
            if v < 1:
                v = 1
            self._x_steps_per_rev = v
            self._recalc_derived()

    def set_ycal(self, v):
        with self._lock:
            if v <= 0:
                return
            self._y_steps_per_mm = float(v)
            self._recalc_derived()

    def goal(self, n):
        with self._lock:
            self._goal_turns = n if n > 0 else -1

    def run(self):
        with self._lock:
            if self._job == "RUN":
                return
            self._turns_x = 0
            self._x_steps_mod = 0
            self._enc_ticks = 0
            self._recalc_derived()
            self._job = "RUN"
        if self._gpio_ready and GPIO_AVAILABLE:
            GPIO.output(EN_PIN, GPIO.LOW)

    def stop(self):
        with self._lock:
            self._job = "PAUSE"
        if self._gpio_ready and GPIO_AVAILABLE:
            time.sleep(0.12)
            GPIO.output(EN_PIN, GPIO.HIGH)

    def resume(self):
        with self._lock:
            if self._job == "RUN":
                return
            self._job = "RUN"
        if self._gpio_ready and GPIO_AVAILABLE:
            GPIO.output(EN_PIN, GPIO.LOW)

    def yzero(self):
        with self._lock:
            self._y_pos_steps = 0

    def get_status(self):
        with self._lock:
            job = self._job
            turns = self._turns_x
            y_pos = self._y_pos_steps
            eff_w = self._eff_w_mm
            pitch = self._pitch_mm
        with self._enc_lock:
            enc = self._enc_ticks
        turns_per_layer = (eff_w / pitch) if pitch > 0 else None
        real_turns = (enc / ENC_TICKS_PER_REV) if ENC_TICKS_PER_REV else None
        endstop = self._endstop_y()
        return {
            "connected": True,
            "state": job,
            "current_turns": turns,
            "current_turns_real": round(real_turns, 3) if real_turns is not None else None,
            "current_y": round(y_pos / self._y_steps_per_mm, 3) if self._y_steps_per_mm else None,
            "current_rpm": self._rpm,
            "eff_w": eff_w,
            "turns_per_layer": round(turns_per_layer, 2) if turns_per_layer is not None else None,
            "endstop": 1 if endstop else 0,
            "sections_mode": self.sections_mode,
            "section_ptr": self.section_ptr,
            "section_plan_len": len(self.section_plan),
            "log": [],
        }

    def _run_loop(self):
        self._recalc_derived()
        next_x_time = time.perf_counter()
        while self._running:
            job = self._job
            # One-shot Y zero z krańcówki
            if self._y_home_armed and not self._y_home_done and self._endstop_y():
                with self._lock:
                    self._y_pos_steps = 0
                    self._y_home_done = True
                    self._y_home_armed = False
            elif not self._endstop_y():
                self._y_home_done = False

            if job != "RUN":
                next_x_time = time.perf_counter()
                time.sleep(0.05)
                continue

            now = time.perf_counter()
            if now < next_x_time:
                time.sleep(min(0.001, next_x_time - now))
                continue

            interval = getattr(self, "_x_interval_sec", 0.00005)
            next_x_time += interval

            # Kierunek X
            if self._gpio_ready and GPIO_AVAILABLE:
                GPIO.output(X_DIR, GPIO.HIGH if self._x_dir_sign > 0 else GPIO.LOW)
                self._step_pulse(X_STEP)

            with self._lock:
                self._x_steps_mod += 1
                if self._x_steps_per_rev and self._x_steps_mod >= self._x_steps_per_rev:
                    self._x_steps_mod -= self._x_steps_per_rev
                    self._turns_x += 1
                    if self._goal_turns > 0 and self._turns_x >= self._goal_turns:
                        self._job = "PAUSE"
                        if GPIO_AVAILABLE:
                            GPIO.output(EN_PIN, GPIO.HIGH)
                        self._on_goal_reached()
                        continue

                self._y_acc += self._y_step_per_xstep

            while self._y_acc >= 1.0:
                self._y_acc -= 1.0
                y_mm = self._y_mm()
                with self._lock:
                    if self._y_dir_sign > 0 and y_mm >= self._eff_w_mm:
                        self._y_dir_sign = -1
                    elif self._y_dir_sign < 0 and y_mm <= 0.0:
                        self._y_dir_sign = 1
                    direction = self._y_dir_sign
                    self._y_pos_steps += direction
                if self._gpio_ready and GPIO_AVAILABLE:
                    GPIO.output(Y_DIR, GPIO.HIGH if direction > 0 else GPIO.LOW)
                    self._step_pulse(Y_STEP)

    def _on_goal_reached(self):
        """Wywołane gdy goal osiągnięty – dla sekcji / auto-next."""
        if self.sections_mode and self.section_ptr < len(self.section_plan):
            self.section_ptr += 1
            if self.section_ptr < len(self.section_plan):
                time.sleep(0.12)
                if GPIO_AVAILABLE:
                    GPIO.output(EN_PIN, GPIO.HIGH)
                self.yzero()
                if self.auto_next_section:
                    time.sleep(0.3)
                    self._start_next_section()
            else:
                pass  # koniec wszystkich sekcji
        else:
            pass

    def _start_next_section(self):
        if self.section_ptr >= len(self.section_plan):
            return
        next_size = self.section_plan[self.section_ptr]
        with self._lock:
            self.last_goal = self._turns_x + next_size
            self._goal_turns = self.last_goal
            self._job = "RUN"
        if GPIO_AVAILABLE:
            GPIO.output(EN_PIN, GPIO.LOW)

    def start_thread(self):
        if self._thread is not None:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def shutdown(self):
        self._running = False
        self._job = "PAUSE"
        if self._gpio_ready and GPIO_AVAILABLE:
            GPIO.output(EN_PIN, GPIO.HIGH)
        if self._thread:
            self._thread.join(timeout=2.0)
        if GPIO_AVAILABLE:
            try:
                GPIO.cleanup()
            except Exception:
                pass


# Singleton dla serwera
_engine = None

def get_engine():
    global _engine
    if _engine is None:
        _engine = WinderEngineRPi()
        _engine.start_thread()
    return _engine
