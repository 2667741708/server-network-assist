'use strict';
(async () => {
  const status = document.getElementById('cms-status');
  try {
    const response = await fetch('./config.json');
    if (!response.ok) throw new Error('无法读取后台配置');
    const {config,oauthConfigured} = await response.json();
    const local = ['localhost','127.0.0.1','[::1]'].includes(location.hostname);
    if (local) {
      config.local_backend = {url:'http://localhost:8081/api/v1'};
      config.display_url = location.origin + '/projects/';
      config.site_url = location.origin + '/projects/';
    } else if (!oauthConfigured) {
      status.innerHTML = '写作后台尚未接通 GitHub 登录。请完成 OAuth 配置后重建后台配置：<br/>'
        + '1）在该域名创建 GitHub OAuth App 并配置回调 URL；<br/>'
        + '2）在 site/blog/.env 写入 BLOG_OAUTH_BASE_URL 并运行 npm run prepare:cms；<br/>'
        + '3）重新部署并刷新当前页面。';
      return;
    }
    if (!window.CMS) throw new Error('Decap 编辑器未加载');
    CMS.init({config});
    status.hidden = true;
  } catch (error) { status.textContent = error.message; }
})();
