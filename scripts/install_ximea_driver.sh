#!/usr/bin/env bash
#
# Downloads, installs, and cleans up the XIMEA Linux PCIe camera driver.
# Source: https://www.ximea.com/support/wiki/apis/XIMEA_Linux_Software_Package
#
# Usage: sudo ./install_ximea_driver.sh

set -euo pipefail

URL="https://updates.ximea.com/public/ximea_linux_sp_beta.tgz"
WORKDIR="$(mktemp -d /tmp/ximea_install.XXXXXX)"
ARCHIVE="$WORKDIR/ximea_linux_sp_beta.tgz"
STATE_DIR="/var/lib/ximea_installer"
HASH_FILE="$STATE_DIR/last_sha256"

cleanup() {
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root (the XIMEA installer requires it)." >&2
    echo "Try: sudo $0" >&2
    exit 1
fi

echo "==> Downloading XIMEA Linux software package..."
wget -q --show-progress -O "$ARCHIVE" "$URL"

NEW_HASH="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
if [[ -f "$HASH_FILE" ]] && [[ "$(cat "$HASH_FILE")" == "$NEW_HASH" ]]; then
    echo "==> Downloaded package matches the last installed release (sha256: $NEW_HASH)."
    echo "==> Nothing to do."
    exit 0
fi

echo "==> New or updated release detected (sha256: $NEW_HASH)."

echo "==> Extracting..."
tar xzf "$ARCHIVE" -C "$WORKDIR"

echo "==> Running installer (PCIe support)..."
(cd "$WORKDIR/package" && ./install -pcie)

mkdir -p "$STATE_DIR"
echo "$NEW_HASH" > "$HASH_FILE"

echo "==> Verifying installation..."
if command -v xiSample >/dev/null 2>&1 || [[ -f /usr/lib/libm3api.so ]] || ldconfig -p | grep -q libm3api; then
    echo "==> XIMEA driver appears to be installed."
else
    echo "!! Could not confirm installation automatically. Check the installer output above." >&2
fi

echo
echo "============================================================"
echo " Installation complete. A REBOOT IS REQUIRED for the PCIe"
echo " driver to take effect."
echo "============================================================"
