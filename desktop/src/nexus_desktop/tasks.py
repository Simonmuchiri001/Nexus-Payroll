"""Run blocking REST calls off the Qt event loop."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot


class WorkerSignals(QObject):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(object)
    finished = pyqtSignal()


class Worker[T](QRunnable):
    def __init__(self, operation: Callable[[], T]) -> None:
        super().__init__()
        self.operation = operation
        self.signals = WorkerSignals()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = self.operation()
        except Exception as exc:
            self.signals.failed.emit(exc)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()


class TaskRunner(QObject):
    """Keep Python references alive until each Qt worker finishes."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        pool = QThreadPool.globalInstance()
        if pool is None:
            raise RuntimeError("The Qt thread pool is unavailable.")
        self._pool = pool
        self._active: set[Worker[Any]] = set()

    def submit(
        self,
        operation: Callable[[], Any],
        *,
        on_success: Callable[[Any], None],
        on_failure: Callable[[Exception], None],
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        worker: Worker[Any] = Worker(operation)
        self._active.add(worker)
        worker.signals.succeeded.connect(on_success)
        worker.signals.failed.connect(on_failure)
        if on_finished is not None:
            worker.signals.finished.connect(on_finished)

        def release() -> None:
            self._active.discard(worker)

        worker.signals.finished.connect(release)
        self._pool.start(worker)
