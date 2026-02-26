#!/usr/bin/env python3
"""
Serwer WWW nawijarki – **tylko Raspberry Pi, bez Arduino**.
Sterowanie przez GPIO (silniki + enkoder + krańcówka). Uruchom na RPi.
"""
import os
from flask import Flask, request, jsonify

from winder_engine_rpi import get_engine

app = Flask(__name__)


def _engine():
    return get_engine()


def _html():
    return """
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Nawijarka (RPi GPIO)</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:sans-serif;max-width:600px;margin:1rem auto;padding:1rem;background:#1a1a2e;color:#eee;}
h1{color:#0f0;} .badge{background:#0a0;color:#000;padding:2px 8px;border-radius:4px;font-size:12px;}
button{margin:4px;padding:8px 12px;border-radius:6px;cursor:pointer;}
.run{background:#0a0;color:#fff;}.stop{background:#c00;color:#fff;}
.sec{background:#333;color:#fff;} input{width:80px;padding:4px;}
#status{margin-top:1rem;padding:0.5rem;background:#252540;border-radius:6px;min-height:60px;}
</style></head>
<body>
<h1>Nawijarka <span class="badge">RPi GPIO (bez Arduino)</span></h1>
<p><strong>Połączenie:</strong> sterowanie przez GPIO – gotowe.</p>
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
<script>
const api = path => fetch(path).then(r=>r.json()).catch(()=>null);
const post = (path, body) => fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)}).then(r=>r.json()).catch(()=>null);
function refreshStatus(){ api('/api/status').then(d=>{ if(!d) return; document.getElementById('status').innerHTML='Stan: '+d.state+' | Zwoje: '+d.current_turns+(d.current_turns_real!=null ? ' (enc: '+d.current_turns_real.toFixed(2)+')' : '')+' | Y: '+(d.current_y!=null ? d.current_y.toFixed(2) : '—')+' mm | RPM: '+(d.current_rpm||'—'); }); }
document.getElementById('btnRun').onclick = ()=>{ post('/api/start', { total: +document.getElementById('total').value, sections: +document.getElementById('sections').value, auto_next: document.getElementById('autoNext').checked }); };
document.getElementById('btnStop').onclick = ()=>{ post('/api/command', {cmd:'stop'}); };
document.getElementById('btnResume').onclick = ()=>{ post('/api/command', {cmd:'resume'}); };
document.getElementById('btnYzero').onclick = ()=>{ post('/api/command', {cmd:'yzero'}); };
document.getElementById('rpm').onchange = ()=>{ post('/api/rpm', {rpm: document.getElementById('rpm').value}); };
document.getElementById('pitch').onchange = ()=>{ post('/api/pitch', {pitch: document.getElementById('pitch').value}); };
document.getElementById('bwidth').onchange = ()=>{ post('/api/bwidth', {bwidth: document.getElementById('bwidth').value}); };
setInterval(refreshStatus, 1500); refreshStatus();
</script>
</body></html>
"""


@app.route("/")
def index():
    return _html()


@app.route("/api/status")
def api_status():
    try:
        return jsonify(_engine().get_status())
    except Exception as e:
        return jsonify(connected=False, state="IDLE", error=str(e)), 500


@app.route("/api/command", methods=["POST"])
def api_command():
    data = request.get_json() or {}
    cmd = (data.get("cmd") or "").strip().lower()
    if not cmd:
        return jsonify(ok=False, error="Brak komendy"), 400
    eng = _engine()
    if cmd == "run":
        eng.run()
    elif cmd == "resume":
        if eng.sections_mode and eng.section_ptr < len(eng.section_plan):
            eng._start_next_section()
        else:
            eng.resume()
    elif cmd == "stop":
        eng.stop()
    elif cmd == "yzero":
        eng.yzero()
    else:
        return jsonify(ok=False, error="Nieznana komenda"), 400
    return jsonify(ok=True)


@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.get_json() or {}
    total = int(data.get("total") or 0)
    sections = int(data.get("sections") or 0)
    if total <= 0:
        return jsonify(ok=False, error="Ilość zwojów musi być > 0"), 400
    eng = _engine()
    eng.auto_next_section = bool(data.get("auto_next"))
    eng.sections_mode = False
    eng.section_plan = []
    eng.section_ptr = 0
    eng.last_goal = None
    if sections > 0:
        per = total // sections
        rem = total % sections
        plan = [per + (1 if i < rem else 0) for i in range(sections)]
        eng.sections_mode = True
        eng.section_plan = plan
        eng.section_ptr = 0
        eng.last_goal = plan[0]
        eng.goal(plan[0])
        eng.run()
    else:
        eng.goal(total)
        eng.run()
    return jsonify(ok=True)


@app.route("/api/rpm", methods=["POST"])
def api_rpm():
    data = request.get_json() or request.form
    try:
        rpm = int(data.get("rpm") or 0)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="RPM musi być liczbą"), 400
    _engine().set_rpm(rpm)
    return jsonify(ok=True)


@app.route("/api/pitch", methods=["POST"])
def api_pitch():
    data = request.get_json() or request.form
    try:
        v = float(data.get("pitch") or 0)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Pitch musi być liczbą"), 400
    if v > 0:
        _engine().set_pitch(v)
    return jsonify(ok=True)


@app.route("/api/bwidth", methods=["POST"])
def api_bwidth():
    data = request.get_json() or request.form
    try:
        v = float(data.get("bwidth") or 0)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Szerokość musi być liczbą"), 400
    if v > 0:
        _engine().set_bwidth(v)
    return jsonify(ok=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = "0.0.0.0"
    print("Nawijarka – RPi GPIO (bez Arduino)")
    print(f"Serwer: http://<adres-RPi>:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
