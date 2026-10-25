#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DESKTOP="$ROOT/desktop-app"
APP="$DESKTOP/build/Argo.app"
AGENT_SOURCE="$ROOT/agent-server"

SWIFT_CACHE="${ARGO_SWIFT_CACHE:-/private/tmp/argo-swift-cache}"
CLANG_CACHE="${ARGO_CLANG_CACHE:-/private/tmp/argo-clang-cache}"
mkdir -p "$SWIFT_CACHE" "$CLANG_CACHE"
CLANG_MODULE_CACHE_PATH="$CLANG_CACHE" \
SWIFTPM_MODULECACHE_OVERRIDE="$CLANG_CACHE" \
swift build --package-path "$DESKTOP" --cache-path "$SWIFT_CACHE" --manifest-cache local -debug-info-format none --disable-sandbox -c release
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/agent-server"
cp "$DESKTOP/.build/release/Argo" "$APP/Contents/MacOS/Argo"
cp "$DESKTOP/argo_logo.png" "$APP/Contents/Resources/argo_logo.png"
cp "$DESKTOP/Argo.icns" "$APP/Contents/Resources/Argo.icns"
rsync -a \
  --exclude '.env' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'audit.jsonl' \
  --exclude 'tests' \
  "$AGENT_SOURCE/" "$APP/Contents/Resources/agent-server/"

PYTHON_PATH="${ARGO_PYTHON_PATH:-$AGENT_SOURCE/.venv/bin/python}"
if [[ ! -x "$PYTHON_PATH" ]]; then
  PYTHON_PATH="$(command -v python3 || true)"
fi
if [[ -z "$PYTHON_PATH" || ! -x "$PYTHON_PATH" ]]; then
  echo "Python 3 was not found. Install the agent requirements before building Argo." >&2
  exit 1
fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>Argo</string>
  <key>CFBundleIdentifier</key><string>com.scrummy.argo.desktop</string>
  <key>CFBundleName</key><string>Argo</string>
  <key>CFBundleDisplayName</key><string>Argo</string>
  <key>CFBundleIconFile</key><string>Argo.icns</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Argo listens for your voice commands to manage Jira issues during your meetings.</string>
  <key>ArgoPythonPath</key><string>$PYTHON_PATH</string>
  <key>ArgoLegacyDotEnvPath</key><string>$AGENT_SOURCE/.env</string>
</dict>
</plist>
PLIST

chmod +x "$APP/Contents/MacOS/Argo"
echo "Built $APP"
echo "Open it with: open \"$APP\""
