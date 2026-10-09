from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from .auth import COOKIE_NAME, login_response, require_auth, valid_session
from .settings import Settings

settings = Settings.load()
app = FastAPI(title="modbusua Engineering UI", docs_url=None, redoc_url=None)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_FILE = DATA_DIR / "ui_state.json"
DEFAULT_STATE = {"address_base": "1-based", "byte_order": "normal", "register_order": "normal"}


def state() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
        return {**DEFAULT_STATE, **(data if isinstance(data, dict) else {})}
    except (OSError, ValueError, json.JSONDecodeError):
        return DEFAULT_STATE.copy()


def save_state(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def config_text() -> str:
    try:
        return settings.config_file.read_text(encoding="utf-8-sig") if settings.config_file.exists() else ""
    except OSError:
        return ""


def sections(text: str) -> list[tuple[str, str]]:
    return re.findall(r"(?ms)^\[([^\]]+)\]\s*(.*?)(?=^\[[^\]]+\]|\Z)", text)


def devices(text: str) -> list[dict[str, str]]:
    out = []
    for name, body in sections(text):
        if name.lower().startswith("device") or re.search(r"^PortName\s*=", body, re.I | re.M):
            m = re.search(r"^Name\s*=\s*['\"]?([^\r\n'\"]+)", body, re.I | re.M)
            out.append({"section": name, "name": m.group(1).strip() if m else name})
    return out


def ports(text: str) -> list[dict[str, Any]]:
    out = []
    for name, body in sections(text):
        if not name.lower().startswith("port"):
            continue

        def find(pattern: str, default: str) -> str:
            m = re.search(pattern, body, re.I | re.M)
            return m.group(1).strip() if m else default

        out.append(
            {
                "section": name,
                "name": find(r"^Name\s*=\s*['\"]?([^\r\n'\"]+)", name),
                "host": find(r"^Host\s*=\s*['\"]?([^\r\n'\"]+)", "127.0.0.1"),
                "port": int(find(r"^Port\s*=\s*(\d+)", "502")),
            }
        )
    return out


def items(text: str) -> list[dict[str, Any]]:
    current = "Device"
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            current = s[1:-1]
            continue

        m = re.match(r"^i:([^=\s]+)\s*=\s*['\"]?([^'\"]+)['\"]?$", s)
        if not m:
            continue

        ref = m.group(2).strip()
        a = re.match(r"([0134])(\d{5})", ref)
        out.append(
            {
                "name": m.group(1),
                "reference": ref,
                "device": current,
                "memory": f"{a.group(1)}x" if a else "",
                "address": int(a.group(2)) if a else None,
            }
        )
    return out


def protocol_address(address: int, base: str) -> int:
    if base not in {"0-based", "1-based"}:
        raise ValueError("Нумерация должна быть 0-based или 1-based")
    if base == "0-based":
        if address < 0 or address > 65535:
            raise ValueError("При 0-based адресации адрес должен быть от 0 до 65535")
        return address + 1
    if address < 1 or address > 65536:
        raise ValueError("При 1-based адресации адрес должен быть от 1 до 65536")
    return address


class Login(BaseModel):
    password: str


class AddressCheck(BaseModel):
    address: int = Field(ge=0, le=65536)
    base: str = "1-based"
    memory: str = "4x"


class Variable(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    address: int = Field(ge=0, le=99998)
    base: str = "1-based"
    memory: str = "4x"
    suffix: str = Field(default="F", max_length=20)
    byte_order: str = "normal"
    register_order: str = "normal"
    device: str = "Device"
    period: int = Field(default=500, ge=10, le=3600000)


def auth(request: Request) -> None:
    require_auth(request, settings)


@app.get("/", response_class=HTMLResponse)
def root(request: Request) -> Response:
    if not valid_session(settings, request.cookies.get(COOKIE_NAME)):
        return RedirectResponse("/login", status_code=303)
    auth(request)
    return HTMLResponse(PAGE)


@app.get("/login", response_class=HTMLResponse)
def login_page() -> HTMLResponse:
    return HTMLResponse(LOGIN_PAGE)


@app.post("/api/login")
def api_login(model: Login) -> Response:
    if model.password != settings.password:
        raise HTTPException(401, "Неверный пароль")
    response = JSONResponse({"ok": True})
    login_response(response, settings)
    return response


@app.post("/api/logout")
def api_logout() -> Response:
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@app.get("/api/state")
def api_state(request: Request) -> dict[str, Any]:
    auth(request)
    s = state()
    text = config_text()
    return {
        **s,
        "config_file": str(settings.config_file),
        "config_text": text[:16000],
        "devices": devices(text),
        "ports": ports(text),
        "variables": items(text),
    }


@app.put("/api/state")
def api_put_state(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    auth(request)
    s = state()
    for key in DEFAULT_STATE:
        if key in payload:
            s[key] = payload[key]
    save_state(s)
    return s


@app.post("/api/check-address")
def api_check(request: Request, model: AddressCheck) -> dict[str, Any]:
    auth(request)
    try:
        p = protocol_address(model.address, model.base)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    if model.memory not in {"0x", "1x", "3x", "4x"}:
        raise HTTPException(400, "Недопустимый тип памяти")

    return {
        "entered": model.address,
        "base": model.base,
        "protocol_address": p,
        "offset": p - 1,
        "item_reference": f"{model.memory[0]}{p:05d}",
    }


@app.post("/api/variables/preview")
def api_preview(request: Request, model: Variable) -> dict[str, Any]:
    auth(request)
    try:
        p = protocol_address(model.address, model.base)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    ref = f"{model.memory[0]}{p:05d} {model.suffix}".strip()
    return {"reference": ref, "offset": p - 1, "protocol_address": p}


@app.post("/api/variables")
def api_add(request: Request, model: Variable) -> dict[str, Any]:
    auth(request)

    if not re.fullmatch(r"[A-Za-zА-Яа-я_][A-Za-zА-Яа-я0-9_.-]*", model.name):
        raise HTTPException(400, "Недопустимое имя переменной")

    try:
        p = protocol_address(model.address, model.base)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    if re.search(rf"^i:{re.escape(model.name)}\s*=", config_text(), re.M):
        raise HTTPException(409, "Переменная с таким именем уже существует")

    ref = f"{model.memory[0]}{p:05d} {model.suffix}".strip()
    text = config_text()
    if not text:
        raise HTTPException(500, "Конфигурационный файл недоступен")

    block = re.compile(rf"(?ms)^\[{re.escape(model.device)}\]\s*(.*?)(?=^\[[^\]]+\]|\Z)")
    line = f'i:{model.name:<24}="{ref}, {model.period}"\n'
    m = block.search(text)
    if m:
        new_text = text[:m.end(1)].rstrip() + "\n" + line + text[m.end(1):]
    else:
        new_text = text.rstrip() + f"\n\n[{model.device}]\n" + line

    try:
        settings.config_file.write_text(new_text, encoding="utf-8", newline="\n")
    except OSError as e:
        raise HTTPException(500, f"Не удалось сохранить конфигурацию: {e}") from e

    return {"ok": True, "reference": ref, "offset": p - 1}


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "time": int(time.time()), "ui": "0.7.0"}


LOGIN_PAGE = '''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>modbusua — вход</title><style>body{margin:0;background:#080d15;color:#edf2fa;font:14px Segoe UI,Arial;display:grid;place-items:center;min-height:100vh}.card{width:min(390px,calc(100vw - 32px));background:#111a26;border:1px solid #24334a;border-radius:18px;padding:28px;box-shadow:0 25px 80px #0008}input,button{font:inherit}input{width:100%;height:44px;box-sizing:border-box;padding:0 12px;border-radius:10px;border:1px solid #2a3b55;background:#09111d;color:#fff;margin-top:8px}button{margin-top:16px;width:100%;height:44px;border:0;border-radius:10px;background:#5b8cff;color:#fff;font-weight:700;cursor:pointer}.muted{color:#8d9db6;margin:7px 0 22px}.err{display:none;color:#ff9ca7;margin-top:10px}</style></head><body><form class="card" id="f"><b style="font-size:21px">modbusua</b><div class="muted">Engineering UI</div><label>Пароль<input id="p" type="password" autofocus></label><button>Войти</button><div class="err" id="e"></div></form><script>f.onsubmit=async x=>{x.preventDefault();let r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:p.value})});if(r.ok)location='/';else{e.textContent='Неверный пароль';e.style.display='block'}}</script></body></html>'''


PAGE = '''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>modbusua Engineering UI</title><style>
:root{--bg:#080d15;--p:#101924;--p2:#0b131e;--line:#223149;--txt:#eaf0f8;--mut:#8392aa;--a:#5b8cff;--ok:#32c78a;--bad:#ff6878;font:14px Segoe UI,Arial}*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#09111c,#070b12);color:var(--txt)}.app{display:grid;grid-template-columns:230px 1fr;min-height:100vh}.side{background:#0a1019;border-right:1px solid var(--line);padding:18px 13px}.brand{font-size:20px;font-weight:800;padding:7px 10px 21px}.brand small{display:block;font-size:11px;color:var(--mut);font-weight:400;margin-top:4px}.nav{display:grid;gap:6px}.nav button{color:#aebbd0;background:transparent;text-align:left;border:1px solid transparent;border-radius:10px;padding:11px 12px;cursor:pointer;font:inherit}.nav button:hover,.nav button.active{background:#111c2a;border-color:#263853;color:#fff}.foot{position:fixed;bottom:18px;left:23px;color:#56657c;font-size:11px}.main{min-width:0}.top{height:64px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 24px;background:#09111b}.actions{display:flex;gap:8px}.wrap{padding:24px;max-width:1400px}.section{display:none}.section.active{display:block}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.card{background:linear-gradient(180deg,#101a26,#0d1621);border:1px solid var(--line);border-radius:14px;padding:18px}.k{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.08em}.v{font-size:26px;font-weight:800;margin-top:8px}.small{color:var(--mut);font-size:12px;line-height:1.55}.toolbar{display:flex;align-items:center;gap:12px;margin-bottom:14px}.btn{border:1px solid #2b3c57;background:#101a28;color:#dce6f5;padding:9px 12px;border-radius:9px;cursor:pointer;font:inherit}.btn:hover{border-color:#42618e}.btn.primary{background:var(--a);border-color:var(--a);color:#fff;font-weight:700}.split{display:grid;grid-template-columns:1.2fr .8fr;gap:14px;margin-top:14px}.form{display:grid;grid-template-columns:1fr 1fr;gap:13px}.field{display:grid;gap:6px}.field.full{grid-column:1/-1}.field label{font-size:12px;color:#aebbd0}.field input,.field select{height:40px;padding:0 10px;border:1px solid #2b3c57;border-radius:9px;background:#09111b;color:#eaf0f8;font:inherit}.preview{background:#09111b;border:1px dashed #344965;border-radius:10px;padding:12px;line-height:1.65}.preview code{color:#9ec2ff}.notice{margin-top:12px;background:#0c1520;border:1px solid #25364f;border-radius:10px;padding:11px;color:#9aaac1;font-size:12px}.notice.bad{border-color:#63313b;color:#ffadb6}.tag{display:inline-block;padding:3px 7px;border-radius:999px;background:#162338;color:#9fc1ff;font-size:11px}.table{width:100%;border-collapse:collapse}.table th,.table td{padding:11px 9px;border-bottom:1px solid #1d2a3d;text-align:left;vertical-align:top}.table th{color:#73849d;font-size:11px;text-transform:uppercase}.table code{color:#a9caff}.modal{position:fixed;inset:0;display:none;place-items:center;background:#03070caa;backdrop-filter:blur(5px);z-index:10;padding:20px}.modal .card{width:min(780px,100%);max-height:92vh;overflow:auto}.row{display:flex;align-items:center;justify-content:space-between;gap:10px}.row h3{margin:0}.row .btn{margin-top:0}pre{white-space:pre-wrap;word-break:break-word;color:#9eb2cd;line-height:1.55}h3{margin:0 0 14px}@media(max-width:1000px){.cards{grid-template-columns:repeat(2,1fr)}.split{grid-template-columns:1fr}}@media(max-width:700px){.app{grid-template-columns:1fr}.side{display:none}.wrap{padding:13px}.cards,.form{grid-template-columns:1fr}}
</style></head><body><div class="app"><aside class="side"><div class="brand">modbusua<small>Engineering UI 0.7.0</small></div><nav class="nav"><button class="active" data-page="home">Обзор</button><button data-page="vars">Переменные</button><button data-page="check">Проверка</button><button data-page="addr">Адресация</button><button data-page="cfg">Конфигурация</button></nav><div class="foot">Industrial configuration console</div></aside><main class="main"><header class="top"><b id="title">Обзор</b><div class="actions"><button class="btn" onclick="load()">Обновить</button><button class="btn" onclick="out()">Выйти</button></div></header><div class="wrap">
<section id="home" class="section active"><div class="cards"><div class="card"><div class="k">UI</div><div class="v">● Онлайн</div></div><div class="card"><div class="k">Нумерация</div><div class="v" id="baseKpi">1-based</div></div><div class="card"><div class="k">Переменные</div><div class="v" id="countKpi">0</div></div><div class="card"><div class="k">Порты</div><div class="v" id="portKpi">0</div></div></div><div class="split"><div class="card"><h3>Адресация</h3><div class="small">Режим теперь доступен и в форме добавления переменной.</div><div class="preview" style="margin-top:12px"><code id="homeBase">1-based</code></div></div><div class="card"><h3>Устройства</h3><div id="devices" class="small">Загрузка...</div></div></div></section>
<section id="vars" class="section"><div class="toolbar"><button class="btn primary" onclick="openModal()">+ Добавить переменную</button><span class="small" id="varCount"></span></div><div class="card" id="varTable"></div></section>
<section id="check" class="section"><div class="card"><h3>Проверка переменной</h3><div class="form"><div class="field"><label>Тип памяти</label><select id="cm"><option>0x</option><option>1x</option><option>3x</option><option selected>4x</option></select></div><div class="field"><label>Нумерация адреса</label><select id="cb"><option value="1-based">1-based</option><option value="0-based">0-based</option></select></div><div class="field full"><label>Адрес</label><input id="ca" value="1" inputmode="numeric" oninput="check()"></div></div><div id="cr" class="preview" style="margin-top:12px">Введите адрес.</div></div></section>
<section id="addr" class="section"><div class="card"><h3>Нумерация адреса</h3><div class="form"><div class="field"><label>Режим по умолчанию</label><select id="base" onchange="setBase(this.value)"><option value="1-based">1-based — первый адрес 1</option><option value="0-based">0-based — первый адрес 0</option></select></div><div class="field"><label>Пример</label><div class="preview"><code id="example">1 → 400001 → offset 0</code></div></div></div><div class="notice">Это настройка интерпретации адреса UI. Ядро modbusua хранит Item Reference в 1-based формате.</div></div></section>
<section id="cfg" class="section"><div class="split"><div class="card"><h3>Конфигурация</h3><div class="small" id="path"></div><pre id="text" style="max-height:65vh;overflow:auto"></pre></div><div class="card"><h3>Преобразование адреса</h3><pre>1-based
ввод 1
  ↓
Item Reference 400001
  ↓
offset 0

0-based
ввод 0
  ↓
Item Reference 400001
  ↓
offset 0</pre></div></div></section></div></main></div>
<div class="modal" id="modal"><div class="card"><div class="row"><h3>Добавить переменную</h3><button class="btn" onclick="closeModal()">Закрыть</button></div><div class="form"><div class="field"><label>Имя</label><input id="vn" placeholder="Pump_Frequency"></div><div class="field"><label>Устройство</label><select id="vd"></select></div><div class="field"><label>Тип памяти</label><select id="vm"><option>0x</option><option>1x</option><option>3x</option><option selected>4x</option></select></div><div class="field"><label>Тип данных</label><select id="vt"><option value="S">Int16</option><option value="R">UInt16</option><option value="I">Int32</option><option value="U">UInt32</option><option value="LL">Int64</option><option value="UL">UInt64</option><option value="F" selected>Float</option><option value="LF">Double</option><option value="B">Byte Array</option></select></div><div class="field"><label>Порядок регистров</label><select id="vr"><option value="normal" selected>Обычный</option><option value="swapped">Обратный</option></select></div><div class="field"><label>Порядок байт</label><select id="vb"><option value="normal" selected>Обычный</option><option value="swapped">Обратный</option></select></div><div class="field"><label>Адрес</label><input id="va" value="1" inputmode="numeric" oninput="previewVar()"></div><div class="field"><label>Нумерация адреса (как в тестере Modbus)</label><select id="vbase" onchange="previewVar()"><option value="1-based">1-based — адрес начинается с 1</option><option value="0-based">0-based — адрес начинается с 0</option></select><div class="small">0-based: ввод 0 создаёт первый регистр в Item Reference (например, 400001).</div></div><div class="field"><label>Период, мс</label><input id="vp" value="500" inputmode="numeric"></div><div class="field full"><label>Предпросмотр</label><div id="pv" class="preview">—</div></div></div><div id="ve" class="notice bad" style="display:none"></div><div class="row" style="justify-content:flex-end;margin-top:14px"><button class="btn" onclick="closeModal()">Отмена</button><button class="btn primary" onclick="addVar()">Добавить</button></div></div></div>
<script>
const $=id=>document.getElementById(id), titles={home:'Обзор',vars:'Переменные',check:'Проверка',addr:'Адресация',cfg:'Конфигурация'};let S={};
document.querySelectorAll('.nav button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));$(b.dataset.page).classList.add('active');$('title').textContent=titles[b.dataset.page]});
const esc=x=>String(x??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function api(u,o={}){let r=await fetch(u,{credentials:'same-origin',...o});if(r.status===401){location='/login';throw Error('auth')}let j=await r.json().catch(()=>({}));if(!r.ok)throw Error(j.detail||'Ошибка');return j}
async function load(){S=await api('/api/state');render()}
function render(){ $('baseKpi').textContent=S.address_base;$('homeBase').textContent=S.address_base;$('base').value=S.address_base;$('cb').value=S.address_base;$('countKpi').textContent=(S.variables||[]).length;$('portKpi').textContent=(S.ports||[]).length;$('varCount').textContent=`${(S.variables||[]).length} переменных`;$('path').textContent=S.config_file||'';$('text').textContent=S.config_text||'';$('devices').innerHTML=(S.devices||[]).map(d=>`<div style="padding:6px 0;border-bottom:1px solid #1c293c">${esc(d.name)} <span class="tag">${esc(d.section)}</span></div>`).join('')||'Устройства не найдены';$('vd').innerHTML=(S.devices||[{section:'Device',name:'Device'}]).map(d=>`<option value="${esc(d.section)}">${esc(d.name)}</option>`).join('');let rows=S.variables||[];$('varTable').innerHTML=rows.length?`<table class="table"><thead><tr><th>Имя</th><th>Адрес</th><th>Тип</th><th>Item Reference</th><th>Устройство</th></tr></thead><tbody>${rows.map(v=>`<tr><td><b>${esc(v.name)}</b></td><td>${esc(v.address)}</td><td><span class="tag">${esc(v.memory)}</span></td><td><code>${esc(v.reference)}</code></td><td>${esc(v.device)}</td></tr>`).join('')}</tbody></table>`:'<div style="padding:35px;text-align:center;color:#8392aa">Переменных пока нет</div>';previewVar();check();example()}
function setBase(v){api('/api/state',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({address_base:v})}).then(load).catch(e=>alert(e.message))}function example(){let b=$('base').value;$('example').textContent=b==='0-based'?'0 → 400001 → offset 0':'1 → 400001 → offset 0'}
async function check(){let a=Number($('ca').value||0);try{let r=await api('/api/check-address',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({address:a,base:$('cb').value,memory:$('cm').value})});$('cr').innerHTML=`Ввод: <b>${a}</b> (${esc(r.base)})<br>Item Reference: <b>${esc(r.item_reference)}</b><br>Server offset: <b>${r.offset}</b>`}catch(e){$('cr').textContent=e.message}}
function suffix(){let t=$('vt').value,b=$('vb').value,r=$('vr').value;if(t==='B')return'B';let both={I:'ISB',U:'USB',LL:'LLSB',UL:'ULSB',F:'FSB',LF:'LFSB'},reg={I:'IS',U:'US',LL:'LLS',UL:'ULS',F:'FS',LF:'LFS'},byt={S:'SB',R:'RB',I:'IB',U:'UB',LL:'LLB',UL:'ULB',F:'FB',LF:'LFB'};if(b==='swapped'&&r==='swapped'&&both[t])return both[t];if(r==='swapped'&&reg[t])return reg[t];if(b==='swapped'&&byt[t])return byt[t];return t}
async function previewVar(){try{let r=await api('/api/variables/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('vn').value||'Variable',address:Number($('va').value||0),base:$('vbase').value,memory:$('vm').value,suffix:suffix(),byte_order:$('vb').value,register_order:$('vr').value,device:$('vd').value||'Device',period:Number($('vp').value||500)})});$('pv').innerHTML=`Нумерация: <b>${esc($('vbase').value)}</b><br>Ввод: <b>${$('va').value}</b> → <code>${esc(r.reference)}</code><br>Server offset: <b>${r.offset}</b>`}catch(e){$('pv').textContent=e.message}}
['vm','vt','vr','vb','vbase'].forEach(id=>$(id).onchange=previewVar);function openModal(){$('modal').style.display='grid';$('vbase').value=S.address_base||'1-based';previewVar()}function closeModal(){$('modal').style.display='none'}
async function addVar(){ $('ve').style.display='none';try{let r=await api('/api/variables',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('vn').value,address:Number($('va').value),base:$('vbase').value,memory:$('vm').value,suffix:suffix(),byte_order:$('vb').value,register_order:$('vr').value,device:$('vd').value,period:Number($('vp').value||500)})});closeModal();await load();alert(`Добавлено: ${r.reference}`)}catch(e){$('ve').textContent=e.message;$('ve').style.display='block'}}
async function out(){await api('/api/logout',{method:'POST'});location='/login'}load().catch(console.error);
</script></body></html>'''

