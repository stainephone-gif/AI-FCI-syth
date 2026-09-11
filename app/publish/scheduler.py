"""Планировщик. Cron-задачи из конфига плюс одноразовые публикации."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

log = logging.getLogger(__name__)

Job = Callable[[], Awaitable[None]]

# Насколько поздно ещё можно выполнить пропущенную публикацию (сервер лежал).
PUBLISH_GRACE_SECONDS = 2 * 3600


class Scheduler:
    def __init__(self, tz: str) -> None:
        self._sched = AsyncIOScheduler(timezone=tz)

    def add_cron(self, job_id: str, cron: str, fn: Job) -> None:
        trigger = CronTrigger.from_crontab(cron, timezone=self._sched.timezone)
        self._sched.add_job(fn, trigger, id=job_id, replace_existing=True, misfire_grace_time=600)
        log.info("Задача %s по расписанию '%s'", job_id, cron)

    def add_once(self, job_id: str, when: datetime, fn: Job, *args) -> None:
        self._sched.add_job(
            fn,
            DateTrigger(run_date=when),
            args=args,
            id=job_id,
            replace_existing=True,
            misfire_grace_time=PUBLISH_GRACE_SECONDS,
        )
        log.info("Задача %s на %s", job_id, when.isoformat())

    def remove(self, job_id: str) -> bool:
        try:
            self._sched.remove_job(job_id)
            return True
        except JobLookupError:
            return False

    def has(self, job_id: str) -> bool:
        return self._sched.get_job(job_id) is not None

    def jobs(self):
        return self._sched.get_jobs()

    def start(self) -> None:
        self._sched.start()

    def shutdown(self) -> None:
        if self._sched.running:
            self._sched.shutdown(wait=False)
