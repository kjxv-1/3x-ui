#!/usr/bin/env bash
# Maintain the user's single preset release after every build/install check passes.
# Only this preset tag can move; original tutorial versions are never touched.
set -euo pipefail
readonly tag="v3.10.2-preset"
readonly repo="kjxv-1/3x-ui"
readonly branch="codex/v3.10.2-preset"
[[ "${GITHUB_REPOSITORY:?}" == "$repo" ]]
[[ "${GITHUB_SHA:?}" =~ ^[0-9a-f]{40}$ ]]
[[ "${PRESET_RELEASE_TAG:?}" == "$tag" ]]
[[ "v$(cat internal/config/version)" == "$tag" ]]
[[ "$(git rev-parse HEAD)" == "$GITHUB_SHA" ]]
[[ "$(gh api "repos/$repo/git/ref/heads/$branch" --jq '.object.sha')" == "$GITHUB_SHA" ]]
assets="$(realpath "${1:?package directory required}")"
python3 - "$assets" <<'PY'
import pathlib,sys
root=pathlib.Path(sys.argv[1])
expected={f'x-ui-linux-{arch}.tar.gz' for arch in ('amd64','arm64','armv7','armv6','armv5','386','s390x')} | {'x-ui-windows-amd64.zip'}
assert {p.name for p in root.glob('x-ui-*') if p.name.endswith(('.tar.gz','.zip'))} == expected, 'Exactly eight platform packages are required'
assert all((root/name).is_file() and (root/name).stat().st_size for name in expected), 'Empty package'
PY
(cd "$assets"; sha256sum *.tar.gz *.zip > checksums.txt; for file in *.tar.gz *.zip; do sha256sum "$file" > "$file.sha256"; done)
# A new upstream baseline gets its own stable preset tag. Old tutorial releases stay intact.
if ! gh api "repos/$repo/releases/tags/$tag" > /dev/null 2>&1; then
    if ! gh api "repos/$repo/git/ref/tags/$tag" > /dev/null 2>&1; then
        gh api --method POST "repos/$repo/git/refs" -f ref="refs/tags/$tag" -f sha="$GITHUB_SHA" > /dev/null
    fi
    gh release create "$tag" --repo "$repo" --verify-tag --draft --title "3X-UI $tag 默认节点预设版" --notes-file deploy/preset/release-notes.md
    gh release upload "$tag" "$assets/"* install.sh x-ui.sh deploy/preset/README.md --repo "$repo"
    gh release edit "$tag" --repo "$repo" --draft=false --prerelease=false --latest=false
    echo "Published the first verified $tag release."
    exit 0
fi
backup_parent="$(realpath "${RUNNER_TEMP:-${TMPDIR:-/tmp}}")"
backup="$(mktemp -d "$backup_parent/preset-release.XXXXXX")"
backup="$(realpath "$backup")"
[[ "$backup" == "$backup_parent"/preset-release.* && -d "$backup" ]]
cleanup_backup() {
    if [[ "$(realpath "$backup")" == "$backup_parent"/preset-release.* ]]; then
        rm -rf -- "$backup"
    fi
}
trap cleanup_backup EXIT
gh api "repos/$repo/releases/tags/$tag" > "$backup/release.json"
gh api "repos/$repo/git/ref/tags/$tag" --jq '.object.sha' > "$backup/old-ref"
python3 - "$backup" "$GITHUB_SHA" <<'PY'
import json,pathlib,sys
root=pathlib.Path(sys.argv[1]); old=json.loads((root/'release.json').read_text())
assert old['tag_name']=='v3.10.2-preset' and not old.get('immutable',False), 'Only the mutable preset release may be maintained'
assert not old['draft'], 'The previous published release must be available for backup'
notes=pathlib.Path('deploy/preset/release-notes.md').read_text(encoding='utf-8')
new={'name':'3X-UI v3.10.2 默认节点预设版（Xray 26.6.27）','body':notes+'\n维护提交：`'+sys.argv[2]+'`。\n','prerelease':False,'make_latest':'false'}
for name,draft in [('stage',True),('publish',False)]:
    (root/f'{name}.json').write_text(json.dumps(dict(new,draft=draft)),encoding='utf-8')
restore={k:old[k] for k in ('name','body','draft','prerelease')}; restore['make_latest']='false'
(root/'restore.json').write_text(json.dumps(restore),encoding='utf-8')
(root/'release-id').write_text(str(old['id']))
PY
release_id="$(cat "$backup/release-id")"
mkdir "$backup/assets"
gh release download "$tag" --repo "$repo" --dir "$backup/assets"
# Save all previous files before making the release unavailable during replacement.
python3 - "$backup" <<'PY'
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]); meta=json.loads((root/'release.json').read_text())
assert len(meta['assets'])==20, 'Expected the complete prior release'
for asset in meta['assets']:
    p=root/'assets'/asset['name']
    assert p.is_file() and p.stat().st_size==asset['size'], 'Incomplete rollback backup'
    if asset.get('digest'):
        assert 'sha256:'+hashlib.file_digest(p.open('rb'),'sha256').hexdigest()==asset['digest'], 'Backup checksum mismatch'
PY
changed=0
restore_previous_release() {
    local result=$?
    trap - ERR
    if [[ "$changed" == 1 ]]; then
        set +e
        echo "Preset publication failed; restoring the previous tag and installation packages." >&2
        local rollback_failed=0
        gh release edit "$tag" --repo "$repo" --draft=true || rollback_failed=1
        gh release upload "$tag" "$backup/assets/"* --repo "$repo" --clobber || rollback_failed=1
        gh api --method PATCH "repos/$repo/git/refs/tags/$tag" -f sha="$(cat "$backup/old-ref")" -F force=true > /dev/null || rollback_failed=1
        if [[ "$rollback_failed" == 0 ]]; then
            gh api --method PATCH "repos/$repo/releases/$release_id" --input "$backup/restore.json" > /dev/null || rollback_failed=1
        fi
        if [[ "$rollback_failed" != 0 ]]; then
            echo "Rollback could not finish; the preset release stays unavailable until repaired." >&2
        fi
    fi
    exit "$result"
}
trap restore_previous_release ERR
changed=1
gh api --method PATCH "repos/$repo/releases/$release_id" --input "$backup/stage.json" > /dev/null
gh release upload "$tag" "$assets/"* install.sh x-ui.sh deploy/preset/README.md --repo "$repo" --clobber
gh api --method PATCH "repos/$repo/git/refs/tags/$tag" -f sha="$GITHUB_SHA" -F force=true > /dev/null
gh api --method PATCH "repos/$repo/releases/$release_id" --input "$backup/publish.json" > /dev/null
changed=0
trap - ERR
echo "Updated $tag with verified packages from $GITHUB_SHA. The deployment command is unchanged."
