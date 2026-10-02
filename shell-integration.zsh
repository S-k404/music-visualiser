# Source this file from your ~/.zshrc to get the `musicvis` /
# `musicvis-esp32` commands from any directory:
#
#   source "/path/to/music-visualiser/shell-integration.zsh"
#
# Resolves its own location at source-time so it works regardless of
# where the repo is cloned -- no hardcoded path. Uses functions rather
# than aliases: aliases re-split their expansion text on whitespace, which
# breaks on a path containing a space (as this repo's does by default).
export MUSICVIS_DIR="${0:A:h}"

musicvis() {
  "$MUSICVIS_DIR/macos/run.sh" "$@"
}

musicvis-esp32() {
  "$MUSICVIS_DIR/esp32/build.sh" "$@"
}
