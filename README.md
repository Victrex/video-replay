# 🎥 Sistema de Video Replay Multi-Cámara

Sistema de replay en vivo para competencias de Taekwondo. Conecta entre 1 y 10 webcams simultáneamente, guarda un buffer continuo de los últimos 30/45/60 segundos y permite revisar cualquier momento con un solo clic.

---

## Requisitos

- **Sistema operativo:** Windows 10/11 (también funciona en Linux/macOS)
- **Python:** 3.10 o superior
- **Cámaras:** Webcams USB estándar (UVC), cámaras virtuales (OBS, etc.)

---

## Instalación

### 1. Instalar Python

Descargá Python desde [python.org](https://www.python.org/downloads/) e instalalo.  
Durante la instalación, marcá la opción **"Add Python to PATH"**.

Verificá en la terminal:
```bash
python --version
```
Debe mostrar `Python 3.10.x` o superior.

---

### 2. Descargar el proyecto

**Opción A — Con Git:**
```bash
git clone <URL-del-repositorio>
cd "SISTEMA DE VIDEOREPLAY"
```

**Opción B — Sin Git:**  
Descargá el ZIP del repositorio, descomprimilo y abrí una terminal en la carpeta `SISTEMA DE VIDEOREPLAY`.

---

### 3. Instalar dependencias

Desde la carpeta del proyecto, ejecutá:

```bash
pip install -r requirements.txt
```

Esto instala:
- `opencv-python` — captura de video
- `Pillow` — renderizado de frames en la interfaz
- `numpy` — procesamiento de imágenes

---

### 4. Conectar las cámaras

Conectá las webcams **antes** de iniciar el programa. El sistema detecta automáticamente todas las cámaras disponibles al arrancar.

---

### 5. Ejecutar

```bash
python main.py
```

---

## Uso

### Vista en vivo

Al iniciar, el sistema detecta todas las cámaras y las muestra:

- **Canvas principal (grande):** la cámara activa seleccionada
- **Miniaturas (abajo):** todas las cámaras. Hacé clic en cualquier miniatura para verla en grande.
- **Panel derecho:** estado, controles y métricas de cada cámara

### Activar el Replay

1. Presioná el botón **⏮ REPLAY** en el panel derecho
2. El sistema congela un snapshot sincronizado de **todas** las cámaras al mismo instante
3. Comienza la reproducción automática

### Durante el Replay

| Acción | Cómo |
|---|---|
| Saltar a un momento | Clic en la barra de **timeline** |
| Scrubbing | Arrastrá el playhead del timeline |
| Pausar / Reanudar | Botón **⏸ PAUSAR** / **▶ REANUDAR** |
| Cambiar velocidad | Botones **0.25×** / **0.5×** / **1×** |
| Volver a live | Botón **● LIVE** |
| Cambiar cámara principal | Clic en miniatura |

Al terminar el replay, el video se **congela en el último frame** (no vuelve al live automáticamente). Usá el botón **● LIVE** cuando estés listo.

### Configurar el buffer

En el panel derecho podés elegir cuántos segundos atrás querés guardar:

- **30 s** — uso de RAM más bajo
- **45 s** — balance recomendado
- **60 s** — máximo disponible

> ⚠️ El cambio se aplica en el momento; si reducís el buffer, los frames más antiguos se descartan.

---

## Métricas en pantalla

El panel derecho muestra por cada cámara:

```
Cam 1: 30fps  buf=28s  [OK]
Cam 2: 29fps  buf=28s  [OK]
```

- **fps:** fotogramas por segundo reales de esa cámara
- **buf:** segundos de video disponibles en el buffer
- **OK / SIN SEÑAL:** estado de conexión

---

## Uso estimado de RAM

| Cámaras | Buffer | RAM estimada |
|---|---|---|
| 1 | 30 s | ~22 MB |
| 4 | 45 s | ~130 MB |
| 10 | 60 s | ~430 MB |

Los frames se comprimen en JPEG antes de guardarse en el buffer. La compresión se configura en `config.py` (`BUFFER_JPEG_QUALITY`).

---

## Solución de problemas

**"No se detectó ninguna cámara"**
- Verificá que la webcam esté conectada y no la esté usando otro programa (Zoom, Teams, OBS, etc.)
- En Windows, revisá los permisos de cámara en *Configuración → Privacidad → Cámara*

**Video muy lento o lagueado**
- Reducí la resolución de captura en `config.py`: `CAPTURE_WIDTH = 480`, `CAPTURE_HEIGHT = 360`
- Reducí `BUFFER_JPEG_QUALITY` a `60`

**La cámara aparece como "SIN SEÑAL" intermitentemente**
- Probá con otro puerto USB (preferentemente USB 3.0)
- Evitá usar un hub USB para múltiples cámaras simultáneas

**Error con cámaras virtuales (OBS Virtual Camera, etc.)**
- El sistema usa el backend `CAP_MSMF` en Windows, compatible con cámaras virtuales. Si sigue fallando, cambiá en `config.py`: `CAPTURE_BACKEND = cv2.CAP_ANY`

---

## Estructura del proyecto

```
SISTEMA DE VIDEOREPLAY/
├── main.py               # Punto de entrada
├── config.py             # Parámetros ajustables
├── camera_manager.py     # Captura y buffer por cámara
├── replay_controller.py  # Lógica de replay sincronizado
├── display_manager.py    # Renderizado de video en Tkinter
├── ui.py                 # Interfaz gráfica completa
└── requirements.txt      # Dependencias Python
```

---

## Personalización rápida (`config.py`)

```python
CAPTURE_WIDTH  = 640    # Resolución de captura
CAPTURE_HEIGHT = 480
CAPTURE_FPS    = 30     # FPS objetivo

BUFFER_DURATIONS = [30, 45, 60]   # Opciones de buffer (segundos)
BUFFER_JPEG_QUALITY = 75          # Calidad JPEG del buffer (0–100)

REPLAY_SPEEDS = [0.25, 0.5, 1.0]  # Velocidades disponibles

WINDOW_WIDTH  = 1280    # Tamaño de la ventana
WINDOW_HEIGHT = 760
```
