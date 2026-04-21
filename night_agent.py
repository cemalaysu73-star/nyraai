from __future__ import annotations

"""
NightAgent — autonomous background task executor.

User says: "Nyra, while I sleep research X and have it ready by morning."
Nyra queues the task, runs it in a background thread using AgentCore,
saves the result, and sends a Windows notification when done.

On next startup, Nyra includes completed overnight tasks in her greeting.
"""

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from config import DATA_DIR

TASKS_FILE  = DATA_DIR / "night_tasks.json"
RESULTS_DIR = DATA_DIR / "results"


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class Task:
    id: str
    description: str
    status: str          # pending | running | done | failed
    created_at: str
    completed_at: str = ""
    result: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @staticmethod
    def from_dict(d: dict) -> "Task":
        return Task(**{k: v for k, v in d.items() if k in Task.__dataclass_fields__})


# ── NightAgent ────────────────────────────────────────────────────────────────

class NightAgent:
    """
    Queues and executes long-running tasks in a background thread.
    Results are persisted to disk and delivered via Windows notification.
    """

    def __init__(self) -> None:
        self._tasks: list[Task] = []
        self._lock = threading.Lock()
        self._agent = None          # set via set_agent()
        self._notify_fn: Optional[Callable[[str, str], None]] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.load()

    def set_agent(self, agent) -> None:
        """Inject the AgentCore after construction (avoids circular import)."""
        self._agent = agent

    def set_notify(self, fn: Callable[[str, str], None]) -> None:
        """fn(title, message) — called when a task completes."""
        self._notify_fn = fn

    # ── Public API ────────────────────────────────────────────────────────────

    def schedule(self, description: str) -> Task:
        """Queue a new task. Returns the Task object."""
        task = Task(
            id=str(uuid.uuid4())[:8],
            description=description,
            status="pending",
            created_at=datetime.now().isoformat(),
        )
        with self._lock:
            self._tasks.append(task)
        self.save()
        self._ensure_worker()
        print(f"[NightAgent] Queued: {description[:60]}")
        return task

    def pending_tasks(self) -> list[Task]:
        with self._lock:
            return [t for t in self._tasks if t.status == "pending"]

    def completed_since_last_check(self) -> list[Task]:
        """Return done tasks that haven't been acknowledged yet. Marks them read."""
        with self._lock:
            done = [t for t in self._tasks if t.status == "done"]
        return done

    def clear_done(self) -> None:
        with self._lock:
            self._tasks = [t for t in self._tasks if t.status != "done"]
        self.save()

    def status_summary(self, language: str = "en") -> str:
        with self._lock:
            pending = sum(1 for t in self._tasks if t.status == "pending")
            running = sum(1 for t in self._tasks if t.status == "running")
            done    = sum(1 for t in self._tasks if t.status == "done")
        if language == "tr":
            parts = []
            if running: parts.append(f"{running} görev çalışıyor")
            if pending: parts.append(f"{pending} bekliyor")
            if done:    parts.append(f"{done} tamamlandı")
            return ", ".join(parts) if parts else "Aktif görev yok."
        else:
            parts = []
            if running: parts.append(f"{running} running")
            if pending: parts.append(f"{pending} pending")
            if done:    parts.append(f"{done} completed")
            return ", ".join(parts) if parts else "No active tasks."

    def morning_briefing(self, language: str = "en") -> str:
        """Call on startup to surface overnight results."""
        done = self.completed_since_last_check()
        if not done:
            return ""
        summaries = []
        for t in done:
            preview = t.result[:120].replace("\n", " ") if t.result else t.error[:80]
            summaries.append(f"• {t.description[:50]}: {preview}…")
        if language == "tr":
            header = f"Gece {len(done)} görev tamamlandı, efendim:\n"
        else:
            header = f"Overnight, {len(done)} task{'s' if len(done) > 1 else ''} completed, sir:\n"
        return header + "\n".join(summaries)

    # ── Persistence ───────────────────────────────────────────────────────────

    def load(self) -> None:
        if not TASKS_FILE.exists():
            return
        try:
            raw = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
            self._tasks = [Task.from_dict(d) for d in raw.get("tasks", [])]
            # Reset any "running" tasks that were interrupted
            for t in self._tasks:
                if t.status == "running":
                    t.status = "pending"
        except Exception:
            self._tasks = []

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with self._lock:
            data = {"tasks": [t.to_dict() for t in self._tasks]}
        TASKS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ── Worker thread ─────────────────────────────────────────────────────────

    def _ensure_worker(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self) -> None:
        while self._running:
            task = self._next_pending()
            if task is None:
                time.sleep(5)
                continue
            self._execute(task)

    def _next_pending(self) -> Optional[Task]:
        with self._lock:
            for t in self._tasks:
                if t.status == "pending":
                    t.status = "running"
                    return t
        return None

    def _execute(self, task: Task) -> None:
        print(f"[NightAgent] Running: {task.description[:60]}")
        try:
            if self._agent is None:
                raise RuntimeError("AgentCore not set")

            result = self._agent.respond(
                task.description,
                language="en",
                session_app="",
                long_mem_context="You are running as a background agent. Be thorough and complete. Save results clearly.",
            )

            task.result = result
            task.status = "done"
            task.completed_at = datetime.now().isoformat()

            # Save result to file
            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            result_file = RESULTS_DIR / f"{task.id}.txt"
            result_file.write_text(
                f"Task: {task.description}\n"
                f"Completed: {task.completed_at}\n"
                f"{'='*60}\n{result}",
                encoding="utf-8",
            )

            if self._notify_fn:
                preview = result[:100].replace("\n", " ")
                self._notify_fn("Nyra — Task Complete", f"{task.description[:40]}: {preview}…")

            print(f"[NightAgent] Done: {task.id}")

        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            task.completed_at = datetime.now().isoformat()
            if self._notify_fn:
                self._notify_fn("Nyra — Task Failed", str(exc)[:100])
            print(f"[NightAgent] Failed: {exc}")

        self.save()
