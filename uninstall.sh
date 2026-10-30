#!/bin/sh
# Remove face unlock. Your face data in /var/lib/facelock is deleted too
# unless you pass --keep-data. Run with sudo.
set -e
[ "$(id -u)" = 0 ] || { echo "Run with sudo."; exit 1; }
UUID=facelock@kumarsujay03.github.io
systemctl disable --now facelockd.service facelock-update.timer 2>/dev/null || true
rm -f /etc/systemd/system/facelockd.service /etc/systemd/system/facelock-update.service \
    /etc/systemd/system/facelock-update.timer
rm -f /etc/pam.d/gdm-face
if [ -f /usr/share/pam-configs/facelock-keyring ]; then
    pam-auth-update --package --remove facelock-keyring
    rm -f /usr/share/pam-configs/facelock-keyring
fi
rm -f /etc/systemd/system/facelock-keyring-seal@.service
rm -rf /var/lib/facelock-keyring /run/facelock-keyring /etc/facelock/keyring-users
systemctl daemon-reload
find /usr/lib /usr/lib64 /lib /lib64 \( -name pam_face.so -o -name pam_facelock_keyring.so \) \
    -path '*security*' -delete 2>/dev/null || true
rm -rf "/usr/share/gnome-shell/extensions/$UUID"
if [ -f /etc/dconf/db/gdm.d/03-facelock ]; then
    rm -f /etc/dconf/db/gdm.d/03-facelock
    dconf update
fi
rm -f /usr/local/bin/facelock
rm -rf /opt/facelock
if [ "$1" != "--keep-data" ]; then
    rm -rf /var/lib/facelock
    echo "Face data deleted."
fi
echo "Removed. /etc/facelock/facelock.conf was left in place."
