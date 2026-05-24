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
                 is_main: bool = False):

        self.cam_index = cam_index
        self._width    = width
        self._height   = height
        self._is_main  = is_main

        # Referencia fuerte a PhotoImage (necesario para que Tkinter no la elimine)
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._lock  = threading.Lock()

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
            text=f"Cam {cam_index + 1}",
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

        pil_img = bgr_to_pil(frame)
        pil_img = resize_letterbox(pil_img, self._width, self._height)
        photo   = ImageTk.PhotoImage(pil_img)

        with self._lock:
            self._photo = photo                  # Mantener referencia fuerte
            self._canvas.create_image(0, 0, anchor=tk.NW, image=self._photo)

    # ------------------------------------------------------------------
    # Apariencia
    # ------------------------------------------------------------------

    def set_selected(self, selected: bool) -> None:
        """Cambia el borde de selección."""
        color = (config.COLOR_THUMB_SELECTED if selected
                 else config.COLOR_THUMB_BORDER)
        self._outer.config(highlightbackground=color)

    def set_label_text(self, text: str) -> None:
        self._label.config(text=text)

    def set_label_color(self, color: str) -> None:
        self._label.config(fg=color)

    # ------------------------------------------------------------------
    # Placeholder
    # ------------------------------------------------------------------

    def _show_placeholder(self) -> None:
        placeholder = _make_placeholder(
            self._width, self._height, f"Cam {self.cam_index + 1}"
        )
        self._photo = ImageTk.PhotoImage(placeholder)
        self._canvas.create_image(0, 0, anchor=tk.NW, image=self._photo)
