# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
#
# Secure Clipboard Control for iSyncBrain Research Lab
# Provides security controls for clipboard OUT (workspace -> browser)

import os
import time
import json
import hashlib
import logging
import asyncio
import aiohttp
from collections import deque
from typing import Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("clipboard_security")


@dataclass
class ClipboardLogEntry:
    """Represents a clipboard OUT event for audit logging."""
    timestamp: str
    size_bytes: int
    sha256_hash: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    blocked: bool = False
    block_reason: Optional[str] = None
    mime_type: str = "text/plain"

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "size_bytes": self.size_bytes,
            "sha256_hash": self.sha256_hash,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "mime_type": self.mime_type
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class ClipboardSecurityConfig:
    """Configuration for clipboard security controls, loaded from environment variables."""

    def __init__(self):
        # CLIPBOARD_OUT_ENABLED: true|false - Enable/disable clipboard OUT completely
        self.out_enabled = os.environ.get("CLIPBOARD_OUT_ENABLED", "true").lower() == "true"

        # CLIPBOARD_OUT_MAX_BYTES: Maximum bytes per single operation (default 10KB)
        self.out_max_bytes = int(os.environ.get("CLIPBOARD_OUT_MAX_BYTES", "10240"))

        # CLIPBOARD_OUT_RATE_LIMIT_BYTES: Maximum bytes per time window (default 50KB)
        self.rate_limit_bytes = int(os.environ.get("CLIPBOARD_OUT_RATE_LIMIT_BYTES", "51200"))

        # CLIPBOARD_OUT_RATE_LIMIT_WINDOW_SECONDS: Time window for rate limiting (default 60s)
        self.rate_limit_window_seconds = int(os.environ.get("CLIPBOARD_OUT_RATE_LIMIT_WINDOW_SECONDS", "60"))

        # CLIPBOARD_LOG_FILE: Path to JSON lines log file (optional)
        self.log_file = os.environ.get("CLIPBOARD_LOG_FILE", "")

        # CLIPBOARD_LOG_ENDPOINT: HTTP endpoint for log submission (optional)
        self.log_endpoint = os.environ.get("CLIPBOARD_LOG_ENDPOINT", "")

        # Session identification (can be set externally)
        self.user_id = os.environ.get("SELKIES_USER_ID", os.environ.get("USER", ""))
        self.session_id = os.environ.get("SELKIES_SESSION_ID", "")

    def __repr__(self):
        return (
            f"ClipboardSecurityConfig(out_enabled={self.out_enabled}, "
            f"out_max_bytes={self.out_max_bytes}, "
            f"rate_limit_bytes={self.rate_limit_bytes}, "
            f"rate_limit_window_seconds={self.rate_limit_window_seconds}, "
            f"log_file={self.log_file!r}, "
            f"log_endpoint={self.log_endpoint!r})"
        )


class RateLimiter:
    """Sliding window rate limiter for clipboard OUT operations."""

    def __init__(self, max_bytes: int, window_seconds: int):
        self.max_bytes = max_bytes
        self.window_seconds = window_seconds
        # Store (timestamp, bytes) tuples
        self._history: deque = deque()
        self._lock = asyncio.Lock()

    def _cleanup_old_entries(self, current_time: float):
        """Remove entries outside the current time window."""
        cutoff = current_time - self.window_seconds
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

    def get_current_usage(self) -> int:
        """Get current bytes used in the time window."""
        current_time = time.time()
        self._cleanup_old_entries(current_time)
        return sum(entry[1] for entry in self._history)

    async def check_and_record(self, size_bytes: int) -> Tuple[bool, int, int]:
        """
        Check if the operation is allowed and record it if so.

        Returns:
            Tuple of (allowed, current_usage, remaining_bytes)
        """
        async with self._lock:
            current_time = time.time()
            self._cleanup_old_entries(current_time)

            current_usage = sum(entry[1] for entry in self._history)
            remaining = self.max_bytes - current_usage

            if current_usage + size_bytes > self.max_bytes:
                return False, current_usage, remaining

            # Record this operation
            self._history.append((current_time, size_bytes))
            return True, current_usage + size_bytes, remaining - size_bytes

    def reset(self):
        """Reset the rate limiter history."""
        self._history.clear()


class ClipboardSecurityManager:
    """
    Manages secure clipboard OUT operations with:
    - Enable/disable flag
    - Max bytes per operation limit
    - Rate limiting (bytes/time window)
    - Audit logging (file and/or HTTP endpoint)
    """

    def __init__(self, config: Optional[ClipboardSecurityConfig] = None):
        self.config = config or ClipboardSecurityConfig()
        self.rate_limiter = RateLimiter(
            self.config.rate_limit_bytes,
            self.config.rate_limit_window_seconds
        )
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._log_queue: asyncio.Queue = asyncio.Queue()
        self._log_task: Optional[asyncio.Task] = None

        logger.info(f"ClipboardSecurityManager initialized: {self.config}")

    async def start(self):
        """Start the async log writer task."""
        if self._log_task is None or self._log_task.done():
            self._log_task = asyncio.create_task(self._log_writer_loop())
            logger.info("Clipboard security log writer started")

    async def stop(self):
        """Stop the async log writer and cleanup resources."""
        if self._log_task and not self._log_task.done():
            self._log_task.cancel()
            try:
                await self._log_task
            except asyncio.CancelledError:
                pass

        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None

        logger.info("Clipboard security manager stopped")

    def _compute_hash(self, data: bytes) -> str:
        """Compute SHA256 hash of clipboard content."""
        return hashlib.sha256(data).hexdigest()

    async def check_clipboard_out(self, data: Any, mime_type: str = "text/plain") -> Tuple[bool, Optional[str]]:
        """
        Check if clipboard OUT operation is allowed.

        Args:
            data: Clipboard content (str or bytes)
            mime_type: MIME type of the content

        Returns:
            Tuple of (allowed, block_reason)
            If allowed is True, block_reason is None.
            If allowed is False, block_reason explains why.
        """
        # Convert data to bytes for consistent size calculation
        if isinstance(data, str):
            data_bytes = data.encode("utf-8")
        else:
            data_bytes = bytes(data) if data else b""

        size_bytes = len(data_bytes)
        sha256_hash = self._compute_hash(data_bytes)
        timestamp = datetime.utcnow().isoformat() + "Z"

        # Check 1: Is clipboard OUT enabled?
        if not self.config.out_enabled:
            await self._log_event(ClipboardLogEntry(
                timestamp=timestamp,
                size_bytes=size_bytes,
                sha256_hash=sha256_hash,
                user_id=self.config.user_id,
                session_id=self.config.session_id,
                blocked=True,
                block_reason="clipboard_out_disabled",
                mime_type=mime_type
            ))
            logger.warning(f"Clipboard OUT blocked: feature disabled (size={size_bytes})")
            return False, "clipboard_out_disabled"

        # Check 2: Does it exceed max bytes per operation?
        if size_bytes > self.config.out_max_bytes:
            await self._log_event(ClipboardLogEntry(
                timestamp=timestamp,
                size_bytes=size_bytes,
                sha256_hash=sha256_hash,
                user_id=self.config.user_id,
                session_id=self.config.session_id,
                blocked=True,
                block_reason=f"exceeds_max_bytes:{size_bytes}>{self.config.out_max_bytes}",
                mime_type=mime_type
            ))
            logger.warning(
                f"Clipboard OUT blocked: exceeds max bytes "
                f"({size_bytes} > {self.config.out_max_bytes})"
            )
            return False, f"exceeds_max_bytes:{size_bytes}>{self.config.out_max_bytes}"

        # Check 3: Rate limiting
        allowed, current_usage, remaining = await self.rate_limiter.check_and_record(size_bytes)
        if not allowed:
            await self._log_event(ClipboardLogEntry(
                timestamp=timestamp,
                size_bytes=size_bytes,
                sha256_hash=sha256_hash,
                user_id=self.config.user_id,
                session_id=self.config.session_id,
                blocked=True,
                block_reason=f"rate_limit_exceeded:usage={current_usage},limit={self.config.rate_limit_bytes}",
                mime_type=mime_type
            ))
            logger.warning(
                f"Clipboard OUT blocked: rate limit exceeded "
                f"(current={current_usage}, limit={self.config.rate_limit_bytes}, "
                f"window={self.config.rate_limit_window_seconds}s)"
            )
            return False, f"rate_limit_exceeded:usage={current_usage},limit={self.config.rate_limit_bytes}"

        # All checks passed - log successful operation
        await self._log_event(ClipboardLogEntry(
            timestamp=timestamp,
            size_bytes=size_bytes,
            sha256_hash=sha256_hash,
            user_id=self.config.user_id,
            session_id=self.config.session_id,
            blocked=False,
            block_reason=None,
            mime_type=mime_type
        ))
        logger.info(
            f"Clipboard OUT allowed: size={size_bytes}, "
            f"usage={current_usage}/{self.config.rate_limit_bytes}"
        )
        return True, None

    async def _log_event(self, entry: ClipboardLogEntry):
        """Queue a log entry for async writing."""
        await self._log_queue.put(entry)

    async def _log_writer_loop(self):
        """Async loop that writes log entries to file and/or HTTP endpoint."""
        while True:
            try:
                entry = await self._log_queue.get()

                # Write to file if configured
                if self.config.log_file:
                    await self._write_to_file(entry)

                # Send to HTTP endpoint if configured
                if self.config.log_endpoint:
                    await self._send_to_endpoint(entry)

                self._log_queue.task_done()

            except asyncio.CancelledError:
                # Process remaining items before exiting
                while not self._log_queue.empty():
                    try:
                        entry = self._log_queue.get_nowait()
                        if self.config.log_file:
                            await self._write_to_file(entry)
                        if self.config.log_endpoint:
                            await self._send_to_endpoint(entry)
                    except asyncio.QueueEmpty:
                        break
                raise

            except Exception as e:
                logger.error(f"Error in log writer loop: {e}", exc_info=True)
                await asyncio.sleep(1)  # Prevent tight loop on persistent errors

    async def _write_to_file(self, entry: ClipboardLogEntry):
        """Write log entry to JSON lines file."""
        try:
            # Ensure directory exists
            log_dir = os.path.dirname(self.config.log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)

            # Append to file (JSON lines format)
            with open(self.config.log_file, "a") as f:
                f.write(entry.to_json() + "\n")

        except Exception as e:
            logger.error(f"Failed to write clipboard log to file: {e}")

    async def _send_to_endpoint(self, entry: ClipboardLogEntry):
        """Send log entry to HTTP endpoint."""
        try:
            if self._http_session is None or self._http_session.closed:
                self._http_session = aiohttp.ClientSession()

            async with self._http_session.post(
                self.config.log_endpoint,
                json=entry.to_dict(),
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status >= 400:
                    logger.warning(
                        f"Clipboard log endpoint returned {response.status}: "
                        f"{await response.text()}"
                    )

        except asyncio.TimeoutError:
            logger.warning(f"Timeout sending clipboard log to endpoint")
        except Exception as e:
            logger.error(f"Failed to send clipboard log to endpoint: {e}")

    def set_session_info(self, user_id: Optional[str] = None, session_id: Optional[str] = None):
        """Update session identification information."""
        if user_id is not None:
            self.config.user_id = user_id
        if session_id is not None:
            self.config.session_id = session_id
        logger.info(f"Session info updated: user_id={self.config.user_id}, session_id={self.config.session_id}")

    def get_status(self) -> dict:
        """Get current status of clipboard security controls."""
        return {
            "out_enabled": self.config.out_enabled,
            "out_max_bytes": self.config.out_max_bytes,
            "rate_limit_bytes": self.config.rate_limit_bytes,
            "rate_limit_window_seconds": self.config.rate_limit_window_seconds,
            "current_rate_usage": self.rate_limiter.get_current_usage(),
            "log_file": self.config.log_file,
            "log_endpoint": self.config.log_endpoint,
            "user_id": self.config.user_id,
            "session_id": self.config.session_id
        }


# Global instance (can be initialized on module load or lazily)
_security_manager: Optional[ClipboardSecurityManager] = None


def get_clipboard_security_manager() -> ClipboardSecurityManager:
    """Get or create the global ClipboardSecurityManager instance."""
    global _security_manager
    if _security_manager is None:
        _security_manager = ClipboardSecurityManager()
    return _security_manager


def init_clipboard_security(config: Optional[ClipboardSecurityConfig] = None) -> ClipboardSecurityManager:
    """Initialize the global ClipboardSecurityManager with optional custom config."""
    global _security_manager
    _security_manager = ClipboardSecurityManager(config)
    return _security_manager
