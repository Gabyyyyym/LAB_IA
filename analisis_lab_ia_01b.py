#!/usr/bin/env python3
"""
Fase 10 — Cálculo reproducible de métricas e inferencias (RAE 6.2). Script del grupo.

Lee resultados/resultados.csv y verdad_terreno.csv y regenera, sin pasos manuales, las
Tablas 5 a 11 de la guía (más las Tablas 3 y 4 si existen capturas/*_meta.json y la
calibración). Salidas en analisis/: tablas.md, un CSV por tabla, resumen.json y figuras/.

    python3 analisis_lab_ia_01b.py
    python3 analisis_lab_ia_01b.py --resultados resultados/resultados.csv --verdad verdad_terreno.csv

Convención de la columna grupo (PROTOCOLO_GRUPO.md): G03-dev0, G03-dev1 ... = iteraciones del
prompt sobre escenas D (Tabla 5); G03-exp1 ... G03-exp5 = experimentos; G03-calib =
verificación de la calibración; G03-zona = puntos de la zona útil. Sin etiqueta, el
experimento se infiere de la escena y la tarea (y se avisa).

Reglas (todas declaradas en la Fase 1, antes de consultar):
* Acierto de señalamiento: el primer punto (u, v) cae dentro de la caja de verdad del objeto
  esperado. Un JSON inválido o una lista vacía cuentan como fallo.
* Cajas: emparejamiento voraz por IoU (una vez cada caja); TP si IoU >= 0.5 y la etiqueta
  corresponde al objeto.
* Agarre: positivo = "escena insegura, el modelo se abstiene". JSON inválido = fallo.
* Diferencias entre condiciones: se declaran solo si el intervalo del 95 % de la diferencia
  excluye el cero (proporciones) o si superan la dispersión entre repeticiones (continuas).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

import numpy as np

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import embodied_comun as ec  # noqa: E402
import espacio_trabajo as et  # noqa: E402

ANCHO_DEF, ALTO_DEF = 1920, 1080
RELACIONES = {"seguridad": "Seguridad", "pertenencia": "Pertenencia", "estado": "Estado",
              "contencion": "Contención", "relacional": "Relacional"}
NAN = float("nan")


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def sin_acentos(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def num(s):
    try:
        x = float(s)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def media(xs):
    xs = [x for x in xs if x is not None and not math.isnan(x)]
    return float(np.mean(xs)) if xs else NAN


def desv(xs):
    xs = [x for x in xs if x is not None and not math.isnan(x)]
    return float(np.std(xs, ddof=1)) if len(xs) >= 2 else NAN


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return NAN, NAN
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def dif_proporciones(k1, n1, k2, n2, z=1.96):
    """Diferencia p1 - p2 con intervalo de Newcombe (híbrido de Wilson)."""
    if not n1 or not n2:
        return NAN, NAN, NAN
    p1, p2 = k1 / n1, k2 / n2
    l1, u1 = wilson(k1, n1, z)
    l2, u2 = wilson(k2, n2, z)
    d = p1 - p2
    return d, d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2), d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)


def f(x, nd=3, unidad=""):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:.{nd}f}{unidad}"


def pm(m, s, nd=3):
    if math.isnan(m):
        return "—"
    return f"{m:.{nd}f} ± {s:.{nd}f}" if not math.isnan(s) else f"{m:.{nd}f}"


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else NAN
    r = tp / (tp + fn) if tp + fn else NAN
    f1 = 2 * p * r / (p + r) if (p + r) and not (math.isnan(p) or math.isnan(r)) else NAN
    return p, r, f1


STOP = {"de", "la", "el", "los", "las", "del", "con", "una", "uno", "que", "the", "and", "sobre", "para"}


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9ñ]+", sin_acentos(s)) if len(t) >= 3 and t not in STOP}


def etiqueta_corresponde(pred: str, esperado: str) -> bool:
    """True si la etiqueta predicha comparte una palabra con alguna alternativa (separadas por |).
    Dos palabras coinciden si son iguales o comparten un prefijo de 4 letras (caja/cajas, llave/llaves)."""
    tp = _tokens(pred)
    for alt in str(esperado).split("|"):
        for a in _tokens(alt):
            for b in tp:
                if a == b or (len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4]):
                    return True
    return False


def iou(a, b) -> float:
    au0, av0, au1, av1 = min(a[0], a[2]), min(a[1], a[3]), max(a[0], a[2]), max(a[1], a[3])
    bu0, bv0, bu1, bv1 = min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3])
    iw, ih = max(0.0, min(au1, bu1) - max(au0, bu0)), max(0.0, min(av1, bv1) - max(av0, bv0))
    inter = iw * ih
    union = (au1 - au0) * (av1 - av0) + (bu1 - bu0) * (bv1 - bv0) - inter
    return inter / union if union > 0 else 0.0


def tabla_md(cabecera: list[str], filas: list[list]) -> str:
    lineas = ["| " + " | ".join(cabecera) + " |", "|" + "---|" * len(cabecera)]
    lineas += ["| " + " | ".join(str(c) for c in fila) + " |" for fila in filas]
    return "\n".join(lineas)


# ---------------------------------------------------------------------------
# Carga de datos
# ---------------------------------------------------------------------------
@dataclass
class Consulta:
    fila0: int                       # número de la primera fila en el CSV (trazabilidad)
    fecha: str
    grupo: str
    etiqueta: str
    escena: str
    tarea: str
    objeto: str
    modelo: str
    thinking: str
    registro: str
    latencia_s: float | None
    tokens: dict
    respuesta: str
    detecciones: list = field(default_factory=list)
    estado: str = "ok"               # ok | vacia | json_invalido | error_api
    datos: object = None
    inferida: bool = False

    @property
    def manual(self) -> bool:
        return self.modelo == "manual"

    @property
    def filas(self) -> str:
        n = max(1, len(self.detecciones))
        return f"{self.fila0}" if n == 1 else f"{self.fila0}-{self.fila0 + n - 1}"


CLAVE = ("fecha", "grupo", "escena", "tarea", "objeto", "modelo", "thinking", "registro", "latencia_s", "respuesta")


def cargar_consultas(ruta: Path) -> list[Consulta]:
    if not Path(ruta).exists():
        return []
    with Path(ruta).open(encoding="utf-8") as fcsv:
        filas = list(csv.DictReader(fcsv))
    consultas: list[Consulta] = []
    previa = None
    for i, r in enumerate(filas, start=2):     # fila 1 = cabecera
        clave = tuple(r.get(k, "") for k in CLAVE)
        if clave != previa:
            grupo_base, _, etiqueta = (r.get("grupo") or "").partition("-")
            consultas.append(Consulta(
                fila0=i, fecha=r["fecha"], grupo=grupo_base, etiqueta=etiqueta.lower(), escena=r["escena"],
                tarea=r["tarea"], objeto=r.get("objeto", ""), modelo=r.get("modelo", ""),
                thinking=r.get("thinking", ""), registro=r.get("registro", "si") or "si",
                latencia_s=num(r.get("latencia_s")),
                tokens={k: num(r.get(f"tokens_{k}")) for k in ("entrada", "salida", "pensamiento")},
                respuesta=r.get("respuesta", "")))
            previa = clave
        c = consultas[-1]
        label = r.get("label") or ""
        if label == "ERROR_API":
            c.estado = "error_api"
        elif label == "JSON_INVALIDO":
            c.estado = "json_invalido"
        elif label:
            xyz = [num(r.get("x_m")), num(r.get("y_m")), num(r.get("z_m"))]
            c.detecciones.append({"label": label, "u": num(r.get("u")), "v": num(r.get("v")),
                                  "xyz": None if None in xyz else xyz})
    for c in consultas:
        if c.estado in ("error_api", "json_invalido"):
            continue
        try:
            c.datos = ec.extraer_json(c.respuesta)
        except (json.JSONDecodeError, ValueError):
            c.estado = "json_invalido"
            continue
        if not c.detecciones and c.tarea not in ("exito",):
            c.estado = "vacia"
    return consultas


def cargar_verdad(ruta: Path) -> list[dict]:
    with Path(ruta).open(encoding="utf-8") as fcsv:
        filas = [r for r in csv.DictReader(fcsv) if (r.get("escena") or "").strip() and not r["escena"].startswith("#")]
    for r in filas:
        r["relacion_n"] = sin_acentos(r.get("relacion", ""))
        r["caja"] = None
        cs = [num(r.get(k)) for k in ("u_min", "v_min", "u_max", "v_max")]
        if None not in cs:
            r["caja"] = cs
        posicion = sin_acentos(r.get("posicion", "") or "")
        nota = sin_acentos(r.get("nota", "") or "")
        r["posicion"] = posicion or ("borde" if "borde" in nota else "centro" if "centro" in nota else "")
    return filas


def tamano_imagen(capturas: Path, escena: str) -> tuple[int, int]:
    meta = capturas / f"{escena}_meta.json"
    if meta.exists():
        m = json.loads(meta.read_text(encoding="utf-8"))
        return int(m["color"]["ancho"]), int(m["color"]["alto"])
    return ANCHO_DEF, ALTO_DEF


# ---------------------------------------------------------------------------
# Clasificación por experimento
# ---------------------------------------------------------------------------
def inferir_etiquetas(consultas, verdad, avisos):
    por_escena = defaultdict(set)
    for v in verdad:
        por_escena[v["escena"]].add(v["relacion_n"])
    for c in consultas:
        if c.etiqueta:
            continue
        rel = por_escena.get(c.escena, set())
        e = c.escena.lower()
        if c.tarea == "cajas":
            c.etiqueta = "exp2"
        elif c.tarea in ("agarre", "plan", "exito"):
            c.etiqueta = "exp5"
        elif "distancia" in rel or "altura" in rel or e.startswith("dist"):
            c.etiqueta = "exp3"
        elif e == "regla":
            c.etiqueta = "calib" if len(c.detecciones) == 2 else "zona"
        elif rel & set(RELACIONES) and c.tarea in ("propio", "puntos"):
            c.etiqueta = "exp1" if c.thinking == "low" else "exp4"
        else:
            c.etiqueta = "otro"
        c.inferida = True
    n = sum(c.inferida for c in consultas)
    if n:
        avisos.append(f"{n} consulta(s) sin etiqueta en la columna grupo: experimento inferido por escena/tarea.")


def verdad_semantica(verdad, escena):
    return [v for v in verdad if v["escena"] == escena and v["relacion_n"] in RELACIONES]


def acierto_senalamiento(c: Consulta, cajas_verdad) -> bool:
    if c.estado != "ok" or not cajas_verdad:
        return False
    d = c.detecciones[0]
    if d["u"] is None or d["v"] is None:
        return False
    return any(v["caja"] and v["caja"][0] <= d["u"] <= v["caja"][2] and v["caja"][1] <= d["v"] <= v["caja"][3]
               for v in cajas_verdad)


def latencias(cs):
    return [c.latencia_s for c in cs if not c.manual and c.estado != "error_api" and c.latencia_s is not None]


# ---------------------------------------------------------------------------
# Tablas
# ---------------------------------------------------------------------------
def tabla3(capturas: Path):
    filas = []
    for meta in sorted(capturas.glob("*_meta.json")):
        m = json.loads(meta.read_text(encoding="utf-8"))
        p = m.get("profundidad", {})
        pct = p.get("pixeles_validos_pct")
        filas.append([m.get("escena", meta.stem), f"{m['color']['ancho']}×{m['color']['alto']}",
                      f(pct, 1, " %") + (" ⚠ < 60 %" if pct is not None and pct < 60 else ""),
                      f(m.get("desfase_color_profundidad_ms"), 1), m.get("nota", "")])
    return ["Escena", "Color", "% píxeles de profundidad válidos", "Desfase color-prof. (ms)", "Nota"], filas


def tabla4(consultas, verdad, config: Path):
    filas = []
    if config.exists():
        datos = json.loads(config.read_text(encoding="utf-8"))
        for res, k in datos.get("color", {}).items():
            fov = 2 * math.degrees(math.atan(k["ancho"] / (2 * k["fx"])))
            filas += [[f"Resolución de color ({res})", res, k.get("fuente", "")],
                      ["f_x / f_y (px)", f"{k['fx']:.1f} / {k['fy']:.1f}", k.get("fuente", "")],
                      ["c_x / c_y (px)", f"{k['cx']:.1f} / {k['cy']:.1f}", k.get("fuente", "")],
                      ["Campo de visión horizontal (°)", f"{fov:.1f}", "2·atan(W / 2f_x)"]]
    d_ref = next((num(v.get("d_real_m")) for v in verdad if v["relacion_n"] == "calibracion"), None) or 0.297
    cal = [c for c in consultas if c.etiqueta == "calib" and c.estado == "ok"]
    resumen = {}
    for c in cal:
        xyz = [d["xyz"] for d in c.detecciones[:2]]
        if len(xyz) == 2 and None not in xyz:
            d = float(np.linalg.norm(np.subtract(*xyz)))
            err = 100 * (d - d_ref) / d_ref
            filas += [["Distancia 3D entre esquinas de la hoja (m)", f"{d:.4f}", f"resultados.csv fila {c.filas}"],
                      [f"Error de verificación respecto a {d_ref} m (%)", f"{err:+.2f}" + (" ⚠ > 5 %" if abs(err) > 5 else ""),
                       c.fecha]]
            resumen = {"d_esquinas_m": d, "error_pct": err}
    return ["Parámetro", "Valor", "Fuente"], filas, resumen


def tabla5(consultas, verdad):
    it = defaultdict(list)
    for c in consultas:
        m = re.fullmatch(r"dev(\d+)", c.etiqueta)
        if m:
            it[int(m.group(1))].append(c)
    filas = []
    for k in sorted(it):
        cs = it[k]
        aciertos = sum(acierto_senalamiento(c, verdad_semantica(verdad, c.escena)) for c in cs)
        inval = sum(c.estado in ("json_invalido", "error_api") for c in cs)
        filas.append([k, "(describir)", "(falla observada)", f"{aciertos} / {len(cs)}", inval])
    return ["Iteración", "Cambio en el prompt", "Motivo (falla observada)", "Aciertos en D", "JSON inválidos"], filas


def tabla6(consultas, verdad, avisos):
    exp1 = [c for c in consultas if c.etiqueta == "exp1"]
    celdas = defaultdict(lambda: {"D": [], "P": []})
    for c in exp1:
        vs = verdad_semantica(verdad, c.escena)
        if not vs:
            avisos.append(f"Exp. 1: la escena {c.escena} no tiene verdad de terreno semántica.")
            continue
        rel = RELACIONES[vs[0]["relacion_n"]]
        part = (vs[0].get("particion") or "").strip().upper()[:1] or "D"
        if rel == "Relacional":
            rel = "Relacional (prompt ingenuo)" if c.tarea == "puntos" else "Relacional (prompt propio)"
        celdas[rel][part].append(c)
    orden = ["Seguridad", "Pertenencia", "Estado", "Contención", "Relacional (prompt propio)", "Relacional (prompt ingenuo)"]
    filas, tot = [], {"D": [0, 0], "P": [0, 0]}
    resumen = {}
    for rel in orden:
        if rel not in celdas:
            continue
        fila = [rel]
        for part in ("D", "P"):
            cs = celdas[rel][part]
            k = sum(acierto_senalamiento(c, verdad_semantica(verdad, c.escena)) for c in cs)
            fila.append(f"{k} / {len(cs)}" if cs else "—")
            if rel != "Relacional (prompt ingenuo)":
                tot[part][0] += k
                tot[part][1] += len(cs)
            resumen[f"{rel}|{part}"] = (k, len(cs))
            if cs and len(cs) < 3:
                avisos.append(f"Exp. 1: {rel} {part} tiene {len(cs)} repeticiones (mínimo 3).")
        todas = celdas[rel]["D"] + celdas[rel]["P"]
        lat = latencias(todas)
        fila += [sum(c.estado in ("json_invalido", "error_api") for c in todas), pm(media(lat), desv(lat), 2),
                 "(describir)", ", ".join(c.filas for c in todas)]
        filas.append(fila)
    (kd, nd), (kp, np_) = tot["D"], tot["P"]
    d, lo, hi = dif_proporciones(kd, nd, kp, np_)
    acc_d, acc_p = (kd / nd if nd else NAN), (kp / np_ if np_ else NAN)
    ld, ud = wilson(kd, nd)
    lp, up = wilson(kp, np_)
    sig = "" if math.isnan(lo) else (" (IC95 excluye 0: diferencia declarable)" if lo > 0 or hi < 0
                                     else " (IC95 incluye 0: no se declara diferencia)")
    filas.append(["Accuracy global (sin ingenuo)", f"D: {f(acc_d, 2)} [{f(ld, 2)}, {f(ud, 2)}] (N={nd})",
                  f"P: {f(acc_p, 2)} [{f(lp, 2)}, {f(up, 2)}] (N={np_})", "", "",
                  f"Brecha D − P: {f(d, 2)} [{f(lo, 2)}, {f(hi, 2)}]{sig}", ""])
    cab = ["Relación", "Aciertos D", "Aciertos P", "JSON inválidos (D + P)", "Latencia media ± σ (s)",
           "Observación del error", "Filas CSV"]
    return cab, filas, {"acc_D": acc_d, "acc_P": acc_p, "k_D": kd, "k_P": kp, "N_D": nd, "N_P": np_,
                        "IC_D": [ld, ud], "IC_P": [lp, up],
                        "brecha": d, "brecha_IC": [lo, hi], "celdas": resumen}


def emparejar(cajas_v, preds):
    """Voraz por IoU descendente; cada caja se usa una sola vez. Devuelve {i_verdad: (j_pred, iou)}."""
    pares = sorted(((iou(v["caja"], p["caja"]), i, j) for i, v in enumerate(cajas_v) for j, p in enumerate(preds)),
                   reverse=True)
    usados_v, usados_p, res = set(), set(), {}
    for val, i, j in pares:
        if val <= 0 or i in usados_v or j in usados_p:
            continue
        usados_v.add(i)
        usados_p.add(j)
        res[i] = (j, val)
    return res


def tabla7(consultas, verdad, capturas, avisos):
    exp2 = [c for c in consultas if c.etiqueta == "exp2" and c.tarea == "cajas"]
    filas, tp_t, fp_t, fn_t, miou = [], 0, 0, 0, []
    por_objeto = {}
    for escena in dict.fromkeys(c.escena for c in exp2):
        cv = [v for v in verdad if v["escena"] == escena and v["relacion_n"] == "cajas" and v["caja"]]
        if not cv:
            avisos.append(f"Exp. 2: la escena {escena} no tiene cajas de verdad (relacion = cajas).")
            continue
        ancho, alto = tamano_imagen(capturas, escena)
        reps = [c for c in exp2 if c.escena == escena]
        if len(reps) < 5:
            avisos.append(f"Exp. 2: {escena} tiene {len(reps)} repeticiones (mínimo 5).")
        stats = {i: {"iou": [], "cajas": []} for i in range(len(cv))}
        tp_e = fp_e = fn_e = 0
        for c in reps:
            preds = []
            if c.estado == "ok" and isinstance(c.datos, (list, dict)):
                for item in (c.datos if isinstance(c.datos, list) else [c.datos]):
                    if isinstance(item, dict) and "box_2d" in item:
                        try:
                            preds.append({"label": str(item.get("label", "")),
                                          "caja": list(ec.caja_a_pixeles(item["box_2d"], ancho, alto))})
                        except (ValueError, TypeError):
                            pass
            par = emparejar(cv, preds)
            tp_rep = 0
            for i, v in enumerate(cv):
                if i in par:
                    j, val = par[i]
                    stats[i]["iou"].append(val)
                    stats[i]["cajas"].append(preds[j]["caja"])
                    if val >= 0.5 and etiqueta_corresponde(preds[j]["label"], v["objeto_esperado"]):
                        tp_rep += 1
                else:
                    stats[i]["iou"].append(0.0)
            tp_e += tp_rep
            fp_e += len(preds) - tp_rep
            fn_e += len(cv) - tp_rep
        for i, v in enumerate(cv):
            ious, cajas = stats[i]["iou"], stats[i]["cajas"]
            sig_coord = float(np.mean(np.std(np.array(cajas), axis=0, ddof=1))) if len(cajas) >= 2 else NAN
            iou_rep = [iou(a, b) for a, b in combinations(cajas, 2)]
            nombre = v["objeto_esperado"].split("|")[0] + (f" ({v['nota']})" if (v.get("nota") or "").strip() else "")
            por_objeto[f"{escena}|{nombre}"] = media(ious)
            miou.append(media(ious))
            filas.append([escena, nombre, pm(media(ious), desv(ious)),
                          f(sig_coord, 1), f(media(iou_rep)), ""])
        filas[-len(cv)][-1] = f"{tp_e} / {fp_e} / {fn_e}"
        tp_t, fp_t, fn_t = tp_t + tp_e, fp_t + fp_e, fn_t + fn_e
    p, r, _ = prf(tp_t, fp_t, fn_t)
    m = media(miou)
    filas.append(["Global", "", f"mIoU: {f(m)}", "", "", f"Precision: {f(p)} · Recall: {f(r)}"])
    cab = ["Escena", "Objeto", "IoU medio ± σ con la verdad", "σ de coordenadas (px)",
           "IoU medio entre repeticiones", "TP / FP / FN (IoU ≥ 0.5)"]
    return cab, filas, {"mIoU": m, "precision": p, "recall": r, "TP": tp_t, "FP": fp_t, "FN": fn_t,
                        "iou_por_objeto": por_objeto}


def distancia_consulta(c: Consulta):
    xyz = [d["xyz"] for d in c.detecciones[:2]]
    if c.estado != "ok" or len(xyz) < 2 or None in xyz:
        return None
    return float(np.linalg.norm(np.subtract(*xyz)))


def tabla8(consultas, verdad, avisos):
    gt = {v["escena"]: v for v in verdad if v["relacion_n"] == "distancia"}
    exp3 = [c for c in consultas if c.etiqueta == "exp3" and c.escena in gt]
    filas, err = [], {"si": [], "no": []}
    detalle = []
    for escena in sorted(gt, key=lambda s: (len(s), s)):
        d_real = num(gt[escena].get("d_real_m"))
        fila = [escena, gt[escena]["posicion"] or "—", f(d_real)]
        for reg in ("si", "no"):
            cs = [c for c in exp3 if c.escena == escena and c.registro == reg]
            ds = [distancia_consulta(c) for c in cs]
            sin_dato = sum(d is None for d in ds)
            es = [(d - d_real) * 1000 for d in ds if d is not None and d_real is not None]
            err[reg] += es
            detalle += [{"escena": escena, "posicion": gt[escena]["posicion"], "registro": reg, "e_mm": e} for e in es]
            fila += [pm(media([d for d in ds if d is not None]), desv([d for d in ds if d is not None]), 3),
                     pm(media(es), desv(es), 1) + (f" ({sin_dato} sin prof.)" if sin_dato else "")]
            if len(cs) < 2:
                avisos.append(f"Exp. 3: {escena} registro={reg} tiene {len(cs)} consultas (mínimo 2).")
        filas.append(fila)

    def mae_rmse(es):
        return (float(np.mean(np.abs(es))), float(np.sqrt(np.mean(np.square(es))))) if es else (NAN, NAN)

    (mae_si, rmse_si), (mae_no, rmse_no) = mae_rmse(err["si"]), mae_rmse(err["no"])
    filas.append([f"MAE / RMSE (N = {len(err['si'])} / {len(err['no'])})", "", "", "",
                  f"{f(mae_si, 1)} / {f(rmse_si, 1)}", "", f"{f(mae_no, 1)} / {f(rmse_no, 1)}"])

    # Control de la ablación: cada consulta sin registro debe reutilizar la respuesta de una con registro.
    con = {(c.escena, c.respuesta) for c in exp3 if c.registro == "si"}
    sin = [c for c in exp3 if c.registro == "no"]
    pareadas = sum((c.escena, c.respuesta) in con for c in sin)
    if sin and pareadas < len(sin):
        avisos.append(f"Exp. 3: solo {pareadas}/{len(sin)} consultas sin registro reutilizan exactamente una "
                      "respuesta con registro: la ablación no está controlada.")
    cab = ["Config.", "Posición en la imagen", "d_real (m)", "d_est con registro (m)", "e con registro (mm)",
           "d_est sin registro (m)", "e sin registro (mm)"]
    resumen = {"MAE_con_mm": mae_si, "RMSE_con_mm": rmse_si, "MAE_sin_mm": mae_no, "RMSE_sin_mm": rmse_no,
               "N_con": len(err["si"]), "N_sin": len(err["no"]), "ablacion_pareada": f"{pareadas}/{len(sin)}"}

    # Altura de un objeto: primer punto = mesa, segundo = tapa del objeto.
    alt = []
    for v in verdad:
        if v["relacion_n"] != "altura":
            continue
        for c in consultas:
            if c.escena == v["escena"] and c.estado == "ok" and c.registro == "si" and len(c.detecciones) >= 2:
                a, b = c.detecciones[0]["xyz"], c.detecciones[1]["xyz"]
                if a and b:
                    h, h_real = a[2] - b[2], num(v.get("d_real_m"))
                    alt.append({"escena": v["escena"], "h_real_m": h_real, "h_est_m": h,
                                "error_mm": (h - h_real) * 1000 if h_real is not None else NAN, "filas": c.filas})
    resumen["altura"] = alt
    return cab, filas, resumen, detalle


def tabla9(consultas, verdad, avisos):
    exp4 = [c for c in consultas if c.etiqueta == "exp4"]
    filas, resumen = [], {}
    for nivel in ("low", "high", "minimal", "medium"):
        cs = [c for c in exp4 if c.thinking == nivel]
        if not cs:
            filas.append([nivel, "—", "—", "—", "—", "—", "sin consultas"])
            continue
        k = sum(acierto_senalamiento(c, verdad_semantica(verdad, c.escena)) for c in cs)
        validas = [c for c in cs if c.estado != "error_api"]
        lat = latencias(cs)
        m, s = media(lat), desv(lat)
        tot = [sum(v for v in c.tokens.values() if v is not None) for c in validas if not c.manual]
        pen = [c.tokens["pensamiento"] for c in validas if c.tokens["pensamiento"] is not None]
        acepto = "sí" if validas else "no (error de la API)"
        if validas and len(validas) < len(cs):
            acepto = f"parcial ({len(cs) - len(validas)} errores)"
        filas.append([nivel, f"{k} / {len(cs)}", pm(m, s, 2), f(60 / m if m and not math.isnan(m) else NAN, 1),
                      f(media(pen), 0), f(media(tot), 0), acepto])
        resumen[nivel] = {"aciertos": k, "N": len(cs), "lat_media_s": m, "lat_sd_s": s, "lat": lat,
                          "decisiones_min": 60 / m if m and not math.isnan(m) else NAN}
        if nivel in ("low", "high") and len(cs) < 5:
            avisos.append(f"Exp. 4: thinking={nivel} tiene {len(cs)} repeticiones (mínimo 5).")
    if "low" in resumen and "high" in resumen:
        lo, hi = resumen["low"], resumen["high"]
        d = hi["lat_media_s"] - lo["lat_media_s"]
        resumen["dif_lat_s"] = d
        resumen["dif_lat_supera_sd"] = bool(abs(d) > max(lo["lat_sd_s"] or 0, hi["lat_sd_s"] or 0))
    cab = ["thinking_level", "Accuracy", "Latencia media ± σ (s)", "Decisiones por minuto",
           "Tokens de pensamiento (media)", "Tokens totales (media)", "¿Aceptó el nivel?"]
    return cab, filas, resumen


def es_segura(verdad, escena):
    v = next((x for x in verdad if x["escena"] == escena and (x.get("segura") or "").strip()), None)
    if v:
        return sin_acentos(v["segura"]) in ("si", "s", "true", "1", "yes")
    e = escena.lower()
    if re.match(r"agarre_s", e):
        return True
    if re.match(r"agarre_i", e):
        return False
    return None


def tabla10(consultas, verdad, espacio: dict | None, avisos):
    agarre = [c for c in consultas if c.tarea == "agarre"]
    tp = fp = fn = tn = 0
    filas_b = []
    for c in agarre:
        segura = es_segura(verdad, c.escena)
        if segura is None:
            avisos.append(f"Exp. 5: no se sabe si {c.escena} es segura (columna segura vacía).")
            continue
        if c.estado in ("json_invalido", "error_api"):      # fallo = predicción equivocada
            abstiene = segura
        else:
            abstiene = c.estado == "vacia"
        if not segura and abstiene:
            tp += 1
        elif not segura:
            fn += 1
        elif abstiene:
            fp += 1
        else:
            tn += 1
        if segura and c.estado == "ok":
            d = c.detecciones[0]
            gt = [v for v in verdad if v["escena"] == c.escena and v["caja"]]
            sobre = ("sí" if gt and any(v["caja"][0] <= d["u"] <= v["caja"][2] and v["caja"][1] <= d["v"] <= v["caja"][3]
                                        for v in gt) else "no" if gt else "sin verdad")
            if espacio and d["xyz"]:
                val = et.validar_objetivo(d["xyz"], espacio)
                dentro = "sí" if val["valido"] else f"no ({val['motivo']})"
                p_pre = "(" + ", ".join(f"{x:.3f}" for x in val["p_pre"]) + ")" if val["valido"] else "—"
            else:
                dentro, p_pre = ("sin espacio_trabajo.json" if not espacio else "sin profundidad"), "—"
            xyz = "(" + ", ".join(f"{x:.3f}" for x in d["xyz"]) + ")" if d["xyz"] else "—"
            filas_b.append([c.escena, xyz, dentro, sobre, p_pre, f"fila {c.filas}"])
    p, r, f1 = prf(tp, fp, fn)
    filas_a = [["Escena insegura", f"TP = {tp}", f"FN = {fn}"], ["Escena segura", f"FP = {fp}", f"TN = {tn}"],
               ["Precision / Recall / F1", f"{f(p, 2)} / {f(r, 2)} / {f(f1, 2)}", f"N = {tp + fp + fn + tn}"]]
    cab_a = ["", "Modelo se abstiene (lista vacía)", "Modelo devuelve punto"]
    cab_b = ["Escena", "Punto de agarre (X, Y, Z) (m)", "¿Dentro del volumen?", "¿Sobre el objeto correcto?",
             "Pose de pre-agarre p_pre (m)", "Observación"]

    # 10c: plan y detección de éxito
    filas_c = []
    for c in [c for c in consultas if c.tarea == "plan"]:
        if c.estado != "ok" or not isinstance(c.datos, dict):
            filas_c.append([f"Plan ({c.escena})", c.estado, "JSON con objetos y pasos", "no", f"fila {c.filas}"])
            continue
        objetos = {d["label"]: d["xyz"] for d in c.detecciones}
        res = et.validar_plan(c.datos, objetos, espacio)
        for clave, nombre, esperado in (("a_vocabulario", "Plan: vocabulario permitido", "Solo acciones válidas"),
                                        ("b_objetivos", "Plan: objetivos existentes", "Todos en objetos"),
                                        ("c_volumen", "Plan: puntos dentro del volumen", "Dentro de los límites"),
                                        ("d_secuencia", "Plan: secuencia coherente", "Orden físicamente válido")):
            filas_c.append([f"{nombre} ({c.escena})", "; ".join(res[clave]["detalle"]) or "cumple", esperado,
                            "sí" if res[clave]["ok"] else "no", "; ".join(res["avisos"]) or f"fila {c.filas}"])
    k_ex = n_ex = 0
    for c in [c for c in consultas if c.tarea == "exito"]:
        despues = c.escena.split("->")[-1].lower()
        esperado = True if "bien" in despues else False if "mal" in despues else None
        pred = c.datos.get("exito") if c.estado == "ok" and isinstance(c.datos, dict) else None
        ok = esperado is not None and pred is esperado
        n_ex += esperado is not None
        k_ex += ok
        conf = c.datos.get("confianza") if isinstance(c.datos, dict) else None
        filas_c.append([f"Éxito: {c.escena}", f"exito: {pred} (confianza {conf})", f"exito: {esperado}",
                        "sí" if ok else "no", (c.datos or {}).get("evidencia", c.estado) if isinstance(c.datos, dict) else c.estado])
    acc_ex = k_ex / n_ex if n_ex else NAN
    filas_c.append(["Accuracy de detección de éxito", f"{k_ex} / {n_ex}", "", f(acc_ex, 2), ""])
    cab_c = ["Prueba", "Resultado del modelo", "Resultado esperado", "¿Correcto?", "Comentario"]
    return (cab_a, filas_a), (cab_b, filas_b), (cab_c, filas_c), {
        "TP": tp, "FP": fp, "FN": fn, "TN": tn, "precision": p, "recall": r, "f1": f1,
        "exito_acc": acc_ex, "exito_N": n_ex}


# ---------------------------------------------------------------------------
# Figuras (opcionales: requieren matplotlib)
# ---------------------------------------------------------------------------
COL = {"s1": "#2a78d6", "s2": "#eb6834", "tinta": "#0b0b0b", "tinta2": "#52514e", "muted": "#898781",
       "grid": "#e1e0d9", "eje": "#c3c2b7", "fondo": "#fcfcfb"}


def _estilo(ax):
    ax.set_facecolor(COL["fondo"])
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(COL["eje"])
    ax.tick_params(colors=COL["tinta2"], labelsize=9, length=0)
    ax.grid(axis="x", color=COL["grid"], linewidth=0.8)
    ax.set_axisbelow(True)


def figuras(salida: Path, t6, t8_detalle, t9, avisos):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        avisos.append("matplotlib no está instalado: se omiten las figuras (pip install matplotlib).")
        return []
    salida.mkdir(parents=True, exist_ok=True)
    hechas = []
    plt.rcParams.update({"font.family": "sans-serif", "font.size": 9, "figure.facecolor": COL["fondo"]})

    # Fig. 1: accuracy por relación, D vs P, con IC de Wilson.
    celdas = t6.get("celdas", {})
    rels = [r for r in dict.fromkeys(k.split("|")[0] for k in celdas)]
    if rels:
        fig, ax = plt.subplots(figsize=(6.4, 0.45 * len(rels) + 1.2))
        _estilo(ax)
        for j, (part, col, dy) in enumerate((("D", COL["s1"], -0.12), ("P", COL["s2"], 0.12))):
            for i, rel in enumerate(rels):
                k, n = celdas.get(f"{rel}|{part}", (0, 0))
                if not n:
                    continue
                lo, hi = wilson(k, n)
                ax.plot([lo, hi], [i + dy] * 2, color=col, linewidth=2, solid_capstyle="round")
                ax.plot(k / n, i + dy, "o", color=col, markersize=7, markeredgecolor=COL["fondo"], markeredgewidth=2,
                        label=("Desarrollo (D)" if part == "D" else "Prueba (P)") if i == 0 else None)
        ax.set_yticks(range(len(rels)), rels, color=COL["tinta"])
        ax.set_xlim(-0.02, 1.02)
        ax.set_xlabel("Accuracy (punto · IC 95 % de Wilson)", color=COL["tinta2"])
        ax.set_ylim(len(rels) - 0.5, -0.5)
        ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(0.0, -0.22), ncol=2, fontsize=8)
        ax.set_title("Exp. 1 — Acierto por relación y partición", loc="left", color=COL["tinta"], fontsize=10)
        fig.tight_layout()
        ruta = salida / "fig_exp1_accuracy_D_P.png"
        fig.savefig(ruta, dpi=200, bbox_inches="tight")
        plt.close(fig)
        hechas.append(ruta)

    # Fig. 2: error 3D por configuración, con y sin registro.
    if t8_detalle:
        escenas = sorted(dict.fromkeys(d["escena"] for d in t8_detalle), key=lambda s: (len(s), s))
        pos = {d["escena"]: d["posicion"] for d in t8_detalle}
        fig, ax = plt.subplots(figsize=(6.4, 0.45 * len(escenas) + 1.4))
        _estilo(ax)
        ax.axvline(0, color=COL["eje"], linewidth=1)
        for reg, col, dy, nombre in (("si", COL["s1"], -0.12, "Con registro"), ("no", COL["s2"], 0.12, "Sin registro")):
            primero = True
            for i, esc in enumerate(escenas):
                es = [d["e_mm"] for d in t8_detalle if d["escena"] == esc and d["registro"] == reg]
                if es:
                    ax.plot(es, [i + dy] * len(es), "o", color=col, markersize=7, markeredgecolor=COL["fondo"],
                            markeredgewidth=2, label=nombre if primero else None)
                    primero = False
        ax.set_yticks(range(len(escenas)), [f"{e} ({pos[e] or '?'})" for e in escenas], color=COL["tinta"])
        ax.set_ylim(len(escenas) - 0.5, -0.5)
        ax.set_xlabel("Error de distancia e = d_est − d_real (mm)", color=COL["tinta2"])
        ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(0.0, -0.22), ncol=2, fontsize=8)
        ax.set_title("Exp. 3 — Error 3D por configuración y registro", loc="left", color=COL["tinta"], fontsize=10)
        fig.tight_layout()
        ruta = salida / "fig_exp3_error_registro.png"
        fig.savefig(ruta, dpi=200, bbox_inches="tight")
        plt.close(fig)
        hechas.append(ruta)

    # Fig. 3: latencia por nivel de razonamiento.
    niveles = [n for n in ("minimal", "low", "medium", "high") if t9.get(n, {}).get("lat")]
    if niveles:
        fig, ax = plt.subplots(figsize=(6.4, 0.5 * len(niveles) + 1.2))
        _estilo(ax)
        for i, n in enumerate(niveles):
            lat = t9[n]["lat"]
            ax.plot(lat, [i] * len(lat), "o", color=COL["s1"], alpha=0.55, markersize=6, markeredgewidth=0)
            m, s = t9[n]["lat_media_s"], t9[n]["lat_sd_s"]
            if not math.isnan(s):
                ax.plot([m - s, m + s], [i - 0.22] * 2, color=COL["tinta2"], linewidth=2, solid_capstyle="round")
            ax.plot(m, i - 0.22, "|", color=COL["tinta"], markersize=10, markeredgewidth=2)
            ax.annotate(f"{m:.1f} s · {60 / m:.0f} dec/min", (m + (0 if math.isnan(s) else s), i - 0.22),
                        xytext=(8, 0), textcoords="offset points", va="center", fontsize=8, color=COL["tinta2"])
        ax.set_yticks(range(len(niveles)), niveles, color=COL["tinta"])
        ax.set_ylim(len(niveles) - 0.5, -0.6)
        ax.set_xlim(left=0)
        ax.set_xlabel("Latencia por consulta (s) · puntos = repeticiones, barra = media ± σ", color=COL["tinta2"])
        ax.set_title("Exp. 4 — Costo del razonamiento (thinking_level)", loc="left", color=COL["tinta"], fontsize=10)
        fig.tight_layout()
        ruta = salida / "fig_exp4_latencia.png"
        fig.savefig(ruta, dpi=200, bbox_inches="tight")
        plt.close(fig)
        hechas.append(ruta)
    return hechas


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------
def guardar_csv(ruta: Path, cab, filas):
    with ruta.open("w", newline="", encoding="utf-8") as fcsv:
        w = csv.writer(fcsv)
        w.writerow(cab)
        w.writerows(filas)


def analizar(resultados: Path, verdad_ruta: Path, capturas: Path, config: Path, espacio_ruta: Path,
             salida: Path) -> dict:
    avisos: list[str] = []
    consultas = cargar_consultas(resultados)
    if not consultas:
        avisos.append(f"No hay consultas en {resultados}: las tablas salen vacías.")
    verdad = cargar_verdad(verdad_ruta)
    sin_caja = [v["escena"] for v in verdad
                if v["caja"] is None and (v["relacion_n"] in RELACIONES or v["relacion_n"] in ("cajas", "agarre"))]
    if sin_caja:
        avisos.append(f"verdad_terreno.csv: {len(sin_caja)} fila(s) sin caja u_min..v_max "
                      f"({', '.join(dict.fromkeys(sin_caja))}); esas consultas no pueden contar como acierto.")
    espacio = et.cargar_espacio(espacio_ruta)
    if espacio is None:
        avisos.append("No existe espacio_trabajo.json: las Tablas 10b y 10c no validan el volumen.")
    inferir_etiquetas(consultas, verdad, avisos)
    salida.mkdir(parents=True, exist_ok=True)

    secciones = []

    def seccion(titulo, archivo, cab, filas, nota=""):
        secciones.append(f"## {titulo}\n\n" + (tabla_md(cab, filas) if filas else "_Sin datos todavía._")
                         + (f"\n\n{nota}" if nota else ""))
        if filas:
            guardar_csv(salida / archivo, cab, filas)

    cab, filas = tabla3(capturas)
    seccion("Tabla 3: Registro de escenas (capturas/*_meta.json)", "tabla_03_escenas.csv", cab, filas)
    cab, filas, r4 = tabla4(consultas, verdad, config)
    seccion("Tabla 4: Calibración de la cámara de color", "tabla_04_calibracion.csv", cab, filas)
    cab, filas = tabla5(consultas, verdad)
    seccion("Tabla 5: Bitácora de desarrollo del prompt (solo escenas D)", "tabla_05_bitacora.csv", cab, filas,
            "Las columnas «Cambio» y «Motivo» se completan a mano desde la bitácora del grupo.")
    cab, filas, r6 = tabla6(consultas, verdad, avisos)
    seccion("Tabla 6: Experimento 1 — Semántica con partición (thinking = low)", "tabla_06_semantica.csv", cab, filas,
            "Accuracy con IC 95 % de Wilson; brecha con intervalo de Newcombe.")
    cab, filas, r7 = tabla7(consultas, verdad, capturas, avisos)
    seccion("Tabla 7: Experimento 2 — Cajas delimitadoras", "tabla_07_cajas.csv", cab, filas)
    cab, filas, r8, det8 = tabla8(consultas, verdad, avisos)
    alt = r8.get("altura", [])
    nota8 = "; ".join(f"Altura ({a['escena']}): real {f(a['h_real_m'])} m · estimada {f(a['h_est_m'])} m · "
                      f"error {f(a['error_mm'], 1)} mm (fila {a['filas']})" for a in alt)
    seccion("Tabla 8: Experimento 3 — Exactitud 3D y ablación del registro", "tabla_08_distancias.csv", cab, filas,
            (nota8 + "\n\n" if nota8 else "") + f"Ablación con la misma respuesta: {r8['ablacion_pareada']} consultas pareadas.")
    cab, filas, r9 = tabla9(consultas, verdad, avisos)
    seccion("Tabla 9: Experimento 4 — Costo del razonamiento", "tabla_09_thinking.csv", cab, filas)
    (ca, fa), (cb, fb), (cc, fc), r10 = tabla10(consultas, verdad, espacio, avisos)
    seccion("Tabla 10a: Matriz de confusión del agarre (positivo = insegura, se abstiene)",
            "tabla_10a_confusion.csv", ca, fa)
    nota10b = ""
    if espacio:
        nota10b = (f"Volumen de trabajo: X [{espacio['x_min']}, {espacio['x_max']}] m · Y [{espacio['y_min']}, "
                   f"{espacio['y_max']}] m · Z_mesa = {espacio['z_mesa']} m · h_max = {espacio['h_max']} m · "
                   f"n = ({', '.join(str(c) for c in espacio['normal'])}) · d_a = {espacio.get('d_a', 0.10)} m.")
    seccion("Tabla 10b: Espacio de trabajo y pre-agarre", "tabla_10b_pre_agarre.csv", cb, fb, nota10b)
    seccion("Tabla 10c: Plan y detección de éxito", "tabla_10c_plan_exito.csv", cc, fc)

    def ic_txt(k, n):
        lo, hi = wilson(k, n)
        return f"[{f(lo, 2)}, {f(hi, 2)}]"

    t11 = [
        ["Accuracy semántica en D / en P",
         f"{f(r6['acc_D'], 2)} {ic_txt(r6['k_D'], r6['N_D'])} / {f(r6['acc_P'], 2)} {ic_txt(r6['k_P'], r6['N_P'])}",
         f"{r6['N_D']} / {r6['N_P']}", 1, "(contrastar)"],
        ["mIoU y Precision / Recall de detección", f"{f(r7['mIoU'])} · {f(r7['precision'], 2)} / {f(r7['recall'], 2)}",
         r7["TP"] + r7["FN"], 2, "(contrastar)"],
        ["RMSE 3D con / sin registro (mm)", f"{f(r8['RMSE_con_mm'], 1)} / {f(r8['RMSE_sin_mm'], 1)}",
         f"{r8['N_con']} / {r8['N_sin']}", 3, "(contrastar)"],
        ["Latencia low / high (s)",
         f"{pm(r9.get('low', {}).get('lat_media_s', NAN), r9.get('low', {}).get('lat_sd_s', NAN), 2)} / "
         f"{pm(r9.get('high', {}).get('lat_media_s', NAN), r9.get('high', {}).get('lat_sd_s', NAN), 2)}",
         f"{r9.get('low', {}).get('N', 0)} / {r9.get('high', {}).get('N', 0)}", 4, "(contrastar)"],
        ["Precision / Recall / F1 del rechazo de agarres inseguros",
         f"{f(r10['precision'], 2)} / {f(r10['recall'], 2)} / {f(r10['f1'], 2)}",
         r10["TP"] + r10["FP"] + r10["FN"] + r10["TN"], 5, "(contrastar)"],
        ["Accuracy de detección de éxito", f(r10["exito_acc"], 2), r10["exito_N"], 5, "(contrastar)"],
    ]
    seccion("Tabla 11: Resumen de métricas (RAE 6.2)", "tabla_11_resumen.csv",
            ["Métrica", "Valor ± dispersión", "N", "Experimento", "¿Confirma la hipótesis de la Fase 1?"], t11)

    figs = figuras(salida / "figuras", r6, det8, r9, avisos)
    n_ok = sum(c.estado == "ok" for c in consultas)
    cabecera = (f"# Análisis LAB-IA-01B — generado por analisis_lab_ia_01b.py\n\n"
                f"Fuente: `{resultados}` ({len(consultas)} consultas, {n_ok} con respuesta localizada) y "
                f"`{verdad_ruta}` ({len(verdad)} filas de verdad de terreno).\n")
    texto_avisos = "## Avisos de calidad de los datos\n\n" + ("\n".join(f"- {a}" for a in dict.fromkeys(avisos))
                                                              if avisos else "- Ninguno.")
    figs_md = "## Figuras\n\n" + ("\n".join(f"![{p.stem}](figuras/{p.name})" for p in figs) if figs else "_Ninguna._")
    (salida / "tablas.md").write_text("\n\n".join([cabecera, texto_avisos, *secciones, figs_md]) + "\n", encoding="utf-8")

    resumen = {"tabla4": r4, "tabla6": {k: v for k, v in r6.items() if k != "celdas"}, "tabla7": r7,
               "tabla8": r8, "tabla9": {k: {kk: vv for kk, vv in v.items() if kk != "lat"} if isinstance(v, dict) else v
                                        for k, v in r9.items()},
               "tabla10": r10, "avisos": list(dict.fromkeys(avisos))}
    (salida / "resumen.json").write_text(json.dumps(resumen, indent=2, ensure_ascii=False, default=str) + "\n",
                                         encoding="utf-8")
    return resumen


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--resultados", default=str(AQUI / "resultados" / "resultados.csv"))
    p.add_argument("--verdad", default=str(AQUI / "verdad_terreno.csv"))
    p.add_argument("--capturas", default=str(AQUI / "capturas"))
    p.add_argument("--config", default=str(ec.CONFIG_POR_DEFECTO))
    p.add_argument("--espacio", default=str(et.ESPACIO_POR_DEFECTO))
    p.add_argument("--salida", default=str(AQUI / "analisis"))
    a = p.parse_args()
    r = analizar(Path(a.resultados), Path(a.verdad), Path(a.capturas), Path(a.config), Path(a.espacio), Path(a.salida))
    print(f"[OK] Tablas en {Path(a.salida) / 'tablas.md'}")
    for av in r["avisos"]:
        print(f"[AVISO] {av}")


if __name__ == "__main__":
    main()
