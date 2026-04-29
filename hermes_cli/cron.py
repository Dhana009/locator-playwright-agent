"""Cron subcommand stub — the scheduler package was removed in the minimal Hermes build."""

import sys


def cron_command(args):
    print(
        "Cron scheduling is not available in this minimal Hermes checkout "
        "(the `cron/` package and `tools/cronjob_tools` were removed).",
        file=sys.stderr,
    )
    return 1
