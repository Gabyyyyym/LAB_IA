"""
Prueba del script de métricas (Fase 10) con un resultados.csv SINTÉTICO cuyas métricas se
conocen de antemano. Estos datos solo validan el código: no son resultados de la práctica.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import pytest

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
import analisis_lab_ia_01b as an  # noqa: E402
import embodied_comun as ec  # noqa: E402
import espacio_trabajo as et  # noqa: E402

W, H = 1920, 1080


def px(yx):
    """[y, x] 0-1000 -> (u, v) en 1920x1080, igual que la plantilla."""
    return ec.normalizado_a_pixel(yx, W, H)


def caja_px(b):
    return [round(c, 2) for c in ec.caja_a_pixeles(b, W, H)]


class Escritor:
    def __init__(self, ruta: Path):
        self.ruta, self.seg = ruta, 0

    def consulta(self, escena, tarea, grupo, dets=None, respuesta=None, label=None, modelo="gemini-robotics-er-2-preview",
                 thinking="low", registro="si", latencia=3.0, tokens=(1000, 50, 200), fecha=None):
        self.seg += 1
        dets = dets or []
        if respuesta is None:
            respuesta = json.dumps([{"point": d["point"], "label": d["label"]} for d in dets])
        comunes = {"fecha": fecha or f"2026-10-20T10:{self.seg // 60:02d}:{self.seg % 60:02d}", "grupo": grupo,
                   "opcion": "A-sin-ROS", "escena": escena, "tarea": tarea, "objeto": "x",
                   "modelo": modelo, "thinking": thinking, "registro": registro,
                   "latencia_s": 0.0 if modelo == "manual" else latencia, "tokens_entrada": tokens[0],
                   "tokens_salida": tokens[1], "tokens_pensamiento": tokens[2], "respuesta": respuesta}
        if label:                       # JSON_INVALIDO / ERROR_API
            ec.guardar_fila_csv(self.ruta, {**comunes, "label": label})
            return comunes["respuesta"]
        for d in dets or [{}]:
            u, v = px(d["point"]) if d else (None, None)
            xyz = d.get("xyz", [None, None, None]) if d else [None, None, None]
            ec.guardar_fila_csv(self.ruta, {**comunes, "label": d.get("label"), "punto_yx": d.get("point"),
                                            "u": u, "v": v, "z_m": xyz[2], "x_m": xyz[0], "y_m": xyz[1]})
        return respuesta


CAB_V = ["escena", "particion", "relacion", "objeto_esperado", "u_min", "v_min", "u_max", "v_max", "segura",
         "d_real_m", "nota"]
CAJA_A, CAJA_B = [300, 200, 500, 400], [300, 600, 600, 800]     # [ymin, xmin, ymax, xmax] 0-1000


def verdad_sintetica(ruta: Path):
    a, b = caja_px(CAJA_A), caja_px(CAJA_B)
    filas = [
        ["semantica1", "D", "seguridad", "caja libre", *a, "si", "", ""],
        ["semantica1p", "P", "seguridad", "caja libre", *a, "si", "", ""],
        ["semantica2", "D", "pertenencia", "celular", *b, "", "", ""],
        ["semantica2p", "P", "pertenencia", "celular", *b, "", "", ""],
        ["burger", "D", "relacional", "caja libre", *a, "", "", ""],
        ["semantica1", "-", "cajas", "caja de hamburguesa|caja", *a, "", "", ""],
        ["semantica1", "-", "cajas", "termo|botella", *b, "", "", ""],
        ["dist1", "-", "distancia", "tapa termo|tapa caja", "", "", "", "", "", "0.300", "centro"],
        ["dist2", "-", "distancia", "tapa termo|tapa caja", "", "", "", "", "", "0.300", "borde de la imagen"],
        ["altura1", "-", "altura", "mesa|tapa caja", "", "", "", "", "", "0.100", ""],
        ["agarre_s1", "-", "agarre", "caja", *a, "si", "", ""],
        ["agarre_s2", "-", "agarre", "caja", *a, "si", "", ""],
        ["agarre_i1", "-", "agarre", "caja", *a, "no", "", ""],
        ["regla", "-", "calibracion", "hoja A4", "", "", "", "", "", "0.297", ""],
    ]
    with ruta.open("w", newline="", encoding="utf-8") as fcsv:
        w = csv.writer(fcsv)
        w.writerow(CAB_V)
        w.writerows(filas)


DENTRO = [450, 350]          # [y, x] dentro de CAJA_A
FUERA = [900, 900]
EN_B = [450, 700]


@pytest.fixture(scope="module")
def resumen(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("analisis")
    res, ver = tmp / "resultados.csv", tmp / "verdad_terreno.csv"
    verdad_sintetica(ver)
    e = Escritor(res)

    # Tabla 5: iteración 0 del prompt (1 de 2 aciertos en D).
    e.consulta("semantica1", "propio", "G03-dev0", [{"point": DENTRO, "label": "caja"}])
    e.consulta("semantica2", "propio", "G03-dev0", [{"point": FUERA, "label": "celular"}])
    # Exp. 1: seguridad D 2/3, P 1/3 (+1 JSON inválido); pertenencia D 3/3, P 0/3.
    for p in (DENTRO, DENTRO, FUERA):
        e.consulta("semantica1", "propio", "G03-exp1", [{"point": p, "label": "caja libre"}], latencia=2.0)
    e.consulta("semantica1p", "propio", "G03-exp1", [{"point": DENTRO, "label": "caja libre"}], latencia=4.0)
    e.consulta("semantica1p", "propio", "G03-exp1", [{"point": FUERA, "label": "caja"}], latencia=4.0)
    e.consulta("semantica1p", "propio", "G03-exp1", respuesta="No puedo", label="JSON_INVALIDO")
    for _ in range(3):
        e.consulta("semantica2", "propio", "G03-exp1", [{"point": EN_B, "label": "celular"}])
        e.consulta("semantica2p", "propio", "G03-exp1", [{"point": FUERA, "label": "celular"}])
    e.consulta("burger", "puntos", "G03-exp1", [{"point": FUERA, "label": "caja"}])          # ingenuo: falla
    e.consulta("burger", "propio", "G03-exp1", [{"point": DENTRO, "label": "caja libre"}])   # propio: acierta

    # Exp. 2: rep 1 perfecta; rep 2 con la caja B corrida (IoU < 0.5) y un FP extra.
    cajas1 = [{"box_2d": CAJA_A, "label": "caja de hamburguesa"}, {"box_2d": CAJA_B, "label": "termo"}]
    cajas2 = [{"box_2d": CAJA_A, "label": "caja"}, {"box_2d": [300, 750, 600, 950], "label": "termo"},
              {"box_2d": [800, 800, 900, 900], "label": "celular"}]
    for cj in (cajas1, cajas2):
        dets = [{"point": [(b["box_2d"][0] + b["box_2d"][2]) / 2, (b["box_2d"][1] + b["box_2d"][3]) / 2],
                 "label": b["label"]} for b in cj]
        e.consulta("semantica1", "cajas", "G03-exp2", dets, respuesta=json.dumps(cj))

    # Exp. 3: con registro e = +10 y −10 mm; sin registro (misma respuesta) e = +30 y −30 mm.
    for escena in ("dist1", "dist2"):
        for d_con, d_sin in ((0.310, 0.330), (0.290, 0.270)):
            dets = [{"point": [500, 400], "label": "tapa termo", "xyz": [0.0, 0.0, 0.55]},
                    {"point": [500, 600], "label": "tapa caja", "xyz": [d_con, 0.0, 0.55]}]
            resp = e.consulta(escena, "puntos", "G03-exp3", dets)
            dets[1]["xyz"] = [d_sin, 0.0, 0.55]
            e.consulta(escena, "puntos", "G03-exp3", dets, respuesta=resp, modelo="manual", registro="no")
    e.consulta("altura1", "puntos", "G03-exp3", [{"point": [500, 300], "label": "mesa", "xyz": [0, 0, 0.65]},
                                                 {"point": [500, 500], "label": "tapa", "xyz": [0.1, 0, 0.555]}])

    # Exp. 4: low (2 y 4 s, 2/2 aciertos), high (10 y 12 s, 1/2), minimal rechazado por la API.
    for lat in (2.0, 4.0):
        e.consulta("semantica1p", "propio", "G03-exp4", [{"point": DENTRO, "label": "caja"}], latencia=lat,
                   tokens=(1000, 50, 100))
    for lat, p in ((10.0, DENTRO), (12.0, FUERA)):
        e.consulta("semantica1p", "propio", "G03-exp4", [{"point": p, "label": "caja"}], thinking="high",
                   latencia=lat, tokens=(1000, 50, 900))
    e.consulta("semantica1p", "propio", "G03-exp4", respuesta="ClientError: 400 thinking_level", label="ERROR_API",
               thinking="minimal")

    # Exp. 5: agarre (TP=1, FN=1, FP=1, TN=1), plan válido y dos casos de éxito (1 correcto).
    e.consulta("agarre_s1", "agarre", "G03-exp5", [{"point": DENTRO, "label": "caja", "xyz": [0.0, 0.0, 0.55]}])
    e.consulta("agarre_s2", "agarre", "G03-exp5", respuesta="[]")
    e.consulta("agarre_i1", "agarre", "G03-exp5", respuesta="[]")
    e.consulta("agarre_i2", "agarre", "G03-exp5", [{"point": DENTRO, "label": "caja", "xyz": [0.0, 0.0, 0.55]}])
    plan = {"objetos": [{"point": DENTRO, "label": "caja libre"}, {"point": EN_B, "label": "bandeja"}],
            "pasos": [{"accion": "mover_sobre", "objetivo": "caja libre"}, {"accion": "bajar", "objetivo": None},
                      {"accion": "cerrar_pinza", "objetivo": None}, {"accion": "subir", "objetivo": None},
                      {"accion": "mover_sobre", "objetivo": "bandeja"}, {"accion": "bajar", "objetivo": None},
                      {"accion": "abrir_pinza", "objetivo": None}, {"accion": "subir", "objetivo": None}]}
    e.consulta("burger", "plan", "G03-exp5", [{"point": DENTRO, "label": "caja libre", "xyz": [0.0, 0.0, 0.55]},
                                              {"point": EN_B, "label": "bandeja", "xyz": [0.1, 0.0, 0.63]}],
               respuesta=json.dumps(plan))
    ec.guardar_fila_csv(res, {"fecha": "2026-10-20T11:00:00", "grupo": "G03-exp5", "escena": "exito_antes1->exito_bien1",
                              "tarea": "exito", "modelo": "gemini", "latencia_s": 5,
                              "respuesta": '{"exito": true, "confianza": 0.9, "evidencia": "dentro"}'})
    ec.guardar_fila_csv(res, {"fecha": "2026-10-20T11:00:10", "grupo": "G03-exp5", "escena": "exito_antes2->exito_mal1",
                              "tarea": "exito", "modelo": "gemini", "latencia_s": 5,
                              "respuesta": '```json\n{"exito": true, "confianza": 0.6, "evidencia": "?"}\n```'})

    # Verificación de la calibración: 0.300 m frente a 0.297 m (+1.01 %).
    e.consulta("regla", "puntos", "G03-calib", [{"point": [300, 200], "label": "esq izq", "xyz": [-0.15, -0.1, 0.6]},
                                                {"point": [300, 800], "label": "esq der", "xyz": [0.15, -0.1, 0.6]}])

    espacio = tmp / "espacio_trabajo.json"
    lim = et.limites_desde_puntos([(-0.2, -0.12, 0.65), (0.2, -0.12, 0.65), (0.2, 0.12, 0.65), (-0.2, 0.12, 0.65)],
                                  h_max=0.2)
    espacio.write_text(json.dumps(lim), encoding="utf-8")
    salida = tmp / "analisis"
    r = an.analizar(res, ver, tmp / "capturas", tmp / "no_existe.json", espacio, salida)
    r["_salida"] = salida
    return r


def test_tabla6_particion(resumen):
    t6 = resumen["tabla6"]
    # D: seguridad 2/3 + pertenencia 3/3 + burger propio 1/1 = 6/7. P: 1/3 + 0/3 = 1/6.
    assert (t6["k_D"], t6["N_D"], t6["k_P"], t6["N_P"]) == (6, 7, 1, 6)
    assert t6["brecha"] == pytest.approx(6 / 7 - 1 / 6)
    assert t6["brecha_IC"][0] > 0          # con estos datos la brecha sí es declarable


def test_tabla7_cajas(resumen):
    t7 = resumen["tabla7"]
    assert (t7["TP"], t7["FP"], t7["FN"]) == (3, 2, 1)
    assert t7["precision"] == pytest.approx(0.6) and t7["recall"] == pytest.approx(0.75)
    assert t7["iou_por_objeto"]["semantica1|caja de hamburguesa"] == pytest.approx(1.0)


def test_tabla8_distancias_y_ablacion(resumen):
    t8 = resumen["tabla8"]
    assert t8["RMSE_con_mm"] == pytest.approx(10.0, abs=1e-6)
    assert t8["RMSE_sin_mm"] == pytest.approx(30.0, abs=1e-6)
    assert t8["N_con"] == 4 and t8["N_sin"] == 4
    assert t8["ablacion_pareada"] == "4/4"
    assert t8["altura"][0]["error_mm"] == pytest.approx(-5.0, abs=1e-6)


def test_tabla9_thinking(resumen):
    t9 = resumen["tabla9"]
    assert t9["low"]["lat_media_s"] == pytest.approx(3.0) and t9["low"]["aciertos"] == 2
    assert t9["high"]["lat_media_s"] == pytest.approx(11.0) and t9["high"]["aciertos"] == 1
    assert t9["low"]["decisiones_min"] == pytest.approx(20.0)
    assert t9["dif_lat_supera_sd"] is True


def test_tabla10_agarre_plan_exito(resumen):
    t10 = resumen["tabla10"]
    assert (t10["TP"], t10["FP"], t10["FN"], t10["TN"]) == (1, 1, 1, 1)
    assert t10["recall"] == pytest.approx(0.5)
    assert t10["exito_acc"] == pytest.approx(0.5) and t10["exito_N"] == 2
    texto = (resumen["_salida"] / "tablas.md").read_text(encoding="utf-8")
    assert "Plan: secuencia coherente (burger) | cumple" in texto
    assert "minimal" in texto and "no (error de la API)" in texto


def test_tabla4_verificacion(resumen):
    assert resumen["tabla4"]["error_pct"] == pytest.approx(100 * (0.3 - 0.297) / 0.297)


def test_salidas(resumen):
    s = resumen["_salida"]
    for nombre in ("tablas.md", "resumen.json", "tabla_06_semantica.csv", "tabla_08_distancias.csv",
                   "tabla_10a_confusion.csv", "tabla_11_resumen.csv"):
        assert (s / nombre).exists(), nombre
    assert not math.isnan(json.loads((s / "resumen.json").read_text(encoding="utf-8"))["tabla7"]["mIoU"])


def test_etiquetas():
    assert an.etiqueta_corresponde("Cajas de hamburguesa", "caja de hamburguesa|caja")
    assert an.etiqueta_corresponde("llaves", "llave")
    assert not an.etiqueta_corresponde("celular", "termo|botella")
