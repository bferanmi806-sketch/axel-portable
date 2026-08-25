#!/usr/bin/env sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
profile_root=${1:-"$HOME/Axel-Portable-Profile"}

if [ -e "$profile_root" ] && [ -n "$(find "$profile_root" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
  printf '%s\n' "Profile already exists and is not empty: $profile_root" >&2
  exit 1
fi

mkdir -p "$profile_root"
mkdir -p "$profile_root/bin" "$profile_root/memory" "$profile_root/opencode" "$profile_root/tools" "$profile_root/runtimes" "$profile_root/uv/tools" "$profile_root/uv/cache"
cp "$repo_root/AGENTS.md" "$profile_root/AGENTS.md"
cp -a "$repo_root/identity" "$profile_root/identity"
cp -a "$repo_root/memory/basic-memory" "$profile_root/memory/basic-memory"
cp -a "$repo_root/memory/axel-project" "$profile_root/memory/axel-project"
cp -a "$repo_root/opencode/skills" "$profile_root/opencode/skills"
cp -a "$repo_root/opencode/commands" "$profile_root/opencode/commands"
cp -a "$repo_root/opencode/plans" "$profile_root/opencode/plans"
cp -a "$repo_root/tools/mcp" "$profile_root/tools/mcp"
cp -a "$repo_root/runtimes" "$profile_root/runtimes"

if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' "uv is required to install the pinned Basic Memory runtime." >&2
  exit 1
fi

openssl_prefix=
if command -v brew >/dev/null 2>&1; then
  openssl_prefix=$(brew --prefix openssl@3 2>/dev/null || true)
fi

if [ -n "$openssl_prefix" ] && command -v pkg-config >/dev/null 2>&1; then
  OPENSSL_DIR="$openssl_prefix" \
    PKG_CONFIG_PATH="$openssl_prefix/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}" \
    UV_TOOL_DIR="$profile_root/uv/tools" \
    UV_TOOL_BIN_DIR="$profile_root/bin" \
    UV_CACHE_DIR="$profile_root/uv/cache" \
    uv tool install --force basic-memory==0.23.0
else
  UV_TOOL_DIR="$profile_root/uv/tools" \
    UV_TOOL_BIN_DIR="$profile_root/bin" \
    UV_CACHE_DIR="$profile_root/uv/cache" \
    uv tool install --force basic-memory==0.23.0
fi

sed -e "s|__AXEL_PORTABLE_ROOT__|$(printf '%s' "$repo_root" | sed 's/[&|]/\\&/g')|g" \
  -e "s|__AXEL_PROFILE_ROOT__|$(printf '%s' "$profile_root" | sed 's/[&|]/\\&/g')|g" \
  "$repo_root/opencode/config/opencode.json.template" > "$profile_root/opencode/opencode.json"
sed "s|__AXEL_PORTABLE_ROOT__|$(printf '%s' "$repo_root" | sed 's/[&|]/\\&/g')|g" \
  "$repo_root/memory/config.template.json" > "$profile_root/memory/config.json"

cat > "$profile_root/RESTORE-NOTES.md" <<EOF
# Axel Portable Profile

Generated from: $repo_root

This profile contains identity, human-readable memory, skills and sanitized
runtime templates. It contains no credentials and does not modify global
runtime configuration.
EOF

printf '%s\n' "Profile created: $profile_root"
printf '%s\n' "Build CodeMem and register Basic Memory projects before launching a runtime."
