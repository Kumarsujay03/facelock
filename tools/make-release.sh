#!/bin/sh
# Build facelock-<version>.tar.gz (+ .sha256) from the committed tree and
# publish it as a GitHub release. `facelock update` installs these.
#   tools/make-release.sh 1.0.1 [notes-file]
set -e
cd "$(dirname "$0")/.."
version=$1
[ -n "$version" ] || { echo "usage: $0 <version> [notes-file]"; exit 1; }
[ "$(cat VERSION)" = "$version" ] || { echo "VERSION says $(cat VERSION), not $version"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "commit your changes first"; exit 1; }
out=dist
mkdir -p "$out"
tar=facelock-$version.tar.gz
git archive --format=tar.gz --prefix="facelock-$version/" -o "$out/$tar" HEAD
(cd "$out" && sha256sum "$tar" > "$tar.sha256")
git tag -a "v$version" -m "facelock $version" 2>/dev/null || true
git push origin "v$version"
if [ -n "$2" ]; then
    gh release create "v$version" "$out/$tar" "$out/$tar.sha256" --title "facelock $version" --notes-file "$2"
else
    gh release create "v$version" "$out/$tar" "$out/$tar.sha256" --title "facelock $version" --generate-notes
fi
