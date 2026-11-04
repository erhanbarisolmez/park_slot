"""Exponential backoff ile yeniden deneme mekanizması."""

import logging
import time
from functools import wraps
from typing import Callable, Type

logger = logging.getLogger(__name__)


def retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exceptions: tuple[Type[Exception], ...] = (Exception,),
) -> Callable:
    """Exponential backoff decorator.

    Kullanım:
        @retry(max_retries=5, base_delay=2.0)
        def connect():
            ...

    Her denemede bekleme süresi 2 katına çıkar:
    1. deneme: 1s, 2. deneme: 2s, 3. deneme: 4s ...
    max_delay'i aşmaz.
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt == max_retries:
                        logger.error(
                            "Tüm denemeler başarısız: %s, deneme: %d/%d",
                            func.__name__,
                            attempt + 1,
                            max_retries + 1,
                        )
                        raise

                    delay = min(base_delay * (2 ** attempt), max_delay)
                    logger.warning(
                        "%s başarısız (deneme %d/%d), %0.1fs sonra tekrar: %s",
                        func.__name__,
                        attempt + 1,
                        max_retries + 1,
                        delay,
                        str(e),
                    )
                    time.sleep(delay)

            raise last_exception  # type: ignore

        return wrapper

    return decorator
