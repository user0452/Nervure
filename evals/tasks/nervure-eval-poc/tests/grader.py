"""Hidden verifier for the calculator PoC; this file is never in the agent image."""

from __future__ import annotations

import sys

sys.path.insert(0, "/workspace")

from calculator.statistics import mean, median  # noqa: E402


def main() -> int:
    checks = (
        mean([2, 4]) == 3,
        mean([1, 2, 3, 4]) == 2.5,
        median([9, 1, 5]) == 5,
        median([1, 4, 2, 3]) == 2.5,
    )
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
