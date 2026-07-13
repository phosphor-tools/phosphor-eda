#!/usr/bin/env bash
set -euo pipefail

release_tag=${1:?release tag is required}
main_ref=${2:?main ref is required}

git rev-parse --verify "${release_tag}^{commit}" >/dev/null
git rev-parse --verify "${main_ref}^{commit}" >/dev/null

if ! git merge-base --is-ancestor "${release_tag}^{commit}" "${main_ref}^{commit}"; then
    printf '::error::Release tag %s is not on %s\n' "$release_tag" "$main_ref" >&2
    exit 1
fi
