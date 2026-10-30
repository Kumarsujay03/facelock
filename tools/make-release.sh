#!/bin/sh
# Start a release: checks the version, changelog and CI, then pushes the tag.
# GitHub Actions (release.yml) runs every check again on the tagged commit
# and publishes the release only if they all pass.
#   tools/make-release.sh 1.0.5
set -e
cd "$(dirname "$0")/.."
version=$1
[ -n "$version" ] || { echo "usage: $0 <version>"; exit 1; }
[ "$(cat VERSION)" = "$version" ] || { echo "VERSION says $(cat VERSION), not $version"; exit 1; }
grep -q "^## $version\$" CHANGELOG.md || { echo "add a '## $version' entry to CHANGELOG.md"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "commit your changes first"; exit 1; }
git fetch -q origin
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || { echo "push main first"; exit 1; }
sha=$(git rev-parse HEAD)
ci=$(gh run list --workflow ci.yml --commit "$sha" --json status,conclusion \
    -q '.[0] | "\(.status) \(.conclusion)"' 2>/dev/null)
case $ci in
    "completed success") ;;
    "") echo "no CI run for $sha yet; wait for it (gh run list)"; exit 1 ;;
    completed*) echo "CI failed for $sha: fix it first (gh run list --workflow ci.yml)"; exit 1 ;;
    *) echo "CI is still running for $sha; try again when it has passed"; exit 1 ;;
esac
git tag -a "v$version" -m "facelock $version"
git push origin "v$version"
echo "Tag pushed. The release is published once the checks pass:"
echo "  gh run watch \$(gh run list --workflow release.yml -L1 --json databaseId -q '.[0].databaseId')"
