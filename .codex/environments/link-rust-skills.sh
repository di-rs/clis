#!/bin/sh
set -eu

# The common Git directory belongs to the primary checkout, even in a worktree.
common_dir=$(git rev-parse --path-format=absolute --git-common-dir)
root=$(dirname "$common_dir")
checkout=$(git rev-parse --show-toplevel)
if [ "$root" = "$checkout" ]; then
    exit 0
fi

skills_dir="$checkout/.agents/skills"
mkdir -p "$skills_dir"

# Only share installed Symposium skills; repository-owned skills stay local.
for marker in "$root"/.agents/skills/*/.symposium; do
    [ -f "$marker" ] || continue
    source_dir=${marker%/.symposium}
    [ -f "$source_dir/SKILL.md" ] || continue
    name=${source_dir##*/}
    destination="$skills_dir/$name"
    if [ -L "$destination" ] && [ "$(readlink "$destination")" = "$source_dir" ]; then
        continue
    fi
    if [ -e "$destination" ] || [ -L "$destination" ]; then
        printf 'Keeping existing skill: %s\n' "$destination" >&2
        continue
    fi
    # Symlinks cannot use the .gitignore inside the directory they point to.
    if [ ! -e "$skills_dir/.gitignore" ]; then
        printf '/.gitignore\n' > "$skills_dir/.gitignore"
    fi
    printf '/%s\n' "$name" >> "$skills_dir/.gitignore"
    ln -s "$source_dir" "$destination"
    printf 'Linked Rust skill: %s\n' "$name"
done
