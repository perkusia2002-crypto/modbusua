from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime
from typing import Any, Awaitable, Callable


class OpcUaError(RuntimeError):
    pass


def _format_opcua_error(exc: Exception) -> str:
    text = str(exc)
    if "BadNodeIdUnknown" in text:
        return "OPC UA: NodeId не найден в address space. Обновите дерево OPC UA и проверьте endpoint/namespace."
    return text


class OpcUaService:
    def __init__(self, endpoint: str, namespace_index: int, app_node_name: str = "modbusua"):
        self.endpoint = endpoint
        self.namespace_index = namespace_index
        self.app_node_name = app_node_name
        self._client = None
        self._lock = asyncio.Lock()

    async def _ensure_client(self):
        if self._client is None:
            try:
                from asyncua import Client
            except ImportError as exc:
                raise OpcUaError("Не установлен asyncua. Выполните: pip install -r requirements.txt") from exc
            client = Client(url=self.endpoint)
            try:
                await client.connect()
            except Exception:
                try:
                    await client.disconnect()
                except Exception:
                    pass
                raise
            self._client = client
        return self._client

    async def close(self):
        async with self._lock:
            await self._close()

    async def _close(self):
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def _with_retry(self, operation: Callable[[Any], Awaitable[Any]]):
        async with self._lock:
            first_error = None
            for _ in range(2):
                try:
                    client = await self._ensure_client()
                    return await operation(client)
                except Exception as exc:
                    first_error = exc
                    await self._close()
            raise OpcUaError(_format_opcua_error(first_error)) from first_error

    def node_id(self, device: str, item_name: str) -> str:
        return f"ns={self.namespace_index};s={device}.Items.{item_name}"

    def command_node_id(self, command: str) -> str:
        return f"ns={self.namespace_index};s={self.app_node_name}.Cmd.{command}"

    @staticmethod
    def _timestamp(dv: Any) -> str | None:
        stamp = getattr(dv, "SourceTimestamp", None) or getattr(dv, "ServerTimestamp", None)
        return stamp.isoformat() if stamp else None

    @staticmethod
    def _status(dv: Any) -> str:
        return str(getattr(dv, "StatusCode", "Bad"))

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, (bytes, bytearray, memoryview)):
            return {"encoding": "base64", "value": base64.b64encode(bytes(value)).decode("ascii")}
        if isinstance(value, tuple):
            return [OpcUaService._json_value(x) for x in value]
        if isinstance(value, list):
            return [OpcUaService._json_value(x) for x in value]
        if isinstance(value, dict):
            return {str(k): OpcUaService._json_value(v) for k, v in value.items()}
        return value

    @staticmethod
    def _variant_type_name(variant: Any) -> str | None:
        try:
            return str(variant.VariantType.name)
        except Exception:
            try:
                return str(variant.VariantType).split(".")[-1]
            except Exception:
                return None

    @staticmethod
    def _parse_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "on", "yes", "да", "истина"}:
            return True
        if text in {"0", "false", "off", "no", "нет", "ложь"}:
            return False
        raise OpcUaError("Для Bool используйте TRUE/FALSE или 1/0")

    @staticmethod
    def _parse_bytes(value: Any) -> bytes:
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value)
        if isinstance(value, dict) and value.get("encoding") == "base64":
            try:
                return base64.b64decode(str(value.get("value", "")), validate=True)
            except Exception as exc:
                raise OpcUaError(f"Некорректный Base64: {exc}") from exc
        text = str(value).strip()
        if not text:
            return b""
        compact = text.replace(" ", "").replace("0x", "").replace("0X", "")
        if len(compact) % 2 or any(c not in "0123456789abcdefABCDEF" for c in compact):
            raise OpcUaError("Массив байт: используйте HEX, например AA 01 FF")
        return bytes.fromhex(compact)

    @classmethod
    def _coerce_for_variant(cls, value: Any, variant_type_name: str | None) -> Any:
        name = (variant_type_name or "").lower()
        if name in {"boolean"}:
            return cls._parse_bool(value)
        if name in {"float", "double"}:
            try:
                return float(value)
            except Exception as exc:
                raise OpcUaError("Ожидалось числовое значение") from exc
        if name in {"byte", "sbyte", "uint16", "int16", "uint32", "int32", "uint64", "int64"}:
            try:
                return int(str(value).strip(), 0)
            except Exception as exc:
                raise OpcUaError("Ожидалось целое числовое значение") from exc
        if name in {"bytestring"}:
            return cls._parse_bytes(value)
        if name.startswith("string"):
            return str(value)
        if isinstance(value, list):
            return value
        return value

    async def status(self) -> dict[str, Any]:
        async def op(client):
            await client.nodes.server.read_browse_name()
            return {"connected": True, "endpoint": self.endpoint, "namespace_index": self.namespace_index}

        try:
            return await self._with_retry(op)
        except Exception as exc:
            return {"connected": False, "endpoint": self.endpoint, "namespace_index": self.namespace_index, "error": str(exc)}

    async def _read_node(self, client: Any, node_id: str, include_value: bool = True) -> dict[str, Any]:
        node = client.get_node(node_id)
        result: dict[str, Any] = {"node_id": node_id}
        try:
            browse = await node.read_browse_name()
            result["browse_name"] = str(getattr(browse, "Name", browse))
        except Exception:
            result["browse_name"] = ""
        try:
            display = await node.read_display_name()
            result["display_name"] = str(getattr(display, "Text", display))
        except Exception:
            result["display_name"] = result.get("browse_name", "")
        try:
            node_class = await node.read_node_class()
            result["node_class"] = str(getattr(node_class, "name", node_class))
        except Exception:
            result["node_class"] = "Unknown"
        try:
            result["access_level"] = int(await node.read_access_level())
        except Exception:
            result["access_level"] = None
        try:
            result["user_access_level"] = int(await node.read_user_access_level())
        except Exception:
            result["user_access_level"] = None
        if include_value and result.get("node_class", "").lower().endswith("variable"):
            try:
                dv = await node.read_data_value()
                variant = getattr(dv, "Value", None)
                value = getattr(variant, "Value", None)
                result.update({
                    "value": self._json_value(value),
                    "variant_type": self._variant_type_name(variant),
                    "status": self._status(dv),
                    "timestamp": self._timestamp(dv),
                    "error": None,
                })
            except Exception as exc:
                result.update({"value": None, "variant_type": None, "status": "Bad", "timestamp": None, "error": str(exc)})
        return result

    async def read_node(self, node_id: str) -> dict[str, Any]:
        if not node_id.strip():
            raise OpcUaError("NodeId не задан")
        return await self._with_retry(lambda client: self._read_node(client, node_id.strip(), True))

    async def read_items(self, items: list[dict]) -> list[dict]:
        if not items:
            return []
        try:
            async def op(client):
                result = []
                for item in items:
                    try:
                        node = client.get_node(self.node_id(item["device"], item["name"]))
                        dv = await node.read_data_value()
                        value = getattr(getattr(dv, "Value", None), "Value", None)
                        result.append({
                            **item,
                            "value": self._json_value(value),
                            "status": self._status(dv),
                            "timestamp": self._timestamp(dv),
                            "variant_type": self._variant_type_name(getattr(dv, "Value", None)),
                            "error": None,
                            "can_write": item.get("write_function") is not None,
                        })
                    except Exception as exc:
                        result.append({
                            **item,
                            "value": None,
                            "status": "Bad",
                            "timestamp": None,
                            "variant_type": None,
                            "error": str(exc),
                            "can_write": item.get("write_function") is not None,
                        })
                return result

            return await self._with_retry(op)
        except Exception as exc:
            return [{**item, "value": None, "status": "OPC UA offline", "timestamp": None, "variant_type": None, "error": str(exc), "can_write": item.get("write_function") is not None} for item in items]

    async def read_item(self, device: str, item_name: str, item: dict | None = None) -> dict[str, Any]:
        node_id = self.node_id(device, item_name)
        data = await self.read_node(node_id)
        if item:
            data = {**item, **data, "can_write": item.get("write_function") is not None}
        return data

    async def write_node(self, node_id: str, value: Any) -> dict[str, Any]:
        async def op(client):
            node = client.get_node(node_id.strip())
            dv = await node.read_data_value()
            variant = getattr(dv, "Value", None)
            variant_type = getattr(variant, "VariantType", None)
            type_name = self._variant_type_name(variant)
            converted = self._coerce_for_variant(value, type_name)
            try:
                from asyncua import ua
                write_value = ua.Variant(converted, variant_type) if variant_type is not None else converted
            except ImportError as exc:
                raise OpcUaError("Не установлен asyncua") from exc
            await node.write_value(write_value)
            updated = await node.read_data_value()
            return {
                "node_id": node_id,
                "value": self._json_value(getattr(getattr(updated, "Value", None), "Value", None)),
                "variant_type": self._variant_type_name(getattr(updated, "Value", None)),
                "status": self._status(updated),
                "timestamp": self._timestamp(updated),
                "written": True,
            }
        return await self._with_retry(op)

    async def write_item(self, item: dict, value: Any) -> dict[str, Any]:
        if item.get("write_function") is None:
            raise OpcUaError(f"Переменная '{item.get('name', '')}' доступна только для чтения")
        return await self.write_node(self.node_id(item["device"], item["name"]), value)

    async def browse(self, node_id: str = "") -> dict[str, Any]:
        async def op(client):
            node = client.nodes.objects if not node_id.strip() else client.get_node(node_id.strip())
            node_id_text = node.nodeid.to_string()
            children = await node.get_children()
            result = []
            for child in children:
                try:
                    ref = child.nodeid.to_string()
                except Exception:
                    ref = str(child.nodeid)
                result.append(await self._read_node(client, ref, include_value=False))
            result.sort(key=lambda x: (str(x.get("node_class", "")), str(x.get("display_name", "")).lower()))
            current = await self._read_node(client, node_id_text, include_value=False)
            current["children_count"] = len(result)
            return {"node": current, "children": result}
        return await self._with_retry(op)

    async def reload_config(self) -> None:
        async def op(client):
            node = client.get_node(self.command_node_id("ReloadConfig"))
            await node.write_value(True)
        await self._with_retry(op)
