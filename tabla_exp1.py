import csv, json, statistics as st
verdad = list(csv.DictReader(l for l in open("verdad_terreno.csv", encoding="utf-8") if not l.startswith("#")))
RELS = {"seguridad", "pertenencia", "estado", "contencion", "relacional"}

def caja(escena):
    for v in verdad:
        if v["escena"] == escena and v["relacion"].strip().lower() in RELS and v["u_min"].strip():
            return v["objeto_esperado"], [float(v[k]) for k in ("u_min", "v_min", "u_max", "v_max")]
    return "(sin caja)", None

res = [r for r in csv.DictReader(open("resultados/resultados.csv", encoding="utf-8")) if r["grupo"].endswith("exp1")]
filas = [("semantica1","propio"),("semantica2","propio"),("semantica3","propio"),("semantica4","propio"),("burger","puntos"),("burger","propio")]
nombres = {("burger","puntos"): "burger (ingenuo)", ("burger","propio"): "burger (propio)"}
print(f"{'Escena':18s} {'Esperada':28s} {'Aciertos':9s} {'JSON inv.':9s} {'Latencia (s)':14s} Observación")
for esc, tarea in filas:
    rs = [r for r in res if r["escena"] == esc and r["tarea"] == tarea]
    esperado, bx = caja(esc)
    ac = inval = vacias = 0
    lat = []
    for r in rs:
        if r["latencia_s"]: lat.append(float(r["latencia_s"]))
        if r["u"].strip() and r["v"].strip():
            u, v = float(r["u"]), float(r["v"])
            if bx and bx[0] <= u <= bx[2] and bx[1] <= v <= bx[3]: ac += 1
        else:
            try:
                if json.loads(r["respuesta"]) == []: vacias += 1
                else: inval += 1
            except Exception:
                inval += 1
    l = f"{st.mean(lat):.2f} ± {st.pstdev(lat):.2f}" if len(lat) > 1 else (f"{lat[0]:.2f}" if lat else "-")
    print(f"{nombres.get((esc,tarea),esc):18s} {esperado[:28]:28s} {ac}/{len(rs):<7d} {inval:<9d} {l:14s} {('%d vacía(s) []' % vacias) if vacias else ''}")
