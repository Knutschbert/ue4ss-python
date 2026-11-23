#!/usr/bin/env python3
"""Provision or validate a private bearer-token file for the live TCP listener."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "lib"))
from ue4ss.live.security import TokenSecurityError, provision_token, validate_token_file  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create a fresh owner-only token without overwriting")
    create.add_argument("path", type=Path)
    validate = commands.add_parser("validate", help="check token format and file permissions")
    validate.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            provision_token(args.path)
            print(f"private live token created at {args.path}; token contents were not displayed")
        else:
            validate_token_file(args.path)
            print(f"private live token file is valid: {args.path}")
        return 0
    except (OSError, TokenSecurityError) as error:
        print(f"live token: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
