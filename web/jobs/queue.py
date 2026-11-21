from django.conf import settings
from redis import Redis
from rq import Queue


def get_queue() -> Queue:
    return Queue(settings.JOB_QUEUE, connection=Redis.from_url(settings.REDIS_URL))
