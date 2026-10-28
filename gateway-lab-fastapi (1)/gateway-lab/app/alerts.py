"""Bounded background email queue. No request waits for the SMTP server."""
import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
import logging
import os
import smtplib
import ssl
import time

logger = logging.getLogger('uvicorn.error')


@dataclass
class Settings:
    enabled: bool = False
    host: str = ''
    port: int = 587
    security: str = 'starttls'
    username: str = ''
    password: str = ''
    sender: str = ''
    recipient: str = ''
    cooldown: int = 300

    @classmethod
    def from_env(cls):
        enabled = os.getenv('ALERT_EMAIL_ENABLED', 'false').lower() == 'true'
        if not enabled:
            return cls()
        cfg = cls(enabled=True, host=os.getenv('SMTP_HOST', ''), port=int(os.getenv('SMTP_PORT', '587')), security=os.getenv('SMTP_SECURITY', 'starttls'), username=os.getenv('SMTP_USERNAME', ''), password=os.getenv('SMTP_PASSWORD', ''), sender=os.getenv('SMTP_FROM', ''), recipient=os.getenv('ALERT_EMAIL_TO', ''), cooldown=int(os.getenv('ALERT_COOLDOWN_SECONDS', '300')))
        if not all((cfg.host, cfg.username, cfg.password, cfg.sender, cfg.recipient)):
            raise ValueError('Email enabled but SMTP configuration is incomplete; check .env')
        if cfg.security not in ('starttls', 'ssl') or not 1 <= cfg.port <= 65535 or cfg.cooldown < 30:
            raise ValueError('Use SMTP_SECURITY=starttls/ssl, valid SMTP_PORT, cooldown >= 30')
        for address in (cfg.sender, cfg.recipient):
            if any(c in address for c in '\r\n,; ') or address.count('@') != 1:
                raise ValueError('Use one plain email address for SMTP_FROM and ALERT_EMAIL_TO')
        return cfg


def send_email(cfg, subject, body):
    message = EmailMessage()
    message['From'], message['To'], message['Subject'] = cfg.sender, cfg.recipient, subject
    message['Date'] = format_datetime(datetime.now(timezone.utc))
    message['Message-ID'] = make_msgid()
    message.set_content(body)
    context = ssl.create_default_context()
    if cfg.security == 'ssl':
        connection = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=10, context=context)
    else:
        connection = smtplib.SMTP(cfg.host, cfg.port, timeout=10)
    with connection as smtp:
        if cfg.security == 'starttls':
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(cfg.username, cfg.password)
        smtp.send_message(message)


class EmailAlerts:
    def __init__(self, settings, sender=send_email, clock=time.monotonic):
        self.settings, self.sender, self.clock = settings, sender, clock
        self.queue = asyncio.Queue(maxsize=50)
        self.last_queued = {}
        self.pending = set()
        self.task = None
        self.sent = self.failed = self.suppressed = self.dropped = 0
        self.last_error = None

    def start(self):
        if self.settings.enabled:
            self.task = asyncio.create_task(self._worker())
        logger.info('Email alerts: %s', 'enabled' if self.settings.enabled else 'disabled (configure .env)')

    def submit(self, key, subject, body):
        if not self.settings.enabled:
            return 'disabled'
        now = self.clock()
        if key in self.pending or now - self.last_queued.get(key, -float('inf')) < self.settings.cooldown:
            self.suppressed += 1
            return 'suppressed'
        try:
            self.queue.put_nowait((key, subject, body))
        except asyncio.QueueFull:
            self.dropped += 1
            logger.warning('Email alert queue is full')
            return 'full'
        self.last_queued[key] = now
        self.pending.add(key)
        return 'queued'

    def status(self):
        return dict(enabled=self.settings.enabled, queued=self.queue.qsize(), pending=len(self.pending), smtp_accepted=self.sent, failed=self.failed, suppressed=self.suppressed, dropped=self.dropped, last_error=self.last_error, cooldown_seconds=self.settings.cooldown)

    async def _worker(self):
        while True:
            key, subject, body = await self.queue.get()
            try:
                # Never log exception text: SMTP replies can contain account details.
                for attempt in range(3):
                    try:
                        await asyncio.to_thread(self.sender, self.settings, subject, body)
                        self.sent += 1
                        self.last_error = None
                        logger.info('Email alert accepted by SMTP server')
                        break
                    except Exception as exc:
                        self.last_error = type(exc).__name__
                        if attempt == 2:
                            self.failed += 1
                            logger.warning('Email failed after 3 attempts (%s); check SMTP configuration', self.last_error)
                        else:
                            await asyncio.sleep(2 ** attempt)
            finally:
                self.pending.discard(key)
                self.queue.task_done()

    async def close(self):
        if not self.task:
            return
        try:
            await asyncio.wait_for(self.queue.join(), timeout=15)
        except asyncio.TimeoutError:
            logger.warning('Stopping with unfinished email alerts; queue is not persistent')
        self.task.cancel()
        with suppress(asyncio.CancelledError):
            await self.task
