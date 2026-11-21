"""Background worker: runs queued jobs with Django loaded."""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "premeasure_web.settings")
django.setup()

from django.conf import settings  # noqa: E402
from redis import Redis  # noqa: E402
from rq import Queue, Worker  # noqa: E402

if __name__ == "__main__":
    connection = Redis.from_url(settings.REDIS_URL)
    Worker([Queue(settings.JOB_QUEUE, connection=connection)], connection=connection).work()
