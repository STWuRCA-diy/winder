#!/usr/bin/env python3
"""
Serwer WWW sterownika nawijarki – do uruchomienia na RPi (bez pulpitu).
Arduino podłączone przez USB. Sterowanie z przeglądarki (telefon, laptop).
"""
import re
import threading
import time
from flask import Flask, request, jsonify, send_from_directory
import serial
import serial.tools.list_ports

app = Flask(__name__, static_folder="static", static_url_path="")

# --- Wspólny stan (serial + dane z Arduino) ---
class WinderState:
    def __init__(self):
        self.lock = threading.Lock()
        self.serial_port = None
        self.connected = False
        self.read_thread = None
        self.state = "IDLE"
        self.current_turns = 0
        self.current_turns_real = None
        self.current_y = None
        self.current_rpm = None
        self.eff_w = None
        self.turns_per_layer = None
        self.endstop = None
        self.log_lines = []
        self.max_log = 200
        # Sekcje
        self.sections_mode = False
        self.section_plan = []
        self.section_ptr = 0
        self.last_goal = None
        self.auto_next_section = False

winder = WinderState()


def _send_raw(cmd: str):
    if not (winder.connected and winder.serial_port and winder.serial_port.is_open):
        return
    try:
        winder.serial_port.write((cmd + "\n").encode("utf-8"))
    except serial.SerialException:
        winder.connected = False


def _handle_line(line: str):
    with winder.lock:
        m = re.search(r"\[state=(\w+)", line)
        if m:
            winder.state = m.group(1)
        m = re.search(r"X_turns=(\d+)", line) or re.search(r"\bturns=(\d+)\b", line)
        if m:
            try:
                winder.current_turns = int(m.group(1))
            except ValueError:
                pass
        m = re.search(r"X_turns_real=([-\d\.]+)", line)
        if m:
            try:
                winder.current_turns_real = float(m.group(1))
            except ValueError:
                winder.current_turns_real = None
        m = re.search(r"(?i)\brpm=(\d+)\b", line)
        if m:
            try:
                winder.current_rpm = int(m.group(1))
            except ValueError:
                winder.current_rpm = None
        m = re.search(r"\bY=([-\d\.]+)", line)
        if m:
            try:
                winder.current_y = float(m.group(1))
            except ValueError:
                pass
        m = re.search(r"\b(?:Y_HOME|ENDSTOP_Y)=(\d)\b", line)
        if m:
            try:
                winder.endstop = int(m.group(1))
            except ValueError:
                winder.endstop = None
        m = re.search(r"eff_w=([\d\.]+)\s*mm", line)
        if m:
            try:
                winder.eff_w = float(m.group(1))
            except ValueError:
                pass

        winder.log_lines.append(line)
        if len(winder.log_lines) > winder.max_log:
            winder.log_lines.pop(0)

    # Reakcja na [goal] reached (poza lock żeby nie blokować)
    if "[goal] reached" in line:
        with winder.lock:
            if winder.sections_mode and winder.section_ptr < len(winder.section_plan):
                winder.section_ptr += 1
                if winder.section_ptr < len(winder.section_plan):
                    _send_raw("motoff")
                    _send_raw("yzero")
                    if winder.auto_next_section:
                        time.sleep(0.3)
                        _run_next_section()
                else:
                    time.sleep(0.12)
                    _send_raw("motoff")
            else:
                time.sleep(0.12)
                _send_raw("motoff")


def _run_next_section():
    with winder.lock:
        if winder.section_ptr >= len(winder.section_plan):
            return
        next_size = winder.section_plan[winder.section_ptr]
        winder.last_goal = winder.current_turns + next_size
    _send_raw(f"goal {winder.last_goal}")
    _send_raw("moton")


def read_serial_thread():
    while winder.connected and winder.serial_port and winder.serial_port.is_open:
        try:
            line = winder.serial_port.readline().decode("utf-8", errors="ignore").rstrip("\r\n")
            if line:
                _handle_line(line)
        except (serial.SerialException, OSError):
            with winder.lock:
                winder.connected = False
            break


# --- API ---

@app.route("/")
def index():
    try:
        return send_from_directory("static", "index.html")
    except Exception:
        return _fallback_html()


def _fallback_html():
    return """
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Nawijarka</title>
<style>
body{font-family:sans-serif;max-width:600px;margin:1rem auto;padding:1rem;background:#1a1a2e;color:#eee;}
h1{color:#0f0;}
button{margin:4px;padding:8px 12px;border-radius:6px;cursor:pointer;}
.run{background:#0a0;color:#fff;}.stop{background:#c00;color:#fff;}
.sec{background:#333;color:#fff;} input{width:80px;padding:4px;}
#status{margin-top:1rem;padding:0.5rem;background:#252540;border-radius:6px;min-height:60px;}
#log{height:120px;overflow-y:auto;font-size:12px;font-family:monospace;}
</style></head>
<body>
<h1>Sterownik nawijarki (RPi)</h1>
<p>Port: <select id="port"></select> <button id="btnConn">Połącz</button> <span id="connStatus">—</span></p>
<p>
  <button class="run" id="btnRun">START</button>
  <button class="stop" id="btnStop">STOP</button>
  <button class="sec" id="btnResume">WZNÓW</button>
  <button class="sec" id="btnYzero">Y=0</button>
</p>
<p>RPM: <input type="number" id="rpm" value="200" min="1"> 
   Skok [mm]: <input type="number" id="pitch" value="0.2" step="0.01"> 
   Szer. [mm]: <input type="number" id="bwidth" value="22" step="0.1"></p>
<p>Zwoje (całość): <input type="number" id="total" value="100"> 
   Sekcji: <input type="number" id="sections" value="0"> 
   <label><input type="checkbox" id="autoNext"> Auto następna sekcja</label></p>
<div id="status">Ładowanie…</div>
<pre id="log"></pre>
<script>
const api = path => fetch(path).then(r=>r.json()).catch(()=>null);
const post = (path, body) => fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)}).then(r=>r.json()).catch(()=>null);
function refreshPorts(){ api('/api/ports').then(d=>{ const s=document.getElementById('port'); s.innerHTML=(d.ports||[]).map(p=>'<option>'+p+'</option>').join(''); }); }
function refreshStatus(){ api('/api/status').then(d=>{ if(!d) return; document.getElementById('status').innerHTML='Stan: '+d.state+' | Zwoje: '+d.current_turns+(d.current_turns_real!=null ? ' (enc: '+d.current_turns_real.toFixed(2)+')' : '')+' | Y: '+(d.current_y!=null ? d.current_y.toFixed(2) : '—')+' mm | RPM: '+(d.current_rpm||'—'); document.getElementById('log').textContent=(d.log||[]).slice(-30).join('\\n'); document.getElementById('connStatus').textContent=d.connected?'Połączono':'Rozłączono'; document.getElementById('btnConn').textContent=d.connected?'Rozłącz':'Połącz'; }); }
document.getElementById('btnConn').onclick = ()=>{ const port=document.getElementById('port').value; const btn=document.getElementById('btnConn'); if(btn.textContent==='Rozłącz'){ post('/api/disconnect',{}).then(()=>{ refreshStatus(); btn.textContent='Połącz'; }); return; } if(!port) return; post('/api/connect', {port}).then(d=>{ refreshStatus(); if(d && d.ok) btn.textContent='Rozłącz'; }); };
document.getElementById('btnRun').onclick = ()=>{ post('/api/start', { total: +document.getElementById('total').value, sections: +document.getElementById('sections').value, auto_next: document.getElementById('autoNext').checked }); };
document.getElementById('btnStop').onclick = ()=>{ post('/api/command', {cmd:'stop'}); };
document.getElementById('btnResume').onclick = ()=>{ post('/api/command', {cmd:'resume'}); };
document.getElementById('btnYzero').onclick = ()=>{ post('/api/command', {cmd:'yzero'}); };
document.getElementById('rpm').onchange = ()=>{ post('/api/rpm', {rpm: document.getElementById('rpm').value}); };
document.getElementById('pitch').onchange = ()=>{ post('/api/pitch', {pitch: document.getElementById('pitch').value}); };
document.getElementById('bwidth').onchange = ()=>{ post('/api/bwidth', {bwidth: document.getElementById('bwidth').value}); };
refreshPorts(); setInterval(refreshStatus, 1500); refreshStatus();
</script>
</body></html>
"""


@app.route("/api/ports")
def api_ports():
    ports = [p.device for p in serial.tools.list_ports.comports()]
    return jsonify(ports=ports)


@app.route("/api/status")
def api_status():
    with winder.lock:
        return jsonify(
            connected=winder.connected,
            state=winder.state,
            current_turns=winder.current_turns,
            current_turns_real=winder.current_turns_real,
            current_y=winder.current_y,
            current_rpm=winder.current_rpm,
            eff_w=winder.eff_w,
            turns_per_layer=winder.turns_per_layer,
            endstop=winder.endstop,
            sections_mode=winder.sections_mode,
            section_ptr=winder.section_ptr,
            section_plan_len=len(winder.section_plan),
            log=winder.log_lines[-50:],
        )


@app.route("/api/connect", methods=["POST"])
def api_connect():
    port = (request.get_json() or {}).get("port") or request.form.get("port")
    if not port:
        return jsonify(ok=False, error="Brak portu"), 400
    if winder.connected:
        return jsonify(ok=False, error="Już połączono"), 400
    try:
        winder.serial_port = serial.Serial(port, 115200, timeout=1)
        time.sleep(2)
        with winder.lock:
            winder.connected = True
            winder.state = "IDLE"
            winder.current_turns = 0
            winder.current_turns_real = None
            winder.current_y = None
            winder.current_rpm = None
            winder.log_lines.clear()
        _send_raw("motoff")
        winder.read_thread = threading.Thread(target=read_serial_thread, daemon=True)
        winder.read_thread.start()
        return jsonify(ok=True)
    except serial.SerialException as e:
        return jsonify(ok=False, error=str(e)), 500


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    with winder.lock:
        if winder.serial_port and winder.serial_port.is_open:
            try:
                winder.serial_port.close()
            except Exception:
                pass
        winder.serial_port = None
        winder.connected = False
        winder.state = "IDLE"
        winder.current_turns = 0
        winder.current_turns_real = None
        winder.current_y = None
        winder.current_rpm = None
    return jsonify(ok=True)


@app.route("/api/command", methods=["POST"])
def api_command():
    data = request.get_json() or {}
    cmd = (data.get("cmd") or "").strip().lower()
    if not cmd:
        return jsonify(ok=False, error="Brak komendy"), 400
    if not winder.connected:
        return jsonify(ok=False, error="Brak połączenia"), 400
    if cmd == "run":
        _send_raw("moton")
        with winder.lock:
            winder.sections_mode = False
    elif cmd == "resume":
        with winder.lock:
            if winder.sections_mode and winder.section_ptr < len(winder.section_plan):
                _run_next_section()
                return jsonify(ok=True)
        _send_raw("moton")
    elif cmd == "stop":
        def do():
            time.sleep(0.12)
            _send_raw("motoff")
        threading.Thread(target=do, daemon=True).start()
    elif cmd == "yzero":
        _send_raw("yzero")
    else:
        _send_raw(cmd)
    return jsonify(ok=True)


@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.get_json() or {}
    total = int(data.get("total") or 0)
    sections = int(data.get("sections") or 0)
    winder.auto_next_section = bool(data.get("auto_next"))
    if total <= 0:
        return jsonify(ok=False, error="Ilość zwojów musi być > 0"), 400
    if not winder.connected:
        return jsonify(ok=False, error="Brak połączenia"), 400

    with winder.lock:
        winder.sections_mode = False
        winder.section_plan = []
        winder.section_ptr = 0
        winder.last_goal = None

    if sections > 0:
        per = total // sections
        rem = total % sections
        plan = [per + (1 if i < rem else 0) for i in range(sections)]
        with winder.lock:
            winder.sections_mode = True
            winder.section_plan = plan
            winder.section_ptr = 0
            winder.last_goal = plan[0]
        _send_raw(f"goal {plan[0]}")
        _send_raw("moton")
    else:
        _send_raw(f"goal {total}")
        _send_raw("moton")
    return jsonify(ok=True)


@app.route("/api/rpm", methods=["POST"])
def api_rpm():
    data = request.get_json() or request.form
    try:
        rpm = int(data.get("rpm") or 0)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="RPM musi być liczbą"), 400
    if winder.connected and rpm >= 0:
        _send_raw(f"rpm {rpm}")
    return jsonify(ok=True)


@app.route("/api/pitch", methods=["POST"])
def api_pitch():
    data = request.get_json() or request.form
    try:
        v = float(data.get("pitch") or 0)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Pitch musi być liczbą"), 400
    if winder.connected and v > 0:
        _send_raw(f"pitch {v}")
    return jsonify(ok=True)


@app.route("/api/bwidth", methods=["POST"])
def api_bwidth():
    data = request.get_json() or request.form
    try:
        v = float(data.get("bwidth") or 0)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Szerokość musi być liczbą"), 400
    if winder.connected and v > 0:
        _send_raw(f"bwidth {v}")
    return jsonify(ok=True)


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    host = "0.0.0.0"  # nasłuch na wszystkich interfejsach (LAN, WiFi)
    print(f"Serwer nawijarki: http://<adres-RPi>:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
