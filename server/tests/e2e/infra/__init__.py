"""E2E test infrastructure — re-exports for convenience."""

from tests.e2e.infra.email_capture import CapturedEmail, EmailCapture
from tests.e2e.infra.scheduler_simulator import SchedulerSimulator
from tests.e2e.infra.task_drain import DrainFn, DrainResult, TaskDrain, TaskDrainError

__all__ = [
    "CapturedEmail",
    "DrainFn",
    "DrainResult",
    "EmailCapture",
    "SchedulerSimulator",
    "TaskDrain",
    "TaskDrainError",
]
