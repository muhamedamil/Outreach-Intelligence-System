# app/utils/logger.py

import logging
from app.config.settings import settings


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    level = logging.DEBUG if settings.DEBUG else logging.INFO

    logger.setLevel(level)

    handler = logging.StreamHandler()

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
    )

    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger