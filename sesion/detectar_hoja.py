#!/usr/bin/env python3
"""
Fase 3 — Detección automática de la hoja A4 en la escena 'regla' para la calibración rápida.

Umbraliza la región clara más grande (la hoja blanca), ajusta un rectángulo rotado y entrega
u1, u2 a lo largo del lado largo (0.297 m). Como control independiente de la cinta reporta la
mediana de la profundidad del sensor dentro de la hoja. Deja la imagen de verificación
capturas/regla_hoja_detectada.png (evidencia E3) e imprime el comando calibrar listo.

    python3 sesion/detectar_hoja.py                    # escena regla
    python3 sesion/detectar_hoja.py --z 0.62           # Z medida con cinta (m)
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import cv2
import numpy as np

PLANTILLA = Path(__file__).resolve().parents[1]


def detectar(color: np.ndarray):
    gris = cv2.GaussianBlur(cv2.cvtColor(color, cv2.COLOR_BGR2GRAY), (7, 7), 0)
    _, mascara = cv2.threshold(gris, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mascara = cv2.morphologyEx(mascara, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    contornos, _ = cv2.findContours(mascara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contornos:
        return None
    c = max(contornos, key=cv2.contourArea)
    if cv2.contourArea(c) < 0.02 * color.shape[0] * color.shape[1]:
        return None
    (cx, cy), (w, h), ang = cv2.minAreaRect(c)
    if w < h:                                  # lado largo horizontal
        w, h, ang = h, w, ang + 90
    ang = (ang + 90) % 180 - 90
    return {"cx": cx, "cy": cy, "largo_px": w, "corto_px": h, "angulo_deg": ang,
            "u1": cx - w / 2, "u2": cx + w / 2, "contorno": c}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--escena", default="regla")
    p.add_argument("--capturas", default=str(PLANTILLA / "capturas"))
    p.add_argument("--z", type=float, default=None, help="Z cámara-hoja medida con cinta (m)")
    a = p.parse_args()

    base = Path(a.capturas) / a.escena
    color = cv2.imread(f"{base}_color.png")
    if color is None:
        raise SystemExit(f"[ERROR] No existe {base}_color.png")
    r = detectar(color)
    if r is None:
        raise SystemExit("[ERROR] No se encontró una región clara grande: ¿la hoja está en la imagen y bien iluminada?")

    relacion = r["largo_px"] / r["corto_px"]
    print(f"[OK] Hoja detectada: {r['largo_px']:.1f} × {r['corto_px']:.1f} px, ángulo {r['angulo_deg']:+.1f}°, "
          f"relación {relacion:.3f} (A4 = 1.414)")
    if abs(relacion - 1.414) > 0.07:
        print("[AVISO] La relación de aspecto no es la de una A4: revise la imagen de verificación "
              "(sombra, borde de la mesa o la hoja inclinada respecto de la cámara).")
    if abs(r["angulo_deg"]) > 5:
        print("[AVISO] La hoja está girada más de 5°: se usa el largo del rectángulo rotado.")
    print(f"     u1 = {r['u1']:.1f} px, u2 = {r['u2']:.1f} px (centro de la hoja en u = {r['cx']:.0f}, v = {r['cy']:.0f})")

    z_sensor = None
    depth = cv2.imread(f"{base}_depth.png", cv2.IMREAD_UNCHANGED)
    if depth is not None and depth.dtype == np.uint16:
        validos = depth[depth > 0]
        if validos.size:
            # La hoja está sobre la mesa: la mediana del área central de la imagen de profundidad
            # estima su distancia (marco del sensor de profundidad, t_z = 0 respecto al de color).
            h, w = depth.shape
            centro = depth[h // 3: 2 * h // 3, w // 3: 2 * w // 3]
            centro = centro[centro > 0]
            z_sensor = float(np.median(centro if centro.size else validos)) / 1000
            print(f"     Z según el sensor de profundidad (mediana central): {z_sensor:.3f} m")

    nota = ""
    meta = Path(f"{base}_meta.json")
    if meta.exists():
        nota = json.loads(meta.read_text(encoding="utf-8")).get("nota", "")
        print(f"     Nota de la captura: {nota}")
    z = a.z
    if z is None:
        m = re.search(r"Z\s*=\s*([0-9]+[.,][0-9]+)", nota)
        z = float(m.group(1).replace(",", ".")) if m else None
    if z and z_sensor and abs(z - z_sensor) / z > 0.05:
        print(f"[AVISO] La Z de la cinta ({z:.3f} m) y la del sensor ({z_sensor:.3f} m) difieren más de 5 %: "
              "revise desde dónde se midió (frente del módulo de visión).")

    vis = color.copy()
    caja = cv2.boxPoints(((r["cx"], r["cy"]), (r["largo_px"], r["corto_px"]), r["angulo_deg"]))
    cv2.drawContours(vis, [np.int32(caja)], 0, (0, 0, 255), 3)
    for u in (r["u1"], r["u2"]):
        cv2.line(vis, (int(u), 0), (int(u), vis.shape[0] - 1), (0, 255, 255), 2)
    cv2.putText(vis, f"largo = {r['largo_px']:.0f} px", (int(r["u1"]) + 10, int(r["cy"])),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
    salida = Path(f"{base}_hoja_detectada.png")
    cv2.imwrite(str(salida), vis)
    print(f"[OK] Verificación visual: {salida}")

    z_txt = f"{z:.3f}" if z else (f"{z_sensor:.3f}" if z_sensor else "<Z medida>")
    print("\nComando de calibración (Z de la cinta" + ("" if z else "; si falta, la del sensor") + "):")
    print(f"python3 embodied_sin_ros.py calibrar --escena {a.escena} --u1 {r['u1']:.1f} --u2 {r['u2']:.1f} "
          f"--ancho-real-m 0.297 --distancia-m {z_txt}")


if __name__ == "__main__":
    main()
