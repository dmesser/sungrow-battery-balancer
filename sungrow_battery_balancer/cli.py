"""Command-line entrypoint for Sungrow Battery Balancer."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence

from .balancer import BatteryBalancer
from .config import load_config

logger = logging.getLogger(__name__)


def setup_logging(level_name: str) -> None:
    """Configure structured logging output."""
    level = getattr(logging, level_name.upper(), logging.INFO)
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=level,
        format=log_format,
        datefmt=date_format,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


def main(args: Sequence[str] | None = None) -> int:
    """Main CLI execution routine."""
    config = load_config(args)
    setup_logging(config.log_level)

    balancer = BatteryBalancer(config)

    try:
        if config.one_shot:
            balancer.run_one_shot()
            return 0
        else:
            balancer.run_loop()
            return 0
    except KeyboardInterrupt:  # pragma: no cover
        logger.info("Interrupted by user. Exiting.")
        return 0
    except Exception:
        logger.exception("Fatal error during execution")
        return 1
    finally:
        balancer.close()


if __name__ == "__main__":
    sys.exit(main())
