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
    language: str = "en"

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

    def schedule(self, description: str, language: str = "en") -> Task:
        """Queue a new task. Returns the Task object."""
        task = Task(
            id=str(uuid.uuid4())[:8],
            description=description,
            status="pending",
            created_at=datetime.now().isoformat(),
            language=language,
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
            tasks = list(self._tasks)

        running = [t for t in tasks if t.status == "running"]
        pending = [t for t in tasks if t.status == "pending"]
        done    = [t for t in tasks if t.status == "done"]

        if not running and not pending and not done:
            return "No active tasks." if language == "en" else "Aktif görev yok."

        lines: list[str] = []
        for t in running:
            desc = t.description[:60]
            lines.append(f"[Running] {desc}" if language == "en" else f"[Çalışıyor] {desc}")
        for t in pending:
            desc = t.description[:60]
            lines.append(f"[Queued] {desc}" if language == "en" else f"[Bekliyor] {desc}")
        for t in done:
            desc = t.description[:60]
            lines.append(f"[Done] {desc}" if language == "en" else f"[Tamamlandı] {desc}")

        header = (
            f"{len(running)} running, {len(pending)} queued, {len(done)} done:"
            if language == "en"
            else f"{len(running)} çalışıyor, {len(pending)} bekliyor, {len(done)} tamamlandı:"
        )
        return header + "\n" + "\n".join(lines)

    def morning_briefing(self, language: str = "en") -> str:
        """Call on startup to surface overnight results."""
        done = self.completed_since_last_check()
        if not done:
            return ""
        summaries: list[str] = []
        for t in done:
            preview = t.result[:300].replace("\n", " ").strip() if t.result else t.error[:100]
            if preview and not preview.endswith("…"):
                preview += "…"
            summaries.append(f"• {t.description[:60]}:\n  {preview}")
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

            task_type = self._classify(task.description)
            print(f"[NightAgent] Type: {task_type}")

            if task_type == "research":
                result = self._run_research(task.description, task.language)
            elif task_type == "price":
                result = self._run_price(task.description)
            else:
                result = self._run_agent(task.description, task.language)

            task.result = result
            task.status = "done"
            task.completed_at = datetime.now().isoformat()

            RESULTS_DIR.mkdir(parents=True, exist_ok=True)
            result_file = RESULTS_DIR / f"{task.id}.txt"
            result_file.write_text(
                f"Task: {task.description}\n"
                f"Type: {task_type}\n"
                f"Completed: {task.completed_at}\n"
                f"{'='*60}\n{result}",
                encoding="utf-8",
            )

            if self._notify_fn:
                preview = result[:120].replace("\n", " ")
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

    def _classify(self, description: str) -> str:
        """Classify a task to pick the best execution strategy."""
        lower = description.lower()
        price_kw = {"price", "cost", "how much", "cheapest", "expensive", "buy", "fiyat", "kaç lira", "kaç euro", "compare prices"}
        research_kw = {"research", "investigate", "find out", "analyze", "learn about", "report on",
                       "what is the best", "compare", "araştır", "incele", "rapor", "bul"}
        if any(w in lower for w in price_kw):
            return "price"
        if any(w in lower for w in research_kw):
            return "research"
        return "agent"

    def _run_research(self, description: str, language: str = "en") -> str:
        """Run a deep research task using the research engine + LLM synthesis."""
        import research as _r
        svc = self._agent._llm._svc
        system = (
            "You are a research analyst. Synthesize the provided data into a clear, "
            "well-structured report. Be thorough, cite sources, include key numbers/dates."
        )
        def llm_fn(prompt: str) -> str:
            return svc.chat([{"role": "user", "content": prompt}], system)
        return _r.deep_research(description, llm_fn=llm_fn, depth=2, language=language)

    def _run_price(self, description: str) -> str:
        """Run a price check task."""
        import research as _r
        # Try to separate product from region
        lower = description.lower()
        region = ""
        for kw in ("in the netherlands", "in turkey", "in germany", "in uk", "in us", "europe", "usa"):
            if kw in lower:
                region = kw.replace("in the ", "").replace("in ", "").strip()
                description = description.lower().replace(kw, "").strip()
                break
        return _r.price_check(description, region=region)

    def _run_agent(self, description: str, language: str = "en") -> str:
        """Run a general task through the agent."""
        return self._agent.respond(
            description,
            language=language,
            session_app="",
            long_mem_context=(
                "You are running as an autonomous background agent. "
                "Use TOOL: calls to gather information and complete the task. "
                "Be thorough. When done, write a clear summary of what you accomplished."
            ),
        )
