#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$HOME/tryx-linux"
VENV="$APP_DIR/.venv"
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"

APP_FILES=(
    tryx_gui.py
    tryx_loop_manager.py
    tryx_playlist_manager.py
    tryxctl.py
    tryx_upload.py
    tryx_image.py
    tryx_video_upload.py
    tryx_select_media.py
    tryx_loop_media.py
    tryx_restart_media.py
    tryx_play.py
)

echo "Installing TRYX Linux Display Manager..."

for file in "${APP_FILES[@]}" requirements.txt; do
    if [ ! -f "$SOURCE_DIR/$file" ]; then
        echo "Missing repository file: $SOURCE_DIR/$file" >&2
        exit 1
    fi
done

if command -v apt-get >/dev/null 2>&1; then
    packages=(
        ffmpeg
        python3
        python3-venv
        libxcb-cursor0
        libxkbcommon-x11-0
        libxcb-xinerama0
        libxcb-image0
        libxcb-keysyms1
        libxcb-render-util0
        libxcb-icccm4
    )

    missing_packages=()

    for package in "${packages[@]}"; do
        if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null \
            | grep -q "install ok installed"; then
            missing_packages+=("$package")
        fi
    done

    if [ "${#missing_packages[@]}" -gt 0 ]; then
        echo "Installing required system packages:"
        printf '  %s\n' "${missing_packages[@]}"
        sudo apt-get update
        sudo apt-get install -y "${missing_packages[@]}"
    fi
else
    for command in python3 ffmpeg ffprobe; do
        if ! command -v "$command" >/dev/null 2>&1; then
            echo "Missing required command: $command" >&2
            exit 1
        fi
    done
fi

# Stop older background processes before replacing their files.
if [ -x "$VENV/bin/python3" ]; then
    if [ -f "$APP_DIR/tryx_playlist_manager.py" ]; then
        "$VENV/bin/python3" \
            "$APP_DIR/tryx_playlist_manager.py" \
            --stop >/dev/null 2>&1 || true
    fi

    if [ -f "$APP_DIR/tryx_loop_manager.py" ]; then
        "$VENV/bin/python3" \
            "$APP_DIR/tryx_loop_manager.py" \
            --stop >/dev/null 2>&1 || true
    fi
fi

pkill -f "$APP_DIR/tryx_gui.py" 2>/dev/null || true

mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR"

for file in "${APP_FILES[@]}"; do
    install -m 755 "$SOURCE_DIR/$file" "$APP_DIR/$file"
done

install -m 644 \
    "$SOURCE_DIR/requirements.txt" \
    "$APP_DIR/requirements.txt"

if [ ! -x "$VENV/bin/python3" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv "$VENV"
fi

echo "Installing Python dependencies..."
"$VENV/bin/python3" -m pip install --upgrade pip
"$VENV/bin/python3" -m pip install \
    --requirement "$APP_DIR/requirements.txt"

cat > "$APP_DIR/tryx" <<EOF
#!/usr/bin/env bash
exec "$VENV/bin/python3" "$APP_DIR/tryxctl.py" "\$@"
EOF

cat > "$APP_DIR/tryx-gui" <<EOF
#!/usr/bin/env bash
exec "$VENV/bin/python3" "$APP_DIR/tryx_gui.py" "\$@"
EOF

cat > "$APP_DIR/tryx-loop" <<EOF
#!/usr/bin/env bash
exec "$VENV/bin/python3" "$APP_DIR/tryx_loop_manager.py" "\$@"
EOF

cat > "$APP_DIR/tryx-shuffle" <<EOF
#!/usr/bin/env bash
exec "$VENV/bin/python3" "$APP_DIR/tryx_playlist_manager.py" "\$@"
EOF

chmod 755 \
    "$APP_DIR/tryx" \
    "$APP_DIR/tryx-gui" \
    "$APP_DIR/tryx-loop" \
    "$APP_DIR/tryx-shuffle"

ln -sfn "$APP_DIR/tryx" "$BIN_DIR/tryx"
ln -sfn "$APP_DIR/tryx-gui" "$BIN_DIR/tryx-gui"
ln -sfn "$APP_DIR/tryx-loop" "$BIN_DIR/tryx-loop"
ln -sfn "$APP_DIR/tryx-shuffle" "$BIN_DIR/tryx-shuffle"

cat > "$DESKTOP_DIR/tryx-display-manager.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=TRYX Display Manager
Comment=Unofficial Linux display manager for compatible TRYX displays
Exec=$BIN_DIR/tryx-gui
Icon=video-display
Terminal=false
Categories=AudioVideo;Graphics;Utility;
StartupNotify=true
EOF

chmod 644 "$DESKTOP_DIR/tryx-display-manager.desktop"

# Permit ordinary desktop users to access the compatible USB device.
if [ "${TRYX_SKIP_UDEV:-0}" != "1" ] \
    && command -v sudo >/dev/null 2>&1 \
    && [ -d /etc/udev/rules.d ]; then

    RULE_FILE="/etc/udev/rules.d/99-tryx-turris.rules"
    RULE='SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="391a", ATTR{idProduct}=="2011", MODE="0660", GROUP="plugdev", TAG+="uaccess"'

    if [ ! -f "$RULE_FILE" ] \
        || ! grep -q 'idVendor.*391a.*idProduct.*2011' "$RULE_FILE"; then
        echo "Installing TRYX USB permissions rule..."
        printf '%s\n' "$RULE" | sudo tee "$RULE_FILE" >/dev/null
        sudo udevadm control --reload-rules
        sudo udevadm trigger
    fi
fi

echo
echo "TRYX Linux Display Manager installed successfully."
echo
echo "Launch the GUI:"
echo "  $BIN_DIR/tryx-gui"
echo
echo "Run diagnostics:"
echo "  $BIN_DIR/tryx --doctor"
echo
echo "Shuffle status:"
echo "  $BIN_DIR/tryx-shuffle --status"
