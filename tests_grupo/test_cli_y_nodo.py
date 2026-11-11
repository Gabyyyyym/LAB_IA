"""
Pruebas del grupo (no reemplazan tests/ del docente):

* el CLI ``embodied_sin_ros.py consultar`` de punta a punta con una escena sintética y
  ``--respuesta`` (sin robot ni API), incluido el registro de JSON inválido;
* el TODO 5 del nodo ROS 2 con ``rclpy`` y mensajes simulados (no requiere ROS 2 instalado).

    python3 -m pytest tests_grupo -q
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest

AQUI = Path(__file__).resolve().parent
PLANTILLA = AQUI.parent
sys.path.insert(0, str(AQUI))
import sintetico as st  # noqa: E402


def correr_cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(PLANTILLA / "embodied_sin_ros.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", cwd=PLANTILLA)


@pytest.fixture
def escena_dist(tmp_path):
    cfg = st.config_sintetica()
    a, b = (-0.08, 0.02, 0.55), (0.10, -0.03, 0.58)       # tapas de dos cajas (marco de color)
    depth = st.profundidad_cajas(cfg, [(*a, 0.10, 0.10), (*b, 0.09, 0.09)], z_mesa=0.65)
    ruta = st.guardar_escena(tmp_path / "capturas", "dist1", depth)
    config = st.escribir_config(tmp_path / "config.json", cfg)
    return cfg, a, b, ruta, config, tmp_path / "resultados.csv"


def leer_csv(ruta: Path) -> list[dict]:
    with ruta.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_cli_distancia_con_y_sin_registro(escena_dist):
    cfg, a, b, ruta, config, csv_ruta = escena_dist
    respuesta = json.dumps([{"point": st.punto_gemini(a, cfg.color), "label": "caja A"},
                            {"point": st.punto_gemini(b, cfg.color), "label": "caja B"}])
    comunes = ["consultar", "--escena", str(ruta), "--tarea", "puntos", "--objeto", "las dos tapas",
               "--config", str(config), "--csv", str(csv_ruta), "--grupo", "G00-exp3",
               "--respuesta", respuesta]
    r1 = correr_cli(*comunes)
    r2 = correr_cli(*comunes, "--sin-registro")
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr

    filas = leer_csv(csv_ruta)
    assert len(filas) == 4 and {f["registro"] for f in filas} == {"si", "no"}
    con = [f for f in filas if f["registro"] == "si"]
    xyz = [np.array([float(f["x_m"]), float(f["y_m"]), float(f["z_m"])]) for f in con]
    d_real = np.linalg.norm(np.subtract(a, b))
    assert abs(np.linalg.norm(xyz[0] - xyz[1]) - d_real) < 0.005

    # La imagen con registro no se sobrescribe con la de la ablación (evidencia E6).
    pngs = sorted(p.name for p in ruta.parent.glob("dist1_puntos*_resultado.png"))
    assert any("_sinreg_" in p for p in pngs) and any("_sinreg_" not in p for p in pngs)


def test_cli_registra_json_invalido(escena_dist):
    _, _, _, ruta, config, csv_ruta = escena_dist
    r = correr_cli("consultar", "--escena", str(ruta), "--tarea", "propio", "--objeto", "x",
                   "--config", str(config), "--csv", str(csv_ruta), "--respuesta", "Lo siento, no puedo")
    assert r.returncode != 0
    filas = leer_csv(csv_ruta)
    assert len(filas) == 1 and filas[0]["label"] == "JSON_INVALIDO"


def test_cli_lista_vacia_es_abstencion(escena_dist):
    _, _, _, ruta, config, csv_ruta = escena_dist
    r = correr_cli("consultar", "--escena", str(ruta), "--tarea", "agarre", "--objeto", "la caja",
                   "--config", str(config), "--csv", str(csv_ruta), "--respuesta", "[]")
    assert r.returncode == 0, r.stderr
    filas = leer_csv(csv_ruta)
    assert len(filas) == 1 and filas[0]["label"] == "" and filas[0]["respuesta"] == "[]"


# --- TODO 5 con ROS 2 simulado ----------------------------------------------
def _msg(**campos):
    return types.SimpleNamespace(**campos)


def _instalar_ros_simulado(monkeypatch):
    def vec():
        return _msg(x=0.0, y=0.0, z=0.0)

    def point_stamped():
        return _msg(header=_msg(stamp=None, frame_id=""), point=vec())

    def transform_stamped():
        return _msg(header=_msg(stamp=None, frame_id=""), child_frame_id="",
                    transform=_msg(translation=vec(), rotation=_msg(x=0.0, y=0.0, z=0.0, w=0.0)))

    class Node:  # solo lo que importa el módulo
        pass

    modulos = {
        "rclpy": _msg(init=None, spin=None, ok=lambda: False, shutdown=None),
        "rclpy.node": _msg(Node=Node),
        "rclpy.qos": _msg(qos_profile_sensor_data=None),
        "geometry_msgs": _msg(), "geometry_msgs.msg": _msg(PointStamped=point_stamped,
                                                           TransformStamped=transform_stamped),
        "sensor_msgs": _msg(), "sensor_msgs.msg": _msg(CameraInfo=object, CompressedImage=object, Image=object),
        "std_msgs": _msg(), "std_msgs.msg": _msg(String=object),
        "tf2_ros": _msg(TransformBroadcaster=object),
    }
    for nombre, mod in modulos.items():
        monkeypatch.setitem(sys.modules, nombre, mod)
    sys.path.insert(0, str(PLANTILLA))
    monkeypatch.delitem(sys.modules, "embodied_ros2_nodo", raising=False)
    import embodied_ros2_nodo
    return embodied_ros2_nodo


def test_todo5_publica_point_stamped_y_tf(monkeypatch):
    mod = _instalar_ros_simulado(monkeypatch)
    nodo = mod.NodoEmbodied.__new__(mod.NodoEmbodied)   # sin __init__: no hay ROS real
    publicados, enviados = [], []
    nodo._pub_obj = _msg(publish=publicados.append)
    nodo._tf = _msg(sendTransform=enviados.extend)

    sello = object()
    localizados = [{"label": "a", "xyz_m": [0.1, -0.02, 0.6]},
                   {"label": "sin profundidad", "z_m": None},
                   {"label": "c", "xyz_m": [0.0, 0.05, 0.55]}]
    nodo._publicar(sello, "camera_color_frame", localizados)

    assert len(publicados) == 1
    p = publicados[0]
    assert p.header.stamp is sello and p.header.frame_id == "camera_color_frame"
    assert (p.point.x, p.point.y, p.point.z) == pytest.approx((0.1, -0.02, 0.6))

    assert [t.child_frame_id for t in enviados] == ["objetivo_gemini_0", "objetivo_gemini_2"]
    for t in enviados:
        assert t.header.frame_id == "camera_color_frame" and t.header.stamp is sello
        r = t.transform.rotation
        assert (r.x, r.y, r.z, r.w) == (0.0, 0.0, 0.0, 1.0)
    assert enviados[1].transform.translation.z == pytest.approx(0.55)
