from pathlib import Path

p = Path(__file__).resolve().parents[1] / 'src/server_network_assist/client_ui/client.js'
t = p.read_text(encoding='utf-8')
t = t.replace("state.online_service?.configured?'商业订阅已连接'", "state.active?.kind==='online'?'正在借用源网':state.online_service?.configured?'已读取订阅 · 尚未借网'")
t = t.replace("(connected||state.online_service?.configured?'ok'", "(connected||state.active?.kind==='online'?'ok'")
if "$('leave-network').onclick" not in t:
    t += "\n$('leave-network').onclick=()=>run(async()=>{const b=$('leave-network');b.disabled=true;try{const v=await api('network/leave',{});notice('已退出组网，原网络配置已恢复。'+(v.release_error?' 服务端租约暂未归还，将按短期租约到期。':''));await refresh();}finally{b.disabled=false}});\n"
if "$('app-policies').onclick" not in t:
    t += "\n$('app-policies').onclick=()=>run(async()=>{const v=await api('apps/policies');$('app-policy').replaceChildren(...v.policies.map(p=>{const o=document.createElement('option');o.value=p;o.textContent=p;return o}));});\n$('app-rule-form').onsubmit=e=>{e.preventDefault();run(async()=>{const v=await api('apps/rule',{process:$('app-process').value.trim(),policy:$('app-policy').value});notice('已设置 '+v.rule+'。请使用 Clash 规则模式。');});};\n"
p.write_text(t, encoding='utf-8')
