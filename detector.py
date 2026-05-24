"""
detector.py
-----------
Detección de personas y estimación de pose corporal usando YOLOv8n-pose.

YOLOv8n-pose detecta múltiples personas simultáneamente y dibuja:
  - Bounding box por persona
  - Esqueleto de 17 puntos clave (cabeza, hombros, codos, muñecas, caderas,
    rodillas, tobillos)

La inferencia corre en un hilo de fondo para no bloquear la UI.
El hilo siempre procesa el frame más reciente disponible (descarta frames
intermedios si la UI es más rápida que el modelo).
"""
import threading
from typing import Optional

import numpy as np

import config


class FrameDetector:
    """
    Detector asíncrono de personas y pose corporal.

    Uso típico
    ----------
    detector = FrameDetector()
    detector.enable()          # inicia hilo + carga modelo en fondo

    # En cada render tick:
    detector.submit(frame)
    result = detector.get_result()
    display(result if result is not None else frame)

    detector.disable()         # detiene el hilo
    """

    def __init__(self) -> None:
        self._model = None
        self._enabled: bool = False
        self._loading: bool = False   # True mientras el modelo se carga
        self._in_frame: Optional[np.ndarray] = None
        self._out_frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._new_frame = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Referencia opcional a CombatAnalyzer (se asigna desde ui.py)
        self.analyzer = None

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def loading(self) -> bool:
        """True mientras el modelo se está cargando por primera vez."""
        return self._loading

    def toggle(self) -> bool:
        """Alterna ON/OFF. Devuelve el nuevo estado (True = activado)."""
        if self._enabled:
            self.disable()
        else:
            self.enable()
        return self._enabled

    def enable(self) -> None:
        """Activa la detección (inicia hilo en fondo)."""
        if self._enabled:
            return
        self._enabled = True
        self._loading = True
        self._stop.clear()
        self._out_frame = None
        self._thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name="FrameDetector"
        )
        self._thread.start()

    def disable(self) -> None:
        """Desactiva la detección y detiene el hilo."""
        self._enabled = False
        self._loading = False
        self._stop.set()
        self._new_frame.set()   # desbloquear el worker si está esperando
        with self._lock:
            self._out_frame = None

    def submit(self, frame: np.ndarray) -> None:
        """
        Envía un frame para procesar.
        No bloquea: si el worker aún procesa el anterior, lo sobreescribe.
        """
        if not self._enabled or self._loading:
            return
        with self._lock:
            self._in_frame = frame
        self._new_frame.set()

    def get_result(self) -> Optional[np.ndarray]:
        """
        Devuelve el último frame anotado disponible.
        None si la detección está desactivada o aún no hay resultado.
        """
        with self._lock:
            return self._out_frame

    # ------------------------------------------------------------------
    # Hilo de trabajo
    # ------------------------------------------------------------------

    def _worker(self) -> None:
        try:
            self._load_model()
        except Exception as exc:
            print(f"[Detector] Error al cargar modelo: {exc}")
            self._enabled = False
            self._loading = False
            return

        self._loading = False

        while not self._stop.is_set():
            self._new_frame.wait(timeout=0.5)
            self._new_frame.clear()

            if self._stop.is_set():
                break

            with self._lock:
                frame = self._in_frame
                self._in_frame = None

            if frame is None:
                continue

            try:
                annotated = self._run_inference(frame)
                with self._lock:
                    self._out_frame = annotated
            except Exception as exc:
                print(f"[Detector] Error en inferencia: {exc}")

    def _load_model(self) -> None:
        """
        Carga yolov8n-pose.pt.
        Si el archivo no existe lo descarga automáticamente (~6 MB).
        Hace un warm-up con un frame dummy para que el primer resultado
        sea rápido.
        """
        from ultralytics import YOLO  # import diferido: no bloquea el arranque
        self._model = YOLO("yolov8n-pose.pt")
        # Calentamiento: primera inferencia siempre es más lenta
        dummy = np.zeros((64, 64, 3), dtype=np.uint8)
        self._model(
            dummy,
            verbose=False,
            conf=config.DETECTION_CONFIDENCE,
        )

    def _run_inference(self, frame: np.ndarray) -> np.ndarray:
        """
        Ejecuta YOLOv8n-pose sobre el frame BGR.
        Devuelve el frame anotado con:
          - Bounding boxes de personas
          - Esqueleto de pose (17 keypoints + conexiones)
        """
        results = self._model(
            frame,
            verbose=False,
            conf=config.DETECTION_CONFIDENCE,
            imgsz=640,
        )
        annotated = results[0].plot()
        # Si el analyzer está activo, superponer sus overlays
        if (self.analyzer is not None and
                (self.analyzer.scoring_enabled or self.analyzer.tactical_enabled)):
            annotated = self.analyzer.process_and_draw(results[0], annotated)
        return annotated
