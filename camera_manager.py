"""
camera_manager.py
-----------------
Gestión de cámaras: captura continua en hilos dedicados y buffers
circulares comprimidos en JPEG para uso eficiente de memoria.

Uso de memoria estimado:
  640×480 @ JPEG-75 ≈ 8–15 KB por frame
  30 fps × 60 s × 10 cámaras × 12 KB ≈ 216 MB
"""
import sys
import threading
import time
from collections import deque
from typing import List, Optional

import cv2
import numpy as np

import config


# ------------------------------------------------------------------
# Clase principal de captura
# ------------------------------------------------------------------

class CameraCapture:
    """
    Captura continua de una cámara en un hilo dedicado.
    Almacena frames como bytes JPEG en un deque circular (buffer de replay).
    """

    def __init__(self, camera_index: int,
                 buffer_seconds: int = config.DEFAULT_BUFFER_DURATION):
        self.index = camera_index
        self._buffer_seconds = buffer_seconds
        self._maxlen = config.CAPTURE_FPS * buffer_seconds

        # Buffer circular — frames JPEG (bytes)
        self._buffer: deque = deque(maxlen=self._maxlen)
        self._buffer_lock = threading.Lock()

        # Frame más reciente como ndarray BGR (para display live sin
        # tener que descomprimir el buffer en cada render)
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_lock = threading.Lock()

        self._cap: Optional[cv2.VideoCapture] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # Métricas
        self.is_connected = False
        self.fps_actual: float = 0.0

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Inicia el hilo de captura."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
            name=f"cam-{self.index}"
        )
        self._thread.start()

    def stop(self) -> None:
        """Detiene el hilo de captura y libera los recursos de la cámara."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._release_cap()
        self.is_connected = False

    # ------------------------------------------------------------------
    # Loop de captura (hilo dedicado)
    # ------------------------------------------------------------------

    def _capture_loop(self) -> None:
        self._open_cap()

        fps_counter = 0
        fps_timer = time.time()

        while self._running:
            if self._cap is None or not self._cap.isOpened():
                self.is_connected = False
                time.sleep(0.5)
                self._open_cap()
                continue

            ret, frame = self._cap.read()
            if not ret:
                self.is_connected = False
                time.sleep(0.3)
                self._release_cap()
                self._open_cap()
                continue

            self.is_connected = True

            # Guardar frame más reciente para display live
            with self._latest_lock:
                self._latest_frame = frame

            # Comprimir a JPEG y agregar al buffer circular
            encoded = self._encode_jpeg(frame)
            with self._buffer_lock:
                self._buffer.append(encoded)

            # Calcular FPS real
            fps_counter += 1
            elapsed = time.time() - fps_timer
            if elapsed >= 1.0:
                self.fps_actual = fps_counter / elapsed
                fps_counter = 0
                fps_timer = time.time()

        self._release_cap()

    # ------------------------------------------------------------------
    # Acceso a frames
    # ------------------------------------------------------------------

    def get_latest_frame(self) -> Optional[np.ndarray]:
        """
        Devuelve el frame más reciente como array numpy BGR.
        Thread-safe; devuelve None si todavía no hay frames.
        """
        with self._latest_lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy()

    def snapshot_buffer(self) -> List[bytes]:
        """
        Captura atómica del buffer completo en el instante actual.
        Devuelve lista de bytes JPEG ordenada de más antiguo a más reciente.
        Llamar esto en todos los CameraCapture dentro del mismo lock externo
        garantiza la sincronía del replay.
        """
        with self._buffer_lock:
            return list(self._buffer)

    # ------------------------------------------------------------------
    # Configuración en caliente
    # ------------------------------------------------------------------

    def set_buffer_seconds(self, seconds: int) -> None:
        """
        Cambia la duración del buffer sin perder los frames ya capturados.
        Se conservan los `new_maxlen` frames más recientes.
        """
        new_maxlen = config.CAPTURE_FPS * seconds
        with self._buffer_lock:
            old = list(self._buffer)
            self._buffer = deque(old[-new_maxlen:], maxlen=new_maxlen)
        self._buffer_seconds = seconds
        self._maxlen = new_maxlen

    # ------------------------------------------------------------------
    # Métricas
    # ------------------------------------------------------------------

    @property
    def buffer_fill_percent(self) -> float:
        """Fracción del buffer ocupada (0.0 – 1.0)."""
        with self._buffer_lock:
            if self._maxlen == 0:
                return 0.0
            return len(self._buffer) / self._maxlen

    @property
    def buffer_seconds_filled(self) -> float:
        """Segundos de video disponibles en el buffer."""
        fps = max(self.fps_actual, 1.0)
        with self._buffer_lock:
            return len(self._buffer) / fps

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------

    def _open_cap(self) -> None:
        cap = cv2.VideoCapture(self.index, config.CAPTURE_BACKEND)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  config.CAPTURE_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAPTURE_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS,          config.CAPTURE_FPS)
        cap.set(cv2.CAP_PROP_BUFFERSIZE,   1)   # Minimizar latencia
        self._cap = cap
        self.is_connected = cap.isOpened()

    def _release_cap(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    @staticmethod
    def _encode_jpeg(frame: np.ndarray) -> bytes:
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, config.BUFFER_JPEG_QUALITY]
        success, buf = cv2.imencode('.jpg', frame, encode_params)
        if not success:
            # Fallback: PNG sin compresión (no debería ocurrir)
            _, buf = cv2.imencode('.png', frame)
        return buf.tobytes()


# ------------------------------------------------------------------
# Detección de cámaras disponibles
# ------------------------------------------------------------------

def detect_cameras(max_index: int = config.MAX_CAMERAS_SCAN,
                   progress_callback=None) -> List[int]:
    """
    Escanea índices 0 … max_index-1 y devuelve los que tienen cámara.
    `progress_callback(current, total)` se llama en cada paso si se provee.
    """
    available = []
    for i in range(max_index):
        if progress_callback:
            progress_callback(i, max_index)
        cap = cv2.VideoCapture(i, config.CAPTURE_BACKEND)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                available.append(i)
        cap.release()
    return available
