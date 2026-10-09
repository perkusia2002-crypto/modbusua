from __future__ import annotations

import csv
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path


SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]")
INCLUDE_RE = re.compile(r"^\s*include\s*=\s*[\"']?([^\"'\r\n]+?)[\"']?\s*$", re.IGNORECASE)
ITEM_RE = re.compile(r'^\s*i:([^=\s]+)\s*=\s*["\']?(.*?)["\']?\s*$')
KEY_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(.*?)\s*$')

PORT_FIELDS = ["Name", "Enable", "RepeatCount", "Type", "Host", "Port", "Timeout"]
DEVICE_FIELDS = [
    "Name", "PortName", "EnableDevice", "ModbusUnit", "RepeatCount", "RestoreTimeout",
    "MaxReadCoils", "MaxWriteMultipleCoils", "MaxReadDiscreteInputs", "MaxReadInputRegisters",
    "MaxReadHoldingRegisters", "MaxWriteMultipleRegisters", "DefaultPeriod", "RequestTimeout",
    "itemfile",
]

SUFFIXES = {
    "Int16": "S",
    "UInt16": "R",
    "Int32": "I",
    "UInt32": "U",
    "Int64": "LL",
    "UInt64": "UL",
    "Float": "F",
    "Double": "LF",
}
BYTE_SWAPPED_SUFFIXES = {
    "Int16": "SB",
    "UInt16": "RB",
    "Int32": "IB",
    "UInt32": "UB",
    "Int64": "LLB",
    "UInt64": "ULB",
    "Float": "FB",
    "Double": "LFB",
}
REGISTER_SWAPPED_SUFFIXES = {
    "Int32": "IS",
    "UInt32": "US",
    "Int64": "LLS",
    "UInt64": "ULS",
    "Float": "FS",
    "Double": "LFS",
}
SWAPPED_BYTE_SUFFIXES = {
    "Int32": "ISB",
    "UInt32": "USB",
    "Int64": "LLSB",
    "UInt64": "ULSB",
    "Float": "FSB",
    "Double": "LFSB",
}
SUFFIX_TO_TYPE = {v: k for k, v in SUFFIXES.items()}
SUFFIX_TO_TYPE.update({v: k for k, v in REGISTER_SWAPPED_SUFFIXES.items()})
SUFFIX_TO_TYPE.update({v: k for k, v in BYTE_SWAPPED_SUFFIXES.items()})
SUFFIX_TO_TYPE.update({v: k for k, v in SWAPPED_BYTE_SUFFIXES.items()})


@dataclass
class Section:
    header: str
    logical_name: str
    file: Path
    start_line: int
    end_line: int
    values: dict[str, str]

    @property
    def identity(self) -> str:
        return f"{self.file}|{self.header}|{self.logical_name}"


@dataclass
class Item:
    device: str
    name: str
    reference: str
    source_file: Path
    source_kind: str


class ConfigError(RuntimeError):
    pass


class ConfigManager:
    def __init__(self, config_file: str | Path):
        self.config_file = Path(config_file).expanduser().resolve()
        self._lock = threading.RLock()

    def _read_lines(self, path: Path) -> list[str]:
        try:
            return path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
        except OSError as exc:
            raise ConfigError(f"Не удалось прочитать {path}: {exc}") from exc

    @staticmethod
    def _unquote(value: str) -> str:
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            return value[1:-1]
        return value

    def _included_files(self, path: Path, stack: tuple[Path, ...] = ()) -> list[Path]:
        path = path.resolve()
        if path in stack:
            raise ConfigError("Циклический include: " + " -> ".join(map(str, stack + (path,))))
        if not path.exists():
            raise ConfigError(f"Конфигурационный файл не найден: {path}")
        result = [path]
        for line in self._read_lines(path):
            match = INCLUDE_RE.match(line)
            if match:
                child = (path.parent / self._unquote(match.group(1).strip())).resolve()
                result.extend(self._included_files(child, stack + (path,)))
        return list(dict.fromkeys(result))

    def files(self) -> list[dict]:
        with self._lock:
            files = []
            for path in self._included_files(self.config_file):
                try:
                    relative = str(path.relative_to(self.config_file.parent))
                except ValueError:
                    relative = str(path)
                files.append({"path": str(path), "relative": relative})
            return files

    def _parse_sections(self) -> list[Section]:
        sections: list[Section] = []
        for path in self._included_files(self.config_file):
            lines = self._read_lines(path)
            current: Section | None = None
            for idx, line in enumerate(lines):
                match = SECTION_RE.match(line)
                if match:
                    if current is not None:
                        current.end_line = idx
                        sections.append(current)
                    header = match.group(1).strip()
                    current = Section(header, header, path, idx, len(lines), {})
                    continue
                if current is None:
                    continue
                if ITEM_RE.match(line):
                    continue
                kv = KEY_RE.match(line)
                if kv:
                    current.values[kv.group(1)] = self._unquote(kv.group(2))
            if current is not None:
                current.end_line = len(lines)
                sections.append(current)
        for sec in sections:
            sec.logical_name = self._unquote(sec.values.get("Name", sec.header)).strip() or sec.header
        return sections

    @staticmethod
    def _is_type(section: Section, prefix: str) -> bool:
        # modbusua допускает [Device], [Device1], [Device.Station] и аналогично для Port.
        return re.match(r"^" + re.escape(prefix) + r"(?:$|[._]|\d)", section.header, re.IGNORECASE) is not None

    def _get_section(self, logical_name: str, prefix: str) -> Section:
        matches = [s for s in self._parse_sections() if self._is_type(s, prefix) and s.logical_name == logical_name]
        if not matches:
            raise ConfigError(f"{prefix} '{logical_name}' не найден")
        if len(matches) > 1:
            raise ConfigError(f"Найдено несколько {prefix} с именем '{logical_name}'")
        return matches[0]

    @staticmethod
    def _section_dict(sec: Section, fields: list[str]) -> dict:
        data = {"name": sec.logical_name, "section": sec.header, "file": str(sec.file), "identity": sec.identity}
        for field in fields:
            if field in sec.values:
                data[field] = sec.values[field]
        reserved = set(fields)
        data["extra"] = {k: v for k, v in sec.values.items() if k not in reserved}
        return data

    def ports(self) -> list[dict]:
        with self._lock:
            return [self._section_dict(s, PORT_FIELDS) for s in self._parse_sections() if self._is_type(s, "Port")]

    def _port_by_name(self, name: str) -> Section:
        return self._get_section(name, "Port")

    def devices(self) -> list[dict]:
        with self._lock:
            sections = self._parse_sections()
            port_map = {s.logical_name: self._section_dict(s, PORT_FIELDS) for s in sections if self._is_type(s, "Port")}
            result = []
            for sec in sections:
                if not self._is_type(sec, "Device"):
                    continue
                data = self._section_dict(sec, DEVICE_FIELDS)
                itemfile = sec.values.get("itemfile")
                data["itemfile_path"] = str((sec.file.parent / itemfile).resolve()) if itemfile else None
                data["port"] = port_map.get(sec.values.get("PortName", ""))
                result.append(data)
            return result

    def validate(self) -> dict:
        """Проверяет конфигурацию и ссылки item без изменения файлов."""
        with self._lock:
            errors: list[str] = []
            warnings: list[str] = []
            sections = self._parse_sections()
            names: dict[tuple[str, str], list[Section]] = {}
            for sec in sections:
                key = ("device" if self._is_type(sec, "Device") else "port" if self._is_type(sec, "Port") else sec.header.lower(), sec.logical_name)
                names.setdefault(key, []).append(sec)

            for key, matches in names.items():
                if len(matches) > 1 and key[0] in {"device", "port"}:
                    errors.append(f"Дублируется {key[0]} Name=\"{key[1]}\"")

            port_names = {s.logical_name for s in sections if self._is_type(s, "Port")}
            for device in [s for s in sections if self._is_type(s, "Device")]:
                port_name = device.values.get("PortName", "").strip()
                if port_name and port_name not in port_names:
                    errors.append(f"Device '{device.logical_name}': PortName '{port_name}' не найден")
                elif not port_name:
                    warnings.append(f"Device '{device.logical_name}': не задан PortName")
                itemfile = device.values.get("itemfile")
                if itemfile:
                    item_path = (device.file.parent / itemfile).resolve()
                    if not item_path.exists():
                        errors.append(f"Device '{device.logical_name}': itemfile не найден: {itemfile}")

            bad_items = 0
            for item in self.items():
                parsed = self.parse_reference(item["reference"])
                if not parsed.get("parse_ok"):
                    bad_items += 1
                    errors.append(f"{item['device']}.{item['name']}: некорректная ссылка '{item['reference']}'")
            if bad_items:
                warnings.append(f"Некорректных item: {bad_items}")

            return {
                "ok": not errors,
                "errors": errors,
                "warnings": warnings,
                "files_checked": len(self._included_files(self.config_file)),
                "devices": len(self.devices()),
                "ports": len(self.ports()),
                "items": len(self.items()),
            }

    def backup(self) -> list[str]:
        with self._lock:
            paths = self._included_files(self.config_file)
            created: list[str] = []
            for path in paths:
                if path.exists():
                    target = path.with_suffix(path.suffix + ".bak")
                    shutil.copy2(path, target)
                    created.append(str(target))
            return created

    def device_details(self, name: str) -> dict:
        device = next((d for d in self.devices() if d["name"] == name), None)
        if not device:
            raise ConfigError(f"Устройство '{name}' не найдено")
        return device

    def _device(self, name: str) -> Section:
        return self._get_section(name, "Device")

    @staticmethod
    def _parse_inline_items(device: Section, lines: list[str]) -> list[Item]:
        result: list[Item] = []
        for idx in range(device.start_line + 1, device.end_line):
            match = ITEM_RE.match(lines[idx])
            if match:
                result.append(Item(device.logical_name, match.group(1).strip(), ConfigManager._unquote(match.group(2).strip()), device.file, "inline"))
        return result

    @staticmethod
    def _parse_csv_items(device: Section) -> list[Item]:
        itemfile = device.values.get("itemfile")
        if not itemfile:
            return []
        path = (device.file.parent / itemfile).resolve()
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as fh:
                rows = csv.reader(fh, delimiter=";")
                result = []
                for row in rows:
                    if len(row) < 2:
                        continue
                    name, reference = row[0].strip(), row[1].strip()
                    if not name or not reference or name.startswith("#"):
                        continue
                    result.append(Item(device.logical_name, name, reference, path, "csv"))
                return result
        except OSError as exc:
            raise ConfigError(f"Не удалось прочитать {path}: {exc}") from exc

    def items(self, device_name: str | None = None) -> list[dict]:
        with self._lock:
            result: list[dict] = []
            for device in self._parse_sections():
                if not self._is_type(device, "Device"):
                    continue
                if device_name and device.logical_name != device_name:
                    continue
                lines = self._read_lines(device.file)
                parsed = self._parse_inline_items(device, lines) + self._parse_csv_items(device)
                for item in parsed:
                    editor = self.parse_reference(item.reference)
                    result.append({
                        "device": item.device,
                        "name": item.name,
                        "reference": item.reference,
                        "source_file": str(item.source_file),
                        "source_kind": item.source_kind,
                        "node_id": f"{item.device}.Items.{item.name}",
                        **editor,
                    })
            return result

    def _find_item(self, device_name: str, item_name: str) -> Item:
        for item in self.items(device_name):
            if item["name"] == item_name:
                return Item(device_name, item["name"], item["reference"], Path(item["source_file"]), item["source_kind"])
        raise ConfigError(f"Переменная '{item_name}' в '{device_name}' не найдена")

    @staticmethod
    def _backup(path: Path) -> None:
        if path.exists():
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))

    @classmethod
    def _atomic_write(cls, path: Path, data: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        cls._backup(path)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(data, encoding="utf-8", newline="")
        tmp.replace(path)

    @staticmethod
    def _format_value(value: object) -> str:
        text = "" if value is None else str(value).strip()
        return f'"{text}"' if any(ch in text for ch in " #\t") else text

    def _write_section_values(self, sec: Section, values: dict[str, object], remove: set[str] | None = None) -> None:
        lines = self._read_lines(sec.file)
        remove = {x.lower() for x in (remove or set())}
        wanted = {k: str(v) for k, v in values.items() if v is not None and str(v).strip() != ""}
        lookup = {k.lower(): (k, v) for k, v in wanted.items()}
        found: set[str] = set()
        body: list[str] = []
        for idx in range(sec.start_line, sec.end_line):
            line = lines[idx]
            if idx == sec.start_line or ITEM_RE.match(line):
                body.append(line)
                continue
            kv = KEY_RE.match(line)
            if not kv:
                body.append(line)
                continue
            key = kv.group(1)
            low = key.lower()
            if low in remove:
                continue
            if low in lookup:
                _, value = lookup[low]
                ending = "\r\n" if line.endswith("\r\n") else "\n"
                body.append(f"{key}={self._format_value(value)}{ending}")
                found.add(low)
            else:
                body.append(line)
        for low, (key, value) in lookup.items():
            if low not in found and low not in remove:
                body.append(f"{key}={self._format_value(value)}\n")
        self._atomic_write(sec.file, "".join(lines[:sec.start_line] + body + lines[sec.end_line:]))

    def _append_section(self, path: Path, header: str, values: dict[str, object]) -> None:
        old = path.read_text(encoding="utf-8-sig") if path.exists() else ""
        if old and not old.endswith("\n"):
            old += "\n"
        block = [f"[{header}]\n"]
        for key, value in values.items():
            if value is None or str(value).strip() == "":
                continue
            block.append(f"{key}={self._format_value(value)}\n")
        block.append("\n")
        self._atomic_write(path, old + "".join(block))

    @staticmethod
    def _unique_header(prefix: str, name: str, sections: list[Section]) -> str:
        base = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or prefix
        candidate = f"{prefix}.{base}"
        used = {s.header.lower() for s in sections}
        i = 2
        while candidate.lower() in used:
            candidate = f"{prefix}.{base}.{i}"
            i += 1
        return candidate

    @staticmethod
    def _safe_filename(name: str) -> str:
        value = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or "device"
        return value

    def _auto_itemfile(self, sec: Section) -> Path:
        existing = sec.values.get("itemfile")
        if existing:
            path = (sec.file.parent / existing).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                self._write_csv_rows(path, [])
            return path

        base = self._safe_filename(sec.logical_name)
        path = (sec.file.parent / "items" / f"{base}.csv").resolve()
        i = 2
        while path.exists():
            path = (sec.file.parent / "items" / f"{base}_{i}.csv").resolve()
            i += 1

        inline = self._parse_inline_items(sec, self._read_lines(sec.file))
        rows = [[item.name, item.reference] for item in inline]
        self._write_csv_rows(path, rows)
        self._write_section_values(sec, {"itemfile": str(path.relative_to(sec.file.parent)).replace("\\", "/")})

        if inline:
            refreshed = self._device(sec.logical_name)
            lines = self._read_lines(refreshed.file)
            kept = []
            for idx, line in enumerate(lines):
                if refreshed.start_line < idx < refreshed.end_line and ITEM_RE.match(line):
                    continue
                kept.append(line)
            self._atomic_write(refreshed.file, "".join(kept))
        return path

    @staticmethod
    def _write_csv_rows(path: Path, rows: list[list[str]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            csv.writer(fh, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\n").writerows(rows)
        tmp.replace(path)

    @staticmethod
    def build_reference(area: int, register: int, data_type: str, period_ms: int = 500, end_register: int | None = None, message_id: str = "", register_order: str = "normal", byte_order: str = "standard") -> str:
        if area not in (0, 1, 3, 4):
            raise ConfigError("Область Modbus должна быть 0, 1, 3 или 4")
        if not 1 <= register <= 65536:
            raise ConfigError("Регистр должен быть от 1 до 65536")
        if period_ms < 1 or period_ms > 2147483647:
            raise ConfigError("Период опроса должен быть положительным числом миллисекунд")
        if register_order not in ("normal", "reverse"):
            raise ConfigError("Неизвестный порядок регистров")
        if byte_order not in ("standard", "swapped"):
            raise ConfigError("Неизвестный порядок байт")
        if area not in (3, 4) and byte_order != "standard":
            raise ConfigError("Для областей 0x/1x порядок байт не настраивается")
        if data_type == "ByteArray":
            if register_order != "normal":
                raise ConfigError("Для массива байт порядок регистров не настраивается")
            if end_register is None or not register <= end_register <= 65536:
                raise ConfigError("Для массива байт нужен конечный регистр")
            ref = f"{area}{register:05d}-{area}{end_register:05d} B"
        else:
            if data_type == "Bool":
                if area not in (0, 1):
                    raise ConfigError("Bool доступен только для областей 0x и 1x")
                suffix = ""
            elif data_type == "UInt16" and area in (3, 4) and byte_order == "standard":
                suffix = ""
            else:
                suffix = SUFFIXES.get(data_type)
                if suffix is None:
                    raise ConfigError(f"Неизвестный тип данных: {data_type}")
                if byte_order == "swapped":
                    suffix = BYTE_SWAPPED_SUFFIXES.get(data_type)
                    if suffix is None:
                        raise ConfigError("Для этого типа данных порядок байт не поддерживается")
                if register_order == "reverse":
                    if byte_order == "swapped":
                        suffix = SWAPPED_BYTE_SUFFIXES.get(data_type)
                    else:
                        suffix = REGISTER_SWAPPED_SUFFIXES.get(data_type)
                    if suffix is None:
                        raise ConfigError("Для этого типа данных порядок регистров не поддерживается")
            ref = f"{area}{register:05d}{(' ' + suffix) if suffix else ''}"
        if period_ms != 500:
            ref += f", {period_ms}"
        if message_id:
            ref += f", msgid={message_id.strip()}"
        return ref

    @staticmethod
    def parse_reference(reference: str) -> dict:
        result = {
            "area": 4,
            "register": 1,
            "end_register": None,
            "data_type": "UInt16",
            "period_ms": 500,
            "message_id": "",
            "register_order": "normal",
            "read_function": 3,
            "write_function": 6,
            "register_count": 1,
            "byte_order": "standard",
            "parse_ok": False,
        }
        text = reference.strip()
        match = re.match(r"^([0134])(\d{5})(?:-([0134])(\d{5})\s+B|\s+([A-Za-z]+))?\s*(.*)$", text)
        if not match:
            return result
        area = int(match.group(1))
        register = int(match.group(2))
        end_area = match.group(3)
        end_register = int(match.group(4)) if match.group(4) else None
        suffix = match.group(5)
        tail = match.group(6).strip()
        if end_register is not None:
            if int(end_area) != area:
                return result
            data_type = "ByteArray"
        elif suffix:
            suffix = suffix.upper()
            data_type = SUFFIX_TO_TYPE.get(suffix)
            if not data_type:
                return result
            if suffix in BYTE_SWAPPED_SUFFIXES.values():
                result["byte_order"] = "swapped"
            elif suffix in SWAPPED_BYTE_SUFFIXES.values():
                result["register_order"] = "reverse"
                result["byte_order"] = "swapped"
            elif suffix in REGISTER_SWAPPED_SUFFIXES.values():
                result["register_order"] = "reverse"
        else:
            data_type = "Bool" if area in (0, 1) else "UInt16"
        for part in [x.strip() for x in tail.split(",") if x.strip()]:
            if re.fullmatch(r"\d+", part):
                result["period_ms"] = int(part)
            elif part.lower().startswith("msgid="):
                result["message_id"] = part.split("=", 1)[1].strip()
            else:
                return result
        if end_register is not None:
            register_count = end_register - register + 1
        else:
            register_count = {
                "Bool": 1, "Int16": 1, "UInt16": 1,
                "Int32": 2, "UInt32": 2, "Float": 2,
                "Int64": 4, "UInt64": 4, "Double": 4,
            }.get(data_type, 1)
        if area == 0:
            read_function = 1
            write_function = 5 if register_count == 1 and data_type == "Bool" else 15
        elif area == 1:
            read_function = 2
            write_function = None
        elif area == 3:
            read_function = 4
            write_function = None
        else:
            read_function = 3
            write_function = 6 if register_count == 1 else 16
        result.update({
            "area": area,
            "register": register,
            "end_register": end_register,
            "data_type": data_type,
            "register_order": result.get("register_order", "normal") if end_register is None else "normal",
            "read_function": read_function,
            "write_function": write_function,
            "register_count": register_count,
            "byte_order": result.get("byte_order", "standard"),
            "parse_ok": True,
        })
        return result

    def add_device_with_port(self, data: dict) -> dict:
        with self._lock:
            name = str(data.get("name", "")).strip()
            if not name:
                raise ConfigError("Имя устройства обязательно")
            if any(d["name"] == name for d in self.devices()):
                raise ConfigError(f"Устройство '{name}' уже существует")

            port_name = str(data.get("PortName") or f"{self._safe_filename(name)}_PORT").strip()
            if any(p["name"] == port_name for p in self.ports()):
                raise ConfigError(f"Порт '{port_name}' уже существует")

            dest = Path(data.get("file") or self.config_file).resolve()
            sections = self._parse_sections()
            port_header = self._unique_header("Port", port_name, sections)
            self._append_section(dest, port_header, {
                "Name": port_name,
                "Enable": data.get("PortEnable", "1"),
                "RepeatCount": data.get("PortRepeatCount", "2"),
                "Type": data.get("PortType", "TCP"),
                "Host": data.get("Host", "127.0.0.1"),
                "Port": data.get("Port", "502"),
                "Timeout": data.get("PortTimeout", "2000"),
            })

            sections = self._parse_sections()
            device_header = self._unique_header("Device", name, sections)
            safe_file = self._safe_filename(name)
            itemfile = f"items/{safe_file}.csv"
            values = {k: data.get(k) for k in DEVICE_FIELDS if k not in ("Name", "itemfile", "PortName")}
            values.update({"Name": name, "PortName": port_name, "itemfile": itemfile})
            self._append_section(dest, device_header, values)

            path = (dest.parent / itemfile).resolve()
            self._write_csv_rows(path, [])
            return self.device_details(name)

    def update_device_with_port(self, old_name: str, data: dict) -> dict:
        with self._lock:
            device = self._device(old_name)
            new_name = str(data.get("name", old_name)).strip()
            if not new_name:
                raise ConfigError("Имя устройства обязательно")
            if new_name != old_name and any(d["name"] == new_name for d in self.devices()):
                raise ConfigError(f"Устройство '{new_name}' уже существует")

            old_port_name = device.values.get("PortName", "")
            new_port_name = str(data.get("PortName", old_port_name)).strip()
            if not new_port_name:
                raise ConfigError("Имя порта обязательно")
            other_devices = [d for d in self.devices() if d["name"] != old_name]
            if new_port_name != old_port_name and any(str(d.get("PortName", "")) == new_port_name for d in other_devices):
                raise ConfigError(f"Порт '{new_port_name}' уже используется")
            if new_port_name != old_port_name and any(p["name"] == new_port_name for p in self.ports()):
                raise ConfigError(f"Порт '{new_port_name}' уже существует")

            port = self._port_by_name(old_port_name) if old_port_name else None
            if port is None:
                raise ConfigError(f"Порт '{old_port_name}' не найден")

            port_values = {
                "Name": new_port_name,
                "Enable": data.get("PortEnable", "1"),
                "RepeatCount": data.get("PortRepeatCount", "2"),
                "Type": data.get("PortType", "TCP"),
                "Host": data.get("Host", "127.0.0.1"),
                "Port": data.get("Port", "502"),
                "Timeout": data.get("PortTimeout", "2000"),
            }
            self._write_section_values(port, port_values)

            itemfile = device.values.get("itemfile")
            values = {k: data.get(k) for k in DEVICE_FIELDS if k not in ("Name", "itemfile", "PortName")}
            values.update({"Name": new_name, "PortName": new_port_name, "itemfile": itemfile or f"items/{self._safe_filename(new_name)}.csv"})
            self._write_section_values(device, values)

            if old_name != new_name and itemfile:
                old_path = (device.file.parent / itemfile).resolve()
                if old_path.exists() and self._safe_filename(old_name) in old_path.name:
                    new_path = old_path.with_name(f"{self._safe_filename(new_name)}.csv")
                    if new_path != old_path and not new_path.exists():
                        old_path.replace(new_path)
                        refreshed = self._device(new_name)
                        rel = str(new_path.relative_to(refreshed.file.parent)).replace("\\", "/")
                        self._write_section_values(refreshed, {"itemfile": rel})
            return self.device_details(new_name)

    def delete_device(self, name: str) -> dict:
        with self._lock:
            sec = self._device(name)
            port_name = sec.values.get("PortName", "")
            itemfile = sec.values.get("itemfile")
            lines = self._read_lines(sec.file)
            end = sec.end_line + (1 if sec.end_line < len(lines) and not lines[sec.end_line].strip() else 0)
            self._backup(sec.file)
            tmp = sec.file.with_suffix(sec.file.suffix + ".tmp")
            tmp.write_text("".join(lines[:sec.start_line] + lines[end:]), encoding="utf-8", newline="")
            tmp.replace(sec.file)

            if port_name and not any(str(d.get("PortName", "")) == port_name for d in self.devices()):
                try:
                    port = self._port_by_name(port_name)
                    plines = self._read_lines(port.file)
                    pend = port.end_line + (1 if port.end_line < len(plines) and not plines[port.end_line].strip() else 0)
                    self._backup(port.file)
                    ptmp = port.file.with_suffix(port.file.suffix + ".tmp")
                    ptmp.write_text("".join(plines[:port.start_line] + plines[pend:]), encoding="utf-8", newline="")
                    ptmp.replace(port.file)
                except ConfigError:
                    pass

            if itemfile:
                path = (sec.file.parent / itemfile).resolve()
                if path.exists() and path.name == f"{self._safe_filename(name)}.csv":
                    self._backup(path)
                    path.unlink()
            return {"name": name}

    def _ensure_itemfile(self, device_name: str) -> tuple[Section, Path]:
        sec = self._device(device_name)
        path = self._auto_itemfile(sec)
        return self._device(device_name), path

    def add_item(self, device_name: str, name: str, reference: str) -> dict:
        with self._lock:
            name, reference = name.strip(), reference.strip()
            if not name or not reference:
                raise ConfigError("Имя и Item Reference обязательны")
            if any(i["name"] == name for i in self.items(device_name)):
                raise ConfigError(f"Переменная '{name}' уже существует")
            sec, path = self._ensure_itemfile(device_name)
            rows = []
            if path.exists():
                with path.open("r", encoding="utf-8-sig", newline="") as fh:
                    rows = list(csv.reader(fh, delimiter=";"))
            rows.append([name, reference])
            self._write_csv_rows(path, rows)
            return {"device": device_name, "name": name, "reference": reference}

    def update_item(self, device_name: str, old_name: str, name: str, reference: str) -> dict:
        with self._lock:
            old = self._find_item(device_name, old_name)
            name, reference = name.strip(), reference.strip()
            if not name or not reference:
                raise ConfigError("Имя и Item Reference обязательны")
            if any(i["name"] == name and i["name"] != old_name for i in self.items(device_name)):
                raise ConfigError(f"Переменная '{name}' уже существует")

            sec, path = self._ensure_itemfile(device_name)
            if old.source_kind == "inline":
                # _ensure_itemfile already migrated inline items.
                path = path
            rows = []
            if path.exists():
                with path.open("r", encoding="utf-8-sig", newline="") as fh:
                    rows = list(csv.reader(fh, delimiter=";"))
            changed = False
            for row in rows:
                if len(row) >= 2 and row[0].strip() == old_name:
                    row[0], row[1] = name, reference
                    changed = True
                    break
            if not changed:
                raise ConfigError("Строка переменной в itemfile не найдена")
            self._write_csv_rows(path, rows)
            return {"device": device_name, "name": name, "reference": reference}

    def delete_item(self, device_name: str, name: str) -> dict:
        with self._lock:
            item = self._find_item(device_name, name)
            sec, path = self._ensure_itemfile(device_name)
            rows = []
            if path.exists():
                with path.open("r", encoding="utf-8-sig", newline="") as fh:
                    rows = list(csv.reader(fh, delimiter=";"))
            new_rows = [r for r in rows if not (r and r[0].strip() == name)]
            self._write_csv_rows(path, new_rows)
            return {"device": device_name, "name": name}

    def raw_file(self, path: str) -> str:
        with self._lock:
            target = Path(path).expanduser().resolve()
            if target not in set(self._included_files(self.config_file)):
                raise ConfigError("Доступ к этому файлу через UI запрещён")
            return target.read_text(encoding="utf-8-sig")
