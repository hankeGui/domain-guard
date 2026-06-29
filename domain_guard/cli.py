"""guard-cli: command-line entry point."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("Usage: guard-cli <command> [options]")
        print("")
        print("Commands:")
        print("  calibrate    Sweep embedding thresholds against labeled samples")
        print("  check        Run a single message through a guard config")
        print("  replay       Re-evaluate historical traffic against a config")
        return 0

    cmd, rest = argv[0], argv[1:]
    if cmd == "calibrate":
        from .calibrate import main as cmd_main
        return cmd_main(rest)
    if cmd == "check":
        return _cmd_check(rest)
    if cmd == "replay":
        from .replay import main as cmd_main
        return cmd_main(rest)

    print(f"Unknown command: {cmd}", file=sys.stderr)
    return 2


def _cmd_check(argv: list[str]) -> int:
    import argparse
    from .core import DomainGuard
    from .context import GuardContext

    ap = argparse.ArgumentParser(prog="guard-cli check")
    ap.add_argument("--config", required=True)
    ap.add_argument("message")
    args = ap.parse_args(argv)

    guard = DomainGuard.from_yaml(args.config)
    result = guard.check(args.message, GuardContext())
    verdict = "PASS" if result.passed else "BLOCK"
    print(f"[{verdict}] layer={result.matched_layer} "
          f"conf={result.confidence:.2f} reason={result.reason}")
    if not result.passed and result.fallback_reply:
        print()
        print(result.fallback_reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
