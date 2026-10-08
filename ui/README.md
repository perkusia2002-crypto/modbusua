# modbusua Engineering UI

The companion engineering web console for modbusua Gateway.

## Current UI

- Dashboard with devices, ports and variable count
- Variable list
- Add Variable dialog
- Variable address checker
- Explicit address numbering: `1-based` / `0-based`
- Live address conversion preview
- Modbus Item Reference preview
- Server zero-based offset preview
- Register-order and byte-order selection
- Configuration viewer
- Password authentication through `.env`

### Address numbering

The Add Variable dialog and the standalone address checker use the same rule.

`1-based`:

```text
1 -> 400001 -> server offset 0
2 -> 400002 -> server offset 1
```

`0-based`:

```text
0 -> 400001 -> server offset 0
1 -> 400002 -> server offset 1
```

The gateway parser itself converts Modbus Item References such as `400001` to an internal zero-based offset.

## Run on Windows

Create `ui/.env` from `ui/.env.example`, set a password, then run:

```bat
run.bat
```

The UI listens on the address and port configured by `MODBUSUA_UI_HOST` and `MODBUSUA_UI_PORT` (default `127.0.0.1:8088`).
