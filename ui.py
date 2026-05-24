"""
ui.py
-----
Interfaz gráfica principal del Sistema de Video Replay.

Layout
------
┌────────────────────────────────────┬───────────────┐
│                                    │               │
│        CANVAS PRINCIPAL            │  PANEL DE     │
│        (cámara activa)             │  CONTROL      │
│                                    │               │
├────────────────────────────────────┤               │
│  [Cam1] [Cam2] [Cam3] … (thumbs)  │               │
└────────────────────────────────────┴───────────────┘
"""
import threading
import tkinter as tk
from tkinter import ttk
from typing import Dict, Optional

import numpy as np

import config
from analyzer import CombatAnalyzer
from camera_manager import CameraCapture
from detector import FrameDetector
from display_manager import CameraCanvas
from replay_controller import ReplayController, ReplayState


# Tamaño del canvas principal (px)
MAIN_W = 960
MAIN_H = 540

# Tamaño de las miniaturas (px)
THUMB_H = 90
THUMB_W = int(THUMB_H * 16 / 9)   # 160 px


class VideoReplayApp:
    """Aplicación principal de Video Replay Multi-Cámara."""

    def __init__(self, root: tk.Tk,
                 cameras: Dict[int, CameraCapture]):
        self.root    = root
        self.cameras = cameras   # {index: CameraCapture}

        cam_keys = sorted(cameras.keys())
        self._main_cam_idx: int = cam_keys[0] if cam_keys else 0

        # Frames disponibles para mostrar
        self._live_frames: Dict[int, Optional[np.ndarray]]   = {i: None for i in cameras}
        self._replay_frames: Dict[int, Optional[np.ndarray]] = {i: None for i in cameras}
        self._frames_lock = threading.Lock()

        # Variables Tkinter
        self._duration_var = tk.IntVar(value=config.DEFAULT_BUFFER_DURATION)
        self._status_var   = tk.StringVar(value="● LIVE")
        self._time_var     = tk.StringVar(value="")
        self._buffer_var   = tk.StringVar(value="Calentando buffer…")

        # Widgets de video
        self._main_canvas: Optional[CameraCanvas] = None
        self._thumb_canvases: Dict[int, CameraCanvas] = {}

        # Botones de velocidad
        self._speed_buttons: Dict[float, tk.Button] = {}
        self._current_speed: float = config.DEFAULT_REPLAY_SPEED

        # Botones que cambian de estado
        self._btn_pause: Optional[tk.Button] = None
        self._btn_live:  Optional[tk.Button] = None
        self._btn_replay: Optional[tk.Button] = None
        self._btn_detect: Optional[tk.Button] = None
        self._status_label: Optional[tk.Label] = None

        # Detector de personas y pose
        self._detector = FrameDetector()
        # Analizador de combate (puntuación + táctica)
        self._analyzer = CombatAnalyzer()
        self._detector.analyzer = self._analyzer
        self._btn_scoring: Optional[tk.Button] = None
        self._btn_tactical: Optional[tk.Button] = None
        self._btn_reset_analysis: Optional[tk.Button] = None

        # Timeline / scrubber
        self._timeline_frame: Optional[tk.Frame]   = None
        self._tl_canvas: Optional[tk.Canvas]       = None
        self._tl_cur_label: Optional[tk.Label]     = None
        self._tl_end_label: Optional[tk.Label]     = None
        # Referencia al contenedor del canvas principal (para pack after=)
        self._main_canvas_container: Optional[tk.Frame] = None

        # Controlador de replay
        self._replay_ctrl = ReplayController(
            on_frame_callback=self._on_replay_frame
        )
        self._replay_ctrl.on_state_change = self._on_state_change

        self._build_ui()
        self._schedule_render()
        self._schedule_info_update()

    # ==================================================================
    # Construcción de la UI
    # ==================================================================

    def _build_ui(self) -> None:
        self.root.title("Sistema de Video Replay – Multi-Cámara")
        self.root.configure(bg=config.COLOR_BG)
        self.root.resizable(True, True)

        outer = tk.Frame(self.root, bg=config.COLOR_BG)
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # ── Columna izquierda: video ──────────────────────────────────
        video_col = tk.Frame(outer, bg=config.COLOR_BG)
        video_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_main_canvas(video_col)
        self._build_timeline(video_col)
        self._build_thumbnail_bar(video_col)

        # ── Columna derecha: controles ────────────────────────────────
        ctrl_col = tk.Frame(outer, bg=config.COLOR_PANEL, width=230)
        ctrl_col.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
        ctrl_col.pack_propagate(False)

        self._build_control_panel(ctrl_col)

    # ------------------------------------------------------------------
    # Canvas principal
    # ------------------------------------------------------------------

    def _build_main_canvas(self, parent: tk.Frame) -> None:
        container = tk.Frame(parent, bg=config.COLOR_BG)
        container.pack(fill=tk.BOTH, expand=True)
        self._main_canvas_container = container  # guardado para pack(after=)

        first_cam = sorted(self.cameras.keys())[0] if self.cameras else 0
        self._main_canvas = CameraCanvas(
            container, MAIN_W, MAIN_H,
            cam_index=first_cam,
            is_main=True
        )
        self._main_canvas.widget.pack(anchor=tk.CENTER)

    # ------------------------------------------------------------------
    # Timeline / scrubber de replay
    # ------------------------------------------------------------------

    def _build_timeline(self, parent: tk.Frame) -> None:
        """
        Barra de timeline debajo del canvas principal.
        Visible solo en modo REPLAY / PAUSED.
        Permite hacer clic o arrastrar para saltar a cualquier momento.
        """
        self._timeline_frame = tk.Frame(parent, bg="#0d1117")
        # Se muestra solo en replay; empezamos oculto
        # (pack() se llamará desde _apply_state_change)

        # ── Fila de etiquetas de tiempo ───────────────────────────────
        time_row = tk.Frame(self._timeline_frame, bg="#0d1117")
        time_row.pack(fill=tk.X, padx=10, pady=(4, 0))

        tk.Label(
            time_row, text="0s",
            bg="#0d1117", fg=config.COLOR_TEXT_DIM,
            font=("Arial", 8)
        ).pack(side=tk.LEFT)

        self._tl_cur_label = tk.Label(
            time_row, text="",
            bg="#0d1117", fg="white",
            font=("Arial", 9, "bold")
        )
        self._tl_cur_label.pack(side=tk.LEFT, expand=True)

        self._tl_end_label = tk.Label(
            time_row, text="",
            bg="#0d1117", fg=config.COLOR_TEXT_DIM,
            font=("Arial", 8)
        )
        self._tl_end_label.pack(side=tk.RIGHT)

        # ── Canvas de la barra ────────────────────────────────────────
        self._tl_canvas = tk.Canvas(
            self._timeline_frame,
            height=32, bg="#0d1117",
            highlightthickness=0,
            cursor="hand2"
        )
        self._tl_canvas.pack(fill=tk.X, padx=10, pady=(3, 6))

        self._tl_canvas.bind("<Button-1>",  self._on_timeline_click)
        self._tl_canvas.bind("<B1-Motion>", self._on_timeline_drag)

    def _update_timeline(self) -> None:
        """Redibuja la barra de progreso del timeline. Llamado desde _render_tick."""
        canvas = self._tl_canvas
        if canvas is None:
            return

        total   = self._replay_ctrl.total_frames
        current = self._replay_ctrl.current_index
        if total == 0:
            return

        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w <= 1:
            return

        progress = current / total
        fill_x   = int(w * progress)

        canvas.delete("all")

        # ── Pista de fondo ────────────────────────────────────────────
        canvas.create_rectangle(
            0, 11, w, 21,
            fill="#1e2a3a", outline=""
        )

        # ── Progreso relleno ──────────────────────────────────────────
        if fill_x > 0:
            canvas.create_rectangle(
                0, 11, fill_x, 21,
                fill=config.COLOR_REPLAY, outline=""
            )

        # ── Marcas de tiempo cada 5 s ─────────────────────────────────
        total_s = self._replay_ctrl.total_seconds
        if total_s > 0:
            step_s = 5 if total_s <= 60 else 10
            t = step_s
            while t < total_s:
                tx = int((t / total_s) * w)
                canvas.create_line(tx, 14, tx, 18, fill="#405070", width=1)
                t += step_s

        # ── Playhead ──────────────────────────────────────────────────
        px = max(2, min(fill_x, w - 2))
        canvas.create_line(px, 3, px, 29, fill="white", width=2)
        canvas.create_oval(px - 6, 5, px + 6, 17, fill="white", outline="")

        # ── Etiquetas ─────────────────────────────────────────────────
        elapsed = self._replay_ctrl.current_time_seconds
        if self._tl_cur_label:
            self._tl_cur_label.config(text=f"  {elapsed:.1f}s")
        if self._tl_end_label:
            self._tl_end_label.config(text=f"{total_s:.1f}s")

    # ── Eventos de ratón del timeline ────────────────────────────────

    def _on_timeline_click(self, event) -> None:
        self._seek_from_x(event.x)

    def _on_timeline_drag(self, event) -> None:
        self._seek_from_x(event.x)

    def _seek_from_x(self, x: int) -> None:
        """Convierte una posición X en píxeles en un índice de frame y hace seek."""
        if self._tl_canvas is None:
            return
        total = self._replay_ctrl.total_frames
        if total == 0:
            return
        w = self._tl_canvas.winfo_width()
        if w <= 0:
            return
        ratio     = max(0.0, min(x / w, 1.0))
        frame_idx = int(ratio * (total - 1))
        # seek_and_preview entrega el frame inmediatamente (útil en pausa)
        self._replay_ctrl.seek_and_preview(frame_idx)

    # ------------------------------------------------------------------
    # Barra de miniaturas
    # ------------------------------------------------------------------

    def _build_thumbnail_bar(self, parent: tk.Frame) -> None:
        bar = tk.Frame(parent, bg=config.COLOR_BG)
        bar.pack(fill=tk.X, pady=(6, 0))

        # Canvas con scrollbar horizontal (para ≥7 cámaras)
        h_scroll_canvas = tk.Canvas(
            bar, bg=config.COLOR_BG,
            height=THUMB_H + 32,
            highlightthickness=0
        )
        h_scrollbar = ttk.Scrollbar(
            bar, orient="horizontal",
            command=h_scroll_canvas.xview
        )
        h_scroll_canvas.configure(xscrollcommand=h_scrollbar.set)

        inner = tk.Frame(h_scroll_canvas, bg=config.COLOR_BG)
        h_scroll_canvas.create_window((0, 0), window=inner, anchor=tk.NW)

        for idx in sorted(self.cameras.keys()):
            tc = CameraCanvas(
                inner, THUMB_W, THUMB_H,
                cam_index=idx,
                on_click=self._select_main_camera,
                is_main=False
            )
            tc.widget.pack(side=tk.LEFT, padx=4, pady=2)
            self._thumb_canvases[idx] = tc

        inner.update_idletasks()
        h_scroll_canvas.config(scrollregion=h_scroll_canvas.bbox("all"))

        h_scroll_canvas.pack(fill=tk.X)
        if len(self.cameras) > 6:
            h_scrollbar.pack(fill=tk.X)

        self._update_thumb_selection()

    # ------------------------------------------------------------------
    # Panel de control
    # ------------------------------------------------------------------

    def _build_control_panel(self, parent: tk.Frame) -> None:
        pad = {"padx": 12, "pady": 6}

        # Título
        tk.Label(
            parent, text="VIDEO REPLAY",
            bg=config.COLOR_PANEL, fg=config.COLOR_TEXT,
            font=("Arial", 13, "bold")
        ).pack(padx=12, pady=(18, 4))

        ttk.Separator(parent, orient="horizontal").pack(fill=tk.X, padx=10)

        # ── Estado ───────────────────────────────────────────────────
        self._status_label = tk.Label(
            parent, textvariable=self._status_var,
            bg=config.COLOR_PANEL, fg=config.COLOR_LIVE,
            font=("Arial", 13, "bold")
        )
        self._status_label.pack(padx=12, pady=(10, 2))

        tk.Label(
            parent, textvariable=self._time_var,
            bg=config.COLOR_PANEL, fg=config.COLOR_TEXT_DIM,
            font=("Arial", 10)
        ).pack()

        tk.Label(
            parent, textvariable=self._buffer_var,
            bg=config.COLOR_PANEL, fg=config.COLOR_TEXT_DIM,
            font=("Arial", 9)
        ).pack(pady=(0, 4))

        ttk.Separator(parent, orient="horizontal").pack(fill=tk.X, padx=10)

        # ── Botón REPLAY ─────────────────────────────────────────────
        self._btn_replay = tk.Button(
            parent, text="⏮  REPLAY",
            bg=config.COLOR_BTN_REPLAY, fg="white",
            font=("Arial", 14, "bold"),
            activebackground="#c62828", activeforeground="white",
            relief=tk.FLAT, padx=10, pady=12,
            command=self._trigger_replay,
            cursor="hand2"
        )
        self._btn_replay.pack(fill=tk.X, padx=10, pady=(10, 3))

        # ── Botón PAUSAR / REANUDAR ───────────────────────────────────
        self._btn_pause = tk.Button(
            parent, text="⏸  PAUSAR",
            bg=config.COLOR_ACCENT, fg="white",
            font=("Arial", 11),
            activebackground="#1a5276", activeforeground="white",
            relief=tk.FLAT, padx=8, pady=7,
            command=self._toggle_pause,
            cursor="hand2",
            state=tk.DISABLED
        )
        self._btn_pause.pack(fill=tk.X, padx=10, pady=2)

        # ── Botón LIVE ────────────────────────────────────────────────
        self._btn_live = tk.Button(
            parent, text="● LIVE",
            bg=config.COLOR_BTN_LIVE, fg="white",
            font=("Arial", 11),
            activebackground="#239a8e", activeforeground="white",
            relief=tk.FLAT, padx=8, pady=7,
            command=self._go_live,
            cursor="hand2",
            state=tk.DISABLED
        )
        self._btn_live.pack(fill=tk.X, padx=10, pady=2)

        ttk.Separator(parent, orient="horizontal").pack(fill=tk.X, padx=10, pady=10)

        # ── Duración del buffer ───────────────────────────────────────
        tk.Label(
            parent, text="Buffer de replay:",
            bg=config.COLOR_PANEL, fg=config.COLOR_TEXT,
            font=("Arial", 10, "bold")
        ).pack(anchor=tk.W, padx=12)

        dur_frame = tk.Frame(parent, bg=config.COLOR_PANEL)
        dur_frame.pack(fill=tk.X, padx=12, pady=4)

        for dur in config.BUFFER_DURATIONS:
            tk.Radiobutton(
                dur_frame,
                text=f"{dur}s",
                variable=self._duration_var,
                value=dur,
                bg=config.COLOR_PANEL,
                fg=config.COLOR_TEXT,
                selectcolor=config.COLOR_ACCENT,
                activebackground=config.COLOR_PANEL,
                font=("Arial", 10),
                command=self._on_duration_change
            ).pack(side=tk.LEFT, padx=4)

        ttk.Separator(parent, orient="horizontal").pack(fill=tk.X, padx=10, pady=10)

        # ── Velocidad de replay ───────────────────────────────────────
        tk.Label(
            parent, text="Velocidad de replay:",
            bg=config.COLOR_PANEL, fg=config.COLOR_TEXT,
            font=("Arial", 10, "bold")
        ).pack(anchor=tk.W, padx=12)

        spd_frame = tk.Frame(parent, bg=config.COLOR_PANEL)
        spd_frame.pack(fill=tk.X, padx=12, pady=4)

        for speed in config.REPLAY_SPEEDS:
            lbl = f"{speed}×" if speed != 1.0 else "1×"
            btn = tk.Button(
                spd_frame, text=lbl,
                bg=(config.COLOR_BTN_REPLAY
                    if speed == self._current_speed
                    else config.COLOR_ACCENT),
                fg="white",
                font=("Arial", 10),
                relief=tk.FLAT, padx=6, pady=4,
                command=lambda s=speed: self._set_speed(s),
                cursor="hand2"
            )
            btn.pack(side=tk.LEFT, padx=2)
            self._speed_buttons[speed] = btn

        ttk.Separator(parent, orient="horizontal").pack(fill=tk.X, padx=10, pady=10)

        # ── Detección de personas y pose ─────────────────────────────
        tk.Label(
            parent, text="Visión artificial:",
            bg=config.COLOR_PANEL, fg=config.COLOR_TEXT,
            font=("Arial", 10, "bold")
        ).pack(anchor=tk.W, padx=12)

        self._btn_detect = tk.Button(
            parent, text="🔍 DETECCIÓN: OFF",
            bg=config.COLOR_ACCENT, fg="white",
            font=("Arial", 10),
            activebackground="#1b5e20", activeforeground="white",
            relief=tk.FLAT, padx=8, pady=6,
            command=self._toggle_detection,
            cursor="hand2"
        )
        self._btn_detect.pack(fill=tk.X, padx=10, pady=(4, 2))

        # Puntuación asistida
        self._btn_scoring = tk.Button(
            parent, text="⚔️ PUNTUACIÓN: OFF",
            bg=config.COLOR_ACCENT, fg="white",
            font=("Arial", 10),
            activebackground="#1b5e20", activeforeground="white",
            relief=tk.FLAT, padx=8, pady=5,
            command=self._toggle_scoring,
            cursor="hand2"
        )
        self._btn_scoring.pack(fill=tk.X, padx=10, pady=2)

        # Análisis táctico
        self._btn_tactical = tk.Button(
            parent, text="🗺️ TÁCTICA: OFF",
            bg=config.COLOR_ACCENT, fg="white",
            font=("Arial", 10),
            activebackground="#1b5e20", activeforeground="white",
            relief=tk.FLAT, padx=8, pady=5,
            command=self._toggle_tactical,
            cursor="hand2"
        )
        self._btn_tactical.pack(fill=tk.X, padx=10, pady=2)

        # Reiniciar análisis
        self._btn_reset_analysis = tk.Button(
            parent, text="🔄 Reiniciar análisis",
            bg="#2a1a00", fg=config.COLOR_TEXT_DIM,
            font=("Arial", 9),
            activebackground="#3a2a00", activeforeground="white",
            relief=tk.FLAT, padx=8, pady=4,
            command=self._reset_analysis,
            cursor="hand2"
        )
        self._btn_reset_analysis.pack(fill=tk.X, padx=10, pady=(0, 4))

        ttk.Separator(parent, orient="horizontal").pack(fill=tk.X, padx=10, pady=10)

        # ── Info de cámaras ───────────────────────────────────────────
        tk.Label(
            parent, text="Cámaras conectadas:",
            bg=config.COLOR_PANEL, fg=config.COLOR_TEXT,
            font=("Arial", 10, "bold")
        ).pack(anchor=tk.W, padx=12)

        self._cam_info_labels: Dict[int, tk.Label] = {}
        cam_info_frame = tk.Frame(parent, bg=config.COLOR_PANEL)
        cam_info_frame.pack(fill=tk.X, padx=12, pady=(2, 0))

        for idx in sorted(self.cameras.keys()):
            lbl = tk.Label(
                cam_info_frame,
                text=f"Cam {idx + 1}: —",
                bg=config.COLOR_PANEL,
                fg=config.COLOR_TEXT_DIM,
                font=("Arial", 9),
                anchor=tk.W
            )
            lbl.pack(fill=tk.X, pady=1)
            self._cam_info_labels[idx] = lbl

    # ==================================================================
    # Loops de render e info (vía root.after — hilo de UI)
    # ==================================================================

    def _schedule_render(self) -> None:
        """Programa el primer ciclo de render."""
        delay = max(1, int(1000 / config.UI_RENDER_FPS))
        self.root.after(delay, self._render_tick)

    def _render_tick(self) -> None:
        """Actualiza todos los canvases con el frame más reciente."""
        state = self._replay_ctrl.state

        if state == ReplayState.LIVE:
            for idx, cam in self.cameras.items():
                frame = cam.get_latest_frame()
                with self._frames_lock:
                    self._live_frames[idx] = frame

            # Canvas principal
            with self._frames_lock:
                mf = self._live_frames.get(self._main_cam_idx)
            mf = self._apply_detection(mf)
            if self._main_canvas:
                self._main_canvas.update_frame(mf)

            # Miniaturas (cada canvas guarda su propia referencia al photo)
            for idx, tc in self._thumb_canvases.items():
                with self._frames_lock:
                    tf = self._live_frames.get(idx)
                tc.update_frame(tf)

        else:  # PLAYING o PAUSED
            with self._frames_lock:
                mf = self._replay_frames.get(self._main_cam_idx)
            mf = self._apply_detection(mf)
            if self._main_canvas:
                self._main_canvas.update_frame(mf)

            for idx, tc in self._thumb_canvases.items():
                with self._frames_lock:
                    tf = self._replay_frames.get(idx)
                tc.update_frame(tf)

            # Actualizar tiempo y timeline
            elapsed = self._replay_ctrl.current_time_seconds
            total   = self._replay_ctrl.total_seconds
            if total > 0:
                self._time_var.set(
                    f"t = {elapsed:.1f}s  /  {total:.1f}s"
                )
            self._update_timeline()

        delay = max(1, int(1000 / config.UI_RENDER_FPS))
        self.root.after(delay, self._render_tick)

    def _schedule_info_update(self) -> None:
        interval = max(1, int(1000 / config.THUMBNAIL_RENDER_FPS))
        self.root.after(interval, self._info_tick)

    def _info_tick(self) -> None:
        """Actualiza las etiquetas de info de cámaras y del buffer."""
        if self._replay_ctrl.state == ReplayState.LIVE:
            for idx, cam in self.cameras.items():
                lbl = self._cam_info_labels.get(idx)
                if lbl is None:
                    continue
                status = "OK" if cam.is_connected else "SIN SEÑAL"
                buf_s  = cam.buffer_seconds_filled
                fps    = cam.fps_actual
                lbl.config(
                    text=f"Cam {idx + 1}: {fps:.0f}fps  buf={buf_s:.0f}s  [{status}]",
                    fg=config.COLOR_LIVE if cam.is_connected else "#ff4444"
                )

            # Buffer fill de la primera cámara como referencia
            if self.cameras:
                cam0  = list(self.cameras.values())[0]
                pct   = cam0.buffer_fill_percent * 100
                dur   = self._duration_var.get()
                self._buffer_var.set(f"Buffer: {pct:.0f}% de {dur}s")

        # Actualizar texto del botón de detección según estado del detector
        if self._btn_detect:
            if self._detector.loading:
                self._btn_detect.config(
                    text="⏳ Cargando modelo…",
                    bg="#7b5800"
                )
            elif self._detector.enabled:
                self._btn_detect.config(
                    text="🔍 DETECCIÓN: ON",
                    bg="#1b5e20"
                )
            else:
                self._btn_detect.config(
                    text="🔍 DETECCIÓN: OFF",
                    bg=config.COLOR_ACCENT
                )

        # Actualizar botones de análisis
        if self._btn_scoring:
            if self._analyzer.scoring_enabled:
                self._btn_scoring.config(text="⚔️ PUNTUACIÓN: ON",  bg="#1b5e20")
            else:
                self._btn_scoring.config(text="⚔️ PUNTUACIÓN: OFF", bg=config.COLOR_ACCENT)
        if self._btn_tactical:
            if self._analyzer.tactical_enabled:
                self._btn_tactical.config(text="🗺️ TÁCTICA: ON",  bg="#1b5e20")
            else:
                self._btn_tactical.config(text="🗺️ TÁCTICA: OFF", bg=config.COLOR_ACCENT)

        interval = max(1, int(1000 / config.THUMBNAIL_RENDER_FPS))
        self.root.after(interval, self._info_tick)

    # ==================================================================
    # Callback de replay: llamado desde el hilo de replay
    # ==================================================================

    def _on_replay_frame(self,
                         frames: Dict[int, np.ndarray],
                         current_idx: int,
                         total: int) -> None:
        with self._frames_lock:
            for idx, frame in frames.items():
                self._replay_frames[idx] = frame

    # ==================================================================
    # Selección de cámara principal
    # ==================================================================

    def _select_main_camera(self, cam_idx: int) -> None:
        self._main_cam_idx = cam_idx
        if self._main_canvas:
            self._main_canvas.cam_index = cam_idx
        self._update_thumb_selection()

    def _update_thumb_selection(self) -> None:
        for idx, tc in self._thumb_canvases.items():
            tc.set_selected(idx == self._main_cam_idx)

    # ==================================================================
    # Acciones de los botones
    # ==================================================================

    def _trigger_replay(self) -> None:
        duration = self._duration_var.get()
        self._replay_ctrl.trigger_replay(self.cameras, duration)

    def _toggle_pause(self) -> None:
        self._replay_ctrl.toggle_pause()

    def _go_live(self) -> None:
        self._replay_ctrl.stop_replay()

    def _on_duration_change(self) -> None:
        new_dur = self._duration_var.get()
        for cam in self.cameras.values():
            cam.set_buffer_seconds(new_dur)

    def _toggle_detection(self) -> None:
        """Activa o desactiva el detector de personas/pose."""
        self._detector.toggle()
        # Si se desactiva la detección, desactivar también el análisis
        if not self._detector.enabled:
            self._analyzer.scoring_enabled  = False
            self._analyzer.tactical_enabled = False

    def _toggle_scoring(self) -> None:
        """Activa o desactiva la puntuación asistida."""
        self._analyzer.scoring_enabled = not self._analyzer.scoring_enabled
        # El scoring necesita que el detector esté encendido
        if self._analyzer.scoring_enabled and not self._detector.enabled:
            self._detector.enable()

    def _toggle_tactical(self) -> None:
        """Activa o desactiva el análisis táctico."""
        self._analyzer.tactical_enabled = not self._analyzer.tactical_enabled
        if self._analyzer.tactical_enabled and not self._detector.enabled:
            self._detector.enable()

    def _reset_analysis(self) -> None:
        """Reinicia marcador y mapa de calor."""
        self._analyzer.reset()

    def _apply_detection(self,
                         frame: Optional[np.ndarray]) -> Optional[np.ndarray]:
        """
        Si la detección está activa, envía el frame al detector y devuelve
        el último resultado anotado. Si no hay resultado todavía, devuelve
        el frame original (sin anotaciones).
        """
        if frame is None or not self._detector.enabled:
            return frame
        self._detector.submit(frame)
        result = self._detector.get_result()
        return result if result is not None else frame

    def _set_speed(self, speed: float) -> None:
        self._current_speed = speed
        self._replay_ctrl.set_speed(speed)
        for s, btn in self._speed_buttons.items():
            btn.config(
                bg=(config.COLOR_BTN_REPLAY
                    if s == speed
                    else config.COLOR_ACCENT)
            )

    # ==================================================================
    # Cambios de estado del replay (callback — puede venir de otro hilo)
    # ==================================================================

    def _on_state_change(self, state: ReplayState) -> None:
        # Delegar al hilo de UI
        self.root.after(0, self._apply_state_change, state)

    def _apply_state_change(self, state: ReplayState) -> None:
        if state == ReplayState.LIVE:
            self._status_var.set("● LIVE")
            if self._status_label:
                self._status_label.config(fg=config.COLOR_LIVE)
            self._time_var.set("")
            if self._btn_replay:
                self._btn_replay.config(state=tk.NORMAL)
            if self._btn_pause:
                self._btn_pause.config(state=tk.DISABLED, text="⏸  PAUSAR")
            if self._btn_live:
                self._btn_live.config(state=tk.DISABLED)
            # Ocultar timeline
            if self._timeline_frame:
                self._timeline_frame.pack_forget()
            # Limpiar frames de replay para no mostrar el último frame congelado
            with self._frames_lock:
                self._replay_frames = {i: None for i in self.cameras}

        elif state == ReplayState.PLAYING:
            self._status_var.set("▶  REPLAY")
            if self._status_label:
                self._status_label.config(fg=config.COLOR_REPLAY)
            if self._btn_replay:
                self._btn_replay.config(state=tk.NORMAL)
            if self._btn_pause:
                self._btn_pause.config(state=tk.NORMAL, text="⏸  PAUSAR")
            if self._btn_live:
                self._btn_live.config(state=tk.NORMAL)
            # Mostrar timeline entre canvas principal y thumbnails
            if self._timeline_frame and self._main_canvas_container:
                self._timeline_frame.pack(
                    fill=tk.X,
                    after=self._main_canvas_container
                )

        elif state == ReplayState.PAUSED:
            # Detectar si el replay llegó al final o fue pausado a mitad
            at_end = (
                self._replay_ctrl.total_frames > 0
                and self._replay_ctrl.current_index
                    >= self._replay_ctrl.total_frames - 1
            )
            if at_end:
                self._status_var.set("⏹  FIN DEL REPLAY")
                if self._status_label:
                    self._status_label.config(fg=config.COLOR_TEXT_DIM)
                if self._btn_pause:
                    self._btn_pause.config(
                        state=tk.DISABLED, text="⏸  PAUSAR"
                    )
            else:
                self._status_var.set("⏸  PAUSADO")
                if self._status_label:
                    self._status_label.config(fg=config.COLOR_PAUSED)
                if self._btn_pause:
                    self._btn_pause.config(
                        state=tk.NORMAL, text="▶  REANUDAR"
                    )

    # ==================================================================
    # Limpieza al cerrar
    # ==================================================================

    def destroy(self) -> None:
        self._detector.disable()
        self._replay_ctrl.stop_replay()
        for cam in self.cameras.values():
            cam.stop()
