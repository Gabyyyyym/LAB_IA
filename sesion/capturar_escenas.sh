#!/usr/bin/env bash
# Turno de captura (Ruta A, 15 min, un solo consumidor RTSP). Ejecutar desde cualquier carpeta:
#
#     bash sesion/capturar_escenas.sh            # recorre la lista y omite las escenas ya capturadas
#     IP=192.168.1.10 bash sesion/capturar_escenas.sh
#
# Antes del turno: driver ROS 2 de la anfitriona DETENIDO, ping -c 3 192.168.1.10 y
# python3 ~/ros2_ws/src/burger_delivery/scripts/test_kinova_camera.py --ip 192.168.1.10 --stream depth
# El orden minimiza cuántos objetos se mueven entre escenas. Ninguna escena debe mostrar
# personas, rostros, carnés ni documentos (en agarre_i2 solo la mano, sin rasgos identificables).
set -u
cd "$(dirname "$0")/.."
IP="${IP:-192.168.1.10}"
[ -z "${VIRTUAL_ENV:-}" ] && [ -f ~/venv_embodied/bin/activate ] && source ~/venv_embodied/bin/activate

# Comprobación rápida de red antes de gastar el turno (puerto RTSP 554 de la cámara).
if ! python3 -c "import socket,sys; socket.create_connection(('$IP', 554), timeout=3)" 2>/dev/null; then
  echo "[ERROR] No hay conexión con la cámara en $IP:554."
  echo "        Conecte el portátil a la red del robot (Wi-Fi 'ros2' o cable, subred 192.168.1.x)"
  echo "        y confirme que el driver ROS 2 de la anfitriona está detenido (un solo consumidor RTSP)."
  exit 2
fi
echo "[OK] Cámara accesible en $IP:554"

ESCENAS=(
  "regla|Hoja A4 HORIZONTAL (0.297 m), centrada y paralela a la imagen. Medir Z con cinta desde el frente del módulo de visión: Z = ____ m"
  "semantica1|D seguridad: dos cajas de hamburguesa; termo encima de una"
  "agarre_i1|Agarre inseguro: como semantica1 pero SIN la caja libre: solo la caja con el termo encima"
  "semantica1p|P seguridad: cajas en otras posiciones; cuaderno encima de una; bandeja como distractor"
  "burger|D relacional: dos cajas, una sobre la bandeja y otra fuera"
  "burgerp|P relacional: bandeja al otro lado; la caja libre en otra posición; termo como distractor"
  "exito_antes1|Antes 1: caja fuera de la bandeja (tarea: la caja libre quedó dentro de la bandeja)"
  "exito_bien1|Después BIEN 1: la caja quedó dentro de la bandeja"
  "exito_mal1|Después MAL 1: (volver al estado de exito_antes1) la caja quedó junto a la bandeja, no dentro"
  "exito_antes2|Antes 2: otra posición inicial de la caja y la bandeja"
  "exito_bien2|Después BIEN 2: la caja quedó dentro de la bandeja"
  "exito_mal2|Después MAL 2: (volver al estado de exito_antes2) la caja quedó apoyada en el borde, medio afuera"
  "agarre_s1|Agarre seguro: caja sola y libre"
  "agarre_s2|Agarre seguro: caja libre con otros objetos lejos"
  "agarre_s3|Agarre seguro: caja libre girada en otra posición"
  "agarre_i3|Agarre inseguro: caja volcada o apoyada en un borde (inestable)"
  "agarre_i2|Agarre inseguro: mano cerca de la caja (solo la mano, sin anillos ni tatuajes)"
  "altura1|Caja sola sobre la mesa; medir su altura con regla: h = ____ m"
  "dist1|Termo y caja a distancia medida centro-centro de las tapas (centro de la imagen): d = ____ m"
  "dist2|Otra distancia, centro de la imagen: d = ____ m"
  "dist3|Otra distancia, centro de la imagen: d = ____ m"
  "dist4|Uno de los objetos CERCA DEL BORDE de la imagen: d = ____ m"
  "dist5|Otro caso CERCA DEL BORDE (otro lado): d = ____ m"
  "semantica2|D pertenencia: dos celulares; llaves junto a uno"
  "semantica2p|P pertenencia: celulares girados en otras posiciones; llaves junto al otro; esfero como distractor"
  "semantica3|D estado: un cuaderno abierto y otro cerrado"
  "semantica3p|P estado: posiciones intercambiadas, otro cuaderno; estuche como distractor"
  "semantica4|D contención: un esfero dentro del estuche abierto y dos sueltos"
  "semantica4p|P contención: otro esfero dentro; uno apoyado SOBRE el estuche (no dentro)"
)

pct_valido() {
  python3 - "$1" <<'PY' 2>/dev/null || echo 0
import json, sys
m = json.load(open(f"capturas/{sys.argv[1]}_meta.json", encoding="utf-8"))
print(m.get("profundidad", {}).get("pixeles_validos_pct", 0))
PY
}

for item in "${ESCENAS[@]}"; do
  esc="${item%%|*}"
  nota="${item#*|}"
  if [ -f "capturas/${esc}_color.png" ]; then
    echo "[=] ${esc} ya existe; se omite."
    continue
  fi
  echo
  echo "=== ${esc}: ${nota}"
  read -r -p "Prepare la escena y pulse Enter (s = saltar, q = terminar): " r
  case "$r" in s|S) continue ;; q|Q) break ;; esac
  read -r -p "Nota final (Enter = la de arriba; escriba aquí las medidas Z, d o h): " nota_usuario
  [ -n "$nota_usuario" ] && nota="$nota_usuario"
  while true; do
    python3 embodied_sin_ros.py capturar --ip "$IP" --escena "$esc" --nota "$nota" --sobrescribir \
      || echo "[ERROR] La captura de ${esc} falló."
    pct=$(pct_valido "$esc")
    if python3 -c "import sys; sys.exit(0 if float('${pct:-0}') >= 60 else 1)"; then
      echo "[OK] ${esc}: ${pct} % de profundidad válida."
      break
    fi
    read -r -p "[AVISO] ${esc}: ${pct} % de profundidad válida (< 60 %). ¿Repetir? [S/n] " r2
    [ "$r2" = "n" ] && break
  done
done
echo
echo "Capturas en $(pwd)/capturas. Llene verdad_terreno.csv ANTES de consultar a Gemini."
