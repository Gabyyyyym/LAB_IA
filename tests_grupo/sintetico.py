"""
Escenas RGB-D sintéticas para probar sin robot el CLI, el espacio de trabajo y el análisis.

Imitan el formato que deja ``embodied_sin_ros.py capturar``: <escena>_color.png (1920x1080),
<escena>_depth.png (480x270, uint16 en mm) y <escena>_meta.json. Los intrínsecos de color son
una SUPOSICIÓN (fx = 2612 px, del default_color_calib_1920x1080.ini de kinova_vision con el
punto principal corregido al centro); en la práctica se reemplazan por los calibrados.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import embodied_comun as ec  # noqa: E402

ANCHO_C, ALTO_C = 1920, 1080


def config_sintetica() -> ec.ConfigCamara:
    color = ec.Intrinsecos(2612.0, 2612.0, 960.0, 540.0, ANCHO_C, ALTO_C, "sintética 1920x1080")
    prof = ec.Intrinsecos(360.01333, 360.01337, 243.87228, 137.92184, 480, 270, "default 480x270")
    return ec.ConfigCamara(color, prof, np.eye(3), np.array([-0.0195, -0.005, 0.0]))


def escribir_config(ruta: Path, cfg: ec.ConfigCamara) -> Path:
    c, d = cfg.color, cfg.profundidad
    datos = {
        "color": {f"{c.ancho}x{c.alto}": {"fx": c.fx, "fy": c.fy, "cx": c.cx, "cy": c.cy,
                                          "ancho": c.ancho, "alto": c.alto, "fuente": c.fuente}},
        "profundidad": {"fx": d.fx, "fy": d.fy, "cx": d.cx, "cy": d.cy, "ancho": d.ancho,
                        "alto": d.alto, "fuente": d.fuente},
        "extrinsecos_depth_a_color": {"rotacion": cfg.rotacion_depth_a_color.ravel().tolist(),
                                      "traslacion_m": cfg.traslacion_depth_a_color.tolist()},
    }
    ruta.write_text(json.dumps(datos, indent=2), encoding="utf-8")
    return ruta


def profundidad_cajas(cfg: ec.ConfigCamara, cajas, z_mesa=0.65, huecos=True) -> np.ndarray:
    """Mesa plana a ``z_mesa`` y cajas (prismas) dadas en el marco de COLOR.

    ``cajas``: lista de (x, y, z_tapa, lado_x, lado_y) en metros; (x, y) es el centro de la tapa.
    """
    kd, t = cfg.profundidad, cfg.traslacion_depth_a_color
    v, u = np.mgrid[0:kd.alto, 0:kd.ancho].astype(float)
    rx, ry = (u - kd.cx) / kd.fx, (v - kd.cy) / kd.fy
    z = np.full(u.shape, z_mesa)
    for (x, y, zt, lx, ly) in cajas:
        cd = np.array([x, y, zt]) - t          # centro de la tapa en el marco de profundidad
        sobre = (np.abs(rx * cd[2] - cd[0]) < lx / 2) & (np.abs(ry * cd[2] - cd[1]) < ly / 2)
        z[sobre] = np.minimum(z[sobre], cd[2])
    depth = np.round(z * 1000).astype(np.uint16)
    if huecos:
        depth[::7, ::11] = 0
    return depth


def proyectar(xyz, k: ec.Intrinsecos):
    """Punto 3D (marco de color) -> píxel (u, v)."""
    return k.fx * xyz[0] / xyz[2] + k.cx, k.fy * xyz[1] / xyz[2] + k.cy


def punto_gemini(xyz, k: ec.Intrinsecos):
    """Lo que respondería un Gemini perfecto: [y, x] normalizado 0-1000."""
    u, v = proyectar(xyz, k)
    return [round(v / (k.alto - 1) * 1000, 2), round(u / (k.ancho - 1) * 1000, 2)]


def guardar_escena(carpeta: Path, nombre: str, depth: np.ndarray | None, nota: str = "") -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    color = np.full((ALTO_C, ANCHO_C, 3), 200, np.uint8)
    base = carpeta / nombre
    cv2.imwrite(f"{base}_color.png", color)
    meta = {"escena": nombre, "fuente": "sintética", "color": {"ancho": ANCHO_C, "alto": ALTO_C}}
    if depth is not None:
        cv2.imwrite(f"{base}_depth.png", depth)
        validos = depth[depth > 0]
        meta["profundidad"] = {"ancho": depth.shape[1], "alto": depth.shape[0], "unidad": "mm",
                               "pixeles_validos_pct": round(100 * validos.size / depth.size, 1),
                               "mediana_mm": int(np.median(validos))}
        meta["desfase_color_profundidad_ms"] = 0.0
    if nota:
        meta["nota"] = nota
    Path(f"{base}_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return Path(f"{base}_color.png")
