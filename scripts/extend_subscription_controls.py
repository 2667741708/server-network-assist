"""Extend the existing Tabler forms; exact replacement guards avoid layout rewrites."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
UI=ROOT/'src/server_network_assist/subscription_admin_ui'
def replace(text,old,new):
    assert text.count(old)==1,old[:100]
    return text.replace(old,new,1)
html=(UI/'subscriptions.html').read_text(encoding='utf8')
html=replace(html,'<legend>带宽上限</legend><div class="grid">','<legend>带宽上限</legend><div class="action-group mb-3"><button class="btn btn-outline-primary" type="button" data-speed-preset="unlimited" data-speed-form="service-form">解除上传 / 下载限速</button><button class="btn btn-outline-secondary" type="button" data-speed-preset="custom" data-speed-form="service-form">自定义速度</button></div><p class="text-secondary">速度单位为 Mbps，可分别设置上传和下载，支持 0.001 Mbps 精度。不限速表示服务端不设上限，实际带宽由源网出口决定。</p><div class="grid">')
html=replace(html,'data-service-days="180">从现在起 180 天</button>','data-service-days="180">半年 · 180 天</button><button class="btn btn-outline-secondary" type="button" data-service-days="365">一年 · 365 天</button><button class="btn btn-outline-secondary" type="button" data-service-never>永久</button>')
html=replace(html,'线路永不过期</label>','线路永不过期</label><p class="text-secondary">预设从当前时间起计算；也可填写任意到期日期（未来十年内）。永久仅表示线路不设到期时间，仍可停用或撤销。</p>')
start=html.index('<label class="form-label">下载上限 Mbps<input class="form-control" name="download"')
end=html.index('<label class="form-label">管理员密码或登录密钥 · 确认签发',start)
html=html[:start]+'''</div><fieldset class="form-fieldset"><legend>带宽上限</legend><div class="action-group mb-3"><button class="btn btn-outline-primary" type="button" data-speed-preset="unlimited" data-speed-form="generate-form">解除上传 / 下载限速</button><button class="btn btn-outline-secondary" type="button" data-speed-preset="custom" data-speed-form="generate-form">自定义速度</button></div><div class="grid"><label class="form-label">下载上限 Mbps<input class="form-control" name="download" type="number" min="0.001" max="1000000" step="0.001" value="50" required></label><label class="source form-check"><input class="form-check-input" name="unlimited_download" type="checkbox">下载不限速</label><label class="form-label">上传上限 Mbps<input class="form-control" name="upload" type="number" min="0.001" max="1000000" step="0.001" value="10" required></label><label class="source form-check"><input class="form-check-input" name="unlimited_upload" type="checkbox">上传不限速</label></div><p class="text-secondary">上传与下载可分别自定义，精度 0.001 Mbps；不限速时使用源出口可提供的带宽。</p></fieldset><fieldset class="form-fieldset"><legend>订阅有效期</legend><label class="form-label">有效期<select class="form-select" name="validity"><option value="30">一个月 · 30 天</option><option value="180">半年 · 180 天</option><option value="365">一年 · 365 天</option><option value="permanent">永久 · 不设到期时间</option><option value="custom">自定义到期日期</option></select></label><label id="generate-expiry-label" class="form-label" hidden>自定义到期日期<input class="form-control" name="expires_at" type="datetime-local" disabled></label><p class="text-secondary">预设从签发时起计算；自定义日期限未来十年内。流量额度每 30 天一个账期，永久套餐也可由管理员随时停用。</p></fieldset>'''+html[end:]
for quota in ['10 GB / 30 天','50 GB / 30 天','100 GB / 30 天','无限流量 / 30 天']:
    html=html.replace(quota,quota.replace(' / 30 天',' / 每 30 天'))
html=replace(html,'套餐从签发起有效 30 天；GB 按 GiB 计量。','服务期限按上方所选有效期；流量每 30 天一个账期，GB 按 GiB 计量。')
(UI/'subscriptions.html').write_text(html,encoding='utf8')
js=(UI/'subscriptions.js').read_text(encoding='utf8')
js=replace(js,"new Date(result.expires_at*1000).toLocaleString()","(result.expires_at===null?'永久 · 不设到期时间':new Date(result.expires_at*1000).toLocaleString())")
helpers="""
function inputSpeed(value,unlimited){
  if(unlimited)return null;
  const rate=Math.round(Number(value)*1e6);
  if(!Number.isSafeInteger(rate)||rate<1||rate>2**53-1)throw new Error('速度须大于零；解除限制请勾选不限速。');
  return rate;
}
function inputExpiry(value){
  const expiry=Math.floor(new Date(value).getTime()/1000),now=Date.now()/1000;
  if(!Number.isSafeInteger(expiry)||expiry<=now||expiry>now+3650*86400)throw new Error('请选择未来十年内的有效到期日期。');
  return expiry;
}
function syncGenerationInputs(){
  const f=$('generate-form');
  for(const [input,flag] of [['download','unlimited_download'],['upload','unlimited_upload']]){
    f.elements[input].disabled=f.elements[flag].checked;
    f.elements[input].required=!f.elements[flag].checked;
  }
  const custom=f.elements.validity.value==='custom';
  $('generate-expiry-label').hidden=!custom;
  f.elements.expires_at.disabled=!custom;f.elements.expires_at.required=custom;
}
for(const name of ['unlimited_download','unlimited_upload','validity'])$('generate-form').elements[name].onchange=syncGenerationInputs;
document.querySelectorAll('[data-speed-preset]').forEach(button=>button.onclick=()=>{
  const f=$(button.dataset.speedForm),unlimited=button.dataset.speedPreset==='unlimited';
  f.elements.unlimited_download.checked=unlimited;f.elements.unlimited_upload.checked=unlimited;
  if(!unlimited){for(const [name,fallback] of [[f.id==='generate-form'?'download':'download_mbps',50],[f.id==='generate-form'?'upload':'upload_mbps',10]])if(!f.elements[name].value)f.elements[name].value=fallback;}
  if(f.id==='generate-form')syncGenerationInputs();else syncServiceInputs();
});
syncGenerationInputs();
"""
js=replace(js,"$('generate-form').onsubmit=event=>",helpers+"\n$('generate-form').onsubmit=event=>")
js=replace(js,"download_bps:Number(form.get('download'))*1000000,upload_bps:Number(form.get('upload'))*1000000","download_bps:inputSpeed(form.get('download'),form.has('unlimited_download')),upload_bps:inputSpeed(form.get('upload'),form.has('unlimited_upload')),expires_at:form.get('validity')==='permanent'?null:form.get('validity')==='custom'?inputExpiry(form.get('expires_at')):Math.floor(Date.now()/1000)+Number(form.get('validity'))*86400")
(UI/'subscriptions.js').write_text(js,encoding='utf8')
dash=(UI/'dashboard.js').read_text(encoding='utf8')
dash=replace(dash,"document.querySelectorAll('[data-service-days]').forEach","document.querySelectorAll('[data-service-never]').forEach(b=>b.onclick=()=>{const f=$('service-form');f.elements.change_expiry.checked=true;f.elements.never_expires.checked=true;syncServiceInputs();});\ndocument.querySelectorAll('[data-service-days]').forEach")
dash=replace(dash,"download_bps:f.elements.unlimited_download.checked?null:Math.round(Number(f.elements.download_mbps.value)*1e6),upload_bps:f.elements.unlimited_upload.checked?null:Math.round(Number(f.elements.upload_mbps.value)*1e6)","download_bps:inputSpeed(f.elements.download_mbps.value,f.elements.unlimited_download.checked),upload_bps:inputSpeed(f.elements.upload_mbps.value,f.elements.unlimited_upload.checked)")
dash=replace(dash,"Math.floor(new Date(f.elements.expires_at.value).getTime()/1000)","inputExpiry(f.elements.expires_at.value)")
(UI/'dashboard.js').write_text(dash,encoding='utf8')
for name in ['subscriptions.html','subscriptions.js','dashboard.js']:
    (ROOT/'src/server_network_assist/ui'/name).write_bytes((UI/name).read_bytes())
print('Updated both existing admin UI asset copies.')
