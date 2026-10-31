"""Safe platform error codes with server-directed retry timing."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


class SourceHttpError(RuntimeError):
    def __init__(self, code, retry_after=None):
        super().__init__(f"HTTP_{code}")
        self.retry_after = 0.0
        if retry_after:
            try:
                self.retry_after = max(0.0, float(retry_after))
            except ValueError:
                try:
                    self.retry_after = max(0.0, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError):
                    pass
