#!/usr/bin/env python3
"""
Fase 9 — Representación del espacio de trabajo y del agarre (RAE 1.2). Script del grupo,
fuera de la plantilla del docente.

Todo está en el marco ÓPTICO de la cámara de color (X derecha, Y abajo, Z adelante), con la
cámara fija mirando la mesa. El paso a base_link queda para la extensión con el docente.

Subcomandos:

    # 1) Volumen de trabajo a partir de puntos 3D de la mesa (esquinas de la zona útil).
    #    Los puntos salen de una consulta de la escena 'regla' guardada en resultados.csv
    #    (la última de esa escena) o se pasan a mano con --puntos.
    python3 espacio_trabajo.py volumen --escena regla --h-max 0.20 --d-a 0.10
    python3 espacio_trabajo.py volumen --puntos "-0.15,-0.09,0.66;0.16,-0.09,0.65;0.16,0.09,0.64;-0.15,0.09,0.65" --h-max 0.20

    # 2) Validar con reglas deterministas el último plan de una escena (Tabla 10c).
    python3 espacio_trabajo.py validar-plan --escena burger

    # 3) Validar un punto de agarre y calcular su pose de pre-agarre (Tabla 10b).
    python3 espacio_trabajo.py pre-agarre --xyz 0.05,0.02,0.58
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

AQUI = Path(__file__).resolve().parent
CSV_RESULTADOS = AQUI / "resultados" / "resultados.csv"
ESPACIO_POR_DEFECTO = AQUI / "espacio_trabajo.json"
ACCIONES_PERMITIDAS = ("mover_sobre", "bajar", "cerrar_pinza", "subir", "abrir_pinza")


# ---------------------------------------------------------------------------
# Geometría de la mesa y del volumen de trabajo
# ---------------------------------------------------------------------------
def normal_mesa(p1, p2, p3) -> np.ndarray:
    """Normal unitaria del plano por tres puntos, orientada hacia la cámara (origen óptico).

    n = (P2 - P1) x (P3 - P1) / ||...||. Como la cámara está en el origen, "hacia la cámara"
    significa n · (0 - P1) > 0; si no se cumple se invierte el signo.
    """
    p1, p2, p3 = (np.asarray(p, dtype=float) for p in (p1, p2, p3))
    n = np.cross(p2 - p1, p3 - p1)
    norma = np.linalg.norm(n)
    if norma < 1e-9:
        raise ValueError("Los tres puntos son colineales: no definen un plano.")
    n /= norma
    return -n if np.dot(n, -p1) < 0 else n


def ajustar_plano(puntos) -> tuple[np.ndarray, np.ndarray, float]:
    """Plano por mínimos cuadrados (SVD) con N >= 3 puntos.

    Devuelve (centroide, normal hacia la cámara, residuo RMS en m). Con más de tres puntos
    es más robusto que ``normal_mesa`` porque promedia el ruido de profundidad.
    """
    P = np.asarray(puntos, dtype=float)
    if P.shape[0] < 3:
        raise ValueError("Se necesitan al menos 3 puntos.")
    c = P.mean(axis=0)
    _, _, vt = np.linalg.svd(P - c)
    n = vt[-1] / np.linalg.norm(vt[-1])
    if np.dot(n, -c) < 0:
        n = -n
    residuo = float(np.sqrt(np.mean(((P - c) @ n) ** 2)))
    return c, n, residuo


def limites_desde_puntos(puntos, h_max: float, d_a: float = 0.10) -> dict:
    """Volumen prismático de la guía a partir de las esquinas de la zona útil de la mesa."""
    P = np.asarray(puntos, dtype=float)
    centroide, n, residuo = ajustar_plano(P)
    n3 = normal_mesa(*P[:3])
    return {
        "x_min": round(float(P[:, 0].min()), 4), "x_max": round(float(P[:, 0].max()), 4),
        "y_min": round(float(P[:, 1].min()), 4), "y_max": round(float(P[:, 1].max()), 4),
        "z_mesa": round(float(np.median(P[:, 2])), 4), "h_max": float(h_max), "d_a": float(d_a),
        "normal": [round(float(c), 4) + 0.0 for c in n3],          # + 0.0 evita "-0.0"
        "normal_svd": [round(float(c), 4) + 0.0 for c in n],
        "punto_plano": [round(float(c), 4) + 0.0 for c in centroide],
        "residuo_plano_mm": round(residuo * 1000, 2),
        "inclinacion_deg": round(math.degrees(math.acos(min(1.0, abs(float(n[2]))))), 2),
        "puntos": P.round(4).tolist(),
    }


def dentro_del_volumen(xyz, lim) -> bool:
    """Guía, Fase 9: prisma X_min..X_max, Y_min..Y_max, Z_mesa - h_max <= Z <= Z_mesa."""
    x, y, z = xyz
    return (lim["x_min"] <= x <= lim["x_max"] and lim["y_min"] <= y <= lim["y_max"]
            and lim["z_mesa"] - lim["h_max"] <= z <= lim["z_mesa"])


def altura_sobre_mesa(xyz, lim) -> float:
    """Distancia con signo al plano de la mesa a lo largo de la normal (positiva hacia la cámara)."""
    p0 = np.asarray(lim.get("punto_plano", [0.0, 0.0, lim["z_mesa"]]), dtype=float)
    n = np.asarray(lim.get("normal_svd", lim["normal"]), dtype=float)
    return float(np.dot(np.asarray(xyz, dtype=float) - p0, n))


def dentro_del_volumen_plano(xyz, lim, tolerancia_m: float = 0.01) -> bool:
    """Variante que tolera una cámara inclinada: X/Y dentro de la zona útil y
    -tolerancia <= altura sobre el plano de la mesa <= h_max."""
    x, y, _ = xyz
    h = altura_sobre_mesa(xyz, lim)
    return (lim["x_min"] <= x <= lim["x_max"] and lim["y_min"] <= y <= lim["y_max"]
            and -tolerancia_m <= h <= lim["h_max"])


def pose_pre_agarre(xyz, n, d_a: float = 0.10) -> np.ndarray:
    """p_pre = p + d_a n, con n la normal unitaria de la mesa orientada hacia la cámara."""
    return np.asarray(xyz, dtype=float) + d_a * np.asarray(n, dtype=float)


def validar_objetivo(xyz, lim) -> dict:
    """Validación determinista de un objetivo antes de entregarlo a un planificador."""
    if xyz is None or any(c is None or (isinstance(c, float) and math.isnan(c)) for c in xyz):
        return {"valido": False, "motivo": "sin profundidad válida"}
    if not dentro_del_volumen(xyz, lim):
        return {"valido": False, "motivo": "fuera del volumen de trabajo"}
    p_pre = pose_pre_agarre(xyz, lim["normal"], lim.get("d_a", 0.10))
    return {"valido": True, "motivo": "ok", "p_pre": [round(float(c), 4) for c in p_pre]}


# ---------------------------------------------------------------------------
# Validación del plan propuesto por el VLM (Tabla 10c)
# ---------------------------------------------------------------------------
def _norm_label(s) -> str:
    return " ".join(str(s).strip().lower().split())


def validar_plan(plan: dict, objetos_3d: dict, lim: dict | None) -> dict:
    """Cuatro reglas automáticas de la guía.

    ``plan``: {"objetos": [...], "pasos": [{"accion": ..., "objetivo": ...}]}
    ``objetos_3d``: {label: [x, y, z] o None}, los objetos del plan ya localizados en 3D.
    Devuelve {"a_vocabulario", "b_objetivos", "c_volumen", "d_secuencia"}, cada uno con
    "ok" (bool) y "detalle" (lista de problemas), más "avisos".
    """
    pasos = plan.get("pasos", []) if isinstance(plan, dict) else []
    etiquetas = {_norm_label(k) for k in objetos_3d}
    res = {k: {"ok": True, "detalle": []} for k in ("a_vocabulario", "b_objetivos", "c_volumen", "d_secuencia")}
    avisos = []
    if not pasos:
        for k in res:
            res[k] = {"ok": False, "detalle": ["el plan no tiene pasos"]}
        return {**res, "avisos": avisos}

    # (a) vocabulario permitido
    for i, p in enumerate(pasos, 1):
        accion = p.get("accion") if isinstance(p, dict) else None
        if accion not in ACCIONES_PERMITIDAS:
            res["a_vocabulario"]["ok"] = False
            res["a_vocabulario"]["detalle"].append(f"paso {i}: acción no permitida {accion!r}")

    # (b) cada objetivo existe en "objetos"
    for i, p in enumerate(pasos, 1):
        obj = p.get("objetivo") if isinstance(p, dict) else None
        if obj in (None, "", "null"):
            if isinstance(p, dict) and p.get("accion") == "mover_sobre":
                res["b_objetivos"]["ok"] = False
                res["b_objetivos"]["detalle"].append(f"paso {i}: mover_sobre sin objetivo")
            continue
        if _norm_label(obj) not in etiquetas:
            res["b_objetivos"]["ok"] = False
            res["b_objetivos"]["detalle"].append(f"paso {i}: objetivo {obj!r} no está en objetos")

    # (c) cada punto 3D dentro del volumen de trabajo
    if lim is None:
        res["c_volumen"] = {"ok": False, "detalle": ["sin espacio_trabajo.json: no se puede validar"]}
    else:
        for label, xyz in objetos_3d.items():
            v = validar_objetivo(xyz, lim)
            if not v["valido"]:
                res["c_volumen"]["ok"] = False
                res["c_volumen"]["detalle"].append(f"{label}: {v['motivo']}")

    # (d) secuencia físicamente coherente (máquina de estados de la pinza)
    abajo, pinza_cerrada, sostiene, solto, sobre = False, False, False, False, None
    for i, p in enumerate(pasos, 1):
        accion = p.get("accion") if isinstance(p, dict) else None
        error = None
        if accion == "mover_sobre":
            if abajo:
                error = "mover_sobre estando abajo (sin subir antes)"
            sobre = p.get("objetivo")
        elif accion == "bajar":
            if abajo:
                error = "bajar dos veces seguidas"
            elif sobre is None:
                error = "bajar sin mover_sobre previo"
            abajo = True
        elif accion == "cerrar_pinza":
            if not abajo:
                error = "cerrar_pinza antes de bajar"
            elif pinza_cerrada:
                error = "cerrar_pinza con la pinza ya cerrada"
            pinza_cerrada, sostiene = True, abajo
        elif accion == "subir":
            if not abajo:
                error = "subir sin haber bajado"
            elif not pinza_cerrada and not solto:
                error = "subir con la pinza abierta sin haber soltado"
            abajo = False
        elif accion == "abrir_pinza":
            if sostiene:
                solto, sostiene = True, False
                if not abajo:
                    avisos.append(f"paso {i}: suelta el objeto desde arriba (caída)")
            pinza_cerrada = False
        if error:
            res["d_secuencia"]["ok"] = False
            res["d_secuencia"]["detalle"].append(f"paso {i}: {error}")
    if sostiene:
        avisos.append("el plan termina con el objeto en la pinza (no lo suelta)")
    if not solto:
        avisos.append("el plan nunca suelta un objeto agarrado: no completa un pick-and-place")
    return {**res, "avisos": avisos}


# ---------------------------------------------------------------------------
# Lectura de resultados.csv
# ---------------------------------------------------------------------------
def _float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def ultima_consulta(ruta_csv: Path, escena: str, tarea: str | None = None) -> list[dict]:
    """Filas de la última consulta (misma fecha y respuesta) de una escena."""
    with Path(ruta_csv).open(encoding="utf-8") as f:
        filas = [r for r in csv.DictReader(f)
                 if r["escena"] == escena and (tarea is None or r["tarea"] == tarea)]
    if not filas:
        raise SystemExit(f"[ERROR] No hay filas de la escena '{escena}' en {ruta_csv}")
    ultima = filas[-1]
    return [r for r in filas if (r["fecha"], r["respuesta"]) == (ultima["fecha"], ultima["respuesta"])]


def xyz_de_fila(r: dict):
    xyz = [_float(r.get("x_m")), _float(r.get("y_m")), _float(r.get("z_m"))]
    return None if any(c is None for c in xyz) else xyz


def cargar_espacio(ruta: Path | str | None) -> dict | None:
    ruta = Path(ruta) if ruta else ESPACIO_POR_DEFECTO
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def cmd_volumen(args) -> None:
    if args.puntos:
        puntos = [[float(c) for c in p.split(",")] for p in args.puntos.split(";") if p.strip()]
        fuente = "puntos a mano"
    else:
        filas = ultima_consulta(Path(args.csv), args.escena, args.tarea)
        puntos = [xyz_de_fila(r) for r in filas]
        sin = [r["label"] for r, p in zip(filas, puntos) if p is None]
        if sin:
            raise SystemExit(f"[ERROR] Puntos sin profundidad válida: {sin}")
        fuente = f"{args.csv} · escena {args.escena} · {filas[0]['fecha']}"
    lim = limites_desde_puntos(puntos, args.h_max, args.d_a)
    lim["fuente"] = fuente
    Path(args.salida).write_text(json.dumps(lim, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(lim, indent=2, ensure_ascii=False))
    if lim["residuo_plano_mm"] > 5:
        print(f"[AVISO] Los puntos se apartan {lim['residuo_plano_mm']} mm del plano: revise la profundidad.")
    print(f"[OK] Volumen de trabajo guardado en {args.salida}")


def cmd_validar_plan(args) -> None:
    filas = ultima_consulta(Path(args.csv), args.escena, "plan")
    from embodied_comun import extraer_json  # importación diferida: necesita cv2
    plan = extraer_json(filas[0]["respuesta"])
    objetos = {r["label"]: xyz_de_fila(r) for r in filas if r.get("label")}
    res = validar_plan(plan, objetos, cargar_espacio(args.espacio))
    for clave, nombre in (("a_vocabulario", "(a) vocabulario permitido"), ("b_objetivos", "(b) objetivos existentes"),
                          ("c_volumen", "(c) puntos dentro del volumen"), ("d_secuencia", "(d) secuencia coherente")):
        estado = "OK " if res[clave]["ok"] else "FALLA"
        print(f"{estado} {nombre}: {'; '.join(res[clave]['detalle']) or '-'}")
    for a in res["avisos"]:
        print(f"[AVISO] {a}")


def cmd_pre_agarre(args) -> None:
    lim = cargar_espacio(args.espacio)
    if lim is None:
        raise SystemExit("[ERROR] Primero calcule el volumen: espacio_trabajo.py volumen ...")
    xyz = [float(c) for c in args.xyz.split(",")]
    print(json.dumps({"xyz": xyz, **validar_objetivo(xyz, lim)}, ensure_ascii=False))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("volumen", help="Volumen de trabajo y normal de la mesa")
    v.add_argument("--escena", default="regla")
    v.add_argument("--tarea", default=None, help="Filtrar por tarea (p. ej. puntos)")
    v.add_argument("--puntos", default="", help='"x,y,z;x,y,z;..." en metros (marco óptico)')
    v.add_argument("--h-max", type=float, required=True, help="Altura del objeto más alto (m)")
    v.add_argument("--d-a", type=float, default=0.10, help="Distancia de aproximación (m)")
    v.add_argument("--csv", default=str(CSV_RESULTADOS))
    v.add_argument("--salida", default=str(ESPACIO_POR_DEFECTO))
    v.set_defaults(func=cmd_volumen)

    q = sub.add_parser("validar-plan", help="Reglas (a)-(d) sobre el último plan de una escena")
    q.add_argument("--escena", required=True)
    q.add_argument("--csv", default=str(CSV_RESULTADOS))
    q.add_argument("--espacio", default=str(ESPACIO_POR_DEFECTO))
    q.set_defaults(func=cmd_validar_plan)

    g = sub.add_parser("pre-agarre", help="Validar un punto y calcular p_pre")
    g.add_argument("--xyz", required=True, help="x,y,z en metros")
    g.add_argument("--espacio", default=str(ESPACIO_POR_DEFECTO))
    g.set_defaults(func=cmd_pre_agarre)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
