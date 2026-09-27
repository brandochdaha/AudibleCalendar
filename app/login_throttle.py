"""Bounded, thread-safe login throttling for the single application worker."""

from collections import deque
from math import ceil
from threading import Lock
from time import monotonic


class LoginThrottle:
    def __init__(self, *, window=60, per_account=5, per_client=30, max_clients=4096, clock=monotonic):
        self.window = window
        self.per_account = per_account
        self.per_client = per_client
        self.max_clients = max_clients
        self.clock = clock
        self._attempts = {}
        self._lock = Lock()

    def reserve(self, client: str, username: str) -> int:
        """Count an attempt before password hashing; return seconds to retry."""
        now = self.clock()
        account = username.strip().casefold()
        with self._lock:
            if client not in self._attempts:
                self._attempts = {key: attempts for key, attempts in self._attempts.items()
                                  if attempts[-1][0] > now - self.window}
                if len(self._attempts) >= self.max_clients:
                    return max(1, ceil(min(items[-1][0] for items in self._attempts.values()) + self.window - now))
            attempts = self._attempts.setdefault(client, deque())
            while attempts and attempts[0][0] <= now - self.window:
                attempts.popleft()
            account_times = [stamp for stamp, name in attempts if name == account]
            retry_at = []
            if len(attempts) >= self.per_client:
                retry_at.append(attempts[0][0] + self.window)
            if len(account_times) >= self.per_account:
                retry_at.append(account_times[0] + self.window)
            if retry_at:
                return max(1, ceil(max(retry_at) - now))
            attempts.append((now, account))
            return 0
