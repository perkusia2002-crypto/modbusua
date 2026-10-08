from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = Path(os.getenv("MODBUSUA_UI_ENV_FILE", BASE_DIR / ".env"))
DEFAULT_CONFIG_FILE = BASE_DIR / "conf" / "modbusua.conf"
DEFAULT_CONFIG = """[Project]
Name=modbusua
Log.flags="Error|Warning|Info|TraceDetails"
Log.output="file console"
Log.default.format="[%time] %src. %text"
Log.default.timeformat="%Y-%M-%D %h:%m:%s.%f"
Log.file.format="%time;%cat;%src;%text"
Log.file.timeformat="%h:%m:%s.%f"
Log.file.path="log.csv"
Log.file.maxcount=10
Log.file.maxsize=1000000

[OPCUA]
Port=4840
UncertainAs=Good#Bad#Good#Uncertain
"""

def ensure_default_config(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG, encoding="utf-8", newline="\n")


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


_DOTENV = _load_dotenv(ENV_FILE)


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is not None:
        return value
    return _DOTENV.get(name, default)


@dataclass(frozen=True)
class Settings:
    config_file: Path
    opcua_endpoint: str
    opcua_namespace_index: int
    opcua_app_name: str
    executable: Path | None
    workdir: Path
    run_mode: str
    service_name: str
    ui_host: str
    ui_port: int
    password: str
    secret: str
    session_days: int

    @classmethod
    def load(cls) -> "Settings":
        config_raw = env("MODBUSUA_CONFIG", str(DEFAULT_CONFIG_FILE)) or str(DEFAULT_CONFIG_FILE)
        config_file = Path(config_raw).expanduser().resolve()
        if config_file == DEFAULT_CONFIG_FILE.resolve():
            ensure_default_config(config_file)

        endpoint = env("MODBUSUA_OPCUA_ENDPOINT", "opc.tcp://127.0.0.1:4840") or "opc.tcp://127.0.0.1:4840"
        namespace = int(env("MODBUSUA_OPCUA_NAMESPACE_INDEX", "2") or "2")
        app_name = env("MODBUSUA_OPCUA_APP_NAME", "modbusua") or "modbusua"

        exe_raw = env("MODBUSUA_EXECUTABLE", "") or ""
        default_exe = BASE_DIR.parent / ("modbusua.exe" if os.name == "nt" else "modbusua")
        executable = Path(exe_raw).expanduser().resolve() if exe_raw else default_exe.resolve()
        workdir_raw = env("MODBUSUA_WORKDIR", "") or ""
        workdir = Path(workdir_raw).expanduser().resolve() if workdir_raw else BASE_DIR.parent.resolve()

        run_mode = (env("MODBUSUA_RUN_MODE", "process") or "process").strip().lower()
        if run_mode not in {"process", "service"}:
            raise RuntimeError("MODBUSUA_RUN_MODE должен быть process или service")

        service_name = env("MODBUSUA_SERVICE_NAME", "modbusua") or "modbusua"
        ui_host = env("MODBUSUA_UI_HOST", "127.0.0.1") or "127.0.0.1"
        ui_port = int(env("MODBUSUA_UI_PORT", "8088") or "8088")
        password = env("MODBUSUA_UI_PASSWORD", "") or ""
        if not password:
            raise RuntimeError("Не задан MODBUSUA_UI_PASSWORD в .env")
        secret = env("MODBUSUA_UI_SECRET", "") or hashlib.sha256(password.encode("utf-8")).hexdigest()
        session_days = int(env("MODBUSUA_UI_SESSION_DAYS", "7") or "7")

        return cls(
            config_file=config_file,
            opcua_endpoint=endpoint,
            opcua_namespace_index=namespace,
            opcua_app_name=app_name,
            executable=executable,
            workdir=workdir,
            run_mode=run_mode,
            service_name=service_name,
            ui_host=ui_host,
            ui_port=ui_port,
            password=password,
            secret=secret,
            session_days=session_days,
        )
