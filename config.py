# ==============================================================
# SISTEMA DE VIDEO REPLAY — Configuración Global
# Modifica estos valores para ajustar el comportamiento
# ==============================================================
import sys

# ------------------------------------------------------------------
# Captura de cámaras
# ------------------------------------------------------------------
CAPTURE_WIDTH  = 640    # Resolución de captura (ancho)
CAPTURE_HEIGHT = 480    # Resolución de captura (alto)
CAPTURE_FPS    = 30     # FPS objetivo de captura

# ------------------------------------------------------------------
# Buffer de replay
# ------------------------------------------------------------------
# Opciones de duración del buffer disponibles en la UI (segundos)
BUFFER_DURATIONS = [30, 45, 60]
DEFAULT_BUFFER_DURATION = 30

# Calidad JPEG usada al comprimir frames para el buffer (0–100).
# Mayor calidad = mejor imagen, más RAM. 75 es un buen balance.
# Con 10 cámaras a 30fps, 60s y calidad 75:
#   ~12 KB/frame × 1800 frames × 10 cámaras ≈ 216 MB RAM
BUFFER_JPEG_QUALITY = 75
# Ancho máximo (px) de los frames guardados. Las cámaras IP/NVR en 1080p o 4K
# se reducen a este ancho para no saturar CPU ni RAM. 0 = sin límite.
BUFFER_MAX_WIDTH = 1280

# ------------------------------------------------------------------
# Velocidades de replay
# ------------------------------------------------------------------
REPLAY_SPEEDS         = [0.25, 0.5, 1.0]
DEFAULT_REPLAY_SPEED  = 1.0

# ------------------------------------------------------------------
# Detección de cámaras al arrancar
# ------------------------------------------------------------------
# Se escanearán los índices 0 … MAX_CAMERAS_SCAN-1
MAX_CAMERAS_SCAN = 10

# Red local para detectar cámaras IP / NVR. Si está vacío, se infiere desde
# la IP actual del equipo; si se especifica, debe ser un CIDR válido, por ej.:
# "192.168.1.0/24"
NETWORK_SCAN_SUBNET = ""
NETWORK_SCAN_TIMEOUT = 0.4
# Puertos que se sondean en cada host (se añaden RTSP, HTTP y puertos SDK de
# Hikvision/Dahua automáticamente)
NETWORK_DISCOVERY_PORTS = [554, 8554, 80, 8080, 8000, 37777]
# Rutas RTSP sin credenciales que se prueban en cámaras abiertas
NETWORK_DISCOVERY_PATHS = [
    "/stream1",
    "/live",
    "/cam/realmonitor?channel=1&subtype=0",
    "/h264/ch1/main/av_stream",
    "/Streaming/Channels/101",
    "/11",
]

# URLs fijas que siempre aparecen en el selector, por ej.:
# "rtsp://usuario:clave@192.168.1.50:554/stream1"
# (Las credenciales de NVR y URLs agregadas desde el selector se guardan en
#  network_cameras.json, que no se sube a git.)
MANUAL_NETWORK_CAMERA_URLS = []

# ------------------------------------------------------------------
# Ventana principal
# ------------------------------------------------------------------
WINDOW_WIDTH  = 1280
WINDOW_HEIGHT = 760

# FPS de refresco de la UI (no afecta la captura)
UI_RENDER_FPS        = 30   # Canvas principal
THUMBNAIL_RENDER_FPS = 10   # Miniaturas (se calculan con after())

# ------------------------------------------------------------------
# Colores de la interfaz (tema oscuro profesional)
# ------------------------------------------------------------------
COLOR_BG            = "#1a1a2e"
COLOR_PANEL         = "#16213e"
COLOR_ACCENT        = "#0f3460"
COLOR_LIVE          = "#00c853"
COLOR_REPLAY        = "#ff6d00"
COLOR_PAUSED        = "#ffdd00"
COLOR_BTN_REPLAY    = "#e63946"
COLOR_BTN_LIVE      = "#2ec4b6"
COLOR_TEXT          = "#ffffff"
COLOR_TEXT_DIM      = "#aaaaaa"
COLOR_THUMB_BORDER  = "#2a3a5e"
COLOR_THUMB_SELECTED= "#00c853"

# ------------------------------------------------------------------
# Backend de captura (ajustado por plataforma)
# ------------------------------------------------------------------
import cv2 as _cv2
if sys.platform == "win32":
    # CAP_MSMF (Media Foundation) funciona con cámaras reales y virtuales.
    # CAP_DSHOW puede fallar con algunos drivers de cámara virtual (OBS, etc.).
    CAPTURE_BACKEND = _cv2.CAP_MSMF
else:
    CAPTURE_BACKEND = _cv2.CAP_ANY
