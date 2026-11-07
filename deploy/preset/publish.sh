#!/usr/bin/env bash
# Publish this independent version once. Never move an existing published tag.
set -euo pipefail
readonly tag='v3.10.2-preset-sort'
readonly repo='kjxv-1/3x-ui'
readonly branch='codex/v3.10.2-preset-sort'
[[ "${GITHUB_REPOSITORY:?}" == "$repo" ]]
[[ "${GITHUB_SHA:?}" =~ ^[0-9a-f]{40}$ ]]
[[ "${PRESET_RELEASE_TAG:?}" == "$tag" ]]
[[ "v$(cat internal/config/version)" == "$tag" ]]
[[ "$(git rev-parse HEAD)" == "$GITHUB_SHA" ]]
[[ "$(gh api "repos/$repo/git/ref/heads/$branch" --jq '.object.sha')" == "$GITHUB_SHA" ]]
assets="$(realpath "${1:?package directory required}")"
python3 - "$assets" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
expected = {f'x-ui-linux-{a}.tar.gz' for a in ('amd64', 'arm64', 'armv7', 'armv6', 'armv5', '386', 's390x')} | {'x-ui-windows-amd64.zip'}
actual = {p.name for p in root.glob('x-ui-*') if p.name.endswith(('.tar.gz', '.zip'))}
assert actual == expected, 'Exactly eight platform packages are required'
assert all((root / n).is_file() and (root / n).stat().st_size for n in expected), 'Empty package'
PY
(cd "$assets"; sha256sum *.tar.gz *.zip > checksums.txt; for file in *.tar.gz *.zip; do sha256sum "$file" > "$file.sha256"; done)
# Stage every public attachment together. Only a complete draft may become public.
cp install.sh x-ui.sh update.sh deploy/preset/README.md "$assets/"
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
refresh_release() {
    # The by-tag REST endpoint excludes draft releases. List authenticated
    # releases instead, so interrupted drafts can be verified and resumed.
    gh api --paginate --slurp "repos/$repo/releases" > "$work/releases.json"
    python3 - "$work" "$tag" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
items = [r for page in json.load(open(root / 'releases.json')) for r in page if r['tag_name'] == sys.argv[2]]
assert len(items) <= 1, 'Ambiguous release tag'
release = items[0] if items else None
(root / 'release.json').write_text(json.dumps(release))
(root / 'status').write_text('none' if release is None else 'draft' if release['draft'] else 'published')
assert release is None or not release.get('immutable', False) or not release['draft'], 'Immutable draft cannot be resumed'
PY
}
refresh_release
if gh api "repos/$repo/git/ref/tags/$tag" --jq '.object.sha' > "$work/ref" 2>/dev/null; then
    if [[ "$(cat "$work/ref")" != "$GITHUB_SHA" ]]; then
        [[ "$(cat "$work/status")" == draft ]] || {
            echo 'This fixed tag belongs to another commit; refusing to move it.' >&2; exit 1;
        }
        # Only this version's unpublished draft may follow a repaired build.
        gh api --method PATCH "repos/$repo/git/refs/tags/$tag" -f sha="$GITHUB_SHA" -F force=true >/dev/null
    fi
else
    gh api --method POST "repos/$repo/git/refs" -f ref="refs/tags/$tag" -f sha="$GITHUB_SHA" >/dev/null
fi
if [[ "$(cat "$work/status")" == published ]]; then
    echo 'This commit is already published; the fixed release is unchanged.'
    exit 0
elif [[ "$(cat "$work/status")" == none ]]; then
    gh release create "$tag" --repo "$repo" --verify-tag --draft \
        --title '3X-UI 3.10.2 排序增强版（IPv6 + HY2）' --notes-file deploy/preset/release-notes.md
fi
gh release upload "$tag" "$assets/"* --repo "$repo" --clobber
refresh_release
python3 - "$assets" "$work/release.json" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
local = {p.name: p for p in root.iterdir() if p.is_file()}
remote = {a['name']: a for a in json.load(open(sys.argv[2]))['assets']}
assert set(local) == set(remote) and len(remote) == 21, 'Incomplete release attachments'
for name, p in local.items():
    assert remote[name]['size'] == p.stat().st_size, f'Wrong size: {name}'
    assert remote[name]['digest'] == 'sha256:' + hashlib.file_digest(p.open('rb'), 'sha256').hexdigest(), f'Wrong digest: {name}'
PY
gh release edit "$tag" --repo "$repo" --draft=false --prerelease=false --latest=false
echo "Published fixed $tag from $GITHUB_SHA; all prior tags/releases remain unchanged."
