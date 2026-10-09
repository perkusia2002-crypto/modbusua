from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Any

from .auth import COOKIE_NAME, login_response, require_auth
from .modbus_service import ModbusService, ModbusServiceError
from .config_manager import ConfigError, ConfigManager
from .opcua_service import OpcUaError, OpcUaService
from .process_manager import ProcessManager, ProcessManagerError
from .settings import BASE_DIR, Settings

settings = Settings.load()
config = ConfigManager(settings.config_file)
opcua = OpcUaService(settings.opcua_endpoint, settings.opcua_namespace_index, settings.opcua_app_name)
process = ProcessManager(settings)

app = FastAPI(title="modbusua UI", version="0.6.0")
modbus = ModbusService()
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


class LoginModel(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class ItemModel(BaseModel):
    device: str
    name: str = Field(min_length=1, max_length=128)
    area: int = Field(default=4)
    register: int = Field(default=1, ge=0, le=65536)
    data_type: str = "UInt16"
    period_ms: int = Field(default=500, ge=1, le=2147483647)
    end_register: int | None = Field(default=None, ge=0, le=65536)
    message_id: str = Field(default="", max_length=128)
    register_order: str = Field(default="normal")
    byte_order: str = Field(default="standard")
    address_mode: str = Field(default="one_based")
    reference: str | None = None


class ItemUpdateModel(ItemModel):
    old_name: str = Field(min_length=1, max_length=128)


class ItemDeleteModel(BaseModel):
    device: str
    name: str


class DeviceModel(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    PortName: str = Field(default="", max_length=128)
    PortEnable: str = "1"
    PortRepeatCount: str = "2"
    PortType: str = "TCP"
    Host: str = "127.0.0.1"
    Port: str = "502"
    PortTimeout: str = "2000"
    EnableDevice: str = "1"
    ModbusUnit: str = "1"
    RepeatCount: str = "2"
    RestoreTimeout: str = "10000"
    MaxReadCoils: str = "2000"
    MaxWriteMultipleCoils: str = "2000"
    MaxReadDiscreteInputs: str = "2000"
    MaxReadInputRegisters: str = "120"
    MaxReadHoldingRegisters: str = "120"
    MaxWriteMultipleRegisters: str = "120"
    DefaultPeriod: str = "500"
    RequestTimeout: str = "5000"
    file: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)


class DeviceUpdateModel(DeviceModel):
    old_name: str


class OpcUaWriteModel(BaseModel):
    device: str | None = None
    name: str | None = None
    node_id: str | None = None
    value: Any


class OpcUaBrowseModel(BaseModel):
    node_id: str = "ns=0;i=85"


class ModbusTestModel(BaseModel):
    port_name: str = Field(min_length=1, max_length=128)
    function: int = Field(default=3, ge=1, le=127)
    address: int = Field(default=1, ge=0, le=65536)
    address_mode: str = Field(default="one_based")
    count: int = Field(default=1, ge=1, le=2000)
    unit: int = Field(default=1, ge=1, le=247)
    timeout_ms: int = Field(default=2000, ge=1, le=120000)
    data_type: str = Field(default="UInt16")
    register_order: str = Field(default="normal")
    byte_order: str = Field(default="standard")
    value: Any = None
    values: list[Any] | None = None
    baudrate: int = Field(default=9600, ge=300, le=115200)
    parity: str = Field(default="N")
    stopbits: int = Field(default=1, ge=1, le=2)
    bytesize: int = Field(default=8, ge=7, le=8)


def protected(request: Request) -> None:
    require_auth(request, settings)


def api_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (ConfigError, OpcUaError, ProcessManagerError, ModbusServiceError)):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


@app.get("/")
async def index(request: Request):
    try:
        protected(request)
    except HTTPException:
        return FileResponse(
            BASE_DIR / "static" / "login.html",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache", "Expires": "0"},
        )
    return FileResponse(
        BASE_DIR / "static" / "index.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache", "Expires": "0"},
    )


@app.post("/api/auth/login")
async def login(model: LoginModel):
    import hmac
    if not hmac.compare_digest(model.password.encode("utf-8"), settings.password.encode("utf-8")):
        return JSONResponse(status_code=401, content={"detail": "Неверный пароль"})
    response = JSONResponse({"ok": True})
    login_response(response, settings)
    return response


@app.post("/api/auth/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@app.get("/api/auth/me")
async def me(request: Request):
    protected(request)
    return {"authenticated": True}


@app.get("/api/state")
async def state(request: Request):
    protected(request)
    try:
        devices = config.devices()
        all_items = config.items()
        item_counts: dict[str, int] = {}
        for item in all_items:
            item_counts[item["device"]] = item_counts.get(item["device"], 0) + 1
        for device in devices:
            device["item_count"] = item_counts.get(device["name"], 0)
        return {
            "config_file": str(config.config_file),
            "files": config.files(),
            "devices": devices,
            "ports_count": len({d.get("PortName") for d in devices if d.get("PortName")}),
            "item_count": len(all_items),
            "opcua": await opcua.status(),
            "server": process.telemetry(),
            "run_mode": settings.run_mode,
            "executable": str(settings.executable) if settings.executable else None,
        }
    except Exception as exc:
        raise api_error(exc)


@app.get("/api/diagnostics")
async def diagnostics(request: Request):
    protected(request)
    try:
        validation = config.validate()
        devices = config.devices()
        items = config.items()
        counts: dict[str, int] = {}
        for item in items:
            counts[item["device"]] = counts.get(item["device"], 0) + 1
        for device in devices:
            device["item_count"] = counts.get(device["name"], 0)
        return {
            "server": process.telemetry(),
            "opcua": await opcua.status(),
            "validation": validation,
            "devices": devices,
            "logs": process.recent_logs(),
            "workdir": str(settings.workdir),
            "logdir": str(settings.workdir / "log"),
        }
    except Exception as exc:
        raise api_error(exc)


@app.get("/api/logs")
async def logs(request: Request, path: str, lines: int = 250):
    protected(request)
    try:
        return process.log_tail(path, lines)
    except Exception as exc:
        raise api_error(exc)


@app.post("/api/config/validate")
async def validate_config(request: Request):
    protected(request)
    try:
        return config.validate()
    except Exception as exc:
        raise api_error(exc)


@app.post("/api/config/backup")
async def backup_config(request: Request):
    protected(request)
    try:
        return {"ok": True, "files": config.backup()}
    except Exception as exc:
        raise api_error(exc)


@app.post("/api/modbus/test")
async def modbus_test(request: Request, model: ModbusTestModel):
    protected(request)
    try:
        port = next((p for p in config.ports() if p["name"] == model.port_name), None)
        if not port:
            raise ConfigError(f"Порт '{model.port_name}' не найден")
        result = await modbus.test(port, model.model_dump())
        return {"port": port, "result": result}
    except Exception as exc:
        raise api_error(exc)


@app.get("/api/config/file")
async def config_file(request: Request, path: str):
    protected(request)
    try:
        return {"path": path, "content": config.raw_file(path)}
    except Exception as exc:
        raise api_error(exc)


@app.get("/api/devices")
async def devices(request: Request):
    protected(request)
    try:
        return {"devices": config.devices()}
    except Exception as exc:
        raise api_error(exc)


@app.post("/api/devices")
async def add_device(request: Request, model: DeviceModel):
    protected(request)
    try:
        return {"device": config.add_device_with_port(model.model_dump())}
    except Exception as exc:
        raise api_error(exc)


@app.put("/api/devices")
async def update_device(request: Request, model: DeviceUpdateModel):
    protected(request)
    try:
        return {"device": config.update_device_with_port(model.old_name, model.model_dump())}
    except Exception as exc:
        raise api_error(exc)


@app.delete("/api/devices")
async def delete_device(request: Request, name: str):
    protected(request)
    try:
        return {"device": config.delete_device(name)}
    except Exception as exc:
        raise api_error(exc)


@app.get("/api/items")
async def read_items(request: Request, device: str | None = None):
    protected(request)
    try:
        items = config.items(device)
        return {"items": await opcua.read_items(items), "opcua": await opcua.status()}
    except Exception as exc:
        raise api_error(exc)


@app.get("/api/opcua/read")
async def opcua_read(request: Request, device: str, name: str):
    protected(request)
    try:
        item = next((x for x in config.items(device) if x["name"] == name), None)
        if not item:
            raise ConfigError(f"Переменная '{name}' в '{device}' не найдена")
        return await opcua.read_item(device, name, item)
    except Exception as exc:
        raise api_error(exc)


@app.post("/api/opcua/read-all")
async def opcua_read_all(request: Request, device: str | None = None):
    protected(request)
    try:
        items = config.items(device)
        return {"items": await opcua.read_items(items), "opcua": await opcua.status()}
    except Exception as exc:
        raise api_error(exc)


@app.post("/api/opcua/write")
async def opcua_write(request: Request, model: OpcUaWriteModel):
    protected(request)
    try:
        if model.device and model.name:
            item = next((x for x in config.items(model.device) if x["name"] == model.name), None)
            if not item:
                raise ConfigError(f"Переменная '{model.name}' в '{model.device}' не найдена")
            result = await opcua.write_item(item, model.value)
            return {"item": item, "result": result}
        if model.node_id:
            return {"result": await opcua.write_node(model.node_id, model.value)}
        raise ConfigError("Нужно указать device + name или node_id")
    except Exception as exc:
        raise api_error(exc)


@app.get("/api/opcua/browse")
async def opcua_browse(request: Request, node_id: str = ""):
    protected(request)
    try:
        return await opcua.browse(node_id)
    except Exception as exc:
        raise api_error(exc)


@app.get("/api/opcua/node")
async def opcua_node(request: Request, node_id: str):
    protected(request)
    try:
        return await opcua.read_node(node_id)
    except Exception as exc:
        raise api_error(exc)


def item_reference(model: ItemModel) -> str:
    if model.reference and model.reference.strip():
        # Поддерживаем старые клиенты со ссылкой целиком.
        return model.reference.strip()

    if model.address_mode not in {"one_based", "zero_based"}:
        raise ConfigError("Нумерация адреса должна быть 1-based или 0-based")

    register = model.register
    end_register = model.end_register
    if model.address_mode == "one_based":
        if register < 1 or (end_register is not None and end_register < 1):
            raise ConfigError("При 1-based адресации адрес начинается с 1")
    else:
        if register < 0 or register > 65535:
            raise ConfigError("При 0-based адресации адрес должен быть от 0 до 65535")
        register += 1
        if end_register is not None:
            if end_register < 0 or end_register > 65535:
                raise ConfigError("При 0-based адресации конечный адрес должен быть от 0 до 65535")
            end_register += 1

    return config.build_reference(
        model.area,
        register,
        model.data_type,
        model.period_ms,
        end_register,
        model.message_id,
        model.register_order,
        model.byte_order,
    )


@app.post("/api/items")
async def add_item(request: Request, model: ItemModel):
    protected(request)
    try:
        reference = item_reference(model)
        return {"item": config.add_item(model.device, model.name, reference)}
    except Exception as exc:
        raise api_error(exc)


@app.put("/api/items")
async def update_item(request: Request, model: ItemUpdateModel):
    protected(request)
    try:
        reference = item_reference(model)
        return {"item": config.update_item(model.device, model.old_name, model.name, reference)}
    except Exception as exc:
        raise api_error(exc)


@app.delete("/api/items")
async def delete_item(request: Request, model: ItemDeleteModel):
    protected(request)
    try:
        return {"item": config.delete_item(model.device, model.name)}
    except Exception as exc:
        raise api_error(exc)


@app.post("/api/reload")
async def reload_server(request: Request):
    protected(request)
    try:
        await opcua.reload_config()
        return {"ok": True, "message": "ReloadConfig отправлен"}
    except Exception as exc:
        raise api_error(exc)


@app.get("/api/server/status")
async def server_status(request: Request):
    protected(request)
    try:
        return process.status()
    except Exception as exc:
        raise api_error(exc)


@app.post("/api/server/start")
async def server_start(request: Request):
    protected(request)
    try:
        return process.start()
    except Exception as exc:
        raise api_error(exc)


@app.post("/api/server/stop")
async def server_stop(request: Request):
    protected(request)
    try:
        await opcua.close()
        return process.stop()
    except Exception as exc:
        raise api_error(exc)


@app.post("/api/server/restart")
async def server_restart(request: Request):
    protected(request)
    try:
        await opcua.close()
        return process.restart()
    except Exception as exc:
        raise api_error(exc)
