"""
replay_controller.py
--------------------
Lógica de replay multi-cámara sincronizado.

Flujo:
  1. trigger_replay()  →  hace snapshot atómico de todos los buffers
  2. Construye una lista lineal de frames indexados por posición temporal
  3. El hilo `_replay_loop` entrega cada dict {cam_idx: bgr_frame} al callback
  4. stop_replay() / pause() / resume() controlan el estado
"""
import threading
import time
from enum import Enum
from typing import Callable, Dict, List, Optional

import cv2
import numpy as np

import config


class ReplayState(Enum):
    LIVE    = "live"
    PLAYING = "playing"
    PAUSED  = "paused"


class ReplayController:
    """
    Gestiona la reproducción del replay sincronizado de múltiples cámaras.

    Parámetros
    ----------
    on_frame_callback :
        Callable(frames: Dict[int, np.ndarray], current_idx: int, total: int)
        Llamado desde el hilo de replay con cada frame decodificado.
    """

    def __init__(self,
                 on_frame_callback: Callable[[Dict[int, np.ndarray], int, int], None]):
        self._callback = on_frame_callback

        self._state      = ReplayState.LIVE
        self._state_lock = threading.Lock()

        # Lista de dicts {cam_idx: jpeg_bytes} indexada temporalmente
        self._replay_frames: List[Dict[int, bytes]] = []
        self._current_idx = 0
        self._speed       = config.DEFAULT_REPLAY_SPEED

        self._replay_thread: Optional[threading.Thread] = None
        self._pause_event = threading.Event()
        self._pause_event.set()   # Arrancar sin pausa
        self._stop_event  = threading.Event()

        # Callback opcional al cambiar de estado (se llama desde el hilo de replay)
        self.on_state_change: Optional[Callable[[ReplayState], None]] = None

    # ------------------------------------------------------------------
    # Propiedades de solo lectura
    # ------------------------------------------------------------------

    @property
    def state(self) -> ReplayState:
        return self._state

    @property
    def current_index(self) -> int:
        return self._current_idx

    @property
    def total_frames(self) -> int:
        return len(self._replay_frames)

    @property
    def current_time_seconds(self) -> float:
        fps = max(config.CAPTURE_FPS, 1)
        return self._current_idx / fps

    @property
    def total_seconds(self) -> float:
        fps = max(config.CAPTURE_FPS, 1)
        return len(self._replay_frames) / fps

    # ------------------------------------------------------------------
    # Trigger del replay
    # ------------------------------------------------------------------

    def trigger_replay(self, cameras: dict, duration_sec: int) -> None:
        """
        Inicia el replay.

        Parámetros
        ----------
        cameras     : dict {index: CameraCapture}
        duration_sec: segundos de buffer a reproducir
        """
        # Detener cualquier replay en curso
        self.stop_replay()

        # ── Snapshot sincronizado ──────────────────────────────────────
        # Lanzar un hilo por cámara para llamar snapshot_buffer()
        # simultáneamente y minimizar el desfase temporal entre cámaras.
        snapshots: Dict[int, List[bytes]] = {}
        snap_lock = threading.Lock()

        def _snap(idx, cam):
            buf = cam.snapshot_buffer()
            with snap_lock:
                snapshots[idx] = buf

        threads = [
            threading.Thread(target=_snap, args=(idx, cam), daemon=True)
            for idx, cam in cameras.items()
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if not snapshots:
            return

        # ── Recortar al duration_sec solicitado ────────────────────────
        max_frames = config.CAPTURE_FPS * duration_sec
        for idx in list(snapshots.keys()):
            snapshots[idx] = snapshots[idx][-max_frames:]

        # Usar la longitud más corta para mantener sincronía perfecta
        min_len = min(len(v) for v in snapshots.values())
        if min_len == 0:
            return

        # ── Construir lista temporal de frames ─────────────────────────
        # Cada elemento es un dict con los bytes JPEG de todas las cámaras
        # en ese instante temporal (índice = posición en el tiempo).
        self._replay_frames = [
            {idx: snapshots[idx][i] for idx in snapshots}
            for i in range(min_len)
        ]

        self._current_idx = 0
        self._stop_event.clear()
        self._pause_event.set()   # Empezar reproduciendo (no pausado)

        self._set_state(ReplayState.PLAYING)

        self._replay_thread = threading.Thread(
            target=self._replay_loop,
            daemon=True,
            name="replay-loop"
        )
        self._replay_thread.start()

    # ------------------------------------------------------------------
    # Control de reproducción
    # ------------------------------------------------------------------

    def pause(self) -> None:
        if self._state == ReplayState.PLAYING:
            self._pause_event.clear()
            self._set_state(ReplayState.PAUSED)

    def resume(self) -> None:
        if self._state == ReplayState.PAUSED:
            self._pause_event.set()
            self._set_state(ReplayState.PLAYING)

    def toggle_pause(self) -> None:
        if self._state == ReplayState.PLAYING:
            self.pause()
        elif self._state == ReplayState.PAUSED:
            self.resume()

    def stop_replay(self) -> None:
        """Detiene el replay inmediatamente y vuelve a estado LIVE."""
        self._stop_event.set()
        self._pause_event.set()   # Desbloquear si estaba pausado
        if self._replay_thread and self._replay_thread.is_alive():
            self._replay_thread.join(timeout=2.0)
        self._replay_frames = []
        self._current_idx   = 0
        self._set_state(ReplayState.LIVE)

    def set_speed(self, speed: float) -> None:
        """Cambia la velocidad de reproducción. Efecto inmediato."""
        self._speed = max(0.05, float(speed))

    def seek(self, frame_index: int) -> None:
        """Salta a un frame concreto del replay."""
        if 0 <= frame_index < len(self._replay_frames):
            self._current_idx = frame_index

    def seek_and_preview(self, frame_index: int) -> None:
        """
        Salta a un frame y lo entrega inmediatamente al callback.
        Útil al arrastrar el timeline en modo pausado: el frame
        se actualiza en pantalla sin necesidad de reanudar.
        """
        if not (0 <= frame_index < len(self._replay_frames)):
            return
        self._current_idx = frame_index
        frame_dict_bytes = self._replay_frames[frame_index]
        decoded: Dict[int, np.ndarray] = {}
        for idx, jpeg_bytes in frame_dict_bytes.items():
            arr   = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is not None:
                decoded[idx] = frame
        if decoded:
            try:
                self._callback(decoded, frame_index, len(self._replay_frames))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Loop de reproducción (hilo separado)
    # ------------------------------------------------------------------

    def _replay_loop(self) -> None:
        fps        = max(config.CAPTURE_FPS, 1)
        base_delay = 1.0 / fps          # Delay a velocidad 1×

        while self._current_idx < len(self._replay_frames):
            # ── Cancelación ──────────────────────────────────────────
            if self._stop_event.is_set():
                break

            # ── Pausa ────────────────────────────────────────────────
            self._pause_event.wait()
            if self._stop_event.is_set():
                break

            # ── Decodificar frame actual ──────────────────────────────
            frame_dict_bytes = self._replay_frames[self._current_idx]
            decoded: Dict[int, np.ndarray] = {}

            for idx, jpeg_bytes in frame_dict_bytes.items():
                arr   = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    decoded[idx] = frame

            # ── Entregar al callback ──────────────────────────────────
            if decoded:
                self._callback(decoded, self._current_idx, len(self._replay_frames))

            self._current_idx += 1

            # ── Esperar según velocidad ───────────────────────────────
            delay = base_delay / max(self._speed, 0.05)
            # Dormir en pasos cortos para poder cancelar sin latencia alta
            end_time = time.perf_counter() + delay
            while time.perf_counter() < end_time:
                if self._stop_event.is_set():
                    break
                remaining = end_time - time.perf_counter()
                time.sleep(min(remaining, 0.020))  # pasos de 20 ms máx

        # Fin del replay: quedarse en el último frame (PAUSED).
        # El usuario decide cuándo volver a LIVE.
        if not self._stop_event.is_set():
            # Asegurarse de que el índice apunta al último frame
            self._current_idx = max(0, len(self._replay_frames) - 1)
            self._pause_event.clear()   # Marcar como pausado
            self._set_state(ReplayState.PAUSED)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_state(self, new_state: ReplayState) -> None:
        with self._state_lock:
            self._state = new_state
        if self.on_state_change:
            try:
                self.on_state_change(new_state)
            except Exception:
                pass
