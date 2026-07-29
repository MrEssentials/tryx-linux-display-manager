#!/usr/bin/env python3

from __future__ import annotations

import json
import mimetypes
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import usb.core
from PySide6.QtCore import QMimeData, QPoint, QPointF, QRectF, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QDrag, QIcon, QImageReader, QMouseEvent, QPainter, QPixmap, QTransform, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


WIDTH = 1280
HEIGHT = 720
FPS = 60
BITRATE = "12000k"
SAVE_DELAY = 2.8
VID = 0x391A
PID = 0x2011

HOME = Path.home()
TRYX_CLI = HOME / ".local" / "bin" / "tryx"
TRYX_LOOP_MANAGER = HOME / "tryx-linux" / "tryx_loop_manager.py"
TRYX_PLAYLIST_MANAGER = HOME / "tryx-linux" / "tryx_playlist_manager.py"
CACHE_DIR = HOME / ".cache" / "tryx-display-manager"
RECENT_FILE = CACHE_DIR / "recent.txt"
SAVED_MEDIA_FILE = CACHE_DIR / "saved-media.json"
PLAYLIST_MANIFEST = CACHE_DIR / "shuffle-request.json"
SUPPORT_LINKS_FILE = CACHE_DIR / "support-links.json"

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff",
}
VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".gif", ".h264",
    ".mts", ".m2ts", ".ts", ".mpeg", ".mpg", ".wmv", ".flv", ".3gp",
    ".vob", ".ogv", ".hevc", ".h265",
}


def build_media_filter() -> str:
    # Qt's Linux file picker treats glob patterns as case-sensitive. Include
    # lowercase and uppercase forms so files such as VIDEO.MOV are visible.
    patterns: list[str] = []
    for extension in sorted(IMAGE_EXTENSIONS | VIDEO_EXTENSIONS):
        suffix = extension.removeprefix(".")
        patterns.append(f"*.{suffix}")
        uppercase = suffix.upper()
        if uppercase != suffix:
            patterns.append(f"*.{uppercase}")
    # Put All files first. Some Linux native file pickers silently hide uppercase
    # or phone-exported MP4 files when a long glob filter is selected.
    return "All files (*);;Pictures and videos (" + " ".join(patterns) + ")"


MEDIA_FILE_FILTER = build_media_filter()

ACCENT = "#D577EA"
BG = "#151719"
CARD = "#202326"
CARD_2 = "#292D30"
TEXT = "#F5F6F7"
MUTED = "#9CA3A8"
BORDER = "#34393D"


APP_STYLE = f"""
QMainWindow, QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: Sans Serif;
    font-size: 14px;
}}
QFrame#navRail {{
    background: {ACCENT};
    border: none;
}}
QLabel#brandMark {{
    color: #111315;
    font-size: 27px;
    font-weight: 900;
}}
QToolButton#navButton {{
    color: #111315;
    background: transparent;
    border: none;
    border-radius: 10px;
    font-size: 22px;
    padding: 10px;
}}
QToolButton#navButton:hover {{ background: rgba(0,0,0,0.10); }}
QToolButton#navButton:checked {{
    color: {ACCENT};
    background: #191B1D;
}}
QLabel#pageTitle {{
    font-size: 22px;
    font-weight: 800;
    letter-spacing: 1px;
}}
QLabel#sectionTitle {{
    font-size: 16px;
    font-weight: 700;
}}
QLabel#muted {{ color: {MUTED}; }}
QFrame#card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QFrame#controlBar {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QComboBox {{
    background: {CARD_2};
    color: {TEXT};
    border: 1px solid #3D4347;
    border-radius: 8px;
    padding: 8px 12px;
    min-width: 145px;
}}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{
    background: {CARD_2};
    color: {TEXT};
    selection-background-color: {ACCENT};
    selection-color: #111315;
}}
QPushButton {{
    background: {CARD_2};
    color: {TEXT};
    border: 1px solid #3D4347;
    border-radius: 9px;
    padding: 9px 14px;
    font-weight: 600;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton#primaryButton {{
    background: {ACCENT};
    color: #101214;
    border: none;
    padding: 12px 22px;
    font-weight: 900;
}}
QPushButton#primaryButton:hover {{ background: #F0B7FF; }}
QPushButton#primaryButton:disabled {{ background: #5A3766; color: #B89BC2; }}
QPushButton#ghostButton {{
    background: transparent;
    border: none;
    color: {MUTED};
    padding: 6px;
}}
QPushButton#ghostButton:hover {{ color: {TEXT}; }}
QSlider::groove:horizontal {{
    height: 5px;
    background: #3B4044;
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
    background: {ACCENT};
}}
QProgressBar {{
    background: #2C3033;
    border: none;
    border-radius: 4px;
    height: 7px;
    text-align: center;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QListWidget {{
    background: #171A1C;
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
}}
QListWidget::item {{
    padding: 5px;
    border-radius: 6px;
}}
QListWidget::item:selected {{
    background: #4A275A;
    color: {TEXT};
}}
QCheckBox {{ color: {TEXT}; spacing: 7px; }}
QCheckBox::indicator {{ width: 17px; height: 17px; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT}; }}
QCheckBox::indicator:unchecked {{ background: {CARD_2}; border: 1px solid #4A5155; }}
QTextEdit {{
    background: #101214;
    border: 1px solid {BORDER};
    border-radius: 8px;
    color: #D7DBDE;
    font-family: Monospace;
    font-size: 12px;
}}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:horizontal {{ height: 7px; background: transparent; }}
QScrollBar::handle:horizontal {{ background: #454B4F; border-radius: 3px; min-width: 30px; }}
QToolButton#mediaTile {{
    background: {CARD};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 5px;
}}
QToolButton#mediaTile:hover {{ border-color: {ACCENT}; }}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 12px;
    background: {BG};
    top: -1px;
}}
QTabBar::tab {{
    background: {CARD};
    color: {MUTED};
    border: 1px solid {BORDER};
    border-bottom: none;
    padding: 10px 24px;
    margin-right: 4px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-weight: 800;
}}
QTabBar::tab:selected {{
    background: {ACCENT};
    color: #111315;
}}
QLineEdit {{
    background: {CARD_2};
    color: {TEXT};
    border: 1px solid #3D4347;
    border-radius: 8px;
    padding: 9px 11px;
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}
"""


@dataclass(frozen=True)
class UploadSettings:
    source: Path
    kind: str
    fit: str
    zoom: float
    anchor_x: float
    anchor_y: float
    rotation: int


@dataclass
class PlaylistEntry:
    source: Path
    kind: str
    fit: str = "crop"
    zoom: float = 1.0
    anchor_x: float = 0.5
    anchor_y: float = 0.5
    rotation: int = 0
    framing_saved: bool = False

    def to_manifest(self) -> dict[str, object]:
        return {
            "source": str(self.source),
            "kind": self.kind,
            "fit": self.fit,
            "zoom": self.zoom,
            "anchor_x": self.anchor_x,
            "anchor_y": self.anchor_y,
            "rotation": self.rotation,
        }


def detect_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"

    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type:
        if mime_type.startswith("image/"):
            return "image"
        if mime_type.startswith("video/"):
            return "video"

    raise RuntimeError("This file does not look like a supported image or video.")


def make_filter(
    fit: str,
    zoom: float,
    anchor_x: float,
    anchor_y: float,
    rotation: int,
    *,
    video: bool,
) -> str:
    zoom_text = f"{zoom:.6f}"
    ax_text = f"{anchor_x:.6f}"
    ay_text = f"{anchor_y:.6f}"
    filters: list[str] = []

    if rotation == 90:
        filters.append("transpose=1")
    elif rotation == 180:
        filters.extend(["transpose=1", "transpose=1"])
    elif rotation == 270:
        filters.append("transpose=2")

    if fit == "fit":
        factor = f"min({WIDTH}/iw\\,{HEIGHT}/ih)*{zoom_text}"
        sw = f"trunc(iw*({factor})/2)*2"
        sh = f"trunc(ih*({factor})/2)*2"
    elif fit == "stretch":
        sw = f"trunc({WIDTH}*{zoom_text}/2)*2"
        sh = f"trunc({HEIGHT}*{zoom_text}/2)*2"
    else:  # manual fill/crop
        factor = f"max({WIDTH}/iw\\,{HEIGHT}/ih)*{zoom_text}"
        sw = f"trunc(iw*({factor})/2)*2"
        sh = f"trunc(ih*({factor})/2)*2"

    filters.extend([
        f"scale=w='{sw}':h='{sh}'",
        (
            f"pad=w='max(iw,{WIDTH})':h='max(ih,{HEIGHT})':"
            f"x='if(lt(iw,{WIDTH}),({WIDTH}-iw)*{ax_text},0)':"
            f"y='if(lt(ih,{HEIGHT}),({HEIGHT}-ih)*{ay_text},0)':color=black"
        ),
        (
            f"crop={WIDTH}:{HEIGHT}:"
            f"x='if(gt(iw,{WIDTH}),(iw-{WIDTH})*{ax_text},0)':"
            f"y='if(gt(ih,{HEIGHT}),(ih-{HEIGHT})*{ay_text},0)'"
        ),
        "setsar=1",
    ])
    if video:
        filters.extend([f"fps={FPS}", "format=yuv420p"])
    return ",".join(filters)


def load_oriented_image(path: Path) -> QPixmap:
    """Load an image using its EXIF orientation, matching FFmpeg/LCD output."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        return QPixmap()
    return QPixmap.fromImage(image)


def thumbnail_for(path: Path, size: QSize) -> QPixmap:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        kind = detect_kind(path)
    except RuntimeError:
        return QPixmap()

    if kind == "image":
        pixmap = load_oriented_image(path)
    else:
        key = f"{abs(hash((str(path), path.stat().st_mtime_ns)))}.jpg"
        thumb_path = CACHE_DIR / key
        if not thumb_path.exists():
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", "0.2", "-i", str(path), "-frames:v", "1",
                    "-vf", "scale=480:-2", str(thumb_path),
                ],
                capture_output=True,
            )
        pixmap = QPixmap(str(thumb_path))

    if pixmap.isNull():
        return pixmap
    return pixmap.scaled(
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )


class PreviewCanvas(QWidget):
    position_changed = Signal(float, float)
    zoom_changed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(640, 390)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        self._source = QPixmap()
        self._pixmap = QPixmap()
        self._fit = "crop"
        self._zoom = 1.0
        self._anchor_x = 0.5
        self._anchor_y = 0.5
        self._rotation = 0
        self._show_grid = True
        self._last_mouse: QPointF | None = None

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._source = pixmap
        self._refresh_rotation()
        self.update()

    def set_transform(self, fit: str, zoom: float, x: float, y: float, rotation: int) -> None:
        self._fit = fit
        self._zoom = zoom
        self._anchor_x = x
        self._anchor_y = y
        if rotation != self._rotation:
            self._rotation = rotation
            self._refresh_rotation()
        self.update()

    def set_show_grid(self, enabled: bool) -> None:
        self._show_grid = enabled
        self.update()

    def _refresh_rotation(self) -> None:
        if self._source.isNull():
            self._pixmap = QPixmap()
            return
        self._pixmap = self._source.transformed(
            QTransform().rotate(self._rotation),
            Qt.TransformationMode.SmoothTransformation,
        )

    def _screen_rect(self) -> QRectF:
        # The LCD is exactly 1280x720 (16:9). Keep this box at that ratio even
        # when the application window is resized.
        side_margin = 28.0
        top_margin = 34.0
        bottom_margin = 24.0
        available_width = max(1.0, float(self.width()) - side_margin * 2.0)
        available_height = max(1.0, float(self.height()) - top_margin - bottom_margin)
        lcd_ratio = WIDTH / HEIGHT

        if available_width / available_height > lcd_ratio:
            screen_height = available_height
            screen_width = screen_height * lcd_ratio
        else:
            screen_width = available_width
            screen_height = screen_width / lcd_ratio

        x = (float(self.width()) - screen_width) / 2.0
        y = top_margin + (available_height - screen_height) / 2.0
        return QRectF(x, y, screen_width, screen_height)

    def _draw_rect(self) -> QRectF:
        if self._pixmap.isNull():
            return QRectF()

        screen = self._screen_rect()
        cw, ch = screen.width(), screen.height()
        sw, sh = float(self._pixmap.width()), float(self._pixmap.height())

        if self._fit == "fit":
            factor = min(cw / sw, ch / sh)
            dw, dh = sw * factor * self._zoom, sh * factor * self._zoom
        elif self._fit == "stretch":
            dw, dh = cw * self._zoom, ch * self._zoom
        else:
            factor = max(cw / sw, ch / sh)
            dw, dh = sw * factor * self._zoom, sh * factor * self._zoom

        return QRectF(
            screen.x() + (cw - dw) * self._anchor_x,
            screen.y() + (ch - dh) * self._anchor_y,
            dw,
            dh,
        )

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), QColor("#111315"))

        screen = self._screen_rect()
        painter.setPen(QColor(MUTED))
        painter.drawText(
            QRectF(0, 5, self.width(), 24),
            Qt.AlignmentFlag.AlignCenter,
            "LCD OUTPUT  •  1280 × 720  •  16:9",
        )

        # Everything outside this box is intentionally hidden. The box itself
        # is the exact area the LCD will show.
        painter.fillRect(screen, QColor("#000000"))
        painter.save()
        painter.setClipRect(screen)

        if self._pixmap.isNull():
            painter.setPen(QColor(MUTED))
            painter.drawText(
                screen,
                Qt.AlignmentFlag.AlignCenter,
                "Choose a picture or video",
            )
        else:
            painter.drawPixmap(self._draw_rect(), self._pixmap, QRectF(self._pixmap.rect()))

        if self._show_grid:
            painter.setPen(QColor(255, 255, 255, 105))
            one_third_x = screen.width() / 3.0
            one_third_y = screen.height() / 3.0
            for multiplier in (1, 2):
                x = screen.left() + one_third_x * multiplier
                y = screen.top() + one_third_y * multiplier
                painter.drawLine(QPointF(x, screen.top()), QPointF(x, screen.bottom()))
                painter.drawLine(QPointF(screen.left(), y), QPointF(screen.right(), y))

        painter.restore()
        painter.setPen(QColor(ACCENT))
        painter.drawRect(screen)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self._pixmap.isNull()
            and self._screen_rect().contains(event.position())
        ):
            self._last_mouse = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._last_mouse is None:
            return
        delta = event.position() - self._last_mouse
        self._last_mouse = event.position()
        screen = self._screen_rect()
        rect = self._draw_rect()
        hrange = screen.width() - rect.width()
        vrange = screen.height() - rect.height()
        x, y = self._anchor_x, self._anchor_y
        if abs(hrange) > 1:
            x += delta.x() / hrange
        if abs(vrange) > 1:
            y += delta.y() / vrange
        x, y = max(0.0, min(1.0, x)), max(0.0, min(1.0, y))
        self._anchor_x, self._anchor_y = x, y
        self.position_changed.emit(x, y)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_mouse = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        if self._pixmap.isNull() or not self._screen_rect().contains(event.position()):
            return
        step = 5 if event.angleDelta().y() > 0 else -5
        self.zoom_changed.emit(step)
        event.accept()


class DropZone(QFrame):
    files_dropped = Signal(list)
    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(92)
        self._normal_style = f"""
            QFrame {{ background: #1B1E20; border: 1px dashed #8E52C6; border-radius: 12px; }}
            QLabel {{ background: transparent; }}
        """
        self._hover_style = f"""
            QFrame {{ background: #291733; border: 2px dashed {ACCENT}; border-radius: 12px; }}
            QLabel {{ background: transparent; }}
        """
        self.setStyleSheet(self._normal_style)
        layout = QVBoxLayout(self)
        layout.setSpacing(2)
        title = QLabel("＋  Upload a file")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color:{TEXT}; font-weight:800; font-size:15px;")
        subtitle = QLabel("or drag a photo or video here")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setObjectName("muted")
        layout.addStretch()
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addStretch()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(self._hover_style)

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        del event
        self.setStyleSheet(self._normal_style)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        self.setStyleSheet(self._normal_style)
        urls = event.mimeData().urls()
        paths = [Path(url.toLocalFile()) for url in urls if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()


class MediaTile(QToolButton):
    """Clickable recent-media tile that can also be dragged into the playlist."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self._press_position: QPoint | None = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_position = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return super().mouseMoveEvent(event)
        if self._press_position is None:
            return super().mouseMoveEvent(event)
        distance = (event.position().toPoint() - self._press_position).manhattanLength()
        if distance < QApplication.startDragDistance():
            return super().mouseMoveEvent(event)

        drag = QDrag(self)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.path))])
        drag.setMimeData(mime)
        if not self.icon().isNull():
            drag.setPixmap(self.icon().pixmap(QSize(142, 78)))
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        drag.exec(Qt.DropAction.CopyAction)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._press_position = None

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self._press_position = None
        super().mouseReleaseEvent(event)


class PlaylistDropList(QListWidget):
    """Shuffle list accepting recent tiles and files dragged from the desktop."""

    files_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            paths = [
                Path(url.toLocalFile())
                for url in event.mimeData().urls()
                if url.isLocalFile()
            ]
            if paths:
                self.files_dropped.emit(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)


class UploadThread(QThread):
    log = Signal(str)
    completed = Signal(bool, str)

    def __init__(self, settings: UploadSettings) -> None:
        super().__init__()
        self.settings = settings

    def _run(self, command: list[str]) -> None:
        self.log.emit("$ " + shlex.join(command))
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            self.log.emit(line.rstrip())
        code = process.wait()
        if code != 0:
            raise RuntimeError(f"Command failed ({code}): {shlex.join(command)}")

    def _stop_loop(self) -> None:
        if not TRYX_LOOP_MANAGER.is_file():
            raise RuntimeError(f"Missing loop manager: {TRYX_LOOP_MANAGER}")
        self._run([sys.executable, str(TRYX_LOOP_MANAGER), "--stop"])

    def _stop_playlist(self) -> None:
        if TRYX_PLAYLIST_MANAGER.is_file():
            self._run([sys.executable, str(TRYX_PLAYLIST_MANAGER), "--stop"])

    def _start_loop(self) -> None:
        if not TRYX_LOOP_MANAGER.is_file():
            raise RuntimeError(f"Missing loop manager: {TRYX_LOOP_MANAGER}")
        self.log.emit(
            "Starting the proven continuous display loop at 2.95 seconds. "
            "It keeps running after this app closes."
        )
        self._run([
            sys.executable,
            str(TRYX_LOOP_MANAGER),
            "--start",
            "--interval",
            "2.95",
        ])

    def run(self) -> None:  # type: ignore[override]
        try:
            if not TRYX_CLI.is_file():
                raise RuntimeError(
                    f"Missing proven terminal launcher: {TRYX_CLI}"
                )

            self._stop_playlist()
            self._stop_loop()

            if self.settings.kind == "image":
                self._upload_image()
            else:
                self._upload_video()

            self._start_loop()

        except Exception as exc:
            self.completed.emit(False, str(exc))
            return

        self.completed.emit(
            True,
            "Saved to the TRYX display. The 2.95-second loop will continue "
            "until you save different media or press Stop Display Loop.",
        )

    def _upload_image(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tryx-gui-image-") as tmp:
            prepared = Path(tmp) / "prepared.png"
            vf = make_filter(
                self.settings.fit,
                self.settings.zoom,
                self.settings.anchor_x,
                self.settings.anchor_y,
                self.settings.rotation,
                video=False,
            )

            self.log.emit(
                "Preparing the crop, position, zoom, and rotation locally. "
                "The current LCD media remains active during this step."
            )
            self._run([
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(self.settings.source),
                "-frames:v",
                "1",
                "-vf",
                vf,
                str(prepared),
            ])

            # Use the exact terminal workflow that was confirmed working.
            self._run([str(TRYX_CLI), str(prepared)])

    def _upload_video(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tryx-gui-video-") as tmp:
            prepared = Path(tmp) / "prepared.mp4"
            vf = make_filter(
                self.settings.fit,
                self.settings.zoom,
                self.settings.anchor_x,
                self.settings.anchor_y,
                self.settings.rotation,
                video=True,
            )

            self.log.emit(
                "Preparing the crop, position, zoom, and rotation locally. "
                "The proven terminal uploader will handle the final TRYX encoding."
            )
            self._run([
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-i",
                str(self.settings.source),
                "-map",
                "0:v:0",
                "-an",
                "-vf",
                vf,
                "-fps_mode",
                "cfr",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(prepared),
            ])

            # The prepared file is already 1280x720, so the terminal launcher's
            # default fit step does not alter the chosen framing.
            self._run([str(TRYX_CLI), str(prepared)])


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("TRYX Display Manager — Support tab 0.8.19")
        self.resize(1160, 800)
        self.setMinimumSize(920, 680)

        self.source: Path | None = None
        self.kind: str | None = None
        self.preview_temp = tempfile.TemporaryDirectory(prefix="tryx-gui-preview-")
        self.worker: UploadThread | None = None
        self.saved_media = self.load_saved_media()
        self.support_links = self.load_support_links()
        self.recent_paths = self.load_recent()
        self.playlist_entries: list[PlaylistEntry] = []
        self.active_playlist_index: int | None = None
        self._loading_playlist_controls = False
        self.pending_review = False
        self._suspend_review_updates = False

        outer = QWidget()
        self.setCentralWidget(outer)
        root = QHBoxLayout(outer)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # The old green navigation rail was decorative and did not perform any
        # actions, so the content now uses the full window width.
        root.addWidget(self.build_content(), 1)
        self.refresh_device_status()
        self.refresh_recent_tiles()

    def build_nav(self) -> QFrame:
        nav = QFrame()
        nav.setObjectName("navRail")
        nav.setFixedWidth(70)
        layout = QVBoxLayout(nav)
        layout.setContentsMargins(10, 16, 10, 16)
        layout.setSpacing(12)

        brand = QLabel("><")
        brand.setObjectName("brandMark")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(brand)
        layout.addSpacing(20)

        for text, tip, checked in [
            ("⌂", "Home", False),
            ("▱", "Display customization", True),
            ("☼", "Lighting (coming later)", False),
            ("⌁", "Fans (coming later)", False),
        ]:
            button = QToolButton()
            button.setObjectName("navButton")
            button.setText(text)
            button.setToolTip(tip)
            button.setCheckable(True)
            button.setChecked(checked)
            if not checked:
                button.setEnabled(False)
            layout.addWidget(button)
        layout.addStretch()
        menu = QToolButton()
        menu.setObjectName("navButton")
        menu.setText("☰")
        menu.setToolTip("Menu")
        layout.addWidget(menu)
        return nav

    def build_content(self) -> QWidget:
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.addTab(self.build_display_page(), "DISPLAY")
        tabs.addTab(self.build_support_page(), "SUPPORT")
        return tabs

    def build_display_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 22, 28, 24)
        layout.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("CUSTOMIZATION")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        self.persistent_label = QLabel("●  Single media + mixed shuffle")
        self.persistent_label.setStyleSheet(f"color:{ACCENT}; font-weight:700;")
        self.persistent_label.setToolTip(
            "Single media uses the proven 2.95-second loop. Shuffle gives pictures a chosen duration and lets every video play fully."
        )
        header.addWidget(self.persistent_label)
        header.addSpacing(18)
        self.device_label = QLabel("●  Checking display…")
        self.device_label.setObjectName("muted")
        header.addWidget(self.device_label)
        layout.addLayout(header)

        self.adjustment_controls = QFrame()
        self.adjustment_controls.setObjectName("controlBar")
        c = QHBoxLayout(self.adjustment_controls)
        c.setContentsMargins(14, 10, 14, 10)
        c.setSpacing(10)
        full = QLabel("●  Full Screen")
        full.setStyleSheet(f"color:{ACCENT}; font-weight:800;")
        c.addWidget(full)
        c.addSpacing(12)

        self.fit_combo = QComboBox()
        self.fit_combo.addItem("Fill & adjust", "crop")
        self.fit_combo.addItem("Fit whole image", "fit")
        self.fit_combo.addItem("Stretch", "stretch")
        self.fit_combo.currentIndexChanged.connect(self.update_preview)
        c.addWidget(self.fit_combo)

        rotate_left = QPushButton("↶")
        rotate_left.setToolTip("Rotate 90° left")
        rotate_left.setFixedWidth(42)
        rotate_left.clicked.connect(lambda: self.rotate_by(-90))
        c.addWidget(rotate_left)

        self.rotation_combo = QComboBox()
        self.rotation_combo.addItem("0°", 0)
        self.rotation_combo.addItem("90°", 90)
        self.rotation_combo.addItem("180°", 180)
        self.rotation_combo.addItem("270°", 270)
        self.rotation_combo.currentIndexChanged.connect(self.update_preview)
        c.addWidget(self.rotation_combo)

        rotate_right = QPushButton("↷")
        rotate_right.setToolTip("Rotate 90° right")
        rotate_right.setFixedWidth(42)
        rotate_right.clicked.connect(lambda: self.rotate_by(90))
        c.addWidget(rotate_right)

        c.addWidget(QLabel("Zoom"))
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(50, 300)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setFixedWidth(180)
        self.zoom_slider.valueChanged.connect(self.update_preview)
        c.addWidget(self.zoom_slider)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setFixedWidth(45)
        c.addWidget(self.zoom_label)
        c.addStretch()

        reset = QPushButton("Reset")
        reset.clicked.connect(self.reset_transform)
        c.addWidget(reset)
        layout.addWidget(self.adjustment_controls)

        self.position_controls = QFrame()
        self.position_controls.setObjectName("controlBar")
        pc = QHBoxLayout(self.position_controls)
        pc.setContentsMargins(14, 8, 14, 8)
        pc.setSpacing(10)

        pc.addWidget(QLabel("Horizontal position"))
        self.x_slider = QSlider(Qt.Orientation.Horizontal)
        self.x_slider.setRange(0, 100)
        self.x_slider.setValue(50)
        self.x_slider.setFixedWidth(180)
        self.x_slider.valueChanged.connect(self.update_preview)
        pc.addWidget(self.x_slider)
        self.x_label = QLabel("Center")
        self.x_label.setFixedWidth(80)
        pc.addWidget(self.x_label)

        pc.addSpacing(12)
        pc.addWidget(QLabel("Vertical position"))
        self.y_slider = QSlider(Qt.Orientation.Horizontal)
        self.y_slider.setRange(0, 100)
        self.y_slider.setValue(50)
        self.y_slider.setFixedWidth(180)
        self.y_slider.valueChanged.connect(self.update_preview)
        pc.addWidget(self.y_slider)
        self.y_label = QLabel("Center")
        self.y_label.setFixedWidth(80)
        pc.addWidget(self.y_label)

        pc.addStretch()
        self.grid_checkbox = QCheckBox("Composition grid")
        self.grid_checkbox.setChecked(True)
        self.grid_checkbox.toggled.connect(self.preview_grid_changed)
        pc.addWidget(self.grid_checkbox)
        layout.addWidget(self.position_controls)

        self.preview_card = QFrame()
        self.preview_card.setObjectName("card")
        p = QVBoxLayout(self.preview_card)
        p.setContentsMargins(14, 14, 14, 10)
        p.setSpacing(8)
        self.preview = PreviewCanvas()
        self.preview.position_changed.connect(self.preview_position_changed)
        self.preview.zoom_changed.connect(self.adjust_zoom)
        p.addWidget(self.preview, 1)

        preview_footer = QHBoxLayout()
        hint = QLabel("The purple 16:9 box is exactly what the LCD will show  •  Drag to position  •  Scroll to zoom")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        preview_footer.addWidget(hint, 1)

        self.confirm_adjust_button = QPushButton("SAVE ADJUSTMENTS")
        self.confirm_adjust_button.setObjectName("primaryButton")
        self.confirm_adjust_button.setToolTip(
            "Save this file's crop, zoom, position, and rotation."
        )
        self.confirm_adjust_button.clicked.connect(self.confirm_adjustment)
        self.confirm_adjust_button.setEnabled(False)
        preview_footer.addWidget(self.confirm_adjust_button)

        self.details_button = QPushButton("Show details")
        self.details_button.setObjectName("ghostButton")
        self.details_button.clicked.connect(self.toggle_details)
        preview_footer.addWidget(self.details_button)

        self.stop_loop_button = QPushButton("STOP DISPLAY LOOP")
        self.stop_loop_button.setToolTip(
            "Stop the background 2.95-second restart loop. The LCD may go black when its current playback ends."
        )
        self.stop_loop_button.clicked.connect(self.stop_display_loop)
        preview_footer.addWidget(self.stop_loop_button)

        self.save_button = QPushButton("SAVE TO DISPLAY")
        self.save_button.setObjectName("primaryButton")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.start_upload)
        preview_footer.addWidget(self.save_button)
        p.addLayout(preview_footer)
        layout.addWidget(self.preview_card, 1)

        # The adjustment workspace opens only when a thumbnail or playlist item
        # is clicked. Saving adjustments closes it to keep the main screen clean.
        self.set_adjustment_workspace_visible(False)

        # Keep upload/shuffle progress close to the media that is being managed,
        # rather than leaving it at the bottom of the page.
        progress_card = QFrame()
        progress_card.setObjectName("controlBar")
        progress_layout = QVBoxLayout(progress_card)
        progress_layout.setContentsMargins(14, 9, 14, 9)
        progress_layout.setSpacing(5)
        self.status_label = QLabel("Choose a photo or video")
        self.status_label.setObjectName("muted")
        progress_layout.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        progress_layout.addWidget(self.progress)
        layout.addWidget(progress_card)

        media_header = QHBoxLayout()
        self.library_title = QLabel("Pictures & Videos")
        self.library_title.setObjectName("sectionTitle")
        media_header.addWidget(self.library_title)
        media_header.addStretch()
        self.selected_label = QLabel("No media selected")
        self.selected_label.setObjectName("muted")
        media_header.addWidget(self.selected_label)
        layout.addLayout(media_header)

        self.recent_scroll = QScrollArea()
        self.recent_scroll.setWidgetResizable(True)
        self.recent_scroll.setFixedHeight(104)
        self.recent_host = QWidget()
        self.recent_layout = QHBoxLayout(self.recent_host)
        self.recent_layout.setContentsMargins(0, 6, 0, 6)
        self.recent_layout.setSpacing(10)
        self.recent_layout.addStretch()
        self.recent_scroll.setWidget(self.recent_host)
        layout.addWidget(self.recent_scroll)

        shuffle_card = QFrame()
        shuffle_card.setObjectName("card")
        shuffle_layout = QVBoxLayout(shuffle_card)
        shuffle_layout.setContentsMargins(14, 12, 14, 12)
        shuffle_layout.setSpacing(9)

        shuffle_header = QHBoxLayout()
        shuffle_title = QLabel("Picture & Video Shuffle")
        shuffle_title.setObjectName("sectionTitle")
        shuffle_header.addWidget(shuffle_title)
        shuffle_header.addStretch()
        self.playlist_count_label = QLabel("0 items")
        self.playlist_count_label.setObjectName("muted")
        shuffle_header.addWidget(self.playlist_count_label)
        shuffle_layout.addLayout(shuffle_header)

        shuffle_controls = QHBoxLayout()
        add_many = QPushButton("ADD PICTURES / VIDEOS")
        add_many.clicked.connect(self.choose_playlist_media)
        shuffle_controls.addWidget(add_many)

        shuffle_controls.addWidget(QLabel("Pictures stay"))
        self.image_duration_combo = QComboBox()
        for seconds in (5, 7, 10):
            self.image_duration_combo.addItem(f"{seconds} seconds", seconds)
        self.image_duration_combo.setCurrentIndex(1)
        self.image_duration_combo.setToolTip(
            "This setting applies only to pictures. Videos always play to the end."
        )
        shuffle_controls.addWidget(self.image_duration_combo)

        automatic_timing = QLabel("Videos: source-matched FPS")
        automatic_timing.setStyleSheet(f"color:{ACCENT}; font-weight:700;")
        automatic_timing.setToolTip(
            "The app measures each prepared video's exact frame count at 60 FPS. "
            "The proven 1.30-second TRYX start latency stays constant for every clip."
        )
        shuffle_controls.addWidget(automatic_timing)

        self.shuffle_checkbox = QCheckBox("Shuffle order")
        self.shuffle_checkbox.setChecked(True)
        shuffle_controls.addWidget(self.shuffle_checkbox)
        shuffle_controls.addStretch()

        clear_playlist = QPushButton("Clear")
        clear_playlist.clicked.connect(self.clear_playlist)
        shuffle_controls.addWidget(clear_playlist)
        shuffle_layout.addLayout(shuffle_controls)

        playlist_drop_hint = QLabel(
            "Drag media tiles from the library above—or files from your desktop—into this list."
        )
        playlist_drop_hint.setObjectName("muted")
        shuffle_layout.addWidget(playlist_drop_hint)

        self.playlist_widget = PlaylistDropList()
        self.playlist_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.playlist_widget.setMaximumHeight(150)
        self.playlist_widget.currentRowChanged.connect(self.select_playlist_item)
        self.playlist_widget.files_dropped.connect(self.add_playlist_files)
        shuffle_layout.addWidget(self.playlist_widget)

        shuffle_footer = QHBoxLayout()
        shuffle_note = QLabel(
            "Pictures use the selected time. Videos are never restarted early and play through their full duration."
        )
        shuffle_note.setObjectName("muted")
        shuffle_note.setWordWrap(True)
        shuffle_footer.addWidget(shuffle_note, 1)

        remove_item = QPushButton("Remove selected")
        remove_item.clicked.connect(self.remove_playlist_item)
        shuffle_footer.addWidget(remove_item)

        self.stop_shuffle_button = QPushButton("STOP SHUFFLE")
        self.stop_shuffle_button.clicked.connect(self.stop_shuffle)
        shuffle_footer.addWidget(self.stop_shuffle_button)

        self.start_shuffle_button = QPushButton("START SHUFFLE")
        self.start_shuffle_button.setObjectName("primaryButton")
        self.start_shuffle_button.setEnabled(False)
        self.start_shuffle_button.clicked.connect(self.start_shuffle)
        shuffle_footer.addWidget(self.start_shuffle_button)
        shuffle_layout.addLayout(shuffle_footer)

        layout.addWidget(shuffle_card)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setVisible(False)
        self.log.setMaximumHeight(170)
        layout.addWidget(self.log)

        page.setMinimumWidth(900)
        page_scroll = QScrollArea()
        page_scroll.setWidgetResizable(True)
        page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        page_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        page_scroll.setWidget(page)
        return page_scroll

    def build_support_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(18)

        title = QLabel("SUPPORT DEVELOPMENT")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        intro = QLabel(
            "TRYX Display Manager is an independent Linux utility. Donations are optional "
            "and help support testing, bug fixes, documentation, and future features."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)

        donate_card = QFrame()
        donate_card.setObjectName("card")
        donate_layout = QVBoxLayout(donate_card)
        donate_layout.setContentsMargins(20, 18, 20, 18)
        donate_layout.setSpacing(12)

        donate_title = QLabel("Donation links")
        donate_title.setObjectName("sectionTitle")
        donate_layout.addWidget(donate_title)

        explanation = QLabel(
            "Paste your public donation-page links below. The app stores them only on this "
            "computer. Once saved, the matching support buttons become active."
        )
        explanation.setWordWrap(True)
        explanation.setObjectName("muted")
        donate_layout.addWidget(explanation)

        self.paypal_input = QLineEdit(self.support_links.get("paypal", ""))
        self.paypal_input.setPlaceholderText("PayPal donation page URL")
        paypal_row = QHBoxLayout()
        paypal_row.addWidget(QLabel("PayPal"))
        paypal_row.addWidget(self.paypal_input, 1)
        self.paypal_button = QPushButton("OPEN PAYPAL")
        self.paypal_button.clicked.connect(lambda: self.open_support_link("paypal"))
        paypal_row.addWidget(self.paypal_button)
        donate_layout.addLayout(paypal_row)

        self.kofi_input = QLineEdit(self.support_links.get("kofi", ""))
        self.kofi_input.setPlaceholderText("Ko-fi support page URL")
        kofi_row = QHBoxLayout()
        kofi_row.addWidget(QLabel("Ko-fi"))
        kofi_row.addWidget(self.kofi_input, 1)
        self.kofi_button = QPushButton("OPEN KO-FI")
        self.kofi_button.clicked.connect(lambda: self.open_support_link("kofi"))
        kofi_row.addWidget(self.kofi_button)
        donate_layout.addLayout(kofi_row)

        self.github_input = QLineEdit(self.support_links.get("github", ""))
        self.github_input.setPlaceholderText("GitHub Sponsors page URL")
        github_row = QHBoxLayout()
        github_row.addWidget(QLabel("GitHub Sponsors"))
        github_row.addWidget(self.github_input, 1)
        self.github_button = QPushButton("OPEN SPONSORS")
        self.github_button.clicked.connect(lambda: self.open_support_link("github"))
        github_row.addWidget(self.github_button)
        donate_layout.addLayout(github_row)

        save_row = QHBoxLayout()
        save_row.addStretch()
        save_links = QPushButton("SAVE SUPPORT LINKS")
        save_links.setObjectName("primaryButton")
        save_links.clicked.connect(self.save_support_links_from_ui)
        save_row.addWidget(save_links)
        donate_layout.addLayout(save_row)

        layout.addWidget(donate_card)

        purpose_card = QFrame()
        purpose_card.setObjectName("card")
        purpose_layout = QVBoxLayout(purpose_card)
        purpose_layout.setContentsMargins(20, 18, 20, 18)
        purpose_layout.setSpacing(8)
        purpose_title = QLabel("What support helps fund")
        purpose_title.setObjectName("sectionTitle")
        purpose_layout.addWidget(purpose_title)
        purpose_text = QLabel(
            "• Compatibility testing on Linux Mint and Ubuntu\n"
            "• More reliable picture and video playback\n"
            "• Shuffle improvements and saved layouts\n"
            "• Easier installation, updates, and documentation"
        )
        purpose_text.setWordWrap(True)
        purpose_layout.addWidget(purpose_text)
        layout.addWidget(purpose_card)

        notice = QLabel(
            "Unofficial software — not affiliated with or endorsed by TRYX. "
            "Donations are voluntary and are not required to use the app."
        )
        notice.setWordWrap(True)
        notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        notice.setStyleSheet(f"color:{MUTED}; padding:12px;")
        layout.addWidget(notice)
        layout.addStretch()

        self.refresh_support_buttons()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    def load_support_links(self) -> dict[str, str]:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if not SUPPORT_LINKS_FILE.exists():
            return {"paypal": "", "kofi": "", "github": ""}
        try:
            data = json.loads(SUPPORT_LINKS_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            return {"paypal": "", "kofi": "", "github": ""}
        return {
            "paypal": str(data.get("paypal", "")).strip(),
            "kofi": str(data.get("kofi", "")).strip(),
            "github": str(data.get("github", "")).strip(),
        }

    @staticmethod
    def valid_support_url(value: str) -> bool:
        url = QUrl(value.strip())
        return url.isValid() and url.scheme().lower() in {"http", "https"} and bool(url.host())

    def save_support_links_from_ui(self) -> None:
        links = {
            "paypal": self.paypal_input.text().strip(),
            "kofi": self.kofi_input.text().strip(),
            "github": self.github_input.text().strip(),
        }
        invalid = [name for name, value in links.items() if value and not self.valid_support_url(value)]
        if invalid:
            QMessageBox.warning(
                self,
                "Invalid support link",
                "Use a complete public link beginning with https:// for: " + ", ".join(invalid),
            )
            return
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        SUPPORT_LINKS_FILE.write_text(json.dumps(links, indent=2))
        self.support_links = links
        self.refresh_support_buttons()
        QMessageBox.information(self, "Support links saved", "Your donation links are now configured.")

    def refresh_support_buttons(self) -> None:
        if not hasattr(self, "paypal_button"):
            return
        self.paypal_button.setEnabled(self.valid_support_url(self.support_links.get("paypal", "")))
        self.kofi_button.setEnabled(self.valid_support_url(self.support_links.get("kofi", "")))
        self.github_button.setEnabled(self.valid_support_url(self.support_links.get("github", "")))

    def open_support_link(self, name: str) -> None:
        value = self.support_links.get(name, "").strip()
        if not self.valid_support_url(value):
            QMessageBox.information(
                self,
                "Link not configured",
                "Paste and save a valid public donation link first.",
            )
            return
        if not QDesktopServices.openUrl(QUrl(value)):
            QMessageBox.warning(self, "Could not open link", "Your default web browser could not open this link.")

    def refresh_device_status(self) -> None:
        try:
            found = usb.core.find(idVendor=VID, idProduct=PID) is not None
        except Exception:
            found = False
        if found:
            self.device_label.setText("●  TRYX connected")
            self.device_label.setStyleSheet(f"color:{ACCENT}; font-weight:700;")
        else:
            self.device_label.setText("●  TRYX not detected")
            self.device_label.setStyleSheet("color:#FF8C8C; font-weight:700;")

    def choose_playlist_media(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Add pictures and videos",
            str(HOME),
            MEDIA_FILE_FILTER,
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if paths:
            self.add_playlist_files([Path(path) for path in paths])

    def handle_dropped_files(self, paths: list[Path]) -> None:
        valid = [path for path in paths if path.is_file()]
        if not valid:
            return
        if len(valid) == 1:
            self.load_media(valid[0])
        else:
            self.add_playlist_files(valid)

    def add_playlist_files(self, paths: list[Path]) -> None:
        existing = {entry.source for entry in self.playlist_entries}
        first_new: int | None = None
        rejected: list[str] = []

        for raw_path in paths:
            source = raw_path.expanduser().resolve()
            if not source.is_file() or source in existing:
                continue
            try:
                kind = detect_kind(source)
            except RuntimeError:
                rejected.append(source.name)
                continue

            if first_new is None:
                first_new = len(self.playlist_entries)
            saved = self.saved_settings_for(source)
            entry = PlaylistEntry(source=source, kind=kind)
            if saved is not None:
                entry.fit = str(saved.get("fit", "crop"))
                entry.zoom = float(saved.get("zoom", 1.0))
                entry.anchor_x = float(saved.get("anchor_x", 0.5))
                entry.anchor_y = float(saved.get("anchor_y", 0.5))
                entry.rotation = int(saved.get("rotation", 0))
                entry.framing_saved = True
            self.playlist_entries.append(entry)
            existing.add(source)

        self.refresh_playlist_widget()
        if first_new is not None:
            self.playlist_widget.setCurrentRow(first_new)
        if rejected:
            QMessageBox.warning(
                self,
                "Some files were skipped",
                "These files are not supported:\n" + "\n".join(rejected),
            )

    def refresh_playlist_widget(self) -> None:
        current = self.playlist_widget.currentRow() if hasattr(self, "playlist_widget") else -1
        self.playlist_widget.blockSignals(True)
        self.playlist_widget.clear()
        self.playlist_widget.setIconSize(QSize(88, 50))

        for index, entry in enumerate(self.playlist_entries, start=1):
            status = "✓ Adjusted" if entry.framing_saved else "Needs adjustment"
            label = f"{index}. {entry.source.name}   •   {entry.kind.title()}   •   {status}"
            item = QListWidgetItem(label)
            pixmap = thumbnail_for(entry.source, QSize(88, 50))
            if not pixmap.isNull():
                item.setIcon(QIcon(pixmap))
            item.setToolTip(str(entry.source))
            self.playlist_widget.addItem(item)

        self.playlist_widget.blockSignals(False)
        self.playlist_count_label.setText(
            f"{len(self.playlist_entries)} item" + ("" if len(self.playlist_entries) == 1 else "s")
        )
        self.start_shuffle_button.setEnabled(bool(self.playlist_entries))

        if self.playlist_entries:
            row = min(max(current, 0), len(self.playlist_entries) - 1)
            self.playlist_widget.setCurrentRow(row)
        else:
            self.active_playlist_index = None

    def update_playlist_item_label(self, index: int) -> None:
        if not (0 <= index < len(self.playlist_entries)):
            return
        item = self.playlist_widget.item(index)
        if item is None:
            return
        entry = self.playlist_entries[index]
        status = "✓ Adjusted" if entry.framing_saved else "Needs adjustment"
        item.setText(
            f"{index + 1}. {entry.source.name}   •   {entry.kind.title()}   •   {status}"
        )

    def select_playlist_item(self, row: int) -> None:
        if row < 0 or row >= len(self.playlist_entries):
            self.active_playlist_index = None
            return

        entry = self.playlist_entries[row]
        try:
            preview = self.make_preview(entry.source, entry.kind)
        except Exception as exc:
            QMessageBox.critical(self, "Could not preview playlist item", str(exc))
            return

        self.active_playlist_index = row
        self.source = entry.source
        self.kind = entry.kind
        self.set_adjustment_workspace_visible(True)
        self.preview.set_pixmap(preview)
        self._loading_playlist_controls = True
        self._suspend_review_updates = True
        try:
            for index in range(self.fit_combo.count()):
                if str(self.fit_combo.itemData(index)) == entry.fit:
                    self.fit_combo.setCurrentIndex(index)
                    break
            for index in range(self.rotation_combo.count()):
                if int(self.rotation_combo.itemData(index)) == entry.rotation:
                    self.rotation_combo.setCurrentIndex(index)
                    break
            self.zoom_slider.setValue(round(entry.zoom * 100))
            self.x_slider.setValue(round(entry.anchor_x * 100))
            self.y_slider.setValue(round(entry.anchor_y * 100))
            self._anchor_x = entry.anchor_x
            self._anchor_y = entry.anchor_y
        finally:
            self._loading_playlist_controls = False

        self.update_preview()
        self._suspend_review_updates = False
        self.selected_label.setText(
            f"Shuffle item {row + 1}: {entry.source.name}  •  {entry.kind.title()}"
        )
        if entry.framing_saved:
            self.pending_review = False
            self.confirm_adjust_button.setEnabled(False)
            self.save_button.setEnabled(True)
            self.status_label.setText(
                "Saved framing loaded. Save to Display now, or make a change and save the adjustments again."
            )
            self.status_label.setStyleSheet(f"color:{ACCENT}; font-weight:700;")
        else:
            self.mark_adjustment_pending(
                "Review or adjust this item, then press Save Adjustments. The editor will close when saved."
            )

    def remove_playlist_item(self) -> None:
        row = self.playlist_widget.currentRow()
        if 0 <= row < len(self.playlist_entries):
            self.playlist_entries.pop(row)
            self.refresh_playlist_widget()

    def clear_playlist(self) -> None:
        self.playlist_entries.clear()
        self.playlist_widget.clear()
        self.active_playlist_index = None
        self.playlist_count_label.setText("0 items")
        self.start_shuffle_button.setEnabled(False)

    def start_shuffle(self) -> None:
        if not self.playlist_entries:
            QMessageBox.information(self, "Shuffle is empty", "Add at least one picture or video.")
            return

        unsaved = [
            index for index, entry in enumerate(self.playlist_entries)
            if not entry.framing_saved
        ]
        if unsaved:
            first = unsaved[0]
            self.playlist_widget.setCurrentRow(first)
            QMessageBox.information(
                self,
                "Save each item's adjustments",
                f"{len(unsaved)} playlist item(s) still need their framing saved. "
                "Adjust the selected item and press Save Adjustments before starting the shuffle.",
            )
            return

        if not TRYX_PLAYLIST_MANAGER.is_file():
            QMessageBox.critical(
                self,
                "Shuffle manager missing",
                f"Missing file: {TRYX_PLAYLIST_MANAGER}",
            )
            return

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        manifest = {
            "image_seconds": int(self.image_duration_combo.currentData()),
            "shuffle": self.shuffle_checkbox.isChecked(),
            "items": [entry.to_manifest() for entry in self.playlist_entries],
        }
        PLAYLIST_MANIFEST.write_text(json.dumps(manifest, indent=2))

        result = subprocess.run(
            [
                sys.executable, str(TRYX_PLAYLIST_MANAGER),
                "--start", "--manifest", str(PLAYLIST_MANIFEST),
            ],
            capture_output=True,
            text=True,
        )
        output = (result.stdout + result.stderr).strip()
        if output:
            self.append_log(output)
        if result.returncode != 0:
            QMessageBox.critical(
                self,
                "Could not start shuffle",
                output or f"Shuffle manager exited with status {result.returncode}",
            )
            return


        seconds = int(self.image_duration_combo.currentData())
        order_text = "random order" if self.shuffle_checkbox.isChecked() else "listed order"
        self.status_label.setText(
            f"✓  Shuffle starting — pictures {seconds}s, videos use exact frame timing, {order_text}"
        )
        self.status_label.setStyleSheet(f"color:{ACCENT}; font-weight:700;")
        self.log.append(
            f"The shuffle prepares all media in the background, then repeats indefinitely. "
            f"Pictures stay for {seconds} seconds. Every video is timed from its exact "
            f"prepared frame count at 60 FPS plus the fixed 1.30-second display-start sync."
        )

    def stop_shuffle(self) -> None:
        if not TRYX_PLAYLIST_MANAGER.is_file():
            QMessageBox.critical(self, "Shuffle manager missing", str(TRYX_PLAYLIST_MANAGER))
            return
        result = subprocess.run(
            [sys.executable, str(TRYX_PLAYLIST_MANAGER), "--stop"],
            capture_output=True,
            text=True,
        )
        output = (result.stdout + result.stderr).strip()
        if output:
            self.append_log(output)
        if result.returncode == 0:
            self.status_label.setText("Shuffle stopped")
            self.status_label.setStyleSheet(f"color:{MUTED};")
        else:
            QMessageBox.critical(
                self,
                "Could not stop shuffle",
                output or f"Shuffle manager exited with status {result.returncode}",
            )

    def set_adjustment_workspace_visible(self, visible: bool) -> None:
        for widget in (
            self.adjustment_controls,
            self.position_controls,
            self.preview_card,
        ):
            widget.setVisible(visible)

        if not visible:
            self.preview.set_pixmap(QPixmap())
            if hasattr(self, "log"):
                self.log.setVisible(False)
            if hasattr(self, "details_button"):
                self.details_button.setText("Show details")

    def load_saved_media(self) -> dict[str, dict[str, object]]:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if not SAVED_MEDIA_FILE.exists():
            return {}
        try:
            data = json.loads(SAVED_MEDIA_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): value for key, value in data.items() if isinstance(value, dict)}

    def save_saved_media(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        SAVED_MEDIA_FILE.write_text(json.dumps(self.saved_media, indent=2))

    def saved_settings_for(self, source: Path) -> dict[str, object] | None:
        return self.saved_media.get(str(source.resolve()))

    def choose_media(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "Choose media",
            str(HOME),
            MEDIA_FILE_FILTER,
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if path_text:
            self.load_media(Path(path_text))

    def mark_adjustment_pending(self, message: str | None = None) -> None:
        self.pending_review = True
        self.confirm_adjust_button.setEnabled(self.source is not None)
        self.save_button.setEnabled(False)
        if (
            self.active_playlist_index is not None
            and 0 <= self.active_playlist_index < len(self.playlist_entries)
        ):
            entry = self.playlist_entries[self.active_playlist_index]
            if entry.framing_saved:
                entry.framing_saved = False
                self.update_playlist_item_label(self.active_playlist_index)
        if message:
            self.status_label.setText(message)
            self.status_label.setStyleSheet(f"color:{MUTED};")

    def confirm_adjustment(self) -> None:
        if self.source is None or self.kind is None:
            return

        settings = {
            "kind": self.kind,
            "fit": str(self.fit_combo.currentData()),
            "zoom": self.zoom_slider.value() / 100.0,
            "anchor_x": self._anchor_x,
            "anchor_y": self._anchor_y,
            "rotation": int(self.rotation_combo.currentData()),
        }
        self.saved_media[str(self.source.resolve())] = settings
        self.save_saved_media()
        self.add_recent(self.source)

        if (
            self.active_playlist_index is not None
            and 0 <= self.active_playlist_index < len(self.playlist_entries)
        ):
            entry = self.playlist_entries[self.active_playlist_index]
            entry.framing_saved = True
            self.update_playlist_item_label(self.active_playlist_index)

        saved_name = self.source.name
        self.pending_review = False
        self.confirm_adjust_button.setEnabled(False)
        self.save_button.setEnabled(True)
        self.set_adjustment_workspace_visible(False)
        self.selected_label.setText(f"Saved adjustments: {saved_name}")
        self.status_label.setText(
            "✓ Adjustments saved to Pictures & Videos. Click a thumbnail or shuffle item to edit it again."
        )
        self.status_label.setStyleSheet(f"color:{ACCENT}; font-weight:700;")

    def load_media(self, path: Path) -> None:
        source = path.expanduser().resolve()
        if not source.is_file():
            QMessageBox.warning(self, "File not found", str(source))
            return
        try:
            kind = detect_kind(source)
            preview = self.make_preview(source, kind)
        except Exception as exc:
            QMessageBox.critical(self, "Could not open media", str(exc))
            return

        self.active_playlist_index = None
        if hasattr(self, "playlist_widget"):
            self.playlist_widget.blockSignals(True)
            self.playlist_widget.setCurrentRow(-1)
            self.playlist_widget.clearSelection()
            self.playlist_widget.blockSignals(False)

        self.source, self.kind = source, kind
        self.set_adjustment_workspace_visible(True)
        self.preview.set_pixmap(preview)

        saved = self.saved_settings_for(source)
        self._suspend_review_updates = True
        try:
            if saved is None:
                self.fit_combo.setCurrentIndex(0)
                self.rotation_combo.setCurrentIndex(0)
                self.zoom_slider.setValue(100)
                self.x_slider.setValue(50)
                self.y_slider.setValue(50)
                self._anchor_x = 0.5
                self._anchor_y = 0.5
            else:
                fit = str(saved.get("fit", "crop"))
                rotation = int(saved.get("rotation", 0))
                for index in range(self.fit_combo.count()):
                    if str(self.fit_combo.itemData(index)) == fit:
                        self.fit_combo.setCurrentIndex(index)
                        break
                for index in range(self.rotation_combo.count()):
                    if int(self.rotation_combo.itemData(index)) == rotation:
                        self.rotation_combo.setCurrentIndex(index)
                        break
                self.zoom_slider.setValue(round(float(saved.get("zoom", 1.0)) * 100))
                self.x_slider.setValue(round(float(saved.get("anchor_x", 0.5)) * 100))
                self.y_slider.setValue(round(float(saved.get("anchor_y", 0.5)) * 100))
                self._anchor_x = float(saved.get("anchor_x", 0.5))
                self._anchor_y = float(saved.get("anchor_y", 0.5))
        finally:
            self._suspend_review_updates = False

        self.update_preview()
        self.selected_label.setText(f"Editing: {source.name}  •  {kind.title()}")
        if saved is not None:
            self.pending_review = False
            self.confirm_adjust_button.setEnabled(False)
            self.save_button.setEnabled(True)
            self.status_label.setText(
                "Saved framing loaded. Save to Display now, or make a change and save the adjustments again."
            )
            self.status_label.setStyleSheet(f"color:{ACCENT}; font-weight:700;")
        else:
            self.mark_adjustment_pending(
                "Adjust the LCD preview, then press Save Adjustments. The editor will close when saved."
            )

    def make_preview(self, source: Path, kind: str) -> QPixmap:
        if kind == "image":
            pixmap = load_oriented_image(source)
        else:
            path = Path(self.preview_temp.name) / "preview.jpg"
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.2", "-i", str(source), "-frames:v", "1", str(path)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "FFmpeg could not create a preview")
            pixmap = QPixmap(str(path))
        if pixmap.isNull():
            raise RuntimeError("The selected file could not be previewed")
        return pixmap

    def reset_transform(self) -> None:
        self._suspend_review_updates = True
        try:
            self.fit_combo.setCurrentIndex(0)
            self.rotation_combo.setCurrentIndex(0)
            self.zoom_slider.setValue(100)
            self.x_slider.setValue(50)
            self.y_slider.setValue(50)
            self._anchor_x = 0.5
            self._anchor_y = 0.5
        finally:
            self._suspend_review_updates = False
        self.update_preview()

    def preview_position_changed(self, x: float, y: float) -> None:
        self.x_slider.blockSignals(True)
        self.y_slider.blockSignals(True)
        self.x_slider.setValue(round(x * 100))
        self.y_slider.setValue(round(y * 100))
        self.x_slider.blockSignals(False)
        self.y_slider.blockSignals(False)
        self._anchor_x, self._anchor_y = x, y
        self.update_preview()

    def preview_grid_changed(self, enabled: bool) -> None:
        self.preview.set_show_grid(enabled)

    @staticmethod
    def position_text(value: int, low: str, high: str) -> str:
        if value == 50:
            return "Center"
        if value < 50:
            return f"{low} {100 - value * 2}%"
        return f"{high} {(value - 50) * 2}%"

    def adjust_zoom(self, step: int) -> None:
        self.zoom_slider.setValue(max(50, min(300, self.zoom_slider.value() + step)))

    def update_preview(self) -> None:
        if not hasattr(self, "_anchor_x"):
            self._anchor_x = 0.5
            self._anchor_y = 0.5
        self.zoom_label.setText(f"{self.zoom_slider.value()}%")
        self.x_label.setText(self.position_text(self.x_slider.value(), "Left", "Right"))
        self.y_label.setText(self.position_text(self.y_slider.value(), "Top", "Bottom"))
        fit = str(self.fit_combo.currentData())
        zoom = self.zoom_slider.value() / 100.0
        rotation = int(self.rotation_combo.currentData())
        self._anchor_x = self.x_slider.value() / 100.0
        self._anchor_y = self.y_slider.value() / 100.0
        self.preview.set_transform(
            fit,
            zoom,
            self._anchor_x,
            self._anchor_y,
            rotation,
        )

        if (
            self.active_playlist_index is not None
            and not self._loading_playlist_controls
            and not self._suspend_review_updates
            and 0 <= self.active_playlist_index < len(self.playlist_entries)
        ):
            entry = self.playlist_entries[self.active_playlist_index]
            entry.fit = fit
            entry.zoom = zoom
            entry.anchor_x = self._anchor_x
            entry.anchor_y = self._anchor_y
            entry.rotation = rotation
            if entry.framing_saved:
                entry.framing_saved = False
                self.update_playlist_item_label(self.active_playlist_index)

        if self.source is not None and not self._suspend_review_updates:
            self.mark_adjustment_pending(
                "Preview changed. Press Save Adjustments when this looks right on the LCD preview."
            )

    def rotate_by(self, degrees: int) -> None:
        current = int(self.rotation_combo.currentData())
        target = (current + degrees) % 360
        for index in range(self.rotation_combo.count()):
            if int(self.rotation_combo.itemData(index)) == target:
                self.rotation_combo.setCurrentIndex(index)
                return

    def toggle_details(self) -> None:
        visible = not self.log.isVisible()
        self.log.setVisible(visible)
        self.details_button.setText("Hide details" if visible else "Show details")

    def stop_display_loop(self) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.information(
                self,
                "Upload in progress",
                "Wait for the current upload to finish before stopping the loop.",
            )
            return

        if not TRYX_LOOP_MANAGER.is_file():
            QMessageBox.critical(
                self,
                "Loop manager missing",
                f"Missing file: {TRYX_LOOP_MANAGER}",
            )
            return

        result = subprocess.run(
            [sys.executable, str(TRYX_LOOP_MANAGER), "--stop"],
            capture_output=True,
            text=True,
        )
        output = (result.stdout + result.stderr).strip()
        if output:
            self.append_log(output)
        if result.returncode == 0:
            self.status_label.setText("Display loop stopped")
            self.status_label.setStyleSheet(f"color:{MUTED};")
        else:
            QMessageBox.critical(
                self,
                "Could not stop loop",
                output or f"Loop manager exited with status {result.returncode}",
            )

    def start_upload(self) -> None:
        if self.source is None or self.kind is None:
            return
        if self.pending_review:
            QMessageBox.information(
                self,
                "Confirm your framing first",
                "Adjust the crop/zoom/position if needed, then press Save Adjustments before saving to the display.",
            )
            return
        settings = UploadSettings(
            source=self.source,
            kind=self.kind,
            fit=str(self.fit_combo.currentData()),
            zoom=self.zoom_slider.value() / 100.0,
            anchor_x=self._anchor_x,
            anchor_y=self._anchor_y,
            rotation=int(self.rotation_combo.currentData()),
        )
        self.log.clear()
        self.log.append(f"Source: {settings.source}")
        self.log.append(
            f"Mode={settings.fit}, rotation={settings.rotation}°, zoom={settings.zoom:.2f}, x={settings.anchor_x:.2f}, y={settings.anchor_y:.2f}\n"
        )
        self.save_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.status_label.setText("Preparing, uploading, then starting the proven 2.95-second loop…")
        self.worker = UploadThread(settings)
        self.worker.log.connect(self.append_log)
        self.worker.completed.connect(self.upload_completed)
        self.worker.start()

    def append_log(self, text: str) -> None:
        self.log.append(text)
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def upload_completed(self, success: bool, message: str) -> None:
        self.worker = None
        self.progress.setRange(0, 1)
        self.progress.setValue(1 if success else 0)
        self.save_button.setEnabled(self.source is not None and not self.pending_review)
        if success:
            self.status_label.setText("✓  Saved — continuous loop is running")
            self.status_label.setStyleSheet(f"color:{ACCENT}; font-weight:700;")
        else:
            self.status_label.setText("Upload failed — open Details for the error")
            self.status_label.setStyleSheet("color:#FF8C8C; font-weight:700;")
            if not self.log.isVisible():
                self.toggle_details()
            QMessageBox.critical(self, "Upload failed", message)

    def load_recent(self) -> list[Path]:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if not RECENT_FILE.exists():
            return []
        paths: list[Path] = []
        for line in RECENT_FILE.read_text(errors="ignore").splitlines():
            path = Path(line).expanduser().resolve()
            if path.is_file() and str(path) in self.saved_media:
                paths.append(path)
        return paths[:8]

    def save_recent(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        RECENT_FILE.write_text("\n".join(str(p) for p in self.recent_paths))

    def add_recent(self, path: Path) -> None:
        self.recent_paths = [p for p in self.recent_paths if p != path]
        self.recent_paths.insert(0, path)
        self.recent_paths = self.recent_paths[:8]
        self.save_recent()
        self.refresh_recent_tiles()

    def refresh_recent_tiles(self) -> None:
        while self.recent_layout.count():
            item = self.recent_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for path in self.recent_paths:
            tile = MediaTile(path)
            tile.setObjectName("mediaTile")
            tile.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            tile.setIconSize(QSize(112, 62))
            pixmap = thumbnail_for(path, QSize(142, 78))
            if not pixmap.isNull():
                tile.setIcon(QIcon(pixmap))
            name = path.stem
            tile.setText(name[:20] + ("…" if len(name) > 20 else ""))
            tile.setToolTip(str(path))
            tile.setFixedSize(126, 92)
            tile.clicked.connect(lambda checked=False, p=path: self.load_media(p))
            self.recent_layout.addWidget(tile)
        self.recent_layout.addStretch()
        count = len(self.recent_paths)
        self.library_title.setText(f"Pictures & Videos ({count})")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Upload in progress", "Wait for the upload to finish before closing the app.")
            event.ignore()
            return
        self.preview_temp.cleanup()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("TRYX Display Manager")
    app.setStyleSheet(APP_STYLE)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
