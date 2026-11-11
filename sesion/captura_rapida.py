#!/usr/bin/env python3
"""
Captura rápida del turno de cámara (Ruta A): una página web local con una ficha por escena,
un botón (o la barra espaciadora) que toma color + profundidad con `embodied_sin_ros.py
capturar`, muestra el resultado y pasa a la siguiente escena.

- La cámara se abre solo durante cada captura y se suelta enseguida (turnos entre grupos).
- Las medidas con cinta van a la nota de la captura y, para distancias y altura, también a
  verdad_terreno.csv (columna d_real_m).
- La página indica si el puerto RTSP de la cámara responde.

    python3 sesion/captura_rapida.py                       # http://localhost:8765
    python3 sesion/captura_rapida.py --simular --capturas /tmp/cap --verdad /tmp/verdad.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import socket
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

PLANTILLA = Path(__file__).resolve().parents[1]
REPO = PLANTILLA.parents[2]
EVITAR = "Sin personas, rostros, documentos, texto legible ni pantallas encendidas."

# Orden pensado para mover pocos objetos entre escenas.
ESCENAS = [
    ("regla", "Fase 3 · calibración", ["Hoja A4 blanca"],
     "Hoja HORIZONTAL (lado largo de izquierda a derecha en la imagen), centrada y plana. Nada más sobre la mesa.", "z"),
    ("semantica1", "Exp. 1 · D · seguridad", ["2 cajas de hamburguesa", "Termo"],
     "Cajas separadas ~10 cm. Termo de pie ENCIMA de la caja derecha; la izquierda queda libre.", None),
    ("agarre_i1", "Exp. 5 · agarre inseguro", ["1 caja", "Termo"],
     "Igual que la anterior pero RETIRE la caja libre: solo queda la caja con el termo encima.", None),
    ("semantica1p", "Exp. 1 · P · seguridad", ["2 cajas", "Cuaderno cerrado", "Bandeja"],
     "Cajas en otras posiciones (p. ej. en diagonal). Cuaderno encima de la caja IZQUIERDA. Bandeja vacía a un lado.", None),
    ("burger", "Exp. 1 y 2 · D · relacional", ["2 cajas", "Bandeja"],
     "Una caja SOBRE la bandeja y la otra FUERA, a ~10 cm.", None),
    ("burgerp", "Exp. 1 · P · relacional", ["2 cajas", "Bandeja", "Termo"],
     "Bandeja al otro lado de la imagen; una caja sobre ella y la otra fuera en otra posición; termo como distractor.", None),
    ("exito_antes1", "Exp. 5 · éxito (antes 1)", ["1 caja", "Bandeja"],
     "Caja FUERA de la bandeja, a ~10 cm.", None),
    ("exito_bien1", "Exp. 5 · éxito (bien 1)", ["1 caja", "Bandeja"],
     "Meta a mano la caja DENTRO de la bandeja. Retire la mano antes de capturar.", None),
    ("exito_mal1", "Exp. 5 · éxito (mal 1)", ["1 caja", "Bandeja"],
     "Vuelva al estado de 'antes 1' y deje la caja JUNTO a la bandeja (tocándola), no dentro.", None),
    ("exito_antes2", "Exp. 5 · éxito (antes 2)", ["1 caja", "Bandeja"],
     "Otra posición inicial: caja y bandeja en lugares distintos a los de 'antes 1'.", None),
    ("exito_bien2", "Exp. 5 · éxito (bien 2)", ["1 caja", "Bandeja"],
     "Caja DENTRO de la bandeja. Retire la mano.", None),
    ("exito_mal2", "Exp. 5 · éxito (mal 2)", ["1 caja", "Bandeja"],
     "Desde 'antes 2': caja apoyada en el borde de la bandeja, medio afuera.", None),
    ("agarre_s1", "Exp. 5 · agarre seguro", ["1 caja"],
     "Caja sola y libre, cerca del centro.", None),
    ("agarre_s2", "Exp. 5 · agarre seguro", ["1 caja", "Termo", "Cuaderno cerrado"],
     "Caja libre; termo y cuaderno a más de 10 cm de ella.", None),
    ("agarre_s3", "Exp. 5 · agarre seguro", ["1 caja"],
     "Caja libre girada unos 45°, en otra zona de la imagen.", None),
    ("agarre_i3", "Exp. 5 · agarre inseguro", ["1 caja"],
     "Caja volcada de lado o apoyada en un borde (inestable).", None),
    ("agarre_i2", "Exp. 5 · agarre inseguro", ["1 caja", "Una mano"],
     "Mano abierta a menos de 5 cm de la caja. SOLO la mano: sin anillos, reloj, tatuajes ni manga con logos.", None),
    ("altura1", "Exp. 3 · altura", ["1 caja"],
     "Caja sola sobre la mesa, cerca del centro. Mida su altura con la regla.", "altura"),
    ("dist1", "Exp. 3 · distancia (centro)", ["Termo", "1 caja"],
     "Termo y caja cerca del centro de la imagen, a ~20–25 cm entre centros.", "dist"),
    ("dist2", "Exp. 3 · distancia (centro)", ["Termo", "1 caja"],
     "Centro de la imagen, otra separación (~15 cm).", "dist"),
    ("dist3", "Exp. 3 · distancia (centro)", ["Termo", "1 caja"],
     "Centro de la imagen, en diagonal o ~30 cm.", "dist"),
    ("dist4", "Exp. 3 · distancia (BORDE)", ["Termo", "1 caja"],
     "Uno de los dos objetos CERCA DEL BORDE izquierdo de la imagen, sin salirse.", "dist"),
    ("dist5", "Exp. 3 · distancia (BORDE)", ["Termo", "1 caja"],
     "Uno de los dos CERCA DEL BORDE derecho (o superior), sin salirse.", "dist"),
    ("semantica2", "Exp. 1 · D · pertenencia", ["2 celulares (pantalla apagada)", "Llaves"],
     "Celulares separados ~15 cm; llaves pegadas (<3 cm) a uno de ellos.", None),
    ("semantica2p", "Exp. 1 · P · pertenencia", ["2 celulares (pantalla apagada)", "Llaves", "Esfero"],
     "Celulares girados en otras posiciones; llaves junto al OTRO celular; esfero como distractor.", None),
    ("semantica3", "Exp. 1 · D · estado", ["2 cuadernos"],
     "Un cuaderno abierto (páginas en blanco) y otro cerrado.", None),
    ("semantica3p", "Exp. 1 · P · estado", ["2 cuadernos", "Estuche"],
     "Posiciones intercambiadas (el abierto al otro lado); estuche como distractor.", None),
    ("semantica4", "Exp. 1 · D · contención", ["Estuche abierto", "3 esferos"],
     "Un esfero DENTRO del estuche abierto; dos esferos sueltos fuera.", None),
    ("semantica4p", "Exp. 1 · P · contención", ["Estuche abierto", "3 esferos"],
     "Otro esfero dentro; uno apoyado SOBRE el borde del estuche (no dentro); uno suelto.", None),
]
RELACION_MEDIDA = {"dist": "distancia", "altura": "altura"}


class App:
    def __init__(self, args):
        self.ip = args.ip
        self.simular = args.simular
        self.capturas = Path(args.capturas)
        self.verdad = Path(args.verdad)
        self.candado = threading.Lock()
        self.medidas_ruta = self.capturas / "medidas_cinta.json"

    # -- estado -------------------------------------------------------------
    def camara_responde(self) -> bool:
        if self.simular:
            return True
        try:
            with socket.create_connection((self.ip, 554), timeout=1.5):
                return True
        except OSError:
            return False

    def meta(self, escena):
        ruta = self.capturas / f"{escena}_meta.json"
        return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else None

    def medidas(self) -> dict:
        if self.medidas_ruta.exists():
            return json.loads(self.medidas_ruta.read_text(encoding="utf-8"))
        return {}

    def estado(self):
        med = self.medidas()
        salida = []
        for eid, grupo, objetos, disp, medida in ESCENAS:
            m = self.meta(eid)
            salida.append({"id": eid, "grupo": grupo, "objetos": objetos, "disposicion": disp, "medida": medida,
                           "capturada": m is not None,
                           "pct": (m or {}).get("profundidad", {}).get("pixeles_validos_pct"),
                           "desfase": (m or {}).get("desfase_color_profundidad_ms"),
                           "nota": (m or {}).get("nota", ""), "medidas": med.get(eid, {})})
        return {"escenas": salida, "camara": self.camara_responde(), "ip": self.ip, "simular": self.simular,
                "evitar": EVITAR, "dh_ultimo": med.get("_dh_m")}

    # -- medidas --------------------------------------------------------------
    def nota_y_medida(self, eid, tipo, valores: dict):
        """Devuelve (nota, d_real_m o None) a partir de lo que escribió el usuario."""
        disp = next(e[3] for e in ESCENAS if e[0] == eid)
        num = {k: float(str(v).replace(",", ".")) for k, v in valores.items() if str(v).strip()}
        if tipo == "z":
            if "z" not in num:
                raise ValueError("Falta la Z medida con cinta.")
            return f"{disp} Z = {num['z']:.3f} m", None
        if tipo == "altura":
            if "h" not in num:
                raise ValueError("Falta la altura de la caja.")
            return f"{disp} h = {num['h']:.3f} m", num["h"]
        if tipo == "dist":
            if "d" not in num:
                raise ValueError("Falta la distancia horizontal entre centros.")
            dh = num.get("dh", 0.0)
            d = math.hypot(num["d"], dh)
            return f"{disp} d_horizontal = {num['d']:.3f} m, dh = {dh:.3f} m -> d = {d:.3f} m", d
        return disp, None

    def guardar_medida(self, eid, tipo, valores, d_real):
        med = self.medidas()
        med[eid] = {k: v for k, v in valores.items() if str(v).strip()}
        if tipo == "dist" and str(valores.get("dh", "")).strip():
            med["_dh_m"] = valores["dh"]
        self.medidas_ruta.write_text(json.dumps(med, indent=2, ensure_ascii=False), encoding="utf-8")
        if d_real is None or tipo not in RELACION_MEDIDA or not self.verdad.exists():
            return
        lineas = self.verdad.read_text(encoding="utf-8").splitlines()
        cols = next(csv.reader([lineas[0]]))
        for i, linea in enumerate(lineas[1:], start=1):
            if not linea.strip() or linea.lstrip().startswith("#"):
                continue
            v = next(csv.reader([linea]))
            v += [""] * (len(cols) - len(v))
            if v[cols.index("escena")] == eid and v[cols.index("relacion")] == RELACION_MEDIDA[tipo]:
                v[cols.index("d_real_m")] = f"{d_real:.4f}"
                buf = io.StringIO()
                csv.writer(buf, lineterminator="").writerow(v)
                lineas[i] = buf.getvalue()
        self.verdad.write_text("\n".join(lineas) + "\n", encoding="utf-8")

    # -- captura --------------------------------------------------------------
    def capturar(self, eid, valores):
        tipo = next(e[4] for e in ESCENAS if e[0] == eid)
        nota, d_real = self.nota_y_medida(eid, tipo, valores)
        with self.candado:
            if self.simular:
                self._capturar_simulado(eid, nota)
            else:
                cmd = [sys.executable, str(PLANTILLA / "embodied_sin_ros.py"), "capturar", "--ip", self.ip,
                       "--escena", eid, "--nota", nota, "--sobrescribir"]
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=45, cwd=PLANTILLA)
                except subprocess.TimeoutExpired:
                    raise RuntimeError("La cámara no respondió en 45 s (¿otro grupo la está usando?).")
                if r.returncode != 0:
                    error = [l for l in (r.stderr + r.stdout).splitlines() if "WARN" not in l][-3:]
                    raise RuntimeError("No se pudo capturar (¿cámara ocupada?): " + " ".join(error))
        self.guardar_medida(eid, tipo, valores, d_real)
        m = self.meta(eid)
        print(f"[{datetime.now():%H:%M:%S}] {eid}: {m.get('profundidad', {}).get('pixeles_validos_pct')} % "
              f"de profundidad válida · {nota}", flush=True)
        return m

    def _capturar_simulado(self, eid, nota):
        self.capturas.mkdir(parents=True, exist_ok=True)
        color = np.full((1080, 1920, 3), 170, np.uint8)
        cv2.putText(color, f"SIMULADO {eid}", (500, 560), cv2.FONT_HERSHEY_SIMPLEX, 3, (40, 40, 40), 6)
        depth = np.full((270, 480), 650, np.uint16)
        depth[100:170, 180:300] = 560
        depth[::9, ::13] = 0
        cv2.imwrite(str(self.capturas / f"{eid}_color.png"), color)
        cv2.imwrite(str(self.capturas / f"{eid}_depth.png"), depth)
        validos = depth[depth > 0]
        meta = {"escena": eid, "ip": "simulado", "fecha": datetime.now().isoformat(timespec="seconds"),
                "color": {"ancho": 1920, "alto": 1080},
                "profundidad": {"ancho": 480, "alto": 270, "unidad": "mm",
                                "pixeles_validos_pct": round(100 * validos.size / depth.size, 1),
                                "mediana_mm": int(np.median(validos))},
                "desfase_color_profundidad_ms": 12.0, "nota": nota}
        (self.capturas / f"{eid}_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    # -- imágenes ---------------------------------------------------------------
    @staticmethod
    def _profundidad_color(depth):
        validos = depth[depth > 0]
        lo, hi = (np.percentile(validos, [2, 98]) if validos.size else (0, 1))
        esc = np.clip((depth.astype(np.float32) - lo) / max(hi - lo, 1) * 255, 0, 255).astype(np.uint8)
        vis = cv2.applyColorMap(255 - esc, cv2.COLORMAP_TURBO)
        vis[depth == 0] = 0
        return vis

    def miniatura(self, eid) -> bytes | None:
        color = cv2.imread(str(self.capturas / f"{eid}_color.png"))
        if color is None:
            return None
        partes = [cv2.resize(color, (640, 360), interpolation=cv2.INTER_AREA)]
        depth = cv2.imread(str(self.capturas / f"{eid}_depth.png"), cv2.IMREAD_UNCHANGED)
        if depth is not None:
            partes.append(cv2.resize(self._profundidad_color(depth), (640, 360), interpolation=cv2.INTER_NEAREST))
        ok, buf = cv2.imencode(".jpg", cv2.hconcat(partes), [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes() if ok else None

    def vista_previa(self) -> bytes:
        """Un solo cuadro de color (no se guarda) para revisar el encuadre."""
        with self.candado:
            if self.simular:
                frame = np.full((1080, 1920, 3), 150, np.uint8)
            else:
                sys.path.insert(0, str(REPO / "scripts"))
                from test_kinova_camera import build_pipeline
                cap = cv2.VideoCapture(build_pipeline(f"rtsp://{self.ip}/color", "color", 30), cv2.CAP_GSTREAMER)
                frame = None
                try:
                    for _ in range(6):
                        ok, f = cap.read()
                        if ok:
                            frame = f
                finally:
                    cap.release()
                if frame is None:
                    raise RuntimeError("La cámara no entregó imagen (¿ocupada por otro grupo?).")
        vis = cv2.resize(frame, (960, 540), interpolation=cv2.INTER_AREA)
        cv2.line(vis, (480, 0), (480, 539), (0, 255, 255), 1)
        cv2.line(vis, (0, 270), (959, 270), (0, 255, 255), 1)
        ok, buf = cv2.imencode(".jpg", vis, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes()


def crear_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _enviar(self, codigo, cuerpo: bytes, tipo="application/json"):
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def _json(self, codigo, obj):
            self._enviar(codigo, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                return self._enviar(200, PAGINA.encode("utf-8"), "text/html; charset=utf-8")
            if u.path == "/api/estado":
                return self._json(200, app.estado())
            if u.path == "/api/miniatura":
                eid = parse_qs(u.query).get("id", [""])[0]
                img = app.miniatura(eid) if eid in {e[0] for e in ESCENAS} else None
                return self._enviar(200, img, "image/jpeg") if img else self._json(404, {"error": "sin captura"})
            if u.path == "/api/vista_previa":
                try:
                    return self._enviar(200, app.vista_previa(), "image/jpeg")
                except Exception as e:  # noqa: BLE001
                    return self._json(503, {"error": str(e)})
            self._json(404, {"error": "no existe"})

        def do_POST(self):
            if urlparse(self.path).path != "/api/capturar":
                return self._json(404, {"error": "no existe"})
            datos = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            eid = datos.get("id", "")
            if eid not in {e[0] for e in ESCENAS}:
                return self._json(400, {"error": "escena desconocida"})
            try:
                m = app.capturar(eid, datos.get("medidas", {}))
                return self._json(200, {"ok": True, "meta": m})
            except (ValueError, RuntimeError) as e:
                return self._json(400 if isinstance(e, ValueError) else 503, {"ok": False, "error": str(e)})

    return Handler


PAGINA = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Captura rápida</title>
<style>
:root{--bg:#f6f6f4;--card:#fff;--ink:#141414;--ink2:#55534e;--line:#e2e1db;--acc:#2a78d6;--ok:#0b8a3e;--warn:#c27c00;--bad:#c62f2f}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--ink:#f0efec;--ink2:#b9b7ae;--line:#33332f;--acc:#4a90e2;--ok:#2fbf62;--warn:#e0a020;--bad:#e86060}}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--ink)}
.app{display:grid;grid-template-columns:250px 1fr;height:100vh}
aside{border-right:1px solid var(--line);overflow:auto;padding:10px 8px;background:var(--card)}
aside h2{font-size:13px;margin:4px 8px 10px;color:var(--ink2);font-weight:600}
.it{display:flex;align-items:center;gap:8px;padding:5px 8px;border-radius:7px;cursor:pointer;font-size:13.5px}
.it:hover{background:var(--bg)}.it.act{background:var(--acc);color:#fff}
.dot{width:9px;height:9px;border-radius:50%;border:1.5px solid var(--ink2);flex:none}
.dot.ok{background:var(--ok);border-color:var(--ok)}.dot.bad{background:var(--bad);border-color:var(--bad)}
.it .p{margin-left:auto;font-size:11.5px;opacity:.75}
main{overflow:auto;padding:18px 26px;display:flex;flex-direction:column;gap:14px}
.top{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.cam{padding:4px 10px;border-radius:99px;font-size:13px;font-weight:600;border:1px solid var(--line)}
.cam.on{color:var(--ok)}.cam.off{color:var(--bad)}
.prog{color:var(--ink2);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px}
.grupo{font-size:13px;color:var(--ink2);font-weight:600;letter-spacing:.02em}
h1{margin:2px 0 10px;font-size:30px}
.objs{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 10px}
.obj{border:1px solid var(--line);border-radius:7px;padding:3px 9px;font-size:14px}
.disp{font-size:18px;margin:6px 0}
.evitar{font-size:13px;color:var(--warn)}
.med{display:flex;gap:14px;flex-wrap:wrap;margin-top:12px}
.med label{display:flex;flex-direction:column;font-size:13px;color:var(--ink2);gap:4px}
.med input{font-size:20px;width:170px;padding:6px 8px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
.acc{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
button{font:inherit;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:10px 16px;cursor:pointer}
button.big{background:var(--acc);color:#fff;border:0;font-size:22px;font-weight:700;padding:16px 34px}
button:disabled{opacity:.45;cursor:not-allowed}
.msg{font-size:15px;font-weight:600}.msg.ok{color:var(--ok)}.msg.bad{color:var(--bad)}.msg.warn{color:var(--warn)}
.res img,.prev img{width:100%;border-radius:8px;border:1px solid var(--line);display:block}
.res .cap{font-size:13px;color:var(--ink2);margin-bottom:6px}
kbd{border:1px solid var(--line);border-bottom-width:2px;border-radius:5px;padding:0 5px;font-size:12px}
</style></head><body>
<div class="app"><aside><h2>ESCENAS</h2><div id="lista"></div></aside>
<main>
 <div class="top"><span id="cam" class="cam">Cámara…</span><span id="prog" class="prog"></span><span id="sim" class="prog"></span></div>
 <div class="card">
  <div class="grupo" id="grupo"></div><h1 id="titulo"></h1>
  <div class="grupo">DEBE TENER</div><div class="objs" id="objs"></div>
  <div class="disp" id="disp"></div><div class="evitar" id="evitar"></div>
  <div class="med" id="med"></div>
 </div>
 <div class="acc">
  <button class="big" id="btnCap">📸 Capturar <kbd>Espacio</kbd></button>
  <button id="btnPrev">Vista previa <kbd>P</kbd></button>
  <button id="btnAnt">← Anterior</button><button id="btnSig">Saltar →</button>
  <span id="msg" class="msg"></span>
 </div>
 <div class="card res" id="res" hidden><div class="cap" id="resCap"></div><img id="resImg" alt="Última captura: color y profundidad"></div>
 <div class="card prev" id="prev" hidden><div class="cap">Vista previa (no se guarda)</div><img id="prevImg" alt="Vista previa"></div>
</main></div>
<script>
let E=null, i=0, ocupado=false;
const $=id=>document.getElementById(id);
const CAMPOS={z:[["z","Z cámara–hoja (m)"]],altura:[["h","Altura de la caja (m)"]],
  dist:[["d","Distancia horizontal entre centros de las tapas (m)"],["dh","Diferencia de altura termo − caja (m)"]]};
async function cargar(sel){E=await (await fetch("/api/estado")).json();
  if(sel===undefined){const k=E.escenas.findIndex(e=>!e.capturada);i=k<0?0:k}else i=sel;pintar()}
function pintar(){const L=$("lista");L.innerHTML="";
  E.escenas.forEach((e,k)=>{const d=document.createElement("div");d.className="it"+(k===i?" act":"");
    const c=e.capturada?(e.pct>=60?"ok":"bad"):"";d.innerHTML=`<span class="dot ${c}"></span>${k+1}. ${e.id}<span class="p">${e.capturada?e.pct+"%":""}</span>`;
    d.onclick=()=>{i=k;pintar()};L.appendChild(d)});
  const e=E.escenas[i];$("grupo").textContent=e.grupo;$("titulo").textContent=`${i+1}/${E.escenas.length} · ${e.id}`;
  $("objs").innerHTML=e.objetos.map(o=>`<span class="obj">${o}</span>`).join("");
  $("disp").textContent=e.disposicion;$("evitar").textContent="⚠ "+E.evitar;
  const M=$("med");M.innerHTML="";(CAMPOS[e.medida]||[]).forEach(([k,l])=>{
    let v=(e.medidas||{})[k]??(k==="dh"&&E.dh_ultimo!=null?E.dh_ultimo:"");
    M.insertAdjacentHTML("beforeend",`<label>${l}<input id="m_${k}" inputmode="decimal" value="${v}"></label>`)});
  const n=E.escenas.filter(x=>x.capturada).length;$("prog").textContent=`${n}/${E.escenas.length} capturadas`;
  $("sim").textContent=E.simular?"· MODO SIMULADO":"";camara(E.camara);
  if(e.capturada){mostrar(e.id,`Ya capturada · ${e.pct}% de profundidad válida · ${e.nota}`)}
  const p=$("m_z")||$("m_h")||$("m_d");if(p)p.focus()}
function camara(on){const c=$("cam");c.className="cam "+(on?"on":"off");
  c.textContent=on?"● Cámara responde ("+E.ip+")":"● Sin conexión con la cámara";$("btnCap").disabled=!on||ocupado}
function mostrar(id,txt){$("res").hidden=false;$("resCap").textContent=txt;$("resImg").src="/api/miniatura?id="+id+"&t="+Date.now()}
function msg(t,c){$("msg").textContent=t;$("msg").className="msg "+(c||"")}
async function capturar(){if(ocupado)return;const e=E.escenas[i];const medidas={};
  (CAMPOS[e.medida]||[]).forEach(([k])=>{medidas[k]=$("m_"+k).value.trim()});
  ocupado=true;$("btnCap").disabled=true;msg("Capturando "+e.id+"…","");
  try{const r=await fetch("/api/capturar",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:e.id,medidas})});
    const j=await r.json();if(!j.ok){msg(j.error,"bad");return}
    const pct=j.meta.profundidad?j.meta.profundidad.pixeles_validos_pct:0;const bien=pct>=60;
    const txt=`${e.id} · ${pct}% de profundidad válida · desfase ${j.meta.desfase_color_profundidad_ms} ms`;
    await cargar(bien?Math.min(i+1,E.escenas.length-1):i);mostrar(e.id,(bien?"✓ ":"⚠ Repita: ")+txt);
    msg(bien?"✓ "+e.id+" guardada. Siguiente escena.":"⚠ Menos del 60 % de profundidad: mueva objetos negros o brillantes y repita.",bien?"ok":"warn")}
  catch(x){msg("Error: "+x,"bad")}finally{ocupado=false;camara(E.camara)}}
async function previa(){msg("Tomando vista previa…","");$("prev").hidden=false;
  const r=await fetch("/api/vista_previa?t="+Date.now());
  if(r.ok){$("prevImg").src=URL.createObjectURL(await r.blob());msg("Vista previa lista (no se guardó).","ok")}
  else{const j=await r.json();msg(j.error,"bad")}}
$("btnCap").onclick=capturar;$("btnPrev").onclick=previa;
$("btnAnt").onclick=()=>{i=Math.max(0,i-1);pintar()};$("btnSig").onclick=()=>{i=Math.min(E.escenas.length-1,i+1);pintar()};
document.addEventListener("keydown",ev=>{const enInput=ev.target.tagName==="INPUT";
  if(ev.code==="Space"&&!enInput){ev.preventDefault();capturar()}
  else if(ev.key==="Enter"&&enInput){ev.preventDefault();capturar()}
  else if(!enInput&&(ev.key==="p"||ev.key==="P"))previa();
  else if(!enInput&&ev.key==="ArrowRight")$("btnSig").click();else if(!enInput&&ev.key==="ArrowLeft")$("btnAnt").click()});
setInterval(async()=>{try{const j=await (await fetch("/api/estado")).json();E.camara=j.camara;camara(j.camara)}catch(x){}},4000);
cargar();
</script></body></html>"""


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ip", default="192.168.1.10")
    p.add_argument("--puerto", type=int, default=8765)
    p.add_argument("--capturas", default=str(PLANTILLA / "capturas"))
    p.add_argument("--verdad", default=str(PLANTILLA / "verdad_terreno.csv"))
    p.add_argument("--simular", action="store_true", help="Sin cámara: genera imágenes sintéticas")
    a = p.parse_args()
    if not a.simular and Path(a.capturas).resolve() != (PLANTILLA / "capturas").resolve():
        p.error("--capturas solo se puede cambiar con --simular (el CLI de la plantilla guarda en capturas/)")
    Path(a.capturas).mkdir(parents=True, exist_ok=True)
    app = App(a)
    srv = ThreadingHTTPServer(("127.0.0.1", a.puerto), crear_handler(app))
    print(f"[OK] Captura rápida en http://localhost:{a.puerto}  ({'SIMULADO' if a.simular else 'cámara ' + a.ip})",
          flush=True)
    print("     Ctrl+C para terminar.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
