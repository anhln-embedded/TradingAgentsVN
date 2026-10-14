"""One core run per process; bounded SSE buffering after disconnects."""
import queue
import threading

RUN_LOCK = threading.Lock()


class EventBuffer(queue.Queue):
    def __init__(self):
        super().__init__(maxsize=256)
        self.detached = threading.Event()

    def put(self, item, block=True, timeout=None):
        if self.detached.is_set():
            return
        # Never make report saving depend on a slow browser consuming events.
        while True:
            try:
                return super().put(item, block=False)
            except queue.Full:
                try:
                    self.get_nowait()
                except queue.Empty:
                    pass
