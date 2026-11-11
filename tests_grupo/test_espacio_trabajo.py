"""Pruebas de espacio_trabajo.py (Fase 9) con geometría conocida y con el CLI real."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

AQUI = Path(__file__).resolve().parent
PLANTILLA = AQUI.parent
sys.path.insert(0, str(PLANTILLA))
sys.path.insert(0, str(AQUI))
import espacio_trabajo as et  # noqa: E402
import sintetico as st  # noqa: E402

ESQUINAS = [(-0.15, -0.09, 0.65), (0.16, -0.09, 0.65), (0.16, 0.09, 0.65), (-0.15, 0.09, 0.65)]


@pytest.fixture
def lim():
    return et.limites_desde_puntos(ESQUINAS, h_max=0.20, d_a=0.10)


def test_normal_apunta_a_la_camara():
    n = et.normal_mesa(*ESQUINAS[:3])
    assert n == pytest.approx([0.0, 0.0, -1.0])
    # El orden de los puntos no cambia la orientación final.
    assert et.normal_mesa(ESQUINAS[2], ESQUINAS[1], ESQUINAS[0]) == pytest.approx([0.0, 0.0, -1.0])


def test_normal_mesa_inclinada():
    ang = np.radians(10)
    R = np.array([[1, 0, 0], [0, np.cos(ang), -np.sin(ang)], [0, np.sin(ang), np.cos(ang)]])
    P = [R @ (np.array(p) - [0, 0, 0.65]) + [0, 0, 0.65] for p in ESQUINAS]
    _, n, residuo = et.ajustar_plano(P)
    assert np.degrees(np.arccos(abs(n[2]))) == pytest.approx(10, abs=1e-6)
    assert residuo < 1e-9


def test_colineales_falla():
    with pytest.raises(ValueError):
        et.normal_mesa((0, 0, 0.6), (0.1, 0, 0.6), (0.2, 0, 0.6))


def test_volumen_y_pre_agarre(lim):
    assert (lim["x_min"], lim["x_max"], lim["z_mesa"]) == pytest.approx((-0.15, 0.16, 0.65))
    assert et.dentro_del_volumen((0.0, 0.0, 0.55), lim)
    assert not et.dentro_del_volumen((0.0, 0.0, 0.40), lim)          # más alto que h_max
    assert not et.dentro_del_volumen((0.30, 0.0, 0.60), lim)         # fuera en X
    assert not et.dentro_del_volumen((0.0, 0.0, 0.66), lim)          # bajo la mesa
    assert et.pose_pre_agarre((0.05, 0.02, 0.58), lim["normal"], 0.10) == pytest.approx([0.05, 0.02, 0.48])
    v = et.validar_objetivo((0.05, 0.02, 0.58), lim)
    assert v["valido"] and v["p_pre"] == pytest.approx([0.05, 0.02, 0.48])
    assert et.validar_objetivo(None, lim)["motivo"] == "sin profundidad válida"


def test_volumen_plano_tolera_ruido(lim):
    assert not et.dentro_del_volumen((0.0, 0.0, 0.655), lim)
    assert et.dentro_del_volumen_plano((0.0, 0.0, 0.655), lim, tolerancia_m=0.01)


PLAN_BUENO = {
    "objetos": [{"point": [400, 300], "label": "caja libre"}, {"point": [500, 700], "label": "bandeja"}],
    "pasos": [{"accion": "mover_sobre", "objetivo": "caja libre"}, {"accion": "abrir_pinza", "objetivo": None},
              {"accion": "bajar", "objetivo": "caja libre"}, {"accion": "cerrar_pinza", "objetivo": None},
              {"accion": "subir", "objetivo": None}, {"accion": "mover_sobre", "objetivo": "bandeja"},
              {"accion": "bajar", "objetivo": "bandeja"}, {"accion": "abrir_pinza", "objetivo": None},
              {"accion": "subir", "objetivo": None}],
}
OBJ_3D = {"caja libre": [0.0, 0.0, 0.55], "bandeja": [0.1, 0.05, 0.63]}


def test_plan_bueno_pasa_las_cuatro_reglas(lim):
    r = et.validar_plan(PLAN_BUENO, OBJ_3D, lim)
    assert all(r[k]["ok"] for k in ("a_vocabulario", "b_objetivos", "c_volumen", "d_secuencia")), r
    assert r["avisos"] == []


@pytest.mark.parametrize("pasos, regla, texto", [
    ([{"accion": "teletransportar", "objetivo": "caja libre"}], "a_vocabulario", "no permitida"),
    ([{"accion": "mover_sobre", "objetivo": "termo"}], "b_objetivos", "termo"),
    ([{"accion": "mover_sobre", "objetivo": "caja libre"}, {"accion": "cerrar_pinza", "objetivo": None}],
     "d_secuencia", "cerrar_pinza antes de bajar"),
    ([{"accion": "mover_sobre", "objetivo": "caja libre"}, {"accion": "bajar", "objetivo": None},
      {"accion": "subir", "objetivo": None}], "d_secuencia", "pinza abierta"),
    ([{"accion": "bajar", "objetivo": None}], "d_secuencia", "sin mover_sobre"),
])
def test_plan_con_errores(lim, pasos, regla, texto):
    r = et.validar_plan({"objetos": PLAN_BUENO["objetos"], "pasos": pasos}, OBJ_3D, lim)
    assert not r[regla]["ok"] and texto in " ".join(r[regla]["detalle"])


def test_plan_fuera_del_volumen(lim):
    r = et.validar_plan(PLAN_BUENO, {"caja libre": [0.5, 0.0, 0.55], "bandeja": None}, lim)
    assert not r["c_volumen"]["ok"] and len(r["c_volumen"]["detalle"]) == 2


def test_cli_volumen_desde_escena_regla(tmp_path):
    """Puntos de la zona útil pedidos con una respuesta a mano sobre una escena sintética."""
    cfg = st.config_sintetica()
    ruta = st.guardar_escena(tmp_path / "capturas", "regla", st.profundidad_cajas(cfg, [], z_mesa=0.65))
    config = st.escribir_config(tmp_path / "config.json", cfg)
    csv_ruta, salida = tmp_path / "resultados.csv", tmp_path / "espacio.json"
    puntos = [(-0.10, -0.06, 0.65), (0.10, -0.06, 0.65), (0.10, 0.06, 0.65), (-0.10, 0.06, 0.65)]
    resp = json.dumps([{"point": st.punto_gemini(p, cfg.color), "label": f"esquina {i}"}
                       for i, p in enumerate(puntos)])
    r = subprocess.run([sys.executable, str(PLANTILLA / "embodied_sin_ros.py"), "consultar", "--escena", str(ruta),
                        "--tarea", "puntos", "--objeto", "esquinas", "--config", str(config),
                        "--csv", str(csv_ruta), "--respuesta", resp], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    r = subprocess.run([sys.executable, str(PLANTILLA / "espacio_trabajo.py"), "volumen", "--escena", "regla",
                        "--h-max", "0.2", "--csv", str(csv_ruta), "--salida", str(salida)],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    lim = json.loads(salida.read_text(encoding="utf-8"))
    assert lim["x_min"] == pytest.approx(-0.10, abs=0.003) and lim["x_max"] == pytest.approx(0.10, abs=0.003)
    assert lim["z_mesa"] == pytest.approx(0.65, abs=0.002)
    assert lim["normal"] == pytest.approx([0, 0, -1], abs=0.05)
