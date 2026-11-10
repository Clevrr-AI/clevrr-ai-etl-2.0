"""Print a one-off Kubernetes Job that runs one `etl` command, from the template
cronjobs_dev/etl-manual.yaml (sandbox) or, with --prod, cronjobs/etl-manual-prod.yaml (same
ConfigMap, Secret, service account, node pool and v2 settings as the prod tick CronJobs).
Pipe it to kubectl:

    uv run python scripts/manual_job.py "schedule set --org a.com --source shopify --every 4h" \\
      | kubectl apply --context=<sandbox context> -n etl -f -
    uv run python scripts/manual_job.py "schedule list" --prod | kubectl apply -f - <prod context>

The image defaults to the one in the template; pass --image for another tag.
The Job name is etl-manual-<utc time>-<4 hex>, so repeated runs never collide.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "cronjobs_dev" / "etl-manual.yaml"
PROD_TEMPLATE = ROOT / "cronjobs" / "etl-manual-prod.yaml"


def render(command: str, image: str | None, suffix: str, *, prod: bool = False) -> str:
    text = (PROD_TEMPLATE if prod else TEMPLATE).read_text(encoding="utf-8")
    if '"' in command:
        raise SystemExit("The command must not contain double quotes.")
    text = text.replace("__COMMAND__", command).replace("__SUFFIX__", suffix)
    if image:
        lines = [
            f"{line.split('image:')[0]}image: {image}"
            if line.strip().startswith("image:")
            else line
            for line in text.split("\n")
        ]
        text = "\n".join(lines)
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("command", help='the etl command, e.g. "tick shopify" or pg-migrate')
    parser.add_argument("--image", help="the v2 image to run (default: the template's)")
    parser.add_argument("--prod", action="store_true", help="the prod template")
    args = parser.parse_args(argv)
    suffix = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
    sys.stdout.write(render(args.command, args.image, suffix, prod=args.prod))
    return 0


if __name__ == "__main__":
    sys.exit(main())
