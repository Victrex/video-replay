"""
analyzer.py
-----------
Análisis de combate sobre los resultados de YOLOv8-pose.

Dos módulos independientes, cada uno activable por separado:

1. PUNTUACIÓN ASISTIDA
   - Detecta impactos de pie/rodilla en torso/cabeza del rival
   - Cuerpo → 1 punto | Cabeza → 3 puntos  (scoring WTF)
   - Cooldown de 25 frames entre impactos del mismo atleta
   - Overlay: zonas puntuables + flash de impacto + marcador superior

2. ANÁLISIS TÁCTICO
   - Mapa de calor de posiciones (JET colormap, fade gradual)
   - Línea punteada con distancia en px entre los dos atletas

Se invoca desde el hilo del detector; no requiere locks propios.
Los flags scoring_enabled / tactical_enabled son escritos desde la UI
(seguros con el GIL de Python).
"""
import cv2
import numpy as np
from typing import List, Optional, Tuple

# ── Índices de keypoints COCO (YOLOv8-pose) ─────────────────────────
KP_NOSE            = 0
KP_LEFT_SHOULDER   = 5
KP_RIGHT_SHOULDER  = 6
KP_LEFT_HIP        = 11
KP_RIGHT_HIP       = 12
KP_LEFT_KNEE       = 13
KP_RIGHT_KNEE      = 14
KP_LEFT_ANKLE      = 15
KP_RIGHT_ANKLE     = 16

# ── Colores BGR ──────────────────────────────────────────────────────
_C_BLUE  = (220, 80, 10)    # atleta izquierda (Chung)
_C_RED   = (10,  50, 210)   # atleta derecha   (Hong)
_C_WHITE = (255, 255, 255)
_C_BLACK = (0,   0,   0)


class CombatAnalyzer:
    """
    Procesa un resultado de YOLOv8-pose y dibuja overlays de análisis.

    Uso
    ---
    analyzer = CombatAnalyzer()
    analyzer.scoring_enabled  = True   # activa puntuación
    analyzer.tactical_enabled = True   # activa análisis táctico

    # Desde el hilo del detector:
    annotated = analyzer.process_and_draw(results[0], annotated_frame)

    # Reset de marcador y heatmap:
    analyzer.reset()
    """

    _HEATMAP_SIGMA          = 18     # σ del blob gaussiano (px)
    _HEATMAP_DECAY          = 0.997  # decaimiento por frame
    _HEATMAP_ALPHA          = 0.38   # opacidad del overlay del heatmap

    _IMPACT_COOLDOWN_FRAMES = 25     # frames de cooldown tras un impacto
    _IMPACT_FLASH_FRAMES    = 22     # duración del flash visual

    def __init__(self) -> None:
        self.scoring_enabled:  bool = False
        self.tactical_enabled: bool = False

        # Heatmap
        self._heatmap: Optional[np.ndarray] = None
        self._heatmap_shape: Tuple[int, int] = (0, 0)

        # Puntuación [atleta_izq, atleta_der]
        self._score:     List[int] = [0, 0]
        self._cooldown:  List[int] = [0, 0]
        self._flash:     List[int] = [0, 0]
        self._flash_pts: List[int] = [0, 0]

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reinicia puntuación, cooldowns y mapa de calor."""
        self._score     = [0, 0]
        self._cooldown  = [0, 0]
        self._flash     = [0, 0]
        self._heatmap   = None

    def get_score(self) -> Tuple[int, int]:
        return (self._score[0], self._score[1])

    def process_and_draw(self, result, frame: np.ndarray) -> np.ndarray:
        """
        Procesa un resultado YOLOv8 y dibuja los overlays activos.

        Parameters
        ----------
        result : ultralytics.engine.results.Results  (results[0])
        frame  : np.ndarray  frame BGR ya anotado con skeleton/boxes de YOLO

        Returns
        -------
        np.ndarray  Frame con los overlays de análisis superpuestos.
        """
        # Los cooldowns/flash siempre avanzan, haya o no detección
        self._tick_timers()

        h, w = frame.shape[:2]

        # Sin detecciones: mostrar overlays pasivos y salir
        kps_data = result.keypoints
        if kps_data is None or len(kps_data) == 0:
            if self.tactical_enabled and self._heatmap is not None:
                frame = self._draw_heatmap(frame)
            if self.scoring_enabled:
                frame = self._draw_impact_flashes(frame)
                frame = self._draw_score_panel(frame)
            return frame

        kps_xy   = kps_data.xy.cpu().numpy()    # (N, 17, 2) px
        kps_conf = kps_data.conf.cpu().numpy()  # (N, 17)

        # Ordenar atletas de izquierda a derecha por X media
        persons = sorted(
            range(len(kps_xy)),
            key=lambda i: float(np.mean(kps_xy[i][:, 0]))
        )

        # ── Análisis táctico ──────────────────────────────────────────
        if self.tactical_enabled:
            self._update_heatmap(kps_xy, h, w)
            frame = self._draw_heatmap(frame)
            if len(persons) >= 2:
                frame = self._draw_distance(
                    frame, kps_xy[persons[0]], kps_xy[persons[1]]
                )

        # ── Puntuación asistida ───────────────────────────────────────
        if self.scoring_enabled:
            if len(persons) >= 2:
                frame = self._draw_scoring_zones(frame, kps_xy, persons, kps_conf)
                self._detect_impacts(kps_xy, persons, kps_conf)
            frame = self._draw_impact_flashes(frame)
            frame = self._draw_score_panel(frame)

        return frame

    # ------------------------------------------------------------------
    # Timers internos
    # ------------------------------------------------------------------

    def _tick_timers(self) -> None:
        self._cooldown = [max(0, c - 1) for c in self._cooldown]
        self._flash    = [max(0, f - 1) for f in self._flash]

    # ------------------------------------------------------------------
    # Mapa de calor
    # ------------------------------------------------------------------

    def _update_heatmap(self, kps_xy: np.ndarray, h: int, w: int) -> None:
        if self._heatmap is None or self._heatmap_shape != (h, w):
            self._heatmap = np.zeros((h, w), dtype=np.float32)
            self._heatmap_shape = (h, w)

        self._heatmap *= self._HEATMAP_DECAY

        for person_kps in kps_xy:
            # Centro de masa: caderas (más estable); fallback al centroide
            hip_pts = person_kps[[KP_LEFT_HIP, KP_RIGHT_HIP]]
            valid   = hip_pts[np.any(hip_pts > 0, axis=1)]
            if len(valid) == 0:
                valid = person_kps[np.any(person_kps > 0, axis=1)]
            if len(valid) == 0:
                continue
            cx = int(np.clip(np.mean(valid[:, 0]), 0, w - 1))
            cy = int(np.clip(np.mean(valid[:, 1]), 0, h - 1))

            # Blob gaussiano → acumular en heatmap
            pt_map = np.zeros((h, w), dtype=np.float32)
            pt_map[cy, cx] = 10.0
            blurred = cv2.GaussianBlur(pt_map, (0, 0), float(self._HEATMAP_SIGMA))
            self._heatmap += blurred

    def _draw_heatmap(self, frame: np.ndarray) -> np.ndarray:
        if self._heatmap is None:
            return frame
        h, w = frame.shape[:2]
        if self._heatmap.shape != (h, w):
            return frame

        max_val = float(self._heatmap.max())
        if max_val < 1e-4:
            return frame

        norm    = np.clip(self._heatmap / max_val, 0.0, 1.0)
        heat_u8 = (norm * 255).astype(np.uint8)
        colored = cv2.applyColorMap(heat_u8, cv2.COLORMAP_JET)

        mask   = (norm * self._HEATMAP_ALPHA)[..., np.newaxis]
        result = (frame.astype(np.float32) * (1.0 - mask) +
                  colored.astype(np.float32) * mask).clip(0, 255).astype(np.uint8)
        return result

    # ------------------------------------------------------------------
    # Distancia entre atletas
    # ------------------------------------------------------------------

    def _draw_distance(self, frame: np.ndarray,
                       kps_a: np.ndarray,
                       kps_b: np.ndarray) -> np.ndarray:
        ca = self._hip_center(kps_a)
        cb = self._hip_center(kps_b)
        if ca is None or cb is None:
            return frame

        dist = float(np.linalg.norm(np.array(ca, float) - np.array(cb, float)))
        h, w  = frame.shape[:2]
        ref   = w * 0.18

        if   dist < ref:          color = (0,  40, 200)   # rojo → muy cerca
        elif dist < ref * 2.2:    color = (0, 200, 240)   # amarillo → medio
        else:                     color = (0, 200, 80)    # verde → lejos

        # Línea punteada entre los dos centros
        n   = 14
        tps = [i / (n - 1) for i in range(n)]
        for i in range(0, n - 1, 2):
            p1 = (int(ca[0] + tps[i]   * (cb[0] - ca[0])),
                  int(ca[1] + tps[i]   * (cb[1] - ca[1])))
            p2 = (int(ca[0] + tps[i+1] * (cb[0] - ca[0])),
                  int(ca[1] + tps[i+1] * (cb[1] - ca[1])))
            cv2.line(frame, p1, p2, color, 2, cv2.LINE_AA)

        # Etiqueta en el punto medio
        mid   = ((ca[0] + cb[0]) // 2, (ca[1] + cb[1]) // 2)
        label = f"Dist: {int(dist)} px"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.52, 1)
        cv2.rectangle(frame,
                      (mid[0] - tw // 2 - 5, mid[1] - th - 5),
                      (mid[0] + tw // 2 + 5, mid[1] + 5),
                      (10, 10, 10), -1)
        cv2.putText(frame, label, (mid[0] - tw // 2, mid[1]),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, color, 1, cv2.LINE_AA)
        return frame

    # ------------------------------------------------------------------
    # Zonas de puntuación
    # ------------------------------------------------------------------

    def _draw_scoring_zones(self, frame: np.ndarray,
                            kps_xy: np.ndarray,
                            persons: list,
                            kps_conf: np.ndarray) -> np.ndarray:
        """Dibuja zonas puntuables semi-transparentes sobre cada atleta."""
        overlay = frame.copy()
        colors  = [_C_BLUE, _C_RED]

        for rank, pidx in enumerate(persons[:2]):
            color = colors[rank]
            kps   = kps_xy[pidx]
            conf  = kps_conf[pidx]

            tb = self._get_torso_box(kps, conf)
            if tb:
                cv2.rectangle(overlay, (tb[0], tb[1]), (tb[2], tb[3]), color, -1)

            hb = self._get_head_box(kps, conf)
            if hb:
                cv2.circle(overlay, (hb[0], hb[1]), hb[2], color, -1)

        cv2.addWeighted(overlay, 0.22, frame, 0.78, 0, frame)
        return frame

    # ------------------------------------------------------------------
    # Detección de impactos
    # ------------------------------------------------------------------

    def _detect_impacts(self, kps_xy: np.ndarray,
                        persons: list,
                        kps_conf: np.ndarray) -> None:
        """
        Para cada atleta (atacante), verifica si algún pie/rodilla
        impacta en la zona puntuable del rival (defensor).
        """
        for attacker_rank in range(min(2, len(persons))):
            defender_rank = 1 - attacker_rank
            if defender_rank >= len(persons):
                continue
            if self._cooldown[attacker_rank] > 0:
                continue

            att_kps  = kps_xy[persons[attacker_rank]]
            att_conf = kps_conf[persons[attacker_rank]]
            def_kps  = kps_xy[persons[defender_rank]]
            def_conf = kps_conf[persons[defender_rank]]

            # Puntos de ataque: tobillos y rodillas del atacante
            foot_pts = []
            for kp_idx, min_conf in [
                (KP_LEFT_ANKLE,  0.40),
                (KP_RIGHT_ANKLE, 0.40),
                (KP_LEFT_KNEE,   0.30),
                (KP_RIGHT_KNEE,  0.30),
            ]:
                if att_conf[kp_idx] > min_conf and att_kps[kp_idx][0] > 0:
                    foot_pts.append(att_kps[kp_idx])

            if not foot_pts:
                continue

            tb = self._get_torso_box(def_kps, def_conf)
            hb = self._get_head_box(def_kps, def_conf)

            for fp in foot_pts:
                fx, fy = float(fp[0]), float(fp[1])

                # Impacto en cabeza → 3 pts
                if hb:
                    cx, cy, r = hb
                    if (fx - cx) ** 2 + (fy - cy) ** 2 < (r * 1.4) ** 2:
                        self._register_impact(attacker_rank, 3)
                        break   # un impacto por atacante por frame

                # Impacto en tronco → 1 pt
                if tb and tb[0] <= fx <= tb[2] and tb[1] <= fy <= tb[3]:
                    self._register_impact(attacker_rank, 1)
                    break

    def _register_impact(self, attacker_rank: int, points: int) -> None:
        self._score[attacker_rank]    += points
        self._cooldown[attacker_rank]  = self._IMPACT_COOLDOWN_FRAMES
        self._flash[attacker_rank]     = self._IMPACT_FLASH_FRAMES
        self._flash_pts[attacker_rank] = points

    # ------------------------------------------------------------------
    # Overlays de puntuación
    # ------------------------------------------------------------------

    def _draw_impact_flashes(self, frame: np.ndarray) -> np.ndarray:
        """Flash visual animado cuando se registra un punto."""
        h, w   = frame.shape[:2]
        x_pos  = [w // 5, 4 * w // 5]
        colors = [_C_BLUE, _C_RED]

        for rank in range(2):
            if self._flash[rank] <= 0:
                continue
            alpha = self._flash[rank] / self._IMPACT_FLASH_FRAMES
            pts   = self._flash_pts[rank]
            color = colors[rank]
            text  = f"+{pts}pt{'s' if pts > 1 else ''}"

            fw = w // 5
            overlay = frame.copy()
            cv2.rectangle(overlay,
                          (x_pos[rank] - fw // 2, h // 3),
                          (x_pos[rank] + fw // 2, 2 * h // 3),
                          color, -1)
            cv2.addWeighted(overlay, alpha * 0.40, frame, 1 - alpha * 0.40, 0, frame)

            fs = max(0.5, 2.0 * alpha)
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, fs, 3)
            tx = x_pos[rank] - tw // 2
            ty = h // 2 + th // 2
            cv2.putText(frame, text, (tx, ty),
                        cv2.FONT_HERSHEY_SIMPLEX, fs, _C_BLACK, 5, cv2.LINE_AA)
            cv2.putText(frame, text, (tx, ty),
                        cv2.FONT_HERSHEY_SIMPLEX, fs, _C_WHITE, 2, cv2.LINE_AA)

        return frame

    def _draw_score_panel(self, frame: np.ndarray) -> np.ndarray:
        """Marcador en la parte superior del frame."""
        h, w  = frame.shape[:2]
        pw, ph = 210, 38
        x0 = w // 2 - pw // 2
        y0 = 8
        ty = y0 + 26

        overlay = frame.copy()
        cv2.rectangle(overlay, (x0, y0), (x0 + pw, y0 + ph), (10, 10, 10), -1)
        cv2.addWeighted(overlay, 0.78, frame, 0.22, 0, frame)

        sa, sb = self._score[0], self._score[1]
        full   = f"A  {sa}  —  {sb}  B"
        (tw, _), _ = cv2.getTextSize(full, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
        tx = x0 + (pw - tw) // 2

        # Texto completo en blanco, luego "A" y "B" coloreados
        cv2.putText(frame, full, (tx, ty),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, _C_WHITE, 2, cv2.LINE_AA)

        cv2.putText(frame, "A", (tx, ty),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, _C_BLUE, 2, cv2.LINE_AA)

        prefix = f"A  {sa}  —  {sb}  "
        (tw_pre, _), _ = cv2.getTextSize(prefix, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
        cv2.putText(frame, "B", (tx + tw_pre, ty),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, _C_RED, 2, cv2.LINE_AA)

        return frame

    # ------------------------------------------------------------------
    # Helpers de geometría
    # ------------------------------------------------------------------

    @staticmethod
    def _hip_center(kps: np.ndarray) -> Optional[Tuple[int, int]]:
        """Centro de caderas como proxy del centro de masa."""
        hip_pts = kps[[KP_LEFT_HIP, KP_RIGHT_HIP]]
        valid   = hip_pts[np.any(hip_pts > 0, axis=1)]
        if len(valid) == 0:
            valid = kps[np.any(kps > 0, axis=1)]
        if len(valid) == 0:
            return None
        return (int(np.mean(valid[:, 0])), int(np.mean(valid[:, 1])))

    @staticmethod
    def _get_torso_box(kps: np.ndarray, conf: np.ndarray,
                       conf_thresh: float = 0.3) -> Optional[Tuple[int, int, int, int]]:
        """Bounding box del tronco (hombros → caderas). None si insuficientes keypoints."""
        idxs = [KP_LEFT_SHOULDER, KP_RIGHT_SHOULDER, KP_LEFT_HIP, KP_RIGHT_HIP]
        pts  = [(kps[i][0], kps[i][1])
                for i in idxs
                if conf[i] > conf_thresh and kps[i][0] > 0]
        if len(pts) < 2:
            return None
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        px = (max(xs) - min(xs)) * 0.15
        return (int(min(xs) - px), int(min(ys)),
                int(max(xs) + px), int(max(ys)))

    @staticmethod
    def _get_head_box(kps: np.ndarray, conf: np.ndarray,
                      conf_thresh: float = 0.3) -> Optional[Tuple[int, int, int]]:
        """Círculo (cx, cy, radius) de la cabeza. None si no hay nariz detectable."""
        if conf[KP_NOSE] < conf_thresh or kps[KP_NOSE][0] <= 0:
            return None
        cx, cy = int(kps[KP_NOSE][0]), int(kps[KP_NOSE][1])
        shoulders = [(kps[i][0], kps[i][1])
                     for i in [KP_LEFT_SHOULDER, KP_RIGHT_SHOULDER]
                     if conf[i] > conf_thresh and kps[i][0] > 0]
        if len(shoulders) >= 2:
            r = int(abs(shoulders[0][0] - shoulders[1][0]) * 0.33)
        else:
            r = 22
        return (cx, cy, max(r, 14))
