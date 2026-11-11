# Protocolo del grupo — LAB-IA-01B (Gemini Robotics-ER 2 + Kinova Gen3)

Protocolo de control de variables (evidencia **E6**) y lista de pasos para la sesión. Lo que no
requiere el robot ya está hecho y probado; lo marcado con ☐ queda para el grupo.

## 0. Estado

| Pieza | Estado | Dónde |
|---|---|---|
| TODO 2, 3, 4 (`embodied_comun.py`) | Hecho · `pytest tests` → 15 passed (Windows y Ubuntu 24.04) | `embodied_comun.py` |
| TODO 5 (Ruta B, `_publicar`) | Hecho · probado con `rclpy` simulado | `embodied_ros2_nodo.py`, `tests_grupo/test_cli_y_nodo.py` |
| TODO 1 (prompt propio) | **Iteración 0** escrita; falta ajustarla sobre las escenas D y congelarla | `PROMPTS["propio"]` |
| Fase 9: volumen, normal, pre-agarre, validación del plan (a)-(d) | Hecho · 13 pruebas | `espacio_trabajo.py` |
| Fase 10: script que regenera las Tablas 3-11 y 3 figuras | Hecho · probado con datos sintéticos de resultado conocido | `analisis_lab_ia_01b.py` |
| Verdad de terreno pre-registrada | Plantilla con todas las filas; faltan las cajas en px y las medidas | `verdad_terreno.csv` |
| Scripts de la sesión | Hechos · simulados de punta a punta en Ubuntu | `sesion/` |

Cambios a archivos del docente (decláralos en el informe):

- `embodied_sin_ros.py`: una respuesta con JSON inválido o un error de la API (p. ej. un
  `thinking_level` rechazado) ahora **se registra en `resultados.csv`** (`label = JSON_INVALIDO` /
  `ERROR_API`) antes de salir. Antes se perdía la fila y la guía exige contar el JSON inválido
  como fallo y que toda cifra sea rastreable al CSV.
- `embodied_sin_ros.py`: la imagen anotada se llama `<escena>_<tarea>[_sinreg]_<HHMMSS>_resultado.png`.
  Antes la consulta sin registro sobrescribía la imagen con registro, y la evidencia E6 pide ambas.

Pruebas: `python3 -m pytest tests -q` (las 15 del docente) y `python3 -m pytest tests_grupo -q` (25 del grupo).

## 1. Antes de la sesión (en casa)

- ☐ Fork de `roncanciovl/burger_delivery` y rama del grupo; copiar esta carpeta y hacer commit
  **«TODO 2-5 resueltos»** → SHA en la Tabla 1 (E1). Cada integrante hace commits con su usuario
  (lo pide la pregunta 5 de E10).
- ☐ En Ubuntu: instalación de `README.md` (OpenCV de apt, venv con `--system-site-packages`,
  `pip install -r requirements.txt`). Anotar `pip show google-genai` y `python3 --version` (Tabla 1).
- ☐ Clave en un archivo privado que `sesion/experimentos.sh` lee solo (se escribe sin mostrarla):
  `mkdir -p ~/.config && read -rs -p "Clave: " k && printf '%s' "$k" > ~/.config/gemini_api_key && chmod 600 ~/.config/gemini_api_key`.
  **Nunca** en el repositorio ni en los anexos.
- ☐ Probar clave, nombre del modelo y cuota con una foto propia **sin personas ni documentos**:
  guardarla como `capturas/prueba_color.png` y
  `python3 embodied_sin_ros.py consultar --escena prueba --tarea puntos --objeto "la taza" --grupo G03-prueba`.
- ☐ Revisar la cuota diaria del modelo *preview*: la matriz completa son unas **90 consultas** más
  las iteraciones del prompt (≈ 5 por iteración).
- ☐ Escribir las **hipótesis** en el informe (sección 3) antes de ver datos.
- ☐ Repartir roles. La persona que arma las escenas **P** no escribe el prompt.
- ☐ Conseguir objetos: 2 cajas de hamburguesa, termo, 2 celulares de utilería, llaves,
  2 cuadernos, estuche y 3 esferos, bandeja; cinta métrica, regla y hoja A4.

## 2. En el laboratorio

1. ☐ **Revisión de la matriz (Tabla 2) con el docente**: sin ella no se pasa a la Fase 2.
2. ☐ Turno de captura (Ruta A, 15 min, driver ROS 2 de la anfitriona detenido):
   `bash sesion/capturar_escenas.sh`. Recorre las 29 escenas en un orden que mueve pocos objetos,
   pide la nota de cada una y repite si hay < 60 % de profundidad válida.
3. ☐ Medidas físicas que se anotan en la nota de la escena:
   - `regla`: distancia Z cámara-hoja (cinta, desde el frente del módulo de visión).
   - `dist1…dist5`: distancia centro-centro de las tapas. Si las tapas están a distinta
     altura, d_real = √(d_horizontal² + Δh²).
   - `altura1`: altura de la caja con regla.
4. ☐ Encuadre: si f_x ≈ 2600 px (valor del archivo por defecto), a 0.60 m la imagen cubre solo
   ≈ 44 × 25 cm. Todas las escenas deben caber en ese rectángulo; `dist4` y `dist5` cerca del borde.
5. ☐ Privacidad: nada de rostros, carnés ni documentos. En `agarre_i2` solo la mano.

## 3. Después (sin robot)

1. ☐ **Calibración rápida** (Tabla 4): `python3 sesion/detectar_hoja.py` detecta la hoja, da u1 y u2,
   compara la Z de la cinta con la del sensor, deja `capturas/regla_hoja_detectada.png` (E3) e
   imprime el comando `calibrar` listo. Si el docente comparte `config_camara_kinova.json` de la
   Kortex API, usar ese.
2. ☐ **Verdad de terreno**: `python3 sesion/anotar_verdad.py` abre cada captura para arrastrar la
   caja de cada objeto y pide las distancias y la altura medidas. Commit **«verdad de terreno
   pre-registrada»** → SHA (E5).
3. ☐ Verificación: `GRUPO=G03 bash sesion/experimentos.sh calib` → error < 5 % (si no, recalibrar).
4. ☐ Desarrollo del prompt **solo con escenas D**: editar `PROMPTS["propio"]`,
   `GRUPO=G03 bash sesion/experimentos.sh dev <k>` y anotar en la Tabla 5 qué cambió y por qué.
5. ☐ Commit **«prompt congelado»** → SHA (Tabla 1). Desde aquí el prompt no cambia.
6. ☐ `exp1`, `exp2`, `exp3`, luego `exp4 <escena P de peor resultado en exp1>`,
   `H_MAX=<m> … zona` y `exp5` (todos con `GRUPO=G03 bash sesion/experimentos.sh …`).
7. ☐ `python3 analisis_lab_ia_01b.py` → `analisis/tablas.md`, CSV por tabla y `analisis/figuras/`.
   Revisar la sección «Avisos de calidad de los datos».
8. ☐ Pasar las tablas al informe, escribir análisis y conclusiones contra las hipótesis.

## 4. Convención de etiquetas (columna `grupo`)

| Etiqueta | Qué es | Tabla |
|---|---|---|
| `G03-dev0`, `G03-dev1`, … | Iteraciones del prompt sobre escenas D | 5 |
| `G03-exp1` | Prompt congelado, D y P, 3 rep., `thinking=low`; ingenuo en `burger` | 6 |
| `G03-exp2` | Cajas, 5 rep. en `semantica1` y `burger` | 7 |
| `G03-exp3` | Distancias con y sin registro; altura | 8 |
| `G03-exp4` | `low`/`high` intercalados, 5 rep., escena P fija | 9 |
| `G03-exp5` | Agarre, plan y detección de éxito | 10a-c |
| `G03-calib`, `G03-zona` | Verificación de la calibración y puntos del volumen de trabajo | 4, 10b |

El script de análisis usa la etiqueta para separar experimentos; sin ella infiere por escena y
tarea y lo advierte.

## 5. Control de variables (E6)

| Variable | Cómo se controla |
|---|---|
| Modelo y SDK | `gemini-robotics-er-2-preview`, versión de `google-genai` fija (Tabla 1) |
| Temperatura | 0.5 en todas las consultas (valor por defecto del CLI) |
| `thinking_level` | `low` en todo salvo el Exp. 4 |
| Prompt | Congelado por commit antes de consultar escenas P; SHA en la Tabla 1 |
| Imagen | La misma captura guardada para todas las repeticiones (no hay variación de cámara) |
| Pose de la cámara | Fija durante la sesión (la anfitriona no mueve el brazo) |
| Verdad de terreno | Pre-registrada por commit antes de la primera consulta (SHA) |
| Regla de acierto | Automática: el píxel cae en la caja de verdad (sin juicio a posteriori) |
| Ablación del registro | La consulta sin registro reutiliza **exactamente** la respuesta de la consulta con registro (`--respuesta`); el script verifica el pareo |
| Orden | D y P intercalados en el Exp. 1; `low`/`high` alternando quién va primero en el Exp. 4 |
| Latencia y tokens | Se excluyen las filas `modelo = manual` |
| Fechas | Columna `fecha` del CSV (ISO 8601) en cada fila |
| Sesgo de quien diseña | Las escenas P las arma quien no escribe el prompt |
