"""Планировщик. Cron-задачи из конфига плюс одноразовые публикации (шаг 13)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)

Job = Callable[[], Awaitable[None]]


class Scheduler:
    def __init__(self, tz: str) -> None:
        self._sched = AsyncIOScheduler(timezone=tz)

    def add_cron(self, job_id: str, cron: str, fn: Job) -> None:
        trigger = CronTrigger.from_crontab(cron, timezone=self._sched.timezone)
        self._sched.add_job(fn, trigger, id=job_id, replace_existing=True, misfire_grace_time=600)
        log.info("Задача %s по расписанию '%s'", job_id, cron)

    def jobs(self):
        return self._sched.get_jobs()

    def start(self) -> None:
        self._sched.start()

    def shutdown(self) -> None:
        if self._sched.running:
            self._sched.shutdown(wait=False)


async def job_collect() -> None:
    log.info("collect: сбор источников ещё не реализован (шаг 11)")


async def job_digest() -> None:
    log.info("digest: утренний дайджест ещё не реализован (шаг 12)")
