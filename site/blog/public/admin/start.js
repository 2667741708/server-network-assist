'use strict';
(async () => {
  const status=document.getElementById('cms-status');
  try {
    const response=await fetch('./config.json',{cache:'no-store'});
    if(!response.ok)throw Error('无法读取后台配置');
    const {config,oauthConfigured}=await response.json();
    const local=['localhost','127.0.0.1','[::1]'].includes(location.hostname);
    const github=new URLSearchParams(location.search).get('login')==='github';
    if(local){
      config.local_backend={url:'http://localhost:8081/api/v1'};
      config.display_url=location.origin+'/projects/';config.site_url=config.display_url;
    } else if(!github){
      let session;
      try {
        const check=await fetch('/projects/email-auth/session',{credentials:'same-origin',cache:'no-store'});
        if(check.ok)session=await check.json();
      }catch(error){console.warn('邮箱登录服务暂不可用');}
      if(session){
        if(!session.authenticated){location.replace('/projects/email-auth/login');return;}
        config.backend={name:'blog-email'};
        config.publish_mode='simple';
        const script=document.createElement('script');script.src='./email-backend.js';
        await new Promise((resolve,reject)=>{script.onload=resolve;script.onerror=()=>reject(Error('无法加载邮箱编辑器'));document.head.appendChild(script);});
      } else if(!oauthConfigured){throw Error('登录服务暂不可用，请稍后重试。');}
    } else if(!oauthConfigured){throw Error('GitHub 登录尚未配置，请使用邮箱登录。');}
    if(!window.CMS)throw Error('写作编辑器未加载');
    CMS.init({config});status.hidden=true;
  }catch(error){status.textContent=error.message;}
})();
