'use strict';
(() => {
  const byId = id => document.getElementById(id);
  const status = byId('status');
  let challenge = '';
  async function call(path, body) {
    const response = await fetch('/projects/email-auth/' + path, {
      method: body ? 'POST' : 'GET', credentials: 'same-origin',
      headers: body ? {'Content-Type':'application/json'} : {},
      ...(body ? {body:JSON.stringify(body)} : {})});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '操作失败，请稍后重试');
    return data;
  }
  call('session').then(data => {
    if (data.authenticated) { location.replace('/projects/admin/'); return; }
    byId('google').hidden = !data.google_enabled;
    byId('google-pending').hidden = data.google_enabled;
  }).catch(() => { status.textContent = '无法连接登录服务，请稍后刷新。'; });
  byId('email-form').addEventListener('submit', async event => {
    event.preventDefault();
    const button = byId('send');button.disabled = true;status.textContent = '正在发送验证码…';
    try {
      const result = await call('otp/request', {email:byId('email').value});
      challenge = result.challenge;status.textContent = result.message;
      byId('code-form').hidden = false;byId('code').focus();
      let seconds=60;button.textContent=seconds+' 秒后重发';
      const timer=setInterval(()=>{seconds--;button.textContent=seconds+' 秒后重发';if(seconds<=0){clearInterval(timer);button.disabled=false;button.textContent='重新发送';}},1000);
    } catch(error) {status.textContent=error.message;button.disabled=false;}
  });
  byId('code-form').addEventListener('submit', async event => {
    event.preventDefault();const button=event.currentTarget.querySelector('button');button.disabled=true;
    try {await call('otp/verify',{challenge,code:byId('code').value.trim()});location.replace('/projects/admin/');}
    catch(error){status.textContent=error.message;button.disabled=false;}
  });
})();
