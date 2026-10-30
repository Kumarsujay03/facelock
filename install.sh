#!/bin/sh
# Install (or update) face unlock for the GDM login and lock screens.
#
#   sudo ./install.sh              first install: asks about the keyring
#   sudo ./install.sh --update     reinstall this version, keeping every choice
#   sudo ./install.sh --keyring | --no-keyring   answer the keyring question
#
# Puts the service in /opt/facelock, the PAM module next to the others, a new
# /etc/pam.d/gdm-face (sudo and other apps are not touched), and the GNOME
# Shell extension for both the login screen and your session. Your settings
# (/etc/facelock/facelock.conf) and face data (/var/lib/facelock) are kept.
set -e
[ "$(id -u)" = 0 ] || { echo "Run with sudo."; exit 1; }
HERE=$(dirname "$(readlink -f "$0")")
USER_NAME=${SUDO_USER:-}
DEST=/opt/facelock
UUID=facelock@kumarsujay03.github.io
VERSION=$(cat "$HERE/VERSION")
MODE=install
KEYRING=ask
for arg in "$@"; do
    case $arg in
        --update) MODE=update ;;
        --keyring) KEYRING=yes ;;
        --no-keyring) KEYRING=no ;;
        *) echo "Unknown option $arg"; exit 1 ;;
    esac
done
echo "facelock $VERSION ($MODE)"

# --- requirements ----------------------------------------------------------
command -v gdm3 >/dev/null 2>&1 || command -v gdm >/dev/null 2>&1 ||
    { echo "GDM (the GNOME login screen) is required."; exit 1; }
command -v gnome-shell >/dev/null 2>&1 || { echo "GNOME Shell is required."; exit 1; }
PAMDIR=$(dirname "$(find /usr/lib /usr/lib64 /lib /lib64 -name pam_unix.so 2>/dev/null | head -1)")
[ -d "$PAMDIR" ] || { echo "Can't find the PAM module directory."; exit 1; }
for f in /etc/pam.d/gdm-password /usr/lib/pam.d/gdm-password; do
    [ -f "$f" ] && GDM_PASSWORD=$f && break
done
[ -n "$GDM_PASSWORD" ] || { echo "No gdm-password PAM file: is this GDM?"; exit 1; }

https_help() {
    echo
    echo "Can't download over HTTPS: $1"
    echo "This laptop doesn't trust the certificate it receives. Usually a VPN,"
    echo "company or college proxy, or antivirus web filter is intercepting HTTPS"
    echo "(it shows as 'self-signed certificate in certificate chain'), or the"
    echo "trusted certificates are out of date. See \"Troubleshooting\" in README.md:"
    echo "  https://github.com/Kumarsujay03/facelock#troubleshooting"
    echo "Fix that, then run this installer again."
    exit 1
}
# build $1 if it's missing or older than its source $2
need_build() {
    [ ! -f "$1" ] || [ -n "$(find "$2" -newer "$1" 2>/dev/null)" ]
}
packages() {
    # build tools, PAM and systemd headers, Python venv, curl
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -q >/dev/null || true
        DEBIAN_FRONTEND=noninteractive apt-get install -y -q \
            build-essential libpam0g-dev libsystemd-dev python3-venv curl >/dev/null
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y -q gcc make pam-devel systemd-devel python3 curl
    elif command -v pacman >/dev/null 2>&1; then
        pacman -S --needed --noconfirm base-devel pam systemd-libs python curl
    elif command -v zypper >/dev/null 2>&1; then
        zypper --non-interactive install gcc make pam-devel systemd-devel python3 curl
    else
        echo "Install a C compiler, make, the PAM and systemd headers, Python 3 with venv and curl, then run this again."
        exit 1
    fi
}
# the face models and Python packages come over HTTPS: check that works
# before changing anything
if command -v curl >/dev/null 2>&1 && [ "$MODE" = install ]; then
    err=$(curl -fsS --max-time 20 -o /dev/null https://github.com/opencv/opencv_zoo 2>&1) ||
        https_help "$(echo "$err" | head -1)"
fi

if need_build "$HERE/pam/pam_face.so" "$HERE/pam/pam_face.c" ||
   need_build "$HERE/keyring/pam_facelock_keyring.so" "$HERE/keyring/pam_facelock_keyring.c" ||
   ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
    echo "== Packages needed to build"
    packages
fi

echo "== PAM modules"
need_build "$HERE/pam/pam_face.so" "$HERE/pam/pam_face.c" && make -s -C "$HERE/pam" pam_face.so
need_build "$HERE/keyring/pam_facelock_keyring.so" "$HERE/keyring/pam_facelock_keyring.c" &&
    make -s -C "$HERE/keyring" pam_facelock_keyring.so
install -m 644 "$HERE/pam/pam_face.so" "$PAMDIR/pam_face.so"

echo "== Service in $DEST"
install -d -m 755 "$DEST" "$DEST/bin" "$DEST/models" "$DEST/facelock" "$DEST/keyring"
rm -f "$DEST"/facelock/*.py
install -m 644 "$HERE"/facelock/*.py "$DEST/facelock/"
install -m 755 "$HERE/bin/facelock" "$HERE/bin/facelockd" "$DEST/bin/"
install -m 644 "$HERE/requirements.txt" "$HERE/README.md" "$HERE/VERSION" "$DEST/"
install -m 644 "$HERE/models/SHA256SUMS" "$DEST/models/"
# the face models: from this folder if present, else from the OpenCV model zoo
while read -r sum file; do
    if [ -f "$HERE/models/$file" ]; then
        install -m 644 "$HERE/models/$file" "$DEST/models/$file"
    elif ! echo "$sum  $DEST/models/$file" | sha256sum -c --quiet >/dev/null 2>&1; then
        url=$(grep -F "$file" "$HERE/models/URLS")
        echo "   downloading $file"
        err=$(curl -fsSL --retry 2 -o "$DEST/models/$file.part" "${url#* }" 2>&1) ||
            { rm -f "$DEST/models/$file.part"; https_help "$(echo "$err" | head -1)"; }
        mv "$DEST/models/$file.part" "$DEST/models/$file"
    fi
done < "$HERE/models/SHA256SUMS"
(cd "$DEST/models" && sha256sum -c --quiet SHA256SUMS) ||
    { echo "The face models don't match their checksums."; exit 1; }
if [ ! -x "$DEST/.venv/bin/python" ] || ! cmp -s "$HERE/requirements.txt" "$DEST/.venv/requirements.txt"; then
    echo "   setting up Python packages (NumPy, OpenCV)"
    rm -rf "$DEST/.venv"
    python3 -m venv "$DEST/.venv"
    "$DEST/.venv/bin/pip" install --quiet --disable-pip-version-check -r "$DEST/requirements.txt"
    cp "$DEST/requirements.txt" "$DEST/.venv/requirements.txt"
fi
"$DEST/.venv/bin/python" -I -m compileall -q "$DEST/facelock" >/dev/null
ln -sf "$DEST/bin/facelock" /usr/local/bin/facelock
install -d -m 755 /etc/facelock
CONF=/etc/facelock/facelock.conf
if [ -f "$CONF" ] && ! grep -q '^accept[[:space:]]*=' "$CONF"; then
    # settings from facelock 1.0.x: their match settings let a look-alike in.
    # Keep a copy, install the new file, and carry over the other settings.
    cp -a "$CONF" "$CONF.1.0"
    install -m 644 "$HERE/system/facelock.conf" "$CONF"
    for key in device scan_seconds attempt_seconds max_failures min_face_px liveness \
               lowlight dark_luma light_after auto; do
        value=$(sed -n "s/^${key}[[:space:]]*=[[:space:]]*//p" "$CONF.1.0" | head -1)
        [ -n "$value" ] && sed -i "s|^${key}[[:space:]]*=.*|${key} = ${value}|" "$CONF"
    done
    echo "   new match settings (the old file is kept as $CONF.1.0)"
fi
[ -f "$CONF" ] || install -m 644 "$HERE/system/facelock.conf" "$CONF"
install -m 644 "$HERE/system/facelockd.service" "$HERE/system/facelock-update.service" \
    "$HERE/system/facelock-update.timer" /etc/systemd/system/
# scans logged before the history file existed (updates from 0.x)
"$DEST/bin/facelock" import-log || true
systemctl daemon-reload
systemctl enable --quiet facelockd.service facelock-update.timer
systemctl restart facelockd.service
systemctl start facelock-update.timer

echo "== Keyring unlock"
# A face login can't type your password, so the login keyring (saved Wi-Fi
# and browser passwords) would stay locked. Optionally, the password you type
# at the login or lock screen is sealed (by the TPM if there is one) and
# unsealed only after your face matches.
FP_KEYRING=$(find "$PAMDIR" -name pam_fp_keyring.so 2>/dev/null | head -1)
if [ -n "$FP_KEYRING" ]; then
    echo "   fp-keyring is already installed here; face login uses it"
    KEYRING=no
elif [ "$KEYRING" = ask ]; then
    if [ "$MODE" = update ]; then
        [ -f /usr/share/pam-configs/facelock-keyring ] && KEYRING=yes || KEYRING=no
    elif [ -t 0 ]; then
        printf '   Unlock your login keyring after a face login too? [Y/n] '
        read -r answer
        case $answer in [Nn]*) KEYRING=no ;; *) KEYRING=yes ;; esac
    else
        KEYRING=no
    fi
fi
if [ "$KEYRING" = yes ]; then
    install -m 644 "$HERE/keyring/pam_facelock_keyring.so" "$PAMDIR/pam_facelock_keyring.so"
    install -m 755 "$HERE/keyring/facelock-keyring-seal" "$HERE/keyring/facelock-keyring-seal-job" "$DEST/keyring/"
    install -m 644 "$HERE/keyring/facelock-keyring-seal@.service" /etc/systemd/system/
    systemctl daemon-reload
    install -d -m 700 /var/lib/facelock-keyring
    [ -n "$USER_NAME" ] && { grep -qxF "$USER_NAME" /etc/facelock/keyring-users 2>/dev/null ||
        echo "$USER_NAME" >> /etc/facelock/keyring-users; }
    if command -v pam-auth-update >/dev/null 2>&1; then
        install -m 644 "$HERE/keyring/facelock-keyring.pam-config" /usr/share/pam-configs/facelock-keyring
        pam-auth-update --package --enable facelock-keyring
        echo "   on: unlock once with your password at the lock screen so it can be sealed"
    else
        echo "   This distribution has no pam-auth-update: the keyring will ask for your"
        echo "   password once after a face login (see README, \"Keyring\")."
    fi
else
    [ -z "$FP_KEYRING" ] && echo "   off: after a face login the keyring asks for your password once"
fi

echo "== /etc/pam.d/gdm-face"
# gdm-password with its auth part replaced: account, session and password
# stay as on this system
{
    echo "#%PAM-1.0"
    echo "# Face unlock for the GDM login and lock screens (installed by facelock $VERSION)."
    echo "auth    requisite       pam_nologin.so"
    echo "auth    required        pam_succeed_if.so user != root quiet_success"
    echo "auth    requisite       pam_face.so"
    echo "# hand the sealed login password to the keyring (whichever is installed)"
    echo "-auth   optional        pam_facelock_keyring.so"
    echo "-auth   optional        pam_fp_keyring.so"
    echo "auth    optional        pam_gnome_keyring.so"
    grep -v -E '^#%PAM|^[[:space:]]*-?auth[[:space:]]|^@include[[:space:]]+(common-auth|system-auth|password-auth|system-login)' "$GDM_PASSWORD" || true
} > /etc/pam.d/gdm-face.new
mv /etc/pam.d/gdm-face.new /etc/pam.d/gdm-face
chmod 644 /etc/pam.d/gdm-face

echo "== GNOME Shell extension"
rm -rf "/usr/share/gnome-shell/extensions/$UUID"
install -d -m 755 /usr/share/gnome-shell/extensions
cp -r "$HERE/extension/$UUID" /usr/share/gnome-shell/extensions/
chmod -R a+rX "/usr/share/gnome-shell/extensions/$UUID"
if [ -d /etc/dconf/db ]; then
    # the login screen: keep the extensions other files enable, add ours
    # (dconf uses the last file that sets a key, so this one includes all)
    # the login screen reads /etc/dconf/db/gdm.d only if its settings profile
    # lists "system-db:gdm"; some distributions' own profile doesn't (Ubuntu's
    # in /usr/share/dconf/profile), so use a copy in /etc that does
    PROFILE=/etc/dconf/profile/gdm
    if [ ! -f "$PROFILE" ]; then
        install -d -m 755 /etc/dconf/profile
        if [ -f /usr/share/dconf/profile/gdm ]; then
            cp /usr/share/dconf/profile/gdm "$PROFILE"
        else
            printf 'user-db:user\n' > "$PROFILE"
        fi
    fi
    if ! grep -qx 'system-db:gdm' "$PROFILE"; then
        if grep -q '^user-db:' "$PROFILE"; then
            sed -i '/^user-db:/a system-db:gdm' "$PROFILE"
        else
            sed -i '1i system-db:gdm' "$PROFILE"
        fi
    fi
    install -d -m 755 /etc/dconf/db/gdm.d
    EXISTING=$(grep -h '^enabled-extensions=' /etc/dconf/db/gdm.d/* 2>/dev/null | grep -v "$UUID" |
        sed "s/^enabled-extensions=//; s/[][' ]//g" | tr ',' '\n' | grep . | sort -u | tr '\n' ' ')
    LIST="'$UUID'"
    for u in $EXISTING; do LIST="$LIST, '$u'"; done
    printf '[org/gnome/shell]\nenabled-extensions=[%s]\n' "$LIST" > /etc/dconf/db/gdm.d/03-facelock
    dconf update
fi
if [ -n "$USER_NAME" ] && [ "$MODE" = install ]; then
    USER_ID=$(id -u "$USER_NAME")
    BUS="unix:path=/run/user/$USER_ID/bus"
    # add it to the session's extension list; GNOME loads it at the next login
    CURRENT=$(runuser -u "$USER_NAME" -- env DBUS_SESSION_BUS_ADDRESS="$BUS" \
        gsettings get org.gnome.shell enabled-extensions 2>/dev/null || true)
    case $CURRENT in
        *"$UUID"*) ;;
        "@as []"|"[]"|"") NEW="['$UUID']" ;;
        *) NEW=$(echo "$CURRENT" | sed "s/]\$/, '$UUID']/") ;;
    esac
    [ -n "$NEW" ] && runuser -u "$USER_NAME" -- env DBUS_SESSION_BUS_ADDRESS="$BUS" \
        gsettings set org.gnome.shell enabled-extensions "$NEW" 2>/dev/null || true
fi

echo
if [ "$MODE" = update ]; then
    echo "Updated to facelock $VERSION. Log out and back in to load the new extension."
else
    echo "Installed facelock $VERSION. Next:"
    echo "  1. sudo facelock enroll     (look at the camera and follow the prompts)"
    echo "  2. sudo facelock test       (one scan: shows the match score and timing)"
    echo "  3. Log out and back in once, so GNOME loads the extension."
    echo "Then lock the screen (Super+L), wait two seconds, move the mouse and look at the camera."
fi
