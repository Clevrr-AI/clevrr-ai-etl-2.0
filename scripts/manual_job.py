"""Print a one-off Kubernetes Job that runs one `etl` command on the sandbox, from the template
cronjobs_dev/etl-manual.yaml (same ConfigMap, Secret, service account and node pool as the
CronJobs). Pipe it to kubectl:

    uv run python scripts/manual_job.py "schedule set --org a.com --source shopify --every 4h" \\
      | kubectl apply --context=<sandbox context> -n etl -f -
    uv run python scripts/manual_job.py pg-migrate --image <image:tag> | kubectl apply ... -f -

The image defaults to the one in the template; pass --image for the tag the CronJobs run.
The Job name is etl-manual-<utc time>, so repeated runs never collide.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "cronjobs_dev" / "etl-manual.yaml"


def render(command: str, image: str | None, suffix: str) -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
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
    args = parser.parse_args(argv)
    suffix = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    sys.stdout.write(render(args.command, args.image, suffix))
    return 0


if __name__ == "__main__":
    sys.exit(main())
