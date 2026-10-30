#!/bin/sh
# Checks an installed (or removed) facelock; used by CI on a fresh machine.
#   sudo sh tests/check-install.sh [keyring|removed]
set -eu
mode=${1:-installed}
fail() { echo "FAIL: $*"; exit 1; }
ok() { echo "ok: $*"; }
pamdir=$(dirname "$(find /usr/lib /usr/lib64 /lib /lib64 -name pam_unix.so 2>/dev/null | head -1)")

if [ "$mode" = removed ]; then
    for p in /opt/facelock /etc/pam.d/gdm-face /usr/local/bin/facelock \
             /etc/systemd/system/facelockd.service /etc/systemd/system/facelock-update.timer \
             /usr/share/gnome-shell/extensions/facelock@kumarsujay03.github.io \
             /usr/share/pam-configs/facelock-keyring /var/lib/facelock "$pamdir/pam_face.so"; do
        [ ! -e "$p" ] || fail "$p is still there"
    done
    ! grep -rq facelock /etc/pam.d/ 2>/dev/null || fail "/etc/pam.d still mentions facelock"
    ok "everything removed"
    exit 0
fi

systemctl is-active --quiet facelockd || { journalctl -u facelockd --no-pager | tail -20; fail "facelockd is not running"; }
ok "facelockd is running"
systemctl is-enabled --quiet facelock-update.timer || fail "the update timer is not enabled"
ok "update timer enabled"
for _ in 1 2 3 4 5 6 7 8 9 10; do [ -S /run/facelock/socket ] && break; sleep 1; done
[ -S /run/facelock/socket ] || fail "no service socket"
[ "$(stat -c %a /run/facelock/socket)" = 600 ] || fail "service socket is not root-only"
[ "$(stat -c %a /run/facelock/control)" = 666 ] || fail "stop socket missing"
ok "sockets: service root-only, stop socket open"
[ -f "$pamdir/pam_face.so" ] || fail "pam_face.so not installed"
grep -q '^auth[[:space:]]*requisite[[:space:]]*pam_face.so' /etc/pam.d/gdm-face || fail "gdm-face has no pam_face"
ok "PAM module and /etc/pam.d/gdm-face"
[ -f /usr/share/gnome-shell/extensions/facelock@kumarsujay03.github.io/extension.js ] || fail "extension missing"
grep -q facelock@kumarsujay03.github.io /etc/dconf/db/gdm.d/03-facelock || fail "extension not enabled for the login screen"
ok "extension installed for the login screen"
facelock status | grep -q "^facelock $(cat /opt/facelock/VERSION)" || fail "facelock status"
ok "facelock status"
facelock report -o /tmp/facelock-report.txt >/dev/null && grep -q "No camera images" /tmp/facelock-report.txt || fail "facelock report"
ok "facelock report"

# a face login for a user with no face enrolled: the module asks the service,
# which says so, and PAM reports "unavailable" rather than success
out=$(pamtester gdm-face root authenticate 2>&1 || true)
echo "$out" | grep -qi "success" && fail "gdm-face authenticated without a face: $out"
user=${SUDO_USER:-$(getent passwd 1000 | cut -d: -f1 || true)}
if [ -n "$user" ]; then
    out=$(pamtester gdm-face "$user" authenticate 2>&1 || true)
    echo "$out" | grep -qi "success" && fail "gdm-face authenticated $user without a face"
    journalctl -t gdm-face --no-pager 2>/dev/null | grep -q "no face enrolled" ||
        journalctl --no-pager -n 200 | grep -q "no face unlock for $user" || fail "pam_face didn't reach the service"
    ok "gdm-face asks the service and refuses without a face"
fi
# the module must refuse to run anywhere but gdm-face
printf 'auth required pam_face.so\naccount required pam_permit.so\n' > /etc/pam.d/facelock-ci-other
out=$(pamtester facelock-ci-other "${user:-root}" authenticate 2>&1 || true)
rm -f /etc/pam.d/facelock-ci-other
echo "$out" | grep -qi "success" && fail "pam_face worked outside gdm-face"
ok "pam_face refuses other PAM services (sudo is safe)"

if [ "$mode" = keyring ]; then
    [ -f "$pamdir/pam_facelock_keyring.so" ] || fail "keyring module missing"
    grep -q 'pam_facelock_keyring.so' /etc/pam.d/gdm-face || fail "gdm-face has no keyring line"
    if command -v pam-auth-update >/dev/null; then
        grep -q facelock-keyring-seal /etc/pam.d/common-auth || fail "password sealing not in common-auth"
    fi
    ok "keyring unlock installed"
fi
echo "all checks passed ($mode)"
