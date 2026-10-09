const state = { data:null, page:'dashboard', selectedDevice:null, portTests:{}, diagnostics:null, selectedOnlineDevice:null, onlineItems:[], editingItem:null, editingDevice:null, poll:null, rwItem:null, browserNode:null, browserRoot:'' };
const $ = id => document.getElementById(id);
const TYPE_LABELS = {
  Bool:'Логический (Bool)',
  UInt16:'Беззнаковое 16 бит (UInt16)',
  Int16:'Знаковое 16 бит (Int16)',
  UInt32:'Беззнаковое 32 бит (UInt32)',
  Int32:'Знаковое 32 бит (Int32)',
  UInt64:'Беззнаковое 64 бит (UInt64)',
  Int64:'Знаковое 64 бит (Int64)',
  Float:'Число с плавающей точкой (Float)',
  Double:'Число двойной точности (Double)',
  ByteArray:'Массив байтов (ByteArray)'
};
const TYPES_BY_AREA = [
  'UInt16','Int16','UInt32','Int32','UInt64','Int64','Float','Double','ByteArray'
];

function esc(v) { return String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function fmt(v) { if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(6))); if (typeof v === 'boolean') return v ? 'TRUE' : 'FALSE'; if (v && typeof v === 'object') { if (v.encoding==='base64') return `Base64: ${v.value||''}`; try{return JSON.stringify(v);}catch{return String(v);} } return String(v ?? '—'); }
function showNotice(msg, kind='ok') { const el=$('notice'); el.textContent=msg; el.className=`notice ${kind}`; setTimeout(()=>el.classList.add('hidden'),4000); }
async function api(url, options={}) { const r=await fetch(url,{cache:'no-store',credentials:'same-origin',headers:{'Content-Type':'application/json',...(options.headers||{})},...options}); if(r.status===401){ location.href='/'; throw new Error('Сеанс завершён'); } const d=await r.json().catch(()=>({})); if(!r.ok) throw new Error(d.detail||`HTTP ${r.status}`); return d; }
async function reloadConfigAfterChange(message) {
  try {
    await api('/api/reload', {method:'POST'});
    showNotice(`${message}. Конфигурация шлюза применена`, 'ok');
    setTimeout(refresh, 800);
  } catch (e) {
    showNotice(`${message}. Файл сохранён, но шлюз не применил изменения: ${e.message}. Запусти шлюз и нажми «Перезагрузить конфигурацию».`, 'error');
  }
}

function setPage(page){
  state.page=page;
  document.querySelectorAll('.page').forEach(x=>x.classList.toggle('active',x.id===`page-${page}`));
  document.querySelectorAll('.nav-btn').forEach(x=>x.classList.toggle('active',x.dataset.page===page));
  if(page==='items') refreshItems(); if(page==='online') refreshOnline(); if(page==='browser' && !state.browserNode) refreshBrowser(); if(page==='diagnostics') refreshDiagnostics();
  if(page==='config') renderConfigFiles();
}
document.querySelectorAll('.nav-btn').forEach(b=>b.onclick=()=>setPage(b.dataset.page));
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>b.closest('dialog').close());

function serverPill(s){ const el=$('server-status'); const on=s?.running; el.textContent=on?`Сервер: RUNNING${s.pid?` (${s.pid})`:''}`:'Сервер: STOPPED'; el.className=`status-pill ${on?'good':'bad'}`; $('card-server').textContent=on?'RUNNING':'STOPPED'; $('card-server').className=on?'good-text':'bad-text'; $('card-server-detail').textContent=s?.pid?`PID ${s.pid} · ${s.source||s.mode}`:'—'; }
function opcPill(o){ const el=$('opc-status'); const on=o?.connected; el.textContent=on?'OPC UA: подключено':'OPC UA: нет связи'; el.className=`status-pill ${on?'good':'bad'}`; $('card-opc').textContent=on?'CONNECTED':'OFFLINE'; $('card-opc').className=on?'good-text':'bad-text'; $('card-opc-detail').textContent=o?.endpoint||'—'; }
function renderDashboard(){ const d=state.data; serverPill(d.server); opcPill(d.opcua); $('card-ports').textContent=d.ports_count; $('card-devices').textContent=d.devices.length; $('card-items').textContent=d.item_count; const sv=d.server||{}; $('dash-cpu').textContent=sv.cpu_percent!=null?`${Number(sv.cpu_percent).toFixed(1)} %`:'—'; $('dash-cpu-detail').textContent=sv.pid?`PID ${sv.pid}`:'процесс не запущен'; $('dash-memory').textContent=sv.memory_mb!=null?`${sv.memory_mb} MB`:'—'; $('dash-memory-detail').textContent=sv.memory_percent!=null?`${Number(sv.memory_percent).toFixed(1)} % RSS`:'RSS процесса'; $('dash-system-cpu').textContent=sv.system_cpu_percent!=null?`${Number(sv.system_cpu_percent).toFixed(1)} %`:'—'; $('dash-system-cpu-detail').textContent=sv.cpu_count?`${sv.cpu_count} logical CPU`:'система'; $('dash-system-memory').textContent=sv.system_memory_percent!=null?`${Number(sv.system_memory_percent).toFixed(1)} %`:'—'; $('dash-system-memory-detail').textContent=sv.system_memory_used_mb!=null?`${sv.system_memory_used_mb} / ${sv.system_memory_total_mb} MB`:'—'; $('dash-threads').textContent=sv.threads!=null?String(sv.threads):'—'; $('card-cpu').textContent=sv.cpu_percent!=null?`${Number(sv.cpu_percent).toFixed(1)} %`:'—'; $('card-cpu-detail').textContent=sv.pid?`PID ${sv.pid}`:'процесс не запущен'; $('card-memory').textContent=sv.memory_mb!=null?`${sv.memory_mb} MB`:'—'; $('card-memory-detail').textContent=sv.memory_percent!=null?`${Number(sv.memory_percent).toFixed(1)} % RSS`:'RSS процесса'; $('card-cpu-meter').style.width=`${Math.min(100,Math.max(0,Number(sv.cpu_percent||0)))}%`; $('card-memory-meter').style.width=`${Math.min(100,Math.max(0,Number(sv.memory_percent||0)))}%`;  $('detail-config').textContent=d.config_file; $('detail-exe').textContent=d.executable||'не задан'; $('detail-mode').textContent=d.run_mode; $('detail-endpoint').textContent=d.opcua.endpoint; $('server-subtitle').textContent=`${d.run_mode} · ${d.config_file}`; $('files-list').innerHTML=(d.files||[]).map(f=>`<div class="file-row"><span>${esc(f.relative)}</span><small>${esc(f.path)}</small></div>`).join(''); renderDeviceSelect(); renderDevices(); }
function renderDeviceSelect(){ const s=$('items-device-select'); s.innerHTML=(state.data?.devices||[]).map(d=>`<option value="${esc(d.name)}">${esc(d.name)}</option>`).join(''); if(state.selectedDevice && state.data.devices.some(d=>d.name===state.selectedDevice)) s.value=state.selectedDevice; else state.selectedDevice=state.data.devices[0]?.name||null; if(state.selectedDevice) s.value=state.selectedDevice; $('items-context').textContent=state.selectedDevice||'Устройств нет'; const os=$('online-device-select'); if(os){os.innerHTML=s.innerHTML; if(state.selectedDevice) os.value=state.selectedDevice;} }
function renderDevices(){
  const body=$('devices-body');
  body.innerHTML=state.data.devices.length?state.data.devices.map(d=>{
    const p=d.port||{}; const count=d._item_count ?? '—';
    return `<tr><td><strong>${esc(d.name)}</strong><div class="muted">${esc(d.itemfile||'')}</div></td><td>${esc(d.PortName||'')}</td><td>${esc(p.Type||'')} ${esc(p.Host||'')}<br>${esc(p.Port||'')}</td><td>${esc(d.ModbusUnit||'')}</td><td>${esc(d.EnableDevice||'')}</td><td>${count}</td><td class="row-actions"><button data-edit-device="${esc(d.name)}">Изменить</button><button class="danger" data-del-device="${esc(d.name)}">Удалить</button></td></tr>`;
  }).join(''):`<tr><td colspan="7" class="empty">Устройств нет</td></tr>`;
  body.querySelectorAll('[data-edit-device]').forEach(b=>b.onclick=()=>openDeviceEdit(state.data.devices.find(x=>x.name===b.dataset.editDevice)));
  body.querySelectorAll('[data-del-device]').forEach(b=>b.onclick=()=>deleteDevice(b.dataset.delDevice));
}
async function refresh(){
  try { state.data=await api('/api/state');
    state.data.devices.forEach(d=>d._item_count=d.item_count||0);
    renderDashboard(); if(state.page==='items') await refreshItems(); if(state.page==='diagnostics') await refreshDiagnostics();
  } catch(e){ if(!location.pathname.includes('login')) showNotice(e.message,'error'); }
}
async function refreshItems(){ if(!state.selectedDevice){ $('items-body').innerHTML=`<tr><td colspan="10" class="empty">Устройств нет</td></tr>`; return; } $('items-device-select').value=state.selectedDevice; try { const d=await api(`/api/items?device=${encodeURIComponent(state.selectedDevice)}`); renderItems(d.items||[]); } catch(e){ showNotice(e.message,'error'); } }
function renderItems(items){
  const body=$('items-body');
  body.innerHTML=items.length?items.map(i=>{
    const good=!i.error&&String(i.status).toLowerCase().includes('good');
    const area=`${i.area}x`;
    const fn=i.write_function ? `${i.read_function} / ${i.write_function}` : `${i.read_function} / —`;
    const order=i.register_count>1 ? (i.register_order==='reverse'?'Обратный':'Прямой') : '—';
    return `<tr><td><strong>${esc(i.name)}</strong></td><td>${area}</td><td>${esc(i.register)}${i.end_register?`–${esc(i.end_register)}`:''}</td><td>${esc(TYPE_LABELS[i.data_type]||i.data_type||'—')}</td><td>${esc(i.period_ms)} мс</td><td>${fn}</td><td>${order}</td><td class="value">${esc(i.error?'—':fmt(i.value))}</td><td class="${good?'good-text':'bad-text'}">${esc(i.error||i.status)}</td><td class="row-actions"><button data-edit-item="${esc(i.name)}">Изменить</button><button class="danger" data-del-item="${esc(i.name)}">Удалить</button></td></tr>`;
  }).join(''):`<tr><td colspan="8" class="empty">Переменных нет</td></tr>`;
  body.querySelectorAll('[data-edit-item]').forEach(b=>b.onclick=()=>openItemEdit(items.find(x=>x.name===b.dataset.editItem)));
  body.querySelectorAll('[data-del-item]').forEach(b=>b.onclick=()=>deleteItem(b.dataset.delItem));
}

function fillSelect(id, values, selected){ const el=$(id); const seen=new Set(); const unique=values.filter(v=>{ const value=String(v.value??v); if(seen.has(value)) return false; seen.add(value); return true; }); el.innerHTML=unique.map(v=>`<option value="${esc(v.value??v)}">${esc(v.label??v)}</option>`).join(''); if(selected!==undefined && selected!==null) el.value=selected; }
function updateTypeOptions(){
  const area=Number($('item-area').value);
  const types=(area===0 || area===1 ? ['Bool', ...TYPES_BY_AREA] : TYPES_BY_AREA);
  const current=$('item-data-type').value;
  const fallback=(area===0 || area===1) ? 'Bool' : 'UInt16';
  fillSelect('item-data-type',types.map(t=>({value:t,label:TYPE_LABELS[t]||t})),types.includes(current)?current:fallback);
  const isArray=$('item-data-type').value==='ByteArray';
  $('item-end-wrap').classList.toggle('hidden',!isArray);
  updateOrderOptions();
}
function updateOrderOptions(){
  const type=$('item-data-type').value;
  const multi=['Int32','UInt32','Int64','UInt64','Float','Double'].includes(type);
  const el=$('item-register-order');
  el.disabled=!multi;
  if(!multi) el.value='normal';
}
function openItemAdd(){ state.editingItem=null; fillSelect('item-device-input',state.data.devices.map(x=>x.name),state.selectedDevice); $('item-dialog-title').textContent='Добавить переменную'; $('item-old-name').value=''; $('item-name-input').value=''; $('item-area').value='4'; $('item-address-mode').value='one_based'; $('item-register').value='1'; $('item-period').value='500'; $('item-end-register').value=''; $('item-message-id').value=''; $('item-register-order').value='normal'; $('item-byte-order').value='standard'; updateTypeOptions(); updateItemAddressMode(); $('item-device-input').disabled=false; $('item-dialog').showModal(); }
function openItemEdit(i){ state.editingItem=i; fillSelect('item-device-input',state.data.devices.map(x=>x.name),i.device); $('item-device-input').disabled=true; $('item-dialog-title').textContent='Изменить переменную'; $('item-old-name').value=i.name; $('item-name-input').value=i.name; $('item-area').value=i.area||4; $('item-address-mode').value='one_based'; $('item-register').value=i.register||1; $('item-byte-order').value=i.byte_order||'standard'; $('item-period').value=i.period_ms||500; $('item-end-register').value=i.end_register||''; $('item-message-id').value=i.message_id||''; $('item-register-order').value=i.register_order||'normal'; updateTypeOptions(); $('item-data-type').value=i.data_type||'UInt16'; $('item-register-order').value=i.register_order||'normal'; $('item-end-wrap').classList.toggle('hidden',i.data_type!=='ByteArray'); updateOrderOptions(); updateItemAddressMode(); $('item-dialog').showModal(); }
async function saveItem(e){
  e.preventDefault();
  const message = state.editingItem ? 'Переменная изменена' : 'Переменная добавлена';
  try {
    const payload = {device:$('item-device-input').value,old_name:state.editingItem?.name,name:$('item-name-input').value.trim(),area:Number($('item-area').value),address_mode:$('item-address-mode').value,register:Number($('item-register').value),data_type:$('item-data-type').value,period_ms:Number($('item-period').value),register_order:$('item-register-order').value,byte_order:$('item-byte-order').value,end_register:$('item-data-type').value==='ByteArray'?Number($('item-end-register').value):null,message_id:$('item-message-id').value.trim()};
    if (state.editingItem) await api('/api/items',{method:'PUT',body:JSON.stringify(payload)});
    else await api('/api/items',{method:'POST',body:JSON.stringify(payload)});
    $('item-device-input').disabled = false;
    $('item-dialog').close();
    await refresh();
    setPage('items');
    await reloadConfigAfterChange(message);
  } catch (e) { showNotice(e.message, 'error'); }
}
async function deleteItem(name){
  if (!confirm(`Удалить переменную «${name}»?`)) return;
  try {
    await api('/api/items',{method:'DELETE',body:JSON.stringify({device:state.selectedDevice,name})});
    await refresh();
    await reloadConfigAfterChange('Переменная удалена');
  } catch (e) { showNotice(e.message, 'error'); }
}

function setDeviceDefaults(){ $('device-name').value=''; $('device-port-name').value=''; $('device-port-type').value='TCP'; $('device-host').value='127.0.0.1'; $('device-port-number').value='502'; $('device-port-timeout').value='2000'; $('device-port-enable').value='1'; $('device-port-repeat').value='2'; const defs={unit:'1',enable:'1',repeat:'2',restore:'10000',period:'500',request:'5000','max-coils':'2000','max-write-coils':'2000','max-di':'2000','max-ir':'120','max-hr':'120','max-write-reg':'120'}; for(const [id,v] of Object.entries(defs)) $('device-'+id).value=v; }
function openDeviceAdd(){ state.editingDevice=null;$('device-dialog-title').textContent='Добавить устройство';$('device-old-name').value='';setDeviceDefaults();$('device-dialog').showModal(); }
function openDeviceEdit(d){ const p=d.port||{}; state.editingDevice=d;$('device-dialog-title').textContent='Изменить устройство';$('device-old-name').value=d.name;$('device-name').value=d.name;$('device-port-name').value=d.PortName||'';$('device-port-type').value=p.Type||'TCP';$('device-host').value=p.Host||'';$('device-port-number').value=p.Port||'';$('device-port-timeout').value=p.Timeout||'';$('device-port-enable').value=p.Enable||'1';$('device-port-repeat').value=p.RepeatCount||'2'; for(const [id,key] of [['unit','ModbusUnit'],['enable','EnableDevice'],['repeat','RepeatCount'],['restore','RestoreTimeout'],['period','DefaultPeriod'],['request','RequestTimeout'],['max-coils','MaxReadCoils'],['max-write-coils','MaxWriteMultipleCoils'],['max-di','MaxReadDiscreteInputs'],['max-ir','MaxReadInputRegisters'],['max-hr','MaxReadHoldingRegisters'],['max-write-reg','MaxWriteMultipleRegisters']]) $('device-'+id).value=d[key]||''; $('device-dialog').showModal(); }
async function saveDevice(e){
  e.preventDefault();
  const message = state.editingDevice ? 'Устройство и порт изменены' : 'Устройство, порт и CSV созданы';
  try {
    const p = {name:$('device-name').value.trim(),PortName:$('device-port-name').value.trim(),PortEnable:$('device-port-enable').value,PortRepeatCount:$('device-port-repeat').value,PortType:$('device-port-type').value,Host:$('device-host').value.trim(),Port:$('device-port-number').value,PortTimeout:$('device-port-timeout').value,EnableDevice:$('device-enable').value,ModbusUnit:$('device-unit').value,RepeatCount:$('device-repeat').value,RestoreTimeout:$('device-restore').value,DefaultPeriod:$('device-period').value,RequestTimeout:$('device-request').value,MaxReadCoils:$('device-max-coils').value,MaxWriteMultipleCoils:$('device-max-write-coils').value,MaxReadDiscreteInputs:$('device-max-di').value,MaxReadInputRegisters:$('device-max-ir').value,MaxReadHoldingRegisters:$('device-max-hr').value,MaxWriteMultipleRegisters:$('device-max-write-reg').value};
    if (state.editingDevice) {
      p.old_name = state.editingDevice.name;
      await api('/api/devices',{method:'PUT',body:JSON.stringify(p)});
    } else {
      await api('/api/devices',{method:'POST',body:JSON.stringify(p)});
      state.selectedDevice = p.name;
    }
    $('device-dialog').close();
    await refresh();
    setPage('devices');
    await reloadConfigAfterChange(message);
  } catch (e) { showNotice(e.message, 'error'); }
}
async function deleteDevice(name){
  if (!confirm(`Удалить устройство «${name}» и его порт?`)) return;
  try {
    await api(`/api/devices?name=${encodeURIComponent(name)}`,{method:'DELETE'});
    if (state.selectedDevice === name) state.selectedDevice = null;
    await refresh();
    await reloadConfigAfterChange('Устройство удалено');
  } catch (e) { showNotice(e.message, 'error'); }
}

async function serverAction(action){if(!confirm(action==='start'?'Запустить modbusua?':action==='stop'?'Остановить modbusua?':'Перезапустить modbusua?'))return;try{await api(`/api/server/${action}`,{method:'POST'});showNotice(action==='start'?'Сервер запущен':action==='stop'?'Сервер остановлен':'Сервер перезапущен');setTimeout(refresh,800);}catch(e){showNotice(e.message,'error');}}
async function reloadServer(){if(!confirm('Применить изменения и отправить ReloadConfig?'))return;try{await api('/api/reload',{method:'POST'});showNotice('ReloadConfig отправлен');setTimeout(refresh,800);}catch(e){showNotice(e.message,'error');}}
async function logout(){await api('/api/auth/logout',{method:'POST'});location.href='/';}


function showOnlineNotice(msg, kind='ok'){ const el=$('online-notice'); if(!el) return; el.textContent=msg; el.className=`notice ${kind}`; setTimeout(()=>el.classList.add('hidden'),4500); }
function onlineGood(i){ return !i.error && String(i.status||'').toLowerCase().includes('good'); }
function renderOnline(items){
  state.onlineItems=items||[];
  const body=$('online-body');
  const writes=state.onlineItems.filter(i=>i.can_write||i.write_function!==null).length;
  $('online-item-count').textContent=state.onlineItems.length;
  $('online-write-count').textContent=writes;
  const o=state.data?.opcua||{};
  $('online-opc-status').textContent=o.connected?'CONNECTED':'OFFLINE';
  $('online-opc-status').className=o.connected?'good-text':'bad-text';
  $('online-opc-endpoint').textContent=o.endpoint||'—';
  $('online-opc-namespace').textContent=o.namespace_index??'—';
  body.innerHTML=state.onlineItems.length?state.onlineItems.map((i,idx)=>{
    const good=onlineGood(i); const writable=i.can_write||i.write_function!==null;
    return `<tr><td><strong>${esc(i.name)}</strong><div class="muted">${esc(i.node_id||'')}</div></td><td>${esc(i.area)}x ${esc(i.register)}${i.end_register?`–${esc(i.end_register)}`:''}</td><td>${esc(TYPE_LABELS[i.data_type]||i.data_type||i.variant_type||'—')}</td><td class="value">${esc(i.error?'—':fmt(i.value))}</td><td class="${good?'good-text':'bad-text'}">${esc(i.error||i.status||'—')}</td><td>${esc(i.timestamp||'—')}</td><td>${writable?`<button data-online-write="${idx}">Записать</button>`:'<span class="muted">только чтение</span>'}</td><td><button data-online-open="${idx}">Открыть</button></td></tr>`;
  }).join(''):`<tr><td colspan="8" class="empty">Для выбранного устройства нет переменных</td></tr>`;
  body.querySelectorAll('[data-online-open]').forEach(b=>b.onclick=()=>openRw(state.onlineItems[Number(b.dataset.onlineOpen)]));
  body.querySelectorAll('[data-online-write]').forEach(b=>b.onclick=()=>openRw(state.onlineItems[Number(b.dataset.onlineWrite)]));
}
async function refreshOnline(){
  if(!state.selectedDevice){ $('online-body').innerHTML='<tr><td colspan="8" class="empty">Устройств нет</td></tr>'; return; }
  const sel=$('online-device-select'); if(sel) sel.value=state.selectedDevice;
  try { const d=await api(`/api/items?device=${encodeURIComponent(state.selectedDevice)}`); renderOnline(d.items||[]); state.data.opcua=d.opcua||state.data.opcua; } catch(e){ showOnlineNotice(e.message,'error'); }
}
async function readOnlineAll(){
  if(!state.selectedDevice) return;
  try { const d=await api('/api/opcua/read-all',{method:'POST',body:JSON.stringify({device:state.selectedDevice})}); renderOnline(d.items||[]); state.data.opcua=d.opcua||state.data.opcua; showOnlineNotice('Чтение выполнено'); } catch(e){ showOnlineNotice(e.message,'error'); }
}
function openRw(item){
  state.rwItem=item;
  $('rw-device').textContent=item.device||'—'; $('rw-name').textContent=item.name||item.display_name||'—'; $('rw-node').textContent=item.node_id||'—'; $('rw-type').textContent=TYPE_LABELS[item.data_type]||item.variant_type||item.data_type||'—';
  $('rw-current').textContent=item.error?'—':fmt(item.value); $('rw-status').textContent=item.status||'—'; $('rw-timestamp').textContent=item.timestamp||'—';
  $('rw-value').value=(item.value!==null&&item.value!==undefined&&typeof item.value!=='object')?String(item.value):'';
  const writable=item.can_write||item.write_function!==null;
  $('rw-write-btn').disabled=!writable;
  $('rw-help').textContent=writable?`Modbus ${item.area}x: чтение FC${item.read_function}${item.write_function?` · запись FC${item.write_function}`:''}. Порядок регистров: ${item.register_order==='reverse'?'обратный':'прямой'}.`:'Эта переменная доступна только для чтения.';
  $('rw-dialog').showModal();
}
async function rwRead(){
  const i=state.rwItem; if(!i) return;
  try{ const d=await api(`/api/opcua/read?device=${encodeURIComponent(i.device)}&name=${encodeURIComponent(i.name)}`); state.rwItem={...i,...d}; openRw(state.rwItem); renderOnline(state.onlineItems.map(x=>x.name===i.name?{...x,...d}:x)); showOnlineNotice('Значение обновлено'); }catch(e){showOnlineNotice(e.message,'error');}
}
async function rwWrite(){
  const i=state.rwItem; if(!i) return;
  if(!(i.can_write||i.write_function!==null)) return;
  try{ const d=await api('/api/opcua/write',{method:'POST',body:JSON.stringify({device:i.device,name:i.name,value:$('rw-value').value})}); state.rwItem={...i,...(d.result||{})}; $('rw-current').textContent=fmt(d.result?.value); $('rw-status').textContent=d.result?.status||'—'; $('rw-timestamp').textContent=d.result?.timestamp||'—'; renderOnline(state.onlineItems.map(x=>x.name===i.name?{...x,...d.result}:x)); showOnlineNotice('Значение записано'); }catch(e){showOnlineNotice(e.message,'error');}
}
function browserNodeLabel(n){ return `${esc(n.display_name||n.browse_name||n.node_id)}<span class="muted">${esc(n.node_class||'')}</span>`; }
async function loadBrowserNode(nodeId, into=$('browser-tree')){
  try{
    const d=await api(`/api/opcua/browse?node_id=${encodeURIComponent(nodeId)}`);
    if (into === $('browser-tree')) state.browserRoot=nodeId;
    into.innerHTML=`<div class="browser-path"><strong>${esc(d.node.display_name||d.node.node_id)}</strong><code>${esc(d.node.node_id)}</code></div>${(d.children||[]).map((n,idx)=>{const expandable=['object','organizingobject'].includes(String(n.node_class||'').toLowerCase())||Number(n.children_count)>0; return `<div class="browser-row"><button class="browser-expand" data-browse="${idx}" data-node="${esc(n.node_id)}" ${expandable?'':'disabled'}>${expandable?'▸':'•'}</button><button class="browser-select" data-select-node="${idx}">${browserNodeLabel(n)}</button><div id="branch-${idx}" class="browser-branch hidden"></div></div>`;}).join('')||'<div class="empty">Дочерних узлов нет</div>'}`;
    const children=d.children||[];
    into.querySelectorAll('[data-select-node]').forEach(b=>b.onclick=()=>selectBrowserNode(children[Number(b.dataset.selectNode)]));
    into.querySelectorAll('[data-browse]').forEach(b=>b.onclick=()=>toggleBrowserBranch(b,children[Number(b.dataset.browse)].node_id));
    if(state.browserNode) selectBrowserNode(state.browserNode); else if(d.node) selectBrowserNode(d.node);
  }catch(e){into.innerHTML=`<div class="empty bad-text">${esc(e.message)}</div>`;}
}
async function toggleBrowserBranch(button,nodeId){ const branch=button.parentElement.querySelector('.browser-branch'); if(!branch.classList.contains('hidden')){branch.classList.add('hidden');button.textContent='▸';return;} button.textContent='⌄'; branch.classList.remove('hidden'); branch.innerHTML='<div class="muted loading">Загрузка…</div>'; await loadBrowserNode(nodeId,branch); }
function selectBrowserNode(n){ const node=(typeof n==='string'?null:n); if(node){state.browserNode=node.node_id;$('browser-node-title').textContent=node.display_name||node.browse_name||node.node_id;$('browser-node-id').textContent=node.node_id;$('browser-node-class').textContent=node.node_class||'—';$('browser-node-type').textContent=node.variant_type||'—';$('browser-node-status').textContent=node.status||'—';$('browser-node-value').textContent=node.error?'—':fmt(node.value); $('browser-read-btn').disabled=String(node.node_class||'').toLowerCase().indexOf('variable')<0; return; } }
async function readBrowserSelected(){ if(!state.browserNode)return; try{const d=await api(`/api/opcua/node?node_id=${encodeURIComponent(state.browserNode)}`);selectBrowserNode(d);}catch(e){showNotice(e.message,'error');} }
function refreshBrowser(){ if($('browser-tree')) loadBrowserNode(state.browserRoot||''); }

function showDiagNotice(msg, kind='ok'){ const el=$('diag-notice'); if(!el) return; el.textContent=msg; el.className=`notice ${kind}`; el.classList.remove('hidden'); setTimeout(()=>el.classList.add('hidden'),5000); }
function fmtUptime(sec){ if(sec==null)return '—'; let s=Math.max(0,Number(sec)); const d=Math.floor(s/86400); s%=86400; const h=Math.floor(s/3600); s%=3600; const m=Math.floor(s/60); const ss=Math.floor(s%60); return `${d?d+' д ':''}${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(ss).padStart(2,'0')}`; }
function renderDiagnostics(d){
  state.diagnostics=d;
  const s=d.server||{}, o=d.opcua||{}, v=d.validation||{};
  $('diag-process').textContent=s.running?'RUNNING':'STOPPED'; $('diag-process').className=s.running?'good-text':'bad-text';
  $('diag-process-detail').textContent=s.pid?`PID ${s.pid} · ${s.source||s.mode}`:'—';
  $('diag-cpu').textContent=s.cpu_percent!=null?`${Number(s.cpu_percent).toFixed(1)} %`:'—'; $('diag-memory').textContent=s.memory_mb!=null?`${s.memory_mb} MB`:'—';
  $('diag-system-cpu').textContent=s.system_cpu_percent!=null?`${Number(s.system_cpu_percent).toFixed(1)} %`:'—'; $('diag-system-memory').textContent=s.system_memory_percent!=null?`${Number(s.system_memory_percent).toFixed(1)} %`:'—'; $('diag-system-memory-detail').textContent=s.system_memory_used_mb!=null?`${s.system_memory_used_mb} / ${s.system_memory_total_mb} MB`:'—';
  $('diag-uptime').textContent=fmtUptime(s.uptime_s); $('diag-threads').textContent=s.threads!=null?`${s.threads} потоков`:'—';
  $('diag-opc').textContent=o.connected?'CONNECTED':'OFFLINE'; $('diag-opc').className=o.connected?'good-text':'bad-text'; $('diag-opc-detail').textContent=o.endpoint||'—';
  $('diag-config-state').textContent=v.ok?'OK':'ОШИБКИ'; $('diag-config-state').className=v.ok?'good-text':'bad-text'; $('diag-config-detail').textContent=`файлов ${v.files_checked??'—'} · устройств ${v.devices??'—'} · tags ${v.items??'—'}`;
  const db=$('diag-devices-body');
  db.innerHTML=(d.devices||[]).length?(d.devices||[]).map(x=>{const p=x.port||{}; return `<tr><td><strong>${esc(x.name)}</strong><div class="muted">${esc(x.itemfile||'')}</div></td><td>${esc(x.PortName||'—')}</td><td>${esc(p.Type||'')} ${esc(p.Host||'')} : ${esc(p.Port||'')}</td><td>${esc(x.ModbusUnit||'—')}</td><td>${esc(x.EnableDevice||'—')}</td><td>${esc(x.item_count??0)}</td><td><span class="port-test-state ${state.portTests[x.PortName||'']?.ok?'good-text':state.portTests[x.PortName||'']?'bad-text':'muted'}">${esc(state.portTests[x.PortName||'']?.label||'не тестировался')}</span><button data-diag-port="${esc(x.PortName||'')}">Тест порта</button></td></tr>`}).join(''):`<tr><td colspan="7" class="empty">Устройств нет</td></tr>`;
  db.querySelectorAll('[data-diag-port]').forEach(b=>b.onclick=()=>{setPage('diagnostics'); $('mb-port').value=b.dataset.diagPort; syncModbusForm(); $('mb-address').focus();});
  fillSelect('mb-port',(d.devices||[]).map(x=>x.PortName).filter(Boolean));
  const files=d.logs||[]; fillSelect('log-file-select',files.map(x=>({value:x.path,label:`${x.name} · ${Math.round(Number(x.size||0)/1024)} KB`})));
  if(!files.length)$('log-view').textContent='Каталог log не найден или журналов нет.';
  else if(!$('log-file-select').value)$('log-file-select').value=files[0].path;
}
async function refreshDiagnostics(){ try{const d=await api('/api/diagnostics'); renderDiagnostics(d); syncModbusForm();}catch(e){showDiagNotice(e.message,'error');} }
async function validateConfig(){ try{const d=await api('/api/config/validate',{method:'POST'}); const msg=d.ok?`Конфигурация корректна: файлов ${d.files_checked}, устройств ${d.devices}, портов ${d.ports}, items ${d.items}`:`Ошибок конфигурации: ${d.errors.length}`; showDiagNotice(msg,d.ok?'ok':'error'); if(state.diagnostics)renderDiagnostics({...state.diagnostics,validation:d});}catch(e){showDiagNotice(e.message,'error');} }
async function backupConfig(){ try{const d=await api('/api/config/backup',{method:'POST'}); showDiagNotice(`Backup создан: ${d.files.length} файл(ов)`);}catch(e){showDiagNotice(e.message,'error');} }
async function runModbusTest(e){
  e.preventDefault(); const badge=$('mb-result-state'); badge.textContent='Выполняется…'; badge.className='status-pill'; $('mb-result').textContent='Ожидание ответа…';
  try{ const list=$('mb-values').value.trim(); const payload={port_name:$('mb-port').value,function:Number($('mb-function').value),address:Number($('mb-address').value),address_mode:$('mb-address-mode').value,count:Number($('mb-count').value),unit:Number($('mb-unit').value),timeout_ms:Number($('mb-timeout').value),data_type:$('mb-data-type').value,register_order:$('mb-register-order').value,byte_order:$('mb-byte-order').value,value:Number($('mb-function').value)===5 ? $('mb-bool-value').value : $('mb-value').value,values:list?list.split(',').map(x=>x.trim()):null,baudrate:Number($('mb-baudrate').value),parity:$('mb-parity').value.charAt(0),stopbits:Number($('mb-stopbits').value),bytesize:Number($('mb-bytesize').value)}; const d=await api('/api/modbus/test',{method:'POST',body:JSON.stringify(payload)}); renderModbusResult(d); state.portTests[payload.port_name]={ok:true,label:`OK · ${d.result.latency_ms} ms`}; if(state.diagnostics)renderDiagnostics(state.diagnostics); }
  catch(e){ badge.textContent='ERROR'; badge.className='status-pill bad'; $('mb-result').innerHTML=`<div><strong class="bad-text">Ошибка:</strong> ${esc(e.message)}</div>`; const pn=$('mb-port').value; if(pn)state.portTests[pn]={ok:false,label:'ошибка'}; if(state.diagnostics)renderDiagnostics(state.diagnostics); }
}
function renderModbusResult(d){ const r=d.result||{}; $('mb-result-state').textContent=r.ok?'OK':'ERROR'; $('mb-result-state').className=`status-pill ${r.ok?'good':'bad'}`; $('mb-result').innerHTML=`<div class="result-cell"><span>Порт</span><strong>${esc(d.port?.name||'—')}</strong></div><div class="result-cell"><span>FC</span><strong>${esc(r.function??'—')}</strong></div><div class="result-cell"><span>Unit</span><strong>${esc(r.unit??'—')}</strong></div><div class="result-cell"><span>Адрес</span><strong>${esc(r.address??'—')} <small>(protocol ${esc(r.address_zero_based??'—')})</small></strong></div><div class="result-cell"><span>Задержка</span><strong>${esc(r.latency_ms??'—')} ms</strong></div><div class="result-cell"><span>Регистры / bits</span><pre>${esc(JSON.stringify(r.registers??r.values??r.written_registers??r.written??[],null,2))}</pre></div><div class="result-cell"><span>Декодированное значение</span><pre>${esc(fmt(r.decoded??'—'))}</pre></div>${r.response?`<div class="result-cell"><span>Ответ</span><pre>${esc(r.response)}</pre></div>`:''}`; }
function syncModbusForm(){ const name=$('mb-port').value; const device=(state.diagnostics?.devices||[]).find(x=>(x.PortName||'')===name); const p=device?.port; if(!p)return; const t=String(p.Type||'TCP').toUpperCase(); $('mb-timeout').value=p.Timeout||$('mb-timeout').value; $('mb-unit').value=device.ModbusUnit||$('mb-unit').value; $('mb-serial-wrap').classList.toggle('hidden',!(t==='RTU'||t==='SERIAL'||t==='RS485'||t==='ASCII')); }
function updateModbusFunctionUi(){ const fc=Number($('mb-function').value); const writeSingle=[5,6].includes(fc); const multiWrite=[15,16].includes(fc); const hasWrite=writeSingle||multiWrite; $('mb-value-label').classList.toggle('hidden',!hasWrite); $('mb-values-label').classList.toggle('hidden',!multiWrite); $('mb-value').classList.toggle('hidden',fc===5); $('mb-bool-value').classList.toggle('hidden',fc!==5); $('mb-value').disabled=fc===5; $('mb-bool-value').disabled=fc!==5; $('mb-value').type=fc===6?'number':'text'; $('mb-value').step=fc===6?'any':''; $('mb-value').placeholder=fc===6?'Например: 485':'HEX / число'; $('mb-value-help').textContent=fc===5?'Выберите TRUE или FALSE.':fc===6?'Введите целое/вещественное значение регистра.':fc===15||fc===16?'Для нескольких значений используйте поле ниже.':'Для записи выберите функцию FC05/06/15/16.'; $('mb-register-order').disabled=!['UInt32','Int32','UInt64','Int64','Float','Double'].includes($('mb-data-type').value); $('mb-byte-order').disabled=!['UInt16','Int16','UInt32','Int32','UInt64','Int64','Float','Double','ByteArray'].includes($('mb-data-type').value); $('mb-count').value=multiWrite ? Math.max(1,Number($('mb-count').value)) : Math.min(Number($('mb-count').value)||1,2000); }

async function openLog(){ const path=$('log-file-select').value; if(!path)return; try{const d=await api(`/api/logs?path=${encodeURIComponent(path)}&lines=400`); $('log-view').textContent=d.content||'Пустой журнал'; $('log-view').scrollTop=$('log-view').scrollHeight;}catch(e){showDiagNotice(e.message,'error');} }

function renderConfigFiles(){ const box=$('config-files-list'); const files=state.data?.files||[]; box.innerHTML=files.map((f,i)=>`<button class="file-select ${i===0?'active':''}" data-file="${esc(f.path)}"><strong>${esc(f.relative)}</strong><small>${esc(f.path)}</small></button>`).join(''); box.querySelectorAll('[data-file]').forEach(b=>b.onclick=()=>viewConfig(b.dataset.file,b)); if(files[0])viewConfig(files[0].path,box.querySelector('[data-file]')); }
async function viewConfig(path, button){document.querySelectorAll('.file-select').forEach(x=>x.classList.remove('active'));button?.classList.add('active');try{const d=await api(`/api/config/file?path=${encodeURIComponent(path)}`);$('config-view-title').textContent=d.path;$('config-view').textContent=d.content;}catch(e){$('config-view').textContent=e.message;}}

$('items-device-select').onchange=()=>{state.selectedDevice=$('items-device-select').value; if($('online-device-select')) $('online-device-select').value=state.selectedDevice;$('items-context').textContent=state.selectedDevice;refreshItems();};
$('item-area').onchange=updateTypeOptions;$('item-data-type').onchange=()=>{ $('item-end-wrap').classList.toggle('hidden',$('item-data-type').value!=='ByteArray'); updateOrderOptions(); }; 
$('add-item-btn').onclick=openItemAdd;$('item-form').onsubmit=saveItem;$('add-device-btn').onclick=openDeviceAdd;$('device-form').onsubmit=saveDevice;

$('online-device-select').onchange=()=>{state.selectedDevice=$('online-device-select').value; state.selectedOnlineDevice=state.selectedDevice; refreshOnline();};
$('online-read-all-btn').onclick=readOnlineAll;$('online-refresh-btn').onclick=refreshOnline;
$('rw-read-btn').onclick=rwRead;$('rw-write-btn').onclick=rwWrite;
$('browser-root-btn').onclick=()=>{state.browserNode=null;state.browserRoot='' ;refreshBrowser();};$('browser-refresh-btn').onclick=refreshBrowser;$('browser-read-btn').onclick=readBrowserSelected;

$('diag-refresh-btn').onclick=refreshDiagnostics;$('diag-validate-btn').onclick=validateConfig;$('diag-backup-btn').onclick=backupConfig;
$('config-validate-btn').onclick=validateConfig;$('config-backup-btn').onclick=backupConfig;
$('modbus-test-form').onsubmit=runModbusTest;document.querySelectorAll('input[name=mb-address-mode-radio]').forEach(r=>r.onchange=()=>{$('mb-address-mode').value=document.querySelector('input[name=mb-address-mode-radio]:checked').value;}); $('mb-clear-btn').onclick=()=>{ $('mb-result').innerHTML='<div class="empty">Результат очищен.</div>'; $('mb-result-state').textContent='Готов'; $('mb-result-state').className='status-pill'; }; $('mb-port').onchange=syncModbusForm;$('mb-function').onchange=updateModbusFunctionUi;$('mb-data-type').onchange=updateModbusFunctionUi;
$('log-open-btn').onclick=openLog;
$('start-btn').onclick=()=>serverAction('start');$('stop-btn').onclick=()=>serverAction('stop');$('restart-btn').onclick=()=>serverAction('restart');$('reload-btn').onclick=reloadServer;$('logout-btn').onclick=logout;

async function boot(){ try { await api('/api/auth/me'); await refresh(); } catch(e) { if(location.pathname!=='/') location.href='/'; } state.poll=setInterval(()=>{refresh().catch(()=>{});},3000); }
boot();

function updateItemAddressMode(){const zero=$('item-address-mode').value==='zero_based';$('item-register').min=zero?'0':'1';$('item-register').max=zero?'65535':'65536';$('item-end-register').min=zero?'0':'1';$('item-end-register').max=zero?'65535':'65536';if(Number($('item-register').value)<Number($('item-register').min))$('item-register').value=$('item-register').min;if($('item-end-register').value!==''&&Number($('item-end-register').value)<Number($('item-end-register').min))$('item-end-register').value=$('item-end-register').min;}
$('item-address-mode').addEventListener('change',updateItemAddressMode);
