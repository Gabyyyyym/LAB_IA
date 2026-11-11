import csv, json, math

# 1) Calibración guardada
cfg = json.load(open("config_camara_kinova.json", encoding="utf-8"))
k = cfg["color"].get("1920x1080")
if not k:
    raise SystemExit("No hay calibración 1920x1080: corre 'calibrar' primero.")
fov = 2 * math.degrees(math.atan(k["ancho"] / (2 * k["fx"])))
print("=== CALIBRACIÓN (Tabla de calibración de la cámara de color) ===")
print(f"Resolución de color : {k['ancho']}x{k['alto']}")
print(f"fx / fy (px)        : {k['fx']} / {k['fy']}")
print(f"cx / cy (px)        : {k['cx']} / {k['cy']}")
print(f"Campo de visión H   : {fov:.1f} °")
print(f"Fuente              : {k.get('fuente')}")

# 2) Última consulta de la escena 'regla' con 2 puntos (grupo G03-calib)
filas = [r for r in csv.DictReader(open("resultados/resultados.csv", encoding="utf-8"))
         if r["escena"] == "regla" and r["tarea"] == "puntos" and r["grupo"] == "G03-calib"
         and r["z_m"] not in ("", None)]
if len(filas) < 2:
    raise SystemExit("No encuentro 2 puntos con profundidad en resultados.csv para la escena regla.")
a, b = filas[-2], filas[-1]
p1 = [float(a[c]) for c in ("x_m", "y_m", "z_m")]
p2 = [float(b[c]) for c in ("x_m", "y_m", "z_m")]
d = math.dist(p1, p2)
err = abs(d - 0.297) / 0.297 * 100
print("\n=== VERIFICACIÓN CON LA HOJA ===")
print(f"Punto 1 ({a['label']}): píxel=({a['u']}, {a['v']})  xyz={p1}")
print(f"Punto 2 ({b['label']}): píxel=({b['u']}, {b['v']})  xyz={p2}")
print(f"Distancia 3D entre esquinas : {d:.4f} m")
print(f"Error respecto a 0.297 m    : {err:.1f} %  ->", "APROBADO (< 5 %)" if err < 5 else "REPETIR calibración o revisar los puntos")
