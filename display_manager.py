"""
display_manager.py
------------------
Utilidades de renderizado: convierte frames BGR de OpenCV en imágenes
PIL/ImageTk para mostrarlas en canvases Tkinter.

Expone:
  - bgr_to_pil()         : ndarray BGR → PIL Image RGB
  - resize_letterbox()   : redimensiona conservando aspecto (barras negras)
  - CameraCanvas         : widget Tkinter que muestra un stream de video
"""
import threading
import tkinter as tk
from typing import Callable, Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageTk

import config


# ------------------------------------------------------------------
# Funciones de utilidad de imagen
# ------------------------------------------------------------------

def bgr_to_pil(frame: np.ndarray) -> Image.Image:
    """Convierte un frame BGR de OpenCV a imagen PIL en modo RGB."""
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def resize_letterbox(img: Image.Image,
                     target_w: int,
                     target_h: int) -> Image.Image:
    """
    Redimensiona `img` al tamaño (target_w × target_h) manteniendo la
    relación de aspecto original.  El espacio sobrante se rellena con negro
    (letterboxing).
    """
    img_w, img_h = img.size
    if img_w == 0 or img_h == 0:
        return Image.new("RGB", (target_w, target_h), (0, 0, 0))

    scale  = min(target_w / img_w, target_h / img_h)
    new_w  = max(1, int(img_w * scale))
    new_h  = max(1, int(img_h * scale))

    resized = img.resize((new_w, new_h), Image.LANCZOS)
    result  = Image.new("RGB", (target_w, target_h), (0, 0, 0))
    offset_x = (target_w - new_w) // 2
    offset_y = (target_h - new_h) // 2
    result.paste(resized, (offset_x, offset_y))
    return result


def _make_placeholder(width: int, height: int, label: str) -> Image.Image:
    """Imagen de placeholder con fondo oscuro y texto centrado."""
    img  = Image.new("RGB", (width, height), (28, 28, 46))
    draw = ImageDraw.Draw(img)
    # Texto grande centrado
    tw = len(label) * 7
    th = 14
    draw.text(
        ((width - tw) // 2, (height - th) // 2),
        label,
        fill=(80, 90, 130)
    )
    return img


# ------------------------------------------------------------------
# Widget CameraCanvas
# ------------------------------------------------------------------

class CameraCanvas:
    """
    Widget compuesto (Frame + Canvas + Label) que muestra el video
    de una sola cámara dentro de una ventana Tkinter.

    Parámetros
    ----------
    parent      : tk.Widget padre
    width/height: tamaño en píxeles del área de video
    cam_index   : índice de la cámara (usado solo para etiquetas)
    on_click    : callback(cam_index) cuando el usuario hace clic
    is_main     : si True, no muestra cursor "hand2" (es la vista principal)
    """

    def __init__(self,
                 parent: tk.Widget,
                 width: int,
                 height: int,
                 cam_index: int,
                 on_click: Optional[Callable[[int], None]] = None,
                 is_main: bool = False,
                 label: Optional[str] = None):

        self.cam_index = cam_index
        self._cam_name = label or f"Cam {cam_index + 1}"
        self._width    = width
        self._height   = height
        self._is_main  = is_main

        # Referencia fuerte a PhotoImage (necesario para que Tkinter no la elimine)
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._lock  = threading.Lock()

        # Estado de zoom (solo se usa cuando la UI lo modifica)
        self._zoom_factor = 1.0
        self._zoom_center_x = 0.5  # normalizado [0..1]
        self._zoom_center_y = 0.5  # normalizado [0..1]

        # Metadatos del último render para mapear clics de canvas -> imagen
        self._last_source_w = width
        self._last_source_h = height
        self._last_crop_x = 0
        self._last_crop_y = 0
        self._last_crop_w = width
        self._last_crop_h = height
        self._last_draw_w = width
        self._last_draw_h = height
        self._last_offset_x = 0
        self._last_offset_y = 0

        # ── Contenedor con borde de selección ────────────────────────
        self._outer = tk.Frame(
            parent,
            bg=config.COLOR_BG,
            highlightthickness=2,
            highlightbackground=(
                config.COLOR_THUMB_SELECTED if is_main
                else config.COLOR_THUMB_BORDER
            )
        )

        # ── Canvas de video ──────────────────────────────────────────
        cursor = "arrow" if is_main else "hand2"
        self._canvas = tk.Canvas(
            self._outer,
            width=width,
            height=height,
            bg="black",
            highlightthickness=0,
            cursor=cursor
        )
        self._canvas.pack(fill=tk.BOTH, expand=True)

        # ── Etiqueta inferior ────────────────────────────────────────
        font_size = 10 if is_main else 8
        self._label = tk.Label(
            self._outer,
            text=self._cam_name,
            bg=config.COLOR_PANEL,
            fg=config.COLOR_TEXT_DIM,
            font=("Arial", font_size)
        )
        self._label.pack(side=tk.BOTTOM, fill=tk.X)

        # ── Eventos de clic ──────────────────────────────────────────
        if on_click and not is_main:
            cb = lambda e, idx=cam_index: on_click(idx)
            self._canvas.bind("<Button-1>", cb)
            self._outer.bind("<Button-1>",  cb)
            self._label.bind("<Button-1>",  cb)

        # Mostrar placeholder inicial
        self._show_placeholder()

    # ------------------------------------------------------------------
    # Propiedad pública: widget Tkinter a empaquetar
    # ------------------------------------------------------------------

    @property
    def widget(self) -> tk.Frame:
        return self._outer

    # ------------------------------------------------------------------
    # Actualización de frame
    # ------------------------------------------------------------------

    def update_frame(self, frame: Optional[np.ndarray]) -> None:
        """
        Muestra `frame` (array BGR) en el canvas.
        Si `frame` es None, no hace nada (mantiene la última imagen).
        Thread-safe: puede llamarse desde cualquier hilo.
        """
        if frame is None:
            return

        src_h, src_w = frame.shape[:2]
        self._last_source_w = max(1, src_w)
        self._last_source_h = max(1, src_h)

        crop = frame
        if self._zoom_factor > 1.0:
            crop_w = max(1, int(src_w / self._zoom_factor))
            crop_h = max(1, int(src_h / self._zoom_factor))

            cx = int(self._zoom_center_x * src_w)
            cy = int(self._zoom_center_y * src_h)

            x1 = max(0, min(cx - crop_w // 2, src_w - crop_w))
            y1 = max(0, min(cy - crop_h // 2, src_h - crop_h))
            x2 = x1 + crop_w
            y2 = y1 + crop_h
            crop = frame[y1:y2, x1:x2]

            self._last_crop_x = x1
            self._last_crop_y = y1
            self._last_crop_w = crop_w
            self._last_crop_h = crop_h
        else:
            self._last_crop_x = 0
            self._last_crop_y = 0
            self._last_crop_w = src_w
            self._last_crop_h = src_h

        pil_img = bgr_to_pil(crop)

        img_w, img_h = pil_img.size
        scale = min(self._width / max(1, img_w), self._height / max(1, img_h))
        self._last_draw_w = max(1, int(img_w * scale))
        self._last_draw_h = max(1, int(img_h * scale))
        self._last_offset_x = (self._width - self._last_draw_w) // 2
        self._last_offset_y = (self._height - self._last_draw_h) // 2

        pil_img = resize_letterbox(pil_img, self._width, self._height)
        photo   = ImageTk.PhotoImage(pil_img)

        with self._lock:
            self._photo = photo                  # Mantener referencia fuerte
            self._canvas.create_image(0, 0, anchor=tk.NW, image=self._photo)

    # ------------------------------------------------------------------
    # Zoom
    # ------------------------------------------------------------------

    def set_zoom_factor(self, factor: float) -> None:
        self._zoom_factor = max(1.0, min(float(factor), 4.0))

    def get_zoom_factor(self) -> float:
        return self._zoom_factor

    def zoom_in(self, step: float = 0.25) -> None:
        self.set_zoom_factor(self._zoom_factor + step)

    def zoom_out(self, step: float = 0.25) -> None:
        self.set_zoom_factor(self._zoom_factor - step)

    def reset_zoom(self) -> None:
        self._zoom_factor = 1.0
        self._zoom_center_x = 0.5
        self._zoom_center_y = 0.5

    def set_zoom_center_from_canvas(self, x: int, y: int) -> bool:
        """
        Ajusta el centro del zoom usando coordenadas del canvas.
        Devuelve True si el punto cae dentro del área visible de video.
        """
        dw = self._last_draw_w
        dh = self._last_draw_h
        if dw <= 0 or dh <= 0:
            return False

        if x < self._last_offset_x or y < self._last_offset_y:
            return False
        if x > self._last_offset_x + dw or y > self._last_offset_y + dh:
            return False

        rel_x = (x - self._last_offset_x) / max(1, dw)
        rel_y = (y - self._last_offset_y) / max(1, dh)

        src_x = self._last_crop_x + rel_x * self._last_crop_w
        src_y = self._last_crop_y + rel_y * self._last_crop_h

        self._zoom_center_x = max(0.0, min(src_x / max(1, self._last_source_w), 1.0))
        self._zoom_center_y = max(0.0, min(src_y / max(1, self._last_source_h), 1.0))
        return True

    # ------------------------------------------------------------------
    # Eventos
    # ------------------------------------------------------------------

    def bind_video_event(self, sequence: str, callback) -> None:
        """Asocia un evento del canvas de video."""
        self._canvas.bind(sequence, callback)

    # ------------------------------------------------------------------
    # Apariencia
    # ------------------------------------------------------------------

    def set_selected(self, selected: bool) -> None:
        """Cambia el borde de selección."""
        color = (config.COLOR_THUMB_SELECTED if selected
                 else config.COLOR_THUMB_BORDER)
        self._outer.config(highlightbackground=color)

    def set_label_text(self, text: str) -> None:
        self._cam_name = text
        self._label.config(text=text)

    def set_label_color(self, color: str) -> None:
        self._label.config(fg=color)

    # ------------------------------------------------------------------
    # Placeholder
    # ------------------------------------------------------------------

    def _show_placeholder(self) -> None:
        placeholder = _make_placeholder(
            self._width, self._height, self._cam_name
        )
        self._photo = ImageTk.PhotoImage(placeholder)
        self._canvas.create_image(0, 0, anchor=tk.NW, image=self._photo)
