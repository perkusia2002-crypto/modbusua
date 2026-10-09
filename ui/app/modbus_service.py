from __future__ import annotations

import asyncio
import math
import struct
import time
from typing import Any


class ModbusServiceError(RuntimeError):
    pass


class ModbusService:
    """Инженерный Modbus-тестер.

    Он выполняет отдельный диагностический запрос и не меняет конфигурацию modbusua.
    Для TCP это отдельное соединение; для RS-485 оператору следует учитывать,
    что отдельный клиент не должен одновременно владеть тем же COM-портом.
    """

    _lock = asyncio.Lock()

    @staticmethod
    def _require_pymodbus():
        try:
            from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
            return AsyncModbusTcpClient, AsyncModbusSerialClient
        except ImportError as exc:
            raise ModbusServiceError(
                "Не установлен pymodbus. Выполните: pip install -r requirements.txt"
            ) from exc

    @staticmethod
    def _parse_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "on", "yes", "да"}:
            return True
        if text in {"0", "false", "off", "no", "нет"}:
            return False
        raise ModbusServiceError("Для Bool используйте TRUE/FALSE или 1/0")

    @staticmethod
    def _parse_int(value: Any, name: str, minimum: int, maximum: int) -> int:
        try:
            number = int(str(value).strip(), 0)
        except Exception as exc:
            raise ModbusServiceError(f"{name}: ожидается целое число") from exc
        if number < minimum or number > maximum:
            raise ModbusServiceError(f"{name}: допустимый диапазон {minimum}..{maximum}")
        return number

    @staticmethod
    def _parse_float(value: Any) -> float:
        try:
            number = float(str(value).strip().replace(",", "."))
        except Exception as exc:
            raise ModbusServiceError("Ожидается числовое значение") from exc
        if not math.isfinite(number):
            raise ModbusServiceError("Число должно быть конечным")
        return number

    @classmethod
    def _decode_words(cls, words: list[int], data_type: str, register_order: str, byte_order: str) -> Any:
        if not words:
            return None
        if data_type == "UInt16":
            return words[0]
        if data_type == "Int16":
            return struct.unpack(">h", struct.pack(">H", words[0]))[0]
        if data_type == "Bool":
            return bool(words[0])
        if data_type == "ByteArray":
            raw = b"".join(struct.pack(">H", x & 0xFFFF) for x in words)
            if byte_order == "swapped":
                raw = b"".join(raw[i:i+2][::-1] for i in range(0, len(raw), 2))
            return {"hex": raw.hex(" ").upper(), "bytes": len(raw)}

        if register_order == "reverse":
            words = list(reversed(words))

        raw = b"".join(struct.pack(">H", x & 0xFFFF) for x in words)
        if byte_order == "swapped":
            raw = b"".join(raw[i:i+2][::-1] for i in range(0, len(raw), 2))

        fmt = {
            "UInt32": ">I", "Int32": ">i", "Float": ">f",
            "UInt64": ">Q", "Int64": ">q", "Double": ">d",
        }.get(data_type)
        if not fmt:
            return {"registers": words}
        size = struct.calcsize(fmt)
        if len(raw) < size:
            raise ModbusServiceError(f"Недостаточно регистров для {data_type}: нужно {size // 2}")
        return struct.unpack(fmt, raw[:size])[0]

    @classmethod
    def _encode_value(cls, value: Any, data_type: str, register_order: str, byte_order: str) -> list[int]:
        if data_type == "Bool":
            return [1 if cls._parse_bool(value) else 0]
        if data_type == "UInt16":
            return [cls._parse_int(value, "Значение", 0, 65535)]
        if data_type == "Int16":
            return [struct.unpack(">H", struct.pack(">h", cls._parse_int(value, "Значение", -32768, 32767)))[0]]
        if data_type == "ByteArray":
            text = str(value).replace("0x", "").replace("0X", "").replace(" ", "").replace(":", "")
            if not text or len(text) % 2 or any(c not in "0123456789abcdefABCDEF" for c in text):
                raise ModbusServiceError("Массив байт: используйте HEX, например AA 01 FF")
            raw = bytes.fromhex(text)
            if len(raw) % 2:
                raw += b"\x00"
            if byte_order == "swapped":
                raw = b"".join(raw[i:i+2][::-1] for i in range(0, len(raw), 2))
            return [struct.unpack(">H", raw[i:i+2])[0] for i in range(0, len(raw), 2)]

        num = cls._parse_float(value) if data_type in {"Float", "Double"} else cls._parse_int(
            value,
            "Значение",
            -(2**63) if data_type == "Int64" else 0,
            2**64 - 1 if data_type in {"UInt32", "UInt64"} else 2**63 - 1,
        )
        fmt = {"UInt32": ">I", "Int32": ">i", "Float": ">f", "UInt64": ">Q", "Int64": ">q", "Double": ">d"}.get(data_type)
        if not fmt:
            raise ModbusServiceError(f"Тип данных не поддерживается: {data_type}")
        raw = struct.pack(fmt, num)
        if byte_order == "swapped":
            raw = b"".join(raw[i:i+2][::-1] for i in range(0, len(raw), 2))
        words = [struct.unpack(">H", raw[i:i+2])[0] for i in range(0, len(raw), 2)]
        if register_order == "reverse":
            words.reverse()
        return words

    @classmethod
    async def test(cls, port: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        AsyncModbusTcpClient, AsyncModbusSerialClient = cls._require_pymodbus()
        function = cls._parse_int(request.get("function", 3), "Функция", 1, 127)
        address_mode = str(request.get("address_mode", "one_based"))
        address = cls._parse_int(request.get("address", 1), "Адрес", 0, 65535)
        if address_mode == "one_based":
            if address < 1:
                raise ModbusServiceError("При 1--based адресации адрес начинается с 1")
            address -= 1
        count = cls._parse_int(request.get("count", 1), "Количество", 1, 2000)
        unit = cls._parse_int(request.get("unit", 1), "Unit", 1, 247)
        timeout_ms = cls._parse_int(request.get("timeout_ms", port.get("Timeout", 2000)), "Таймаут", 1, 120000)
        data_type = str(request.get("data_type", "UInt16"))
        register_order = str(request.get("register_order", "normal"))
        byte_order = str(request.get("byte_order", "standard"))
        started = time.perf_counter()

        ptype = str(port.get("Type", "TCP")).upper()
        host = str(port.get("Host", "127.0.0.1"))
        pnum = str(port.get("Port", "502"))
        client = None

        async with cls._lock:
            try:
                if ptype in {"TCP", "TCP/IP"}:
                    client = AsyncModbusTcpClient(host, port=int(pnum), timeout=timeout_ms / 1000.0)
                elif ptype in {"RTU", "SERIAL", "RS485", "ASCII"}:
                    baudrate = cls._parse_int(request.get("baudrate", 9600), "Baudrate", 300, 115200)
                    parity = str(request.get("parity", "N")).upper()
                    stopbits = cls._parse_int(request.get("stopbits", 1), "Stopbits", 1, 2)
                    bytesize = cls._parse_int(request.get("bytesize", 8), "Bytesize", 7, 8)
                    client = AsyncModbusSerialClient(
                        host,
                        baudrate=baudrate,
                        parity=parity,
                        stopbits=stopbits,
                        bytesize=bytesize,
                        timeout=timeout_ms / 1000.0,
                    )
                else:
                    raise ModbusServiceError(f"Неподдерживаемый тип порта: {ptype}")

                connected = await client.connect()
                if connected is False:
                    raise ModbusServiceError("Не удалось открыть Modbus-соединение")

                payload: dict[str, Any] = {}
                if function == 1:
                    rr = await client.read_coils(address, count=count, device_id=unit)
                    payload["values"] = [bool(x) for x in (rr.bits or [])[:count]]
                elif function == 2:
                    rr = await client.read_discrete_inputs(address, count=count, device_id=unit)
                    payload["values"] = [bool(x) for x in (rr.bits or [])[:count]]
                elif function == 3:
                    rr = await client.read_holding_registers(address, count=count, device_id=unit)
                    registers = list(rr.registers or [])
                    payload["registers"] = registers
                    payload["decoded"] = cls._decode_words(registers, data_type, register_order, byte_order)
                elif function == 4:
                    rr = await client.read_input_registers(address, count=count, device_id=unit)
                    registers = list(rr.registers or [])
                    payload["registers"] = registers
                    payload["decoded"] = cls._decode_words(registers, data_type, register_order, byte_order)
                elif function in {5, 15}:
                    values = [cls._parse_bool(x) for x in request.get("values", [])]
                    if function == 5:
                        if not values:
                            values = [cls._parse_bool(request.get("value", False))]
                        rr = await client.write_coil(address, values[0], device_id=unit)
                    else:
                        if not values:
                            raise ModbusServiceError("Для FC15 укажите values")
                        rr = await client.write_coils(address, values, device_id=unit)
                    payload["written"] = values
                    payload["response"] = str(rr)
                elif function in {6, 16}:
                    values = request.get("values")
                    if values is None:
                        values = cls._encode_value(request.get("value", ""), data_type, register_order, byte_order)
                    else:
                        values = [cls._parse_int(x, "Регистр", 0, 65535) for x in values]
                    if function == 6:
                        if len(values) != 1:
                            raise ModbusServiceError("FC06 записывает ровно один регистр")
                        rr = await client.write_register(address, values[0], device_id=unit)
                    else:
                        rr = await client.write_registers(address, values, device_id=unit)
                    payload["written_registers"] = values
                    payload["decoded"] = cls._decode_words(values, data_type, register_order, byte_order)
                    payload["response"] = str(rr)
                else:
                    raise ModbusServiceError("Инженерный тест поддерживает FC1/2/3/4/5/6/15/16")

                if hasattr(rr, "isError") and rr.isError():
                    raise ModbusServiceError(f"Modbus Exception: {rr}")

                return {
                    "ok": True,
                    "function": function,
                    "address": address + 1,
                    "address_zero_based": address,
                    "unit": unit,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    **payload,
                }
            except ModbusServiceError:
                raise
            except Exception as exc:
                raise ModbusServiceError(str(exc)) from exc
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
