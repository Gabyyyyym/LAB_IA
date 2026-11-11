#!/usr/bin/env python3
"""
Anotar la verdad de terreno (Fase 1) sin GIMP, ANTES de consultar a Gemini.

Recorre verdad_terreno.csv y, para cada fila sin caja, abre capturas/<escena>_color.png:
arrastre un rectángulo sobre el objeto indicado en el título y pulse Enter o Espacio
(c = saltar la fila). Las cajas ya anotadas de la misma escena se dibujan en verde.
Para las filas de distancia y altura muestra la nota de la captura y pide el valor medido.
Guarda después de cada fila y no toca los comentarios del archivo.

    python3 sesion/anotar_verdad.py                     # todas las filas pendientes
    python3 sesion/anotar_verdad.py --escena burger     # solo una escena
    python3 sesion/anotar_verdad.py --escena dist3 --rehacer
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import unicodedata
from pathlib import Path

PLANTILLA = Path(__file__).resolve().parents[1]
CON_CAJA = {"seguridad", "pertenencia", "estado", "contencion", "relacional", "cajas", "agarre"}
CON_MEDIDA = {"distancia", "altura", "calibracion"}
CAJA = ("u_min", "v_min", "u_max", "v_max")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def a_linea(valores: list[str]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(valores)
    return buf.getvalue()


def nota_captura(capturas: Path, escena: str) -> str:
    meta = capturas / f"{escena}_meta.json"
    if not meta.exists():
        return "(sin captura)"
    return json.loads(meta.read_text(encoding="utf-8")).get("nota", "")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--verdad", default=str(PLANTILLA / "verdad_terreno.csv"))
    p.add_argument("--capturas", default=str(PLANTILLA / "capturas"))
    p.add_argument("--escena", default="", help="Anotar solo esta escena")
    p.add_argument("--rehacer", action="store_true", help="Volver a anotar filas que ya tienen valor")
    p.add_argument("--solo-medidas", action="store_true", help="Pedir solo distancias/alturas (sin ventanas)")
    a = p.parse_args()

    ruta, capturas = Path(a.verdad), Path(a.capturas)
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    cols = next(csv.reader([lineas[0]]))
    pendientes = 0

    def guardar():
        ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")

    for i, linea in enumerate(lineas[1:], start=1):
        if not linea.strip() or linea.lstrip().startswith("#"):
            continue
        valores = next(csv.reader([linea]))
        valores += [""] * (len(cols) - len(valores))
        fila = dict(zip(cols, valores))
        escena, rel = fila["escena"], norm(fila["relacion"])
        if a.escena and escena != a.escena:
            continue

        if rel in CON_MEDIDA:
            if fila["d_real_m"].strip() and not a.rehacer:
                continue
            print(f"\n=== {escena} ({rel}) · {fila['objeto_esperado']}")
            print(f"    Nota de la captura: {nota_captura(capturas, escena)}")
            texto = input("    Valor medido en metros (Enter = saltar): ")
            texto = texto.replace("﻿", "").strip().replace(",", ".")
            if not texto:
                pendientes += 1
                continue
            try:
                float(texto)
            except ValueError:
                print("    [AVISO] No es un número; se deja vacío.")
                pendientes += 1
                continue
            valores[cols.index("d_real_m")] = texto
            lineas[i] = a_linea(valores)
            guardar()
            continue

        if rel not in CON_CAJA or a.solo_medidas:
            continue
        if all(fila[k].strip() for k in CAJA) and not a.rehacer:
            continue
        imagen = capturas / f"{escena}_color.png"
        if not imagen.exists():
            print(f"[=] {escena}: sin captura todavía; se omite.")
            pendientes += 1
            continue

        import cv2  # importación diferida: --solo-medidas no necesita ventanas
        img = cv2.imread(str(imagen))
        alto, ancho = img.shape[:2]
        escala = min(1.0, 1500 / ancho, 850 / alto)
        vista = cv2.resize(img, (int(ancho * escala), int(alto * escala)))
        for otra in lineas[1:]:                      # contexto: cajas ya anotadas de la escena
            if otra.lstrip().startswith("#") or not otra.strip():
                continue
            o = dict(zip(cols, next(csv.reader([otra]))))
            if o.get("escena") == escena and all((o.get(k) or "").strip() for k in CAJA):
                u0, v0, u1, v1 = (float(o[k]) * escala for k in CAJA)
                cv2.rectangle(vista, (int(u0), int(v0)), (int(u1), int(v1)), (0, 200, 0), 2)
        objeto = fila["objeto_esperado"].split("|")[0]
        titulo = f"{escena} - {objeto} ({fila['nota'][:40]}) - Enter=ok c=saltar"
        print(f"\n=== {escena} [{rel}] marque: {objeto}  ·  {fila['nota']}")
        x, y, w, h = cv2.selectROI(titulo, vista, showCrosshair=True, fromCenter=False)
        cv2.destroyWindow(titulo)
        if w == 0 or h == 0:
            print("    saltada")
            pendientes += 1
            continue
        caja = [x / escala, y / escala, (x + w) / escala, (y + h) / escala]
        for k, val in zip(CAJA, caja):
            valores[cols.index(k)] = f"{val:.0f}"
        lineas[i] = a_linea(valores)
        guardar()
        print(f"    caja (u_min, v_min, u_max, v_max) = ({caja[0]:.0f}, {caja[1]:.0f}, {caja[2]:.0f}, {caja[3]:.0f})")

    print(f"\n[OK] {ruta} guardado. Filas pendientes: {pendientes}.")
    if not pendientes:
        print('Ahora: git add verdad_terreno.csv && git commit -m "verdad de terreno pre-registrada"')


if __name__ == "__main__":
    main()
