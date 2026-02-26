import tkinter as tk
from tkinter import filedialog
from tkinter import ttk
import serial
import serial.tools.list_ports
import threading
import time
import re
import platform

# Czcionka monospace: Windows / macOS / Linux (RPi)
if platform.system() == "Windows":
    FONT_MONO = "Consolas"
elif platform.system() == "Darwin":
    FONT_MONO = "Menlo"
else:
    FONT_MONO = "DejaVu Sans Mono"  # standard na Raspberry Pi OS / Linux

# --- Parametry estymacji emalii (dla wyliczania pitch) ---
ENAMEL_REL = 0.08   # +8% średnicy
ENAMEL_MIN = 0.01   # min. +0.01 mm łącznie

def effective_wire_mm(bare_mm: float) -> float:
    """Zwróć efektywną średnicę (drut + emalia)."""
    if bare_mm <= 0:
        return 0.0
    added = max(ENAMEL_MIN, ENAMEL_REL * bare_mm)
    return bare_mm + added


class CoilWinderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Sterownik Nawijarki Cewek")

        # --- stan połączenia/portu ---
        self.serial_port = None
        self.is_connected = False
        self.read_thread = None
        self.log_buffer = []
        self.last_sent = {}

        # --- stan procesu/telemetrii ---
        self.current_state = "IDLE"
        self.current_turns = 0
        self.current_turns_real = None  # z enkodera (firmware 5), gdy dostępne
        self.current_y = None
        self.eff_w = None
        self.turns_per_layer = None
        self.current_rpm = None  # śledzenie rpm z firmware

        # krańcówka Y i widoczność tylko przed startem
        self.endstop_raw = None
        self.y_zero_tol = 0.01
        self.has_started = False

        # tryb sekcji
        self.sections_mode = False
        self.section_plan = []
        self.section_ptr = 0
        self.last_goal_set = None

        # zapobiegam NameError jeśli kod używa reverse_x_var
        self.reverse_x_var = tk.BooleanVar(value=False)

        # === Połączenie ===
        connection_frame = tk.LabelFrame(root, text="Połączenie", padx=10, pady=10)
        connection_frame.pack(padx=10, pady=5, fill="x")

        tk.Label(connection_frame, text="Port:").pack(side=tk.LEFT, padx=(0, 5))
        self.port_variable = tk.StringVar()
        self.port_menu = tk.OptionMenu(connection_frame, self.port_variable, "—")
        self.port_menu.pack(side=tk.LEFT, fill="x", expand=True)
        tk.Button(connection_frame, text="Odśwież", command=self.refresh_ports).pack(side=tk.LEFT, padx=5)
        self.connect_button = tk.Button(connection_frame, text="Połącz", command=self.toggle_connection)
        self.connect_button.pack(side=tk.LEFT, padx=5)

        self.status_var = tk.StringVar(value="Rozłączono")
        tk.Label(connection_frame, textvariable=self.status_var, anchor="e").pack(side=tk.RIGHT, padx=5)

        # wskaźnik krańcówki (tylko przed startem)
        self.endstop_label = tk.Label(connection_frame, text="Krańcówka Y: —",
                                      bd=1, relief="solid", padx=6, pady=2)
        self.endstop_label.pack(side=tk.RIGHT, padx=5)
        self._endstop_default_bg = self.endstop_label.cget("bg")
        self._endstop_default_fg = self.endstop_label.cget("fg")

        # === Zakładki ===
        notebook = ttk.Notebook(root)
        notebook.pack(padx=10, pady=5, fill="x")

        tab_basic = ttk.Frame(notebook)
        tab_adv = ttk.Frame(notebook)
        notebook.add(tab_basic, text="Sterowanie")
        notebook.add(tab_adv, text="Ustawienia zaawansowane")

        # === Sterowanie ===
        controls = tk.LabelFrame(tab_basic, text="Sterowanie", padx=10, pady=10)
        controls.pack(padx=0, pady=0, fill="x")

        # rząd 0: przyciski
        self.btn_run = tk.Button(controls, text="START", command=self.start_with_sections_or_total)
        self.btn_stop = tk.Button(controls, text="STOP/PAUZA", command=lambda: self.send_command("stop"))
        self.btn_resume = tk.Button(controls, text="WZNÓW", command=self.resume_sections_or_plain)
        self.btn_yzero = tk.Button(controls, text="Y = 0", command=lambda: self.send_command("yzero"))

        self.btn_run.grid(row=0, column=0, padx=5, pady=2, sticky="ew")
        self.btn_stop.grid(row=0, column=1, padx=5, pady=2, sticky="ew")
        self.btn_resume.grid(row=0, column=2, padx=5, pady=2, sticky="ew")
        self.btn_yzero.grid(row=0, column=3, padx=5, pady=2, sticky="ew")

        # rząd 0b: przełącznik auto-startu kolejnej sekcji (domyślnie OFF)
        self.auto_next_var = tk.BooleanVar(value=False)
        self.chk_auto_next = tk.Checkbutton(
            controls, text="Auto-start kolejnej sekcji", variable=self.auto_next_var
        )
        self.chk_auto_next.grid(row=0, column=4, padx=10, sticky="w")

        # rząd 1: RPM — domyślnie 0
        tk.Label(controls, text="Obroty (RPM):").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.rpm_entry = tk.Entry(controls, width=12)
        self.rpm_entry.insert(0, "0")
        self.rpm_entry.grid(row=1, column=1, sticky="ew", padx=5)
        self.rpm_entry.bind("<Return>", lambda e: self.set_rpm())
        self.rpm_entry.bind("<FocusOut>", lambda e: self.set_rpm())

        # rząd 2: Średnica drutu [mm] (liczy pitch z automatyczną emalią)
        tk.Label(controls, text="Śr. drutu (goły) [mm]:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        self.wire_entry = tk.Entry(controls, width=12)
        self.wire_entry.insert(0, "0")
        self.wire_entry.grid(row=2, column=1, sticky="ew", padx=5)
        self.wire_entry.bind("<Return>", lambda e: self.recalc_pitch_from_inputs())
        self.wire_entry.bind("<FocusOut>", lambda e: self.recalc_pitch_from_inputs())

        # rząd 3: Docelowe zwoje
        tk.Label(controls, text="Docelowe zwoje:").grid(row=3, column=0, sticky="w", padx=5, pady=5)
        self.target_entry = tk.Entry(controls, width=12)
        self.target_entry.insert(0, "0")
        self.target_entry.grid(row=3, column=1, sticky="ew", padx=5)
        self.target_entry.bind("<Return>", lambda e: self.arm_goal_only())
        self.target_entry.bind("<FocusOut>", lambda e: None)

        # rząd 4: Całość + sekcje + postęp
        tk.Label(controls, text="Ilość zwojów (całość):").grid(row=4, column=0, sticky="w", padx=5, pady=4)
        self.total_turns_entry = tk.Entry(controls, width=12)
        self.total_turns_entry.insert(0, "0")
        self.total_turns_entry.grid(row=4, column=1, sticky="ew", padx=5)
        self.total_turns_entry.bind("<KeyRelease>", lambda e: self._recalc_sections())

        tk.Label(controls, text="Ilość sekcji:").grid(row=4, column=2, sticky="e", padx=5, pady=4)
        self.sections_entry = tk.Entry(controls, width=12)
        self.sections_entry.insert(0, "0")
        self.sections_entry.grid(row=4, column=3, sticky="w", padx=5)
        self.sections_entry.bind("<KeyRelease>", lambda e: self._recalc_sections())

        self.turns_per_section_var = tk.StringVar(value="Zwojów/sekcję: —")
        tk.Label(controls, textvariable=self.turns_per_section_var).grid(row=4, column=4, sticky="w", padx=10)

        self.sections_progress_var = tk.StringVar(value="Sekcje: —")
        tk.Label(controls, textvariable=self.sections_progress_var, fg="#333").grid(row=4, column=5, sticky="w", padx=10)

        # rząd 5: Wymiary karkasu [mm]
        tk.Label(controls, text="Wymiary karkasu [mm]:").grid(row=5, column=0, sticky="w", padx=5, pady=4)

        dim_frame = tk.Frame(controls)
        dim_frame.grid(row=5, column=1, columnspan=5, sticky="w", padx=5)

        tk.Label(dim_frame, text="wys.").grid(row=0, column=0, sticky="e", padx=(0, 4))
        self.bobbin_h_entry = tk.Entry(dim_frame, width=8); self.bobbin_h_entry.insert(0, "0")
        self.bobbin_h_entry.grid(row=0, column=1, sticky="w", padx=(0, 12))

        tk.Label(dim_frame, text="szer.").grid(row=0, column=2, sticky="e", padx=(0, 4))
        self.bobbin_w_entry = tk.Entry(dim_frame, width=8); self.bobbin_w_entry.insert(0, "0")
        self.bobbin_w_entry.grid(row=0, column=3, sticky="w", padx=(0, 12))
        # --- NOWE: reaguj na zmianę szerokości ---
        self.bobbin_w_entry.bind("<KeyRelease>", lambda e: self._update_eff_w_from_ui())
        self.bobbin_w_entry.bind("<Return>",    lambda e: self._commit_bobbin_width())
        self.bobbin_w_entry.bind("<FocusOut>",  lambda e: self._commit_bobbin_width())

        tk.Label(dim_frame, text="dł.").grid(row=0, column=4, sticky="e", padx=(0, 4))
        self.bobbin_l_entry = tk.Entry(dim_frame, width=8); self.bobbin_l_entry.insert(0, "0")
        self.bobbin_l_entry.grid(row=0, column=5, sticky="w", padx=(0, 12))

        # rząd 6: Zapis logu
        self.btn_save = tk.Button(controls, text="Zapisz log", command=self.save_log)
        self.btn_save.grid(row=6, column=5, padx=5, pady=5, sticky="e")

        for c in range(6):
            controls.grid_columnconfigure(c, weight=1)

        # === Ustawienia zaawansowane ===
        adv = tk.LabelFrame(tab_adv, text="Ustawienia zaawansowane", padx=10, pady=10)
        adv.pack(padx=0, pady=0, fill="x")

        tk.Label(adv, text="Skok [mm/zwój] (liczony z drutu+emalii):").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.pitch_entry = tk.Entry(adv, width=12); self.pitch_entry.insert(0, "0")
        self.pitch_entry.grid(row=0, column=1, sticky="ew", padx=5)
        self.pitch_entry.bind("<Return>", lambda e: self.set_pitch())
        self.pitch_entry.bind("<FocusOut>", lambda e: self.set_pitch())

        tk.Label(adv, text="Kroki X / obrót:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.xrev_entry = tk.Entry(adv, width=12); self.xrev_entry.insert(0, "6400")
        self.xrev_entry.grid(row=1, column=1, sticky="ew", padx=5)
        self.xrev_entry.bind("<Return>", lambda e: self.set_xrev())
        self.xrev_entry.bind("<FocusOut>", lambda e: self.set_xrev())
        self.chk_reverse_x = tk.Checkbutton(
            adv, text="Odwróć kierunek X", variable=self.reverse_x_var,
            command=self.set_xrev
        )
        self.chk_reverse_x.grid(row=1, column=2, padx=10, sticky="w")

        tk.Label(adv, text="Kroki Y / mm:").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        self.ycal_entry = tk.Entry(adv, width=12); self.ycal_entry.insert(0, "800")
        self.ycal_entry.grid(row=2, column=1, sticky="ew", padx=5)
        self.ycal_entry.bind("<Return>", lambda e: self.set_ycal())
        self.ycal_entry.bind("<FocusOut>", lambda e: self.set_ycal())

        tk.Label(adv, text="Upakowanie (0–1):").grid(row=3, column=0, sticky="w", padx=5, pady=5)
        self.packing_entry = tk.Entry(adv, width=12); self.packing_entry.insert(0, "1.0")
        self.packing_entry.grid(row=3, column=1, sticky="ew", padx=5)
        self.packing_entry.bind("<Return>", lambda e: self.recalc_pitch_from_inputs())
        self.packing_entry.bind("<FocusOut>", lambda e: self.recalc_pitch_from_inputs())

        for c in range(3):
            adv.grid_columnconfigure(c, weight=1)

        # Pasek informacji (X_turns_real z enkodera gdy firmware 5)
        self.info_var = tk.StringVar(value="eff_w: —  |  zwojów/warstwę: —  |  X_zwoje: 0  |  stan: IDLE")
        tk.Label(root, textvariable=self.info_var, anchor="w").pack(padx=10, pady=(0, 5), fill="x")

        # === Konsola ===
        output = tk.LabelFrame(root, text="Wyjście Arduino", padx=10, pady=10)
        output.pack(padx=10, pady=5, fill="both", expand=True)
        self.log_list = tk.Listbox(output, height=18, bg="#ffffff", fg="#000000", font=(FONT_MONO, 12))
        self.log_scroll = tk.Scrollbar(output, orient="vertical", command=self.log_list.yview)
        self.log_list.config(yscrollcommand=self.log_scroll.set)
        self.log_list.pack(side="left", fill="both", expand=True)
        self.log_scroll.pack(side="right", fill="y")
        self.log_message("Konsola gotowa (sekcje, enkoder X_turns_real, pitch z emalią, Odwróć X).")

        # przyciski sterujące, które wyłączamy/przełączamy z połączeniem
        self.command_widgets = [self.btn_run, self.btn_stop, self.btn_resume, self.btn_yzero, self.btn_save]
        self.update_ui_state()
        self._recalc_sections()
        self._update_endstop_indicator()
        root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # ---------- Utility ----------
    def refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        menu = self.port_menu["menu"]
        menu.delete(0, "end")
        if ports:
            for port in ports:
                menu.add_command(label=port, command=lambda v=port: self.port_variable.set(v))
            self.port_variable.set(ports[0])
        else:
            self.port_variable.set("Brak portów")

    def update_ui_state(self):
        state = tk.NORMAL if self.is_connected else tk.DISABLED
        for w in self.command_widgets:
            w.config(state=state)

    def save_log(self):
        if not self.log_buffer:
            self.log_message("Log jest pusty — brak danych do zapisania.")
            return
        path = filedialog.asksaveasfilename(
            title="Zapisz log",
            defaultextension=".txt",
            filetypes=[("Pliki tekstowe", "*.txt"), ("Wszystkie pliki", "*.*")]
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    for line in self.log_buffer:
                        f.write(line + "\n")
                self.log_message(f"Zapisano log do: {path}")
            except Exception as e:
                self.log_message(f"Błąd zapisu logu: {e}")

    def _listbox_add(self, line: str):
        self.log_list.insert(tk.END, line)
        self.log_list.yview_moveto(1.0)

    def log_message(self, message: str):
        try:
            print(message)
        except Exception:
            pass
        self.log_buffer.append(message)
        self.root.after(0, lambda m=message: self._listbox_add(m))

    # ---------- Pitch = (drut+emalia) × upakowanie ----------
    def recalc_pitch_from_inputs(self):
        """pitch = (średnica_gołego + est. emalia) × upakowanie."""
        try:
            bare = float(self.wire_entry.get().strip())
            k = float(self.packing_entry.get().strip())
            if not (0 < k <= 1.0):
                raise ValueError
        except Exception:
            self.log_message("Błąd: Śr. drutu / Upakowanie (Zaaw.) muszą być liczbami; upak. w (0,1].")
            return

        eff_wire = effective_wire_mm(bare)
        if eff_wire <= 0:
            self.log_message("Uwaga: średnica drutu = 0 → pitch nie został wysłany.")
            return

        new_pitch = eff_wire * k
        self.pitch_entry.delete(0, tk.END)
        self.pitch_entry.insert(0, f"{new_pitch:.5f}")
        self.set_pitch()

    # ---------- Pasek info + krańcówka ----------
    def _update_info_label(self):
        eff = f"{self.eff_w:.3f} mm" if self.eff_w is not None else "—"
        tpl = f"{self.turns_per_layer:.2f}" if self.turns_per_layer is not None else "—"
        x_txt = str(self.current_turns)
        if self.current_turns_real is not None:
            x_txt += f" (enc: {self.current_turns_real:.2f})"
        self.info_var.set(f"eff_w: {eff}  |  zwojów/warstwę: {tpl}  |  X_zwoje: {x_txt}  |  stan: {self.current_state}")

    def _update_endstop_indicator(self):
        if not self.has_started and self.current_state != "RUN":
            engaged = None
            if self.endstop_raw is not None:
                engaged = (self.endstop_raw == 1)
            elif self.current_y is not None:
                engaged = abs(self.current_y) <= self.y_zero_tol
            if engaged:
                self.endstop_label.config(text="Krańcówka Y: ZWARTA (Y=0)", bg="#c6f6d5", fg="#065f46")
            else:
                self.endstop_label.config(text="Krańcówka Y: otwarta", bg=self._endstop_default_bg, fg=self._endstop_default_fg)
        else:
            self.endstop_label.config(text="Krańcówka Y: —", bg=self._endstop_default_bg, fg=self._endstop_default_fg)

    # ---------- Sekcje ----------
    def _build_section_plan(self, total: int, sections: int):
        if sections <= 0:
            return []
        per = total // sections
        rem = total % sections
        return [per + (1 if i < rem else 0) for i in range(sections)]

    def _recalc_sections(self):
        try:
            total = int(self.total_turns_entry.get().strip())
            sections = int(self.sections_entry.get().strip())
            if total < 0 or sections < 0:
                raise ValueError
        except Exception:
            self.turns_per_section_var.set("Zwojów/sekcję: —")
            self.sections_progress_var.set("Sekcje: —")
            return

        if sections > 0:
            per = total // sections
            rem = total % sections
            txt = f"Zwojów/sekcję: {per}" if rem == 0 else f"Zwojów/sekcję: {per} (+{rem} rozdz.)"
            self.turns_per_section_var.set(txt)
            self.sections_progress_var.set(f"Sekcje: 0/{sections} (pozostało {sections})")
        else:
            self.turns_per_section_var.set("Zwojów/sekcję: — (sekcje=0)")
            self.sections_progress_var.set("Sekcje: —")

    def _update_sections_progress_ui(self):
        if self.sections_mode and len(self.section_plan) > 0:
            done = min(self.section_ptr, len(self.section_plan))
            total = len(self.section_plan)
            left = max(total - done, 0)
            if left == 0:
                self.sections_progress_var.set(f"Sekcje: {done}/{total} (zakończono)")
            else:
                self.sections_progress_var.set(f"Sekcje: {done}/{total} (pozostało {left})")
        else:
            try:
                sections = int(self.sections_entry.get().strip())
                if sections > 0:
                    self.sections_progress_var.set(f"Sekcje: 0/{sections} (pozostało {sections})")
                else:
                    self.sections_progress_var.set("Sekcje: —")
            except Exception:
                self.sections_progress_var.set("Sekcje: —")

    # ---------- Serial ----------
    def toggle_connection(self):
        if not self.is_connected:
            port = self.port_variable.get()
            if port in ("Brak portów", "—", "", None):
                self.log_message("Błąd: wybierz poprawny port.")
                return
            try:
                self.serial_port = serial.Serial(port, 115200, timeout=1)
                time.sleep(2)
                self.is_connected = True
                self.connect_button.config(text="Rozłącz")
                self.status_var.set(f"Połączono: {port}")
                self.log_message(f"Połączono z {port}")
                self._send_raw("motoff")
                self.has_started = False
                self.endstop_raw = None
                self.current_y = None
                self.current_rpm = None
                self.current_turns = 0
                self.current_turns_real = None
                self.last_sent = {}
                self._update_endstop_indicator()
                self.update_ui_state()
                self.read_thread = threading.Thread(target=self.read_from_serial, daemon=True)
                self.read_thread.start()
            except serial.SerialException as e:
                self.log_message(f"Błąd połączenia: {e}")
        else:
            self.disconnect()

    def disconnect(self):
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
            except Exception:
                pass
        self.is_connected = False
        self.connect_button.config(text="Połącz")
        self.status_var.set("Rozłączono")
        self.log_message("Rozłączono.")
        self.update_ui_state()
        self.has_started = False
        self.endstop_raw = None
        self.current_y = None
        self.current_rpm = None
        self.current_turns = 0
        self.current_turns_real = None
        self._update_endstop_indicator()

    def read_from_serial(self):
        while self.is_connected and self.serial_port and self.serial_port.is_open:
            try:
                line = self.serial_port.readline().decode("utf-8", errors="ignore").rstrip("\r\n")
                if line:
                    self.log_message(line)
                    self.root.after(0, lambda l=line: self._handle_line(l))
            except (serial.SerialException, OSError) as e:
                errno = getattr(e, "errno", None)
                if errno == 9:
                    self.root.after(0, self.disconnect); break
                self.log_message(f"Błąd portu szeregowego: {e}")
                self.root.after(0, self.disconnect); break

    def _handle_line(self, line: str):
        m_state = re.search(r"\[state=(\w+)", line)
        if m_state:
            self.current_state = m_state.group(1)

        m_turns = re.search(r"X_turns=(\d+)", line) or re.search(r"\bturns=(\d+)\b", line)
        if m_turns:
            try: self.current_turns = int(m_turns.group(1))
            except ValueError: pass

        m_turns_real = re.search(r"X_turns_real=([-\d\.]+)", line)
        if m_turns_real:
            try: self.current_turns_real = float(m_turns_real.group(1))
            except ValueError: self.current_turns_real = None

        m_rpm = re.search(r"(?i)\brpm=(\d+)\b", line)
        if m_rpm:
            try: self.current_rpm = int(m_rpm.group(1))
            except ValueError:
                self.current_rpm = None

        m_y = re.search(r"\bY=([-\d\.]+)", line)
        if m_y:
            try: self.current_y = float(m_y.group(1))
            except ValueError: pass

        m_home = re.search(r"\b(?:Y_HOME|ENDSTOP_Y)=(\d)\b", line)
        if m_home:
            try: self.endstop_raw = int(m_home.group(1))
            except ValueError: self.endstop_raw = None

        m_eff = re.search(r"eff_w=([\d\.]+)\s*mm", line)
        if m_eff:
            try: self.eff_w = float(m_eff.group(1))
            except ValueError: pass

        try:
            pitch = float(self.pitch_entry.get().strip())
            if self.eff_w and pitch > 0:
                self.turns_per_layer = self.eff_w / pitch
        except Exception:
            pass

        # --- Reakcja na osiągnięcie celu ---
        if "[goal] reached" in line:
            if self.sections_mode and self.section_ptr < len(self.section_plan):
                self.section_ptr += 1
                self._update_sections_progress_ui()

                if self.section_ptr < len(self.section_plan):
                    self._send_raw("motoff")
                    self._send_raw("yzero")
                    if self.auto_next_var.get():
                        self.root.after(300, self.resume_sections_or_plain)
                    else:
                        self.log_message("[sekcje] Koniec sekcji. Y=0. Wciśnij WZNÓW, aby zacząć następną sekcję.")
                else:
                    self.root.after(120, lambda: self._send_raw("motoff"))
            else:
                self.root.after(120, lambda: self._send_raw("motoff"))

        self._update_info_label()
        self._update_endstop_indicator()

    # ---------- Komendy ----------
    def _send_raw(self, cmd: str):
        if not (self.is_connected and self.serial_port and self.serial_port.is_open):
            return
        try:
            self.serial_port.write((cmd + "\n").encode("utf-8"))
        except serial.SerialException as e:
            self.log_message(f"Błąd wysyłania: {e}")
            self.disconnect()

    def send_command(self, command: str):
        self.log_message(f">>> {command}")
        low = command.strip().lower()

        if low in ("run", "resume"):
            self._send_raw("moton")
            self.has_started = True
            self.last_sent = {}
            self._update_endstop_indicator()
        elif low == "stop":
            self.root.after(120, lambda: self._send_raw("motoff"))
            self.last_sent = {}

        if command.startswith("rpm "):
            try:
                desired = int(command.split()[1])
            except Exception:
                desired = None
            if desired is not None and self.current_rpm is not None and desired == self.current_rpm:
                self.log_message(f"(bez zmian firmware) rpm {desired}")
                return
        elif command.startswith(("pitch ", "xrev ", "ycal ", "bwidth ")):
            key = command.split()[0]
            if self.last_sent.get(key) == command:
                self.log_message(f"(bez zmian) {command}")
                return
            self.last_sent[key] = command

        if not (self.is_connected and self.serial_port and self.serial_port.is_open):
            self.log_message("Uwaga: brak połączenia."); return
        try:
            self.serial_port.write((command + "\n").encode("utf-8"))
        except serial.SerialException as e:
            self.log_message(f"Błąd wysyłania: {e}")
            self.disconnect()

    def set_rpm(self):
        rpm = self.rpm_entry.get().strip()
        if rpm.isdigit():
            self.send_command(f"rpm {rpm}")
        else:
            self.log_message("Błąd: RPM musi być liczbą całkowitą.")

    def set_pitch(self):
        v = self.pitch_entry.get().strip()
        try: float(v)
        except ValueError:
            self.log_message("Błąd: skok (pitch) musi być liczbą."); return
        self.send_command(f"pitch {v}")

    def set_xrev(self):
        v = self.xrev_entry.get().strip()
        try:
            val = abs(int(v))
        except ValueError:
            self.log_message("Błąd: XREV musi być liczbą całkowitą."); return
        # Jedna komenda: znak ujemny = odwrócony kierunek X (wymaga obsługi w firmware)
        if self.reverse_x_var.get():
            val = -val
        self.send_command(f"xrev {val}")

    def set_ycal(self):
        v = self.ycal_entry.get().strip()
        try: float(v)
        except ValueError:
            self.log_message("Błąd: YCAL musi być liczbą."); return
        self.send_command(f"ycal {v}")

    # === NOWE: obsługa szerokości karkasu → eff_w + bwidth ===
    def _update_eff_w_from_ui(self):
        """Aktualizuj eff_w na żywo podczas wpisywania szerokości (mm)."""
        txt = self.bobbin_w_entry.get().strip()
        try:
            val = float(txt)
            if val < 0:
                raise ValueError
            self.eff_w = val
        except Exception:
            self.eff_w = None
        # przelicz zwoje/warstwę jeśli pitch > 0
        try:
            pitch = float(self.pitch_entry.get().strip())
            if self.eff_w and pitch > 0:
                self.turns_per_layer = self.eff_w / pitch
            else:
                self.turns_per_layer = None
        except Exception:
            self.turns_per_layer = None
        self._update_info_label()

    def _commit_bobbin_width(self):
        """Zatwierdzenie szerokości: wyślij do firmware 'bwidth <mm>'."""
        txt = self.bobbin_w_entry.get().strip()
        try:
            float(txt)
        except ValueError:
            self.log_message("Błąd: szerokość karkasu musi być liczbą (mm).")
            return
        self.send_command(f"bwidth {txt}")
        # upewnij się, że info jest świeże
        self._update_eff_w_from_ui()

    # ---------- Start / Resume / Goal ----------
    def start_with_sections_or_total(self):
        self.sections_mode = False
        self.section_plan = []
        self.section_ptr = 0
        self.last_goal_set = None

        try:
            total = int(self.total_turns_entry.get().strip())
            if total <= 0:
                raise ValueError
        except Exception:
            self.log_message("Błąd: 'Ilość zwojów (całość)' musi być > 0.")
            return

        try:
            sections = int(self.sections_entry.get().strip())
        except Exception:
            sections = 0

        if sections > 0:
            plan = self._build_section_plan(total, sections)
            if not plan or max(plan) <= 0:
                self.log_message("Błąd: nieprawidłowy plan sekcji.")
                return
            self.sections_mode = True
            self.section_plan = plan
            self.section_ptr = 0
            self._update_sections_progress_ui()

            first_goal = plan[0]
            self.last_goal_set = first_goal
            self.log_message(f"[sekcje] Plan: {plan} → cel pierwszej sekcji = {first_goal}.")
            self._send_raw(f"goal {first_goal}")
            self.send_command("run")
        else:
            self.last_goal_set = total
            self.sections_progress_var.set("Sekcje: —")
            self.log_message(f"[całość] Start: auto-stop po {total} zwojach.")
            self._send_raw(f"goal {total}")
            self.send_command("run")

    def resume_sections_or_plain(self):
        if self.sections_mode:
            if self.section_ptr < len(self.section_plan):
                next_size = self.section_plan[self.section_ptr]
                abs_goal = self.current_turns + next_size
                self.last_goal_set = abs_goal
                self.log_message(f"[sekcje] Następna sekcja: +{next_size} → cel {abs_goal}.")
                self._send_raw(f"goal {abs_goal}")
                self._update_sections_progress_ui()
                self.send_command("resume")
                return
            else:
                self.log_message("[sekcje] Wszystkie sekcje zakończone – nic nie wznawiam.")
                self._update_sections_progress_ui()
                return

        self.send_command("resume")

    def arm_goal_only(self):
        t = self.target_entry.get().strip()
        if not t.isdigit():
            self.log_message("Błąd: docelowe zwoje muszą być liczbą całkowitą.")
            return
        self.last_goal_set = int(t)
        self._send_raw(f"goal {t}")
        self.log_message(f"[auto-stop] Uzbrojono cel {t} (bez startu)")

    # ---------- Zamknięcie ----------
    def on_closing(self):
        self.disconnect()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 2.0)
    except tk.TclError:
        pass
    app = CoilWinderGUI(root)
    root.mainloop()