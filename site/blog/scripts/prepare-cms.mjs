import { access } from 'node:fs/promises';
import { constants } from 'node:fs';
import { mkdir, readdir, copyFile, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = fileURLToPath(new URL('../', import.meta.url));
const admin = path.join(root, 'public/admin');

async function readEnvFile(filePath) {
  try {
    await access(filePath, constants.F_OK);
  } catch {
    return {};
  }
  const raw = await readFile(filePath, 'utf8');
  const env = {};
  for (const line of raw.split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const equalIndex = t.indexOf('=');
    if (equalIndex < 0) continue;
    const key = t.slice(0, equalIndex).trim();
    const value = t.slice(equalIndex + 1).trim().replace(/^['"]|['"]$/g, '');
    if (key) env[key] = value;
  }
  return env;
}

const envFromFile = await readEnvFile(path.join(root, '.env'));
const env = (name, fallback) => {
  const value = process.env[name] ?? envFromFile[name];
  if (typeof value === 'string' && value.trim()) return value.trim();
  return fallback;
};

const oauthBaseUrl = env('BLOG_OAUTH_BASE_URL', '');
const oauthEndpoint = env('BLOG_OAUTH_ENDPOINT', 'auth');
const repo = env('BLOG_REPO', '2667741708/server-network-assist');
const branch = env('BLOG_BRANCH', 'main');
const siteUrl = env('BLOG_SITE_URL', 'https://whm12.art/projects/');
const displayUrl = env('BLOG_DISPLAY_URL', 'https://whm12.art/projects/');

await copyFile(path.join(root,'public/images/windows-proxy-diagnosis.png'), path.join(root,'public/network-assist-og.png'));
await mkdir(path.join(admin, 'vendor'), { recursive: true });
const dist = path.join(root, 'node_modules/decap-cms/dist');
for (const name of await readdir(dist)) {
  if (name.endsWith('.decap-cms.js') || ['decap-cms.js','decap-cms.js.LICENSE.txt','cms.css'].includes(name) || name.endsWith('.wasm')) {
    await copyFile(path.join(dist,name), path.join(admin,'vendor',name));
  }
}
const catalog = JSON.parse(await readFile(path.join(root,'../projects/catalog.json'),'utf8'));
const templates = [
  ['tutorials','实操教程','## 你将完成什么\n\n## 环境与机器分工\n\n## 操作步骤\n\n## 预期结果与验证\n\n## 常见问题与恢复\n'],
  ['introductions','项目介绍','## 谁需要这个项目\n\n## 一个实际例子\n\n## 如何开始\n\n## 当前边界\n'],
  ['retrospectives','故障复盘','## 现象与影响\n\n## 排查证据\n\n## 根因与修复\n\n## 修复前后验证\n\n## 后续改进\n'],
  ['experiments','实验记录','## 问题与假设\n\n## 对照条件\n\n## 方法\n\n## 实测结果\n\n## 局限与复现材料\n'],
  ['explanations','原理解说','## 一个具体问题\n\n## 最小例子\n\n## 实现机制\n\n## 边界与选择\n'],
];
const config = {
  load_config_file: false, locale:'zh_Hans',
  backend: {
    name:'github',
    repo,
    branch,
    ...(oauthBaseUrl
      ? { base_url: oauthBaseUrl, auth_endpoint: oauthEndpoint }
      : {}),
  },
  publish_mode:'editorial_workflow',
  media_folder:'site/blog/public/images/uploads', public_folder:'/projects/images/uploads',
  site_url: siteUrl, display_url: displayUrl,
  collections:templates.map(([name,label,body])=>({name,label,folder:`site/blog/src/content/posts/${name}`,create:true,slug:'{{slug}}',preview_path:`projects/posts/${name}/{{slug}}/`,fields:[
    {name:'project',label:'关联项目',widget:'select',options:catalog.projects.map(p=>({label:p.name,value:p.name}))},
    {name:'articleType',label:'文章类型',widget:'hidden',default:label},
    {name:'title',label:'标题',widget:'string'},
    {name:'description',label:'摘要：读者会得到什么',widget:'text'},
    {name:'author',label:'作者',widget:'string',default:'2667741708'},
    {name:'pubDatetime',label:'发布日期',widget:'datetime',format:'YYYY-MM-DDTHH:mm:ss[Z]',picker_utc:true},
    {name:'modDatetime',label:'修改日期',widget:'datetime',format:'YYYY-MM-DDTHH:mm:ss[Z]',picker_utc:true,required:false},
    {name:'draft',label:'保留为草稿（正式构建不显示）',widget:'boolean',default:true,hint:'准备正式发布时关闭此项；随后仍需通过编辑工作流发布。'},
    {name:'featured',label:'首页推荐',widget:'boolean',default:false},
    {name:'testedVersion',label:'适用版本',widget:'string',required:false},
    {name:'verifiedAt',label:'实际验证日期与范围',widget:'string',required:false},
    {name:'tags',label:'标签',widget:'list',default:[]},
    {name:'ogImage',label:'分享图片（正文图片请在正文中插入并添加图注）',widget:'image',required:false},
    {name:'body',label:'正文与图片',widget:'markdown',default:body},
  ]})),
};
await writeFile(path.join(admin,'config.json'),JSON.stringify({oauthConfigured:!!oauthBaseUrl,config},null,2));
console.log('Prepared local Decap assets and five article collections.');
