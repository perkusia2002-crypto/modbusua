from __future__ import annotations

import os
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .settings import Settings

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


class ProcessManagerError(RuntimeError):
    pass


class ProcessManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._process: subprocess.Popen | None = None

    def _matches_local_process(self, proc) -> bool:
        exe = self.settings.executable
        if not exe:
            return False
        try:
            exe_path = Path(proc.exe()).resolve()
        except Exception:
            return False
        if exe_path != exe:
            return False
        try:
            cmd = " ".join(proc.cmdline())
        except Exception:
            cmd = ""
        return str(self.settings.config_file) in cmd or self.settings.config_file.name in cmd

    def _find_external_process(self):
        if psutil is None or self.settings.run_mode != "process":
            return None
        for proc in psutil.process_iter(["pid", "exe", "cmdline"]):
            if proc.pid == os.getpid():
                continue
            try:
                if self._matches_local_process(proc):
                    return proc
            except Exception:
                continue
        return None

    def _service(self, action: str) -> dict:
        if os.name == "nt":
            cmd = ["sc.exe", action, self.settings.service_name]
        else:
            cmd = ["systemctl", action, self.settings.service_name]
        completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise ProcessManagerError(detail or f"Не удалось выполнить: {' '.join(cmd)}")
        return {"ok": True, "action": action, "mode": "service", "service": self.settings.service_name}

    def status(self) -> dict:
        if self.settings.run_mode == "service":
            if os.name == "nt":
                cmd = ["sc.exe", "query", self.settings.service_name]
                completed = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
                text = completed.stdout.upper()
                running = "RUNNING" in text
                stopped = "STOPPED" in text
                return {"running": running, "stopped": stopped, "known": completed.returncode == 0, "mode": "service", "service": self.settings.service_name}
            completed = subprocess.run(["systemctl", "is-active", self.settings.service_name], capture_output=True, text=True)
            state = completed.stdout.strip().lower()
            return {"running": state == "active", "stopped": state in {"inactive", "failed", "unknown"}, "known": True, "mode": "service", "service": self.settings.service_name}

        if self._process and self._process.poll() is None:
            return {"running": True, "stopped": False, "known": True, "mode": "process", "pid": self._process.pid, "source": "ui"}
        proc = self._find_external_process()
        if proc:
            return {"running": True, "stopped": False, "known": True, "mode": "process", "pid": proc.pid, "source": "external"}
        return {"running": False, "stopped": True, "known": bool(self.settings.executable), "mode": "process", "pid": None, "source": None}

    def telemetry(self) -> dict:
        status = self.status()
        result = dict(status)
        if psutil is None:
            return result

        try:
            memory = psutil.virtual_memory()
            result.update({
                "system_cpu_percent": psutil.cpu_percent(interval=0.05),
                "system_memory_percent": round(memory.percent, 1),
                "system_memory_used_mb": round(memory.used / 1024 / 1024),
                "system_memory_total_mb": round(memory.total / 1024 / 1024),
                "cpu_count": psutil.cpu_count(logical=True),
            })
        except Exception:
            pass

        if status.get("pid"):
            try:
                proc = psutil.Process(int(status["pid"]))
                create_time = proc.create_time()
                result.update({
                    "cpu_percent": round(proc.cpu_percent(interval=0.05), 1),
                    "memory_mb": round(proc.memory_info().rss / 1024 / 1024, 2),
                    "memory_percent": round(proc.memory_percent(), 2),
                    "threads": proc.num_threads(),
                    "create_time": datetime.fromtimestamp(create_time, timezone.utc).astimezone().isoformat(),
                    "uptime_s": max(0, round(time.time() - create_time)),
                })
            except Exception:
                pass
        return result

    def recent_logs(self, limit: int = 12) -> list[dict]:
        logdir = self.settings.workdir / "log"
        if not logdir.exists():
            return []
        files = [p for p in logdir.rglob("*") if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        result = []
        for path in files[:max(1, min(limit, 50))]:
            try:
                stat = path.stat()
                result.append({
                    "name": path.name,
                    "path": str(path),
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).astimezone().isoformat(),
                })
            except OSError:
                continue
        return result

    def log_tail(self, path: str, lines: int = 250) -> dict:
        candidate = Path(path).expanduser().resolve()
        logdir = (self.settings.workdir / "log").resolve()
        try:
            candidate.relative_to(logdir)
        except ValueError as exc:
            raise ProcessManagerError("Разрешено читать только файлы из каталога log") from exc
        if not candidate.exists() or not candidate.is_file():
            raise ProcessManagerError(f"Файл журнала не найден: {candidate}")
        count = max(1, min(int(lines), 2000))
        try:
            text = candidate.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            raise ProcessManagerError(str(exc)) from exc
        content = "\n".join(text.splitlines()[-count:])
        return {"path": str(candidate), "content": content}

    def start(self) -> dict:
        if self.settings.run_mode == "service":
            return self._service("start")
        exe = self.settings.executable
        if not exe:
            raise ProcessManagerError("Не задан MODBUSUA_EXECUTABLE в .env")
        if not exe.exists():
            raise ProcessManagerError(f"Исполняемый файл не найден: {exe}")
        existing = self._find_external_process()
        if existing:
            return {"ok": True, "already_running": True, "pid": existing.pid}
        args = [str(exe), "-f", str(self.settings.config_file)]
        logdir = self.settings.workdir / "log"
        args += ["--logdir", str(logdir)]
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        self._process = subprocess.Popen(
            args,
            cwd=str(self.settings.workdir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            start_new_session=(os.name != "nt"),
        )
        time.sleep(0.15)
        if self._process.poll() is not None:
            raise ProcessManagerError(f"modbusua завершился сразу после запуска, код={self._process.returncode}")
        return {"ok": True, "started": True, "pid": self._process.pid}

    def stop(self) -> dict:
        if self.settings.run_mode == "service":
            return self._service("stop")
        proc = self._find_external_process()
        if self._process and self._process.poll() is None:
            target = self._process
            target.terminate()
            try:
                target.wait(timeout=5)
            except subprocess.TimeoutExpired:
                target.kill()
                target.wait(timeout=2)
            self._process = None
            return {"ok": True, "stopped": True}
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception as exc:
                    raise ProcessManagerError(str(exc)) from exc
            return {"ok": True, "stopped": True, "pid": proc.pid}
        return {"ok": True, "already_stopped": True}

    def restart(self) -> dict:
        self.stop()
        return self.start()
