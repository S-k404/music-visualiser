from __future__ import annotations

import argparse
import os
import sys
import traceback

from . import ansi
from .applog import setup_logging
from .config import get_default_library, set_default_library
from .decode import DecodeError, check_ffmpeg_available
from .player import Player, build_playlist


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ascii_visualizer",
        description="Terminal HD ASCII/pixel music visualiser with full media tags.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Path to an audio file or a folder of audio files. If omitted, "
        "uses the saved default library (see --set-library).",
    )
    parser.add_argument(
        "--set-library",
        metavar="PATH",
        help="Save PATH as the default library and exit -- future runs with "
        "no path argument will use it.",
    )
    args = parser.parse_args()

    if args.set_library is not None:
        if not os.path.exists(args.set_library):
            print(f"error: no such file or directory: {args.set_library}", file=sys.stderr)
            sys.exit(1)
        set_default_library(os.path.abspath(args.set_library))
        print(f"Default library set to: {os.path.abspath(args.set_library)}")
        return

    path = args.path or get_default_library()
    if path is None:
        print(
            "error: no path given and no default library configured.\n"
            "  Either pass a path:     musicvis /path/to/music\n"
            "  Or set a default once:  musicvis --set-library /path/to/music",
            file=sys.stderr,
        )
        sys.exit(1)

    logger, log_path = setup_logging()
    logger.info("starting: path=%r", path)

    try:
        check_ffmpeg_available()
        playlist = build_playlist(path)
    except (DecodeError, FileNotFoundError) as exc:
        logger.error("startup failed: %s", exc)
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    logger.info("playlist built: %d track(s)", len(playlist))
    player = Player(playlist, logger=logger)
    try:
        player.run()
        logger.info("exited normally")
    except DecodeError as exc:
        sys.stdout.write(ansi.SHOW_CURSOR + ansi.ALT_SCREEN_OFF)
        sys.stdout.flush()
        logger.error("fatal: %s", exc)
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        # Belt-and-braces: player.run() already restores the terminal on
        # its way out, but make sure *any* unexpected failure surfaces a
        # visible traceback instead of silently dropping back to the shell
        # prompt (alt-screen mode leaves no trace of what happened).
        sys.stdout.write(ansi.SHOW_CURSOR + ansi.ALT_SCREEN_OFF)
        sys.stdout.flush()
        logger.exception("unhandled exception")
        traceback.print_exc()
        print(f"(full log: {log_path})", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
