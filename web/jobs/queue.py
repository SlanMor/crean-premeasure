from importlib import import_module

from django.conf import settings
from redis import Redis
from rq import Queue

JOB_TIMEOUT = 30 * 60  # a large A0 drawing takes a few minutes; rq's default is 3


def get_queue() -> Queue:
    return Queue(settings.JOB_QUEUE, connection=Redis.from_url(settings.REDIS_URL))


def enqueue(func: str, *args) -> None:
    """Run a task in the worker, or straight away when JOBS_INLINE is set (tests, local use)."""
    if settings.JOBS_INLINE:
        module, name = func.rsplit(".", 1)
        getattr(import_module(module), name)(*args)
        return
    get_queue().enqueue(func, *args, job_timeout=JOB_TIMEOUT)
