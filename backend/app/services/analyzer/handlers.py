from __future__ import annotations

from ...core.queue_manager import QueueManager
from .pipeline import run_analysis_pipeline


def register_analyzer_handlers(queue: QueueManager) -> None:
    queue.register_handler("analyze", run_analysis_pipeline)
