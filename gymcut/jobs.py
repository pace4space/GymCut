"""Background job queues. 'gpu' runs pose analysis one at a time; 'cpu' runs exports."""
from __future__ import annotations

import queue
import secrets
import threading
import time
import traceback
from typing import Callable


class Job:
    def __init__(self, kind: str, title: str, video_id: str | None, fn: Callable, args: tuple, kwargs: dict):
        self.id = secrets.token_hex(4)
        self.kind, self.title, self.video_id = kind, title, video_id
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.state = "queued"
        self.stage = ""
        self.progress = 0.0
        self.message = ""
        self.result = None
        self.error = None
        self.created = time.time()
        self.started = None
        self.finished = None
        self.cancelled = False

    def update(self, stage: str | None = None, progress: float | None = None, message: str | None = None):
        if self.cancelled:
            raise JobCancelled()
        if stage is not None:
            self.stage = stage
        if progress is not None:
            self.progress = float(max(0.0, min(1.0, progress)))
        if message is not None:
            self.message = message

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "video_id": self.video_id,
                "state": self.state, "stage": self.stage, "progress": round(self.progress, 4),
                "message": self.message, "result": self.result, "error": self.error,
                "created": self.created, "started": self.started, "finished": self.finished}


class JobCancelled(Exception):
    pass


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._queues = {"gpu": queue.Queue(), "cpu": queue.Queue(), "ue": queue.Queue()}
        self._lock = threading.Lock()
        for name, q in self._queues.items():
            threading.Thread(target=self._worker, args=(q,), name=f"worker-{name}", daemon=True).start()

    def submit(self, queue_name: str, kind: str, title: str, video_id: str | None,
               fn: Callable, *args, **kwargs) -> Job:
        job = Job(kind, title, video_id, fn, args, kwargs)
        with self._lock:
            self._jobs[job.id] = job
            # keep memory bounded
            if len(self._jobs) > 300:
                for jid in sorted(self._jobs, key=lambda k: self._jobs[k].created)[:100]:
                    if self._jobs[jid].state in ("done", "error", "cancelled"):
                        self._jobs.pop(jid, None)
        self._queues[queue_name].put(job)
        return job

    def _worker(self, q: queue.Queue):
        while True:
            job: Job = q.get()
            if job.cancelled:
                job.state = "cancelled"
                continue
            job.state, job.started = "running", time.time()
            try:
                job.result = job.fn(job, *job.args, **job.kwargs)
                job.state, job.progress = "done", 1.0
            except JobCancelled:
                job.state = "cancelled"
            except Exception as e:  # noqa: BLE001
                job.state = "error"
                job.error = f"{type(e).__name__}: {e}"
                traceback.print_exc()
            finally:
                job.finished = time.time()

    def get(self, jid: str) -> Job | None:
        return self._jobs.get(jid)

    def cancel(self, jid: str) -> bool:
        j = self._jobs.get(jid)
        if j and j.state in ("queued", "running"):
            j.cancelled = True
            return True
        return False

    def list(self, active_only: bool = False) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: -j.created)
        if active_only:
            jobs = [j for j in jobs if j.state in ("queued", "running")]
        return [j.to_dict() for j in jobs[:60]]

    def active_for(self, video_id: str) -> list[dict]:
        return [j.to_dict() for j in self._jobs.values()
                if j.video_id == video_id and j.state in ("queued", "running")]


manager = JobManager()
