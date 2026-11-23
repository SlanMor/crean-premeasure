"""A logging handler, loaded before the apps: imports Django models only when used."""

import logging


class ErrorHandler(logging.Handler):
    """A page failed with a program error (a 500): alert, at most every 12 hours per page."""

    def emit(self, record):
        try:
            from .alerts import raise_alert

            request = getattr(record, "request", None)
            path = getattr(request, "path", "") if request is not None else ""
            text = self.format(record)
            raise_alert(f"page-{path}"[:200], f"A page failed: {path or 'unknown page'}", text[-3000:])
        except Exception:
            pass
