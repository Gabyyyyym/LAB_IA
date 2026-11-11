#!/usr/bin/env bash
# Consultas de cada fase con las repeticiones, etiquetas y controles de la matriz de pruebas.
# No necesita el robot: trabaja sobre capturas/. Requiere GEMINI_API_KEY exportada.
#
#     export GEMINI_API_KEY="..."      # nunca en el repositorio
#     GRUPO=G03 bash sesion/experimentos.sh calib          # Fase 3: verificación (< 5 %)
#     GRUPO=G03 bash sesion/experimentos.sh dev 0          # Fase 5: iteración 0 del prompt (solo D)
#     GRUPO=G03 bash sesion/experimentos.sh exp1           # Fase 5: prueba con el prompt CONGELADO
#     GRUPO=G03 bash sesion/experimentos.sh exp2           # Fase 6
#     GRUPO=G03 bash sesion/experimentos.sh exp3           # Fase 7 (ablación automática)
#     GRUPO=G03 bash sesion/experimentos.sh exp4 semantica2p   # Fase 8 (escena P de peor resultado)
#     GRUPO=G03 H_MAX=0.20 bash sesion/experimentos.sh zona     # Fase 9: volumen de trabajo
#     GRUPO=G03 bash sesion/experimentos.sh exp5           # Fase 9
#     bash sesion/experimentos.sh analisis                  # Fase 10
#
# La etiqueta va en la columna grupo (G03-exp1, ...): así analisis_lab_ia_01b.py separa los
# experimentos sin pasos manuales. SIMULAR=1 agrega --respuesta '[]' (prueba el flujo sin cuota).
set -u
cd "$(dirname "$0")/.."
GRUPO="${GRUPO:-G00}"
CSV="resultados/resultados.csv"
[ -z "${VIRTUAL_ENV:-}" ] && [ -f ~/venv_embodied/bin/activate ] && source ~/venv_embodied/bin/activate
# La clave se lee de un archivo privado fuera del repositorio (chmod 600), nunca del código.
if [ -z "${GEMINI_API_KEY:-}" ] && [ -f ~/.config/gemini_api_key ]; then
  GEMINI_API_KEY="$(tr -d '[:space:]' < ~/.config/gemini_api_key)"
  export GEMINI_API_KEY
fi
if [ -z "${GEMINI_API_KEY:-}" ] && [ "${SIMULAR:-0}" != "1" ] && [ "${1:-}" != "analisis" ]; then
  echo "[ERROR] Falta la clave: cree ~/.config/gemini_api_key (ver PROTOCOLO_GRUPO.md) o exporte GEMINI_API_KEY."
  exit 2
fi

# Instrucciones (las mismas en D y en P: cambia la escena, no la pregunta).
declare -A INSTR=(
  [seguridad]="la caja de hamburguesa libre y segura para tomar desde arriba"
  [pertenencia]="el celular que pertenece a la persona que dejó sus llaves al lado"
  [estado]="el cuaderno que está abierto"
  [contencion]="el esfero que está dentro del estuche"
  [relacional]="la caja de hamburguesa que todavía no está sobre la bandeja"
)
declare -A RELACION=(
  [semantica1]=seguridad [semantica1p]=seguridad [semantica2]=pertenencia [semantica2p]=pertenencia
  [semantica3]=estado [semantica3p]=estado [semantica4]=contencion [semantica4p]=contencion
  [burger]=relacional [burgerp]=relacional
)
ESCENAS_D=(semantica1 semantica2 semantica3 semantica4 burger)
ESCENAS_P=(semantica1p semantica2p semantica3p semantica4p burgerp)
OBJ_DIST="el centro de la tapa del termo y el centro de la tapa de la caja"

consultar() {   # consultar <etiqueta> <args de embodied_sin_ros.py consultar...>
  local etiqueta="$1"; shift
  local extra=()
  [ "${SIMULAR:-0}" = "1" ] && extra=(--respuesta '[]')
  echo "--- [$GRUPO-$etiqueta] $*"
  python3 embodied_sin_ros.py consultar --grupo "$GRUPO-$etiqueta" "$@" "${extra[@]}" || true
}

ultima_respuesta() {
  python3 - "$CSV" <<'PY'
import csv, sys
filas = list(csv.DictReader(open(sys.argv[1], encoding="utf-8")))
f = filas[-1]
print("" if f["label"] in ("JSON_INVALIDO", "ERROR_API") or f["registro"] != "si" else f["respuesta"])
PY
}

case "${1:-}" in
  calib)
    consultar calib --escena regla --tarea puntos \
      --objeto "la esquina superior izquierda y la esquina superior derecha de la hoja blanca"
    echo "Compare la Distancia 3D impresa con 0.297 m (error < 5 %) y regístrela en la Tabla 4." ;;

  dev)  # una consulta por escena D con el prompt de la iteración $2
    it="${2:?Uso: experimentos.sh dev <numero_de_iteracion>}"
    for e in "${ESCENAS_D[@]}"; do
      consultar "dev$it" --escena "$e" --tarea propio --thinking low --objeto "${INSTR[${RELACION[$e]}]}"
    done ;;

  exp1)  # prompt congelado: 3 repeticiones por escena D y P, thinking low; ingenuo en burger
    git log -1 --format='Prompt congelado: %H %s' 2>/dev/null || true
    for rep in 1 2 3; do
      for i in 0 1 2 3 4; do      # D y P intercalados: la hora y la cuota afectan por igual a ambas
        for e in "${ESCENAS_D[$i]}" "${ESCENAS_P[$i]}"; do
          consultar exp1 --escena "$e" --tarea propio --thinking low --objeto "${INSTR[${RELACION[$e]}]}"
        done
      done
      consultar exp1 --escena burger --tarea puntos --thinking low --objeto "la caja"
    done ;;

  exp2)
    for e in semantica1 burger; do
      for rep in 1 2 3 4 5; do consultar exp2 --escena "$e" --tarea cajas --objeto ""; done
    done ;;

  exp3)  # 2 consultas por configuración; la ablación reutiliza EXACTAMENTE la misma respuesta
    for e in dist1 dist2 dist3 dist4 dist5; do
      for rep in 1 2; do
        consultar exp3 --escena "$e" --tarea puntos --objeto "$OBJ_DIST"
        resp=$(ultima_respuesta)
        if [ -n "$resp" ]; then
          consultar exp3 --escena "$e" --tarea puntos --objeto "$OBJ_DIST" --sin-registro --respuesta "$resp"
        else
          echo "[AVISO] $e: la consulta falló; no hay respuesta que reutilizar en la ablación."
        fi
      done
    done
    consultar exp3 --escena altura1 --tarea puntos \
      --objeto "un punto de la mesa justo al lado de la caja y el centro de la tapa de la caja" ;;

  exp4)  # 5 repeticiones por nivel, orden intercalado (low-high, high-low, ...)
    e="${2:?Uso: experimentos.sh exp4 <escena_P_de_peor_resultado>}"
    for rep in 1 2 3 4 5; do
      if [ $((rep % 2)) -eq 1 ]; then orden=(low high); else orden=(high low); fi
      for nivel in "${orden[@]}"; do
        consultar exp4 --escena "$e" --tarea propio --thinking "$nivel" --objeto "${INSTR[${RELACION[$e]}]}"
      done
    done
    if [ "${OPCIONALES:-0}" = "1" ]; then
      for nivel in minimal medium; do
        consultar exp4 --escena "$e" --tarea propio --thinking "$nivel" --objeto "${INSTR[${RELACION[$e]}]}"
      done
    fi ;;

  zona)  # volumen de trabajo a partir de las 4 esquinas de la hoja (o de la zona útil marcada)
    consultar zona --escena regla --tarea puntos --objeto "las cuatro esquinas de la hoja blanca"
    python3 espacio_trabajo.py volumen --escena regla --h-max "${H_MAX:?Defina H_MAX (m), altura del objeto más alto}" ;;

  exp5)
    for e in agarre_s1 agarre_s2 agarre_s3 agarre_i1 agarre_i2 agarre_i3; do
      consultar exp5 --escena "$e" --tarea agarre --objeto "la caja de hamburguesa"
    done
    consultar exp5 --escena burger --tarea plan --objeto "llevar la caja libre a la bandeja"
    python3 espacio_trabajo.py validar-plan --escena burger
    sim=()
    [ "${SIMULAR:-0}" = "1" ] && sim=(--respuesta '{"exito": false, "confianza": 0.5, "evidencia": "simulado"}')
    for par in "exito_antes1 exito_bien1" "exito_antes1 exito_mal1" "exito_antes2 exito_bien2" "exito_antes2 exito_mal2"; do
      set -- $par
      echo "--- [$GRUPO-exp5] exito $1 -> $2"
      python3 embodied_sin_ros.py exito --antes "$1" --despues "$2" --grupo "$GRUPO-exp5" \
        --objeto "la caja libre quedó dentro de la bandeja" "${sim[@]}" || true
    done ;;

  analisis)
    python3 analisis_lab_ia_01b.py ;;

  *)
    sed -n '2,20p' "$0"; exit 1 ;;
esac
