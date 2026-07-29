#!/bin/bash
set -euo pipefail

APP_DIR="$HOME/tryx-linux"
VENV="$APP_DIR/.venv"
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR="$APP_DIR/backups/gui-$(date +%Y%m%d-%H%M%S)"
TRYX_CLI="$HOME/.local/bin/tryx"
LOOP_SCRIPT="$HOME/tryx_loop_media.py"

for required in \
    "$TRYX_CLI" \
    "$LOOP_SCRIPT" \
    "$HOME/tryx_restart_media.py" \
    "$HOME/tryx_upload.py" \
    "$HOME/tryx_image.py" \
    "$HOME/tryx_video_upload.py" \
    "$HOME/tryx_select_media.py"
do
    if [ ! -f "$required" ]; then
        echo "Missing required proven terminal file: $required" >&2
        exit 1
    fi
done

if [ ! -x "$VENV/bin/python3" ]; then
    echo "Missing TRYX virtual environment: $VENV" >&2
    exit 1
fi

command -v ffmpeg >/dev/null || {
    echo "ffmpeg is missing. Install it with: sudo apt install ffmpeg" >&2
    exit 1
}
command -v ffprobe >/dev/null || {
    echo "ffprobe is missing. Install it with: sudo apt install ffmpeg" >&2
    exit 1
}

# Close only the GUI and obsolete experimental keeper. Do not touch any of the
# known-good protocol/uploader scripts.
pkill -f "$APP_DIR/tryx_gui.py" 2>/dev/null || true
pkill -f tryx_image_keeper.py 2>/dev/null || true
if [ -f "$APP_DIR/tryx_playlist_manager.py" ]; then
    "$VENV/bin/python3" "$APP_DIR/tryx_playlist_manager.py" --stop >/dev/null 2>&1 || true
fi
rm -f "$HOME/.cache/tryx-display-manager/image-keeper.pid"

if command -v dpkg >/dev/null && ! dpkg -s libxcb-cursor0 >/dev/null 2>&1; then
    echo "Installing the Qt X11 runtime dependency..."
    sudo apt update
    sudo apt install -y libxcb-cursor0 libxkbcommon-x11-0 libxcb-xinerama0
fi

mkdir -p "$APP_DIR" "$BACKUP_DIR"

for existing in "$APP_DIR/tryx_gui.py" "$APP_DIR/tryx_loop_manager.py" "$APP_DIR/tryx_playlist_manager.py"; do
    if [ -f "$existing" ]; then
        cp -a "$existing" "$BACKUP_DIR/"
    fi
done

install -m 755 "$SOURCE_DIR/tryx_gui.py" "$APP_DIR/tryx_gui.py"
install -m 755 "$SOURCE_DIR/tryx_loop_manager.py" "$APP_DIR/tryx_loop_manager.py"
install -m 755 "$SOURCE_DIR/tryx_playlist_manager.py" "$APP_DIR/tryx_playlist_manager.py"

# Install GUI dependencies only when they are missing.
if ! "$VENV/bin/python3" -c 'import PySide6' >/dev/null 2>&1; then
    "$VENV/bin/python3" -m pip install PySide6
fi
if ! "$VENV/bin/python3" -c 'import usb.core' >/dev/null 2>&1; then
    "$VENV/bin/python3" -m pip install pyusb
fi

mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/tryx-display-manager.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=TRYX Display Manager
Comment=Upload, frame, loop, and shuffle pictures and videos on a TRYX display
Exec=$VENV/bin/python3 $APP_DIR/tryx_gui.py
Icon=video-display
Terminal=false
Categories=AudioVideo;Graphics;Utility;
StartupNotify=true
EOF
chmod 644 "$HOME/.local/share/applications/tryx-display-manager.desktop"

mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/tryx-gui" <<EOF
#!/bin/bash
exec "$VENV/bin/python3" "$APP_DIR/tryx_gui.py" "\$@"
EOF
chmod +x "$HOME/.local/bin/tryx-gui"

cat > "$HOME/.local/bin/tryx-loop" <<EOF
#!/bin/bash
exec "$VENV/bin/python3" "$APP_DIR/tryx_loop_manager.py" "\$@"
EOF
chmod +x "$HOME/.local/bin/tryx-loop"

cat > "$HOME/.local/bin/tryx-shuffle" <<EOF
#!/bin/bash
exec "$VENV/bin/python3" "$APP_DIR/tryx_playlist_manager.py" "\$@"
EOF
chmod +x "$HOME/.local/bin/tryx-shuffle"

# The obsolete keeper is deliberately removed from the application directory.
rm -f "$APP_DIR/tryx_image_keeper.py"

echo
echo "Installed TRYX Display Manager 0.8.18 with source-matched video FPS."
echo "Pictures use the selected duration. Each video is encoded at a cadence matched to its source, measured by exact frame count, and scheduled with the fixed proven 1.30-second display-start sync."
echo "No protocol or uploader scripts were replaced."
echo "Previous GUI files were backed up to:"
echo "  $BACKUP_DIR"
echo
echo "Launch:"
echo "  $HOME/.local/bin/tryx-gui"
echo
echo "Check the background single-media loop:"
echo "  $HOME/.local/bin/tryx-loop --status"
echo
echo "Check the background shuffle:"
echo "  $HOME/.local/bin/tryx-shuffle --status"
