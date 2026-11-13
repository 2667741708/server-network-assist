import React from 'react';

const ROOT = '/projects/email-auth';
const utf8 = value => new TextDecoder().decode(Uint8Array.from(atob(value.replace(/\s/g,'')), c => c.charCodeAt(0)));
const encoded = bytes => {
  let result='';
  for(let i=0;i<bytes.length;i+=16384) result+=String.fromCharCode(...bytes.subarray(i,i+16384));
  return btoa(result);
};

class ContinueLogin extends React.Component {
  componentDidMount() { this.props.onLogin({}); }
  render() {return React.createElement('p',null,'正在进入写作后台…');}
}

class EmailBackend {
  constructor(config) {
    this.mediaFolder = config.media_folder;
    this.versions = new Map();
    this.session = null;
  }
  isGitBackend() {return true;}
  async call(path, body) {
    if(body && !this.session) await this.authenticate();
    const response=await fetch(ROOT+path,{credentials:'same-origin',
      headers:body?{'Content-Type':'application/json','X-CSRF-Token':this.session.csrf}:{},
      ...(body?{method:'POST',body:JSON.stringify(body)}:{})});
    const result=await response.json();
    if(response.status===401){location.assign(ROOT+'/login');throw Error('登录已过期');}
    if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'操作失败，请稍后重试');
    return result;
  }
  async authenticate() {
    const session=await this.call('/session');
    if(!session.authenticated){location.assign(ROOT+'/login');throw Error('请登录');}
    this.session=session;
    return {...session.user};
  }
  restoreUser() {return this.authenticate();}
  authComponent() {return ContinueLogin;}
  async logout() {
    try {await this.call('/logout',{});} finally {this.session=null;location.assign(ROOT+'/login');}
  }
  getToken() {return Promise.resolve('');}
  async status() {
    const result=await this.call('/session');
    return {auth:{status:result.authenticated},api:{status:true,statusPage:''}};
  }
  async getEntry(path) {
    const value=await this.call('/content/file?path='+encodeURIComponent(path));
    this.versions.set(path,value.sha);
    return {file:{path,id:value.sha},data:utf8(value.content)};
  }
  async entriesByFolder(folder,extension='md',depth=1) {
    const {files}=await this.call('/content/files?folder='+encodeURIComponent(folder));
    const matches=files.filter(file=>file.path.endsWith('.'+extension)&&file.path.slice(folder.length+1).split('/').length<=depth);
    const entries=[];
    // Bound concurrent requests rather than sending one request for every article at once.
    for(let i=0;i<matches.length;i+=4)entries.push(...await Promise.all(matches.slice(i,i+4).map(file=>this.getEntry(file.path))));
    return entries;
  }
  entriesByFiles(files) {return Promise.all(files.map(file=>this.getEntry(file.path)));}
  async persistEntry(entry) {
    for(const asset of entry.assets||[])await this.persistMedia(asset);
    for(const file of entry.dataFiles){
      if(file.newPath && file.newPath!==file.path)throw Error('暂不支持直接重命名文章，请新建后迁移内容。');
      const value=await this.call('/content/save',{path:file.path,sha:this.versions.get(file.path)||null,
        content:encoded(new TextEncoder().encode(file.raw))});
      this.versions.set(file.path,value.sha);
    }
  }
  async getMedia(folder=this.mediaFolder) {
    const {files}=await this.call('/content/files?folder='+encodeURIComponent(folder));
    return files.map(file=>({id:file.sha,path:file.path,name:file.path.split('/').pop(),size:file.size,displayURL:{path:file.path,id:file.sha}}));
  }
  async getMediaFile(path) {
    const item=await this.call('/content/file?path='+encodeURIComponent(path));
    this.versions.set(path,item.sha);
    const bytes=Uint8Array.from(atob(item.content.replace(/\s/g,'')),c=>c.charCodeAt(0));
    const name=path.split('/').pop();
    const extension=name.split('.').pop().toLowerCase();
    const type=({png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',gif:'image/gif',webp:'image/webp'})[extension];
    const file=new File([bytes],name,{type});const url=URL.createObjectURL(file);
    return {id:item.sha,path,name,size:bytes.length,file,displayURL:url,url};
  }
  async getMediaDisplayURL(display) {return typeof display==='string'?display:(await this.getMediaFile(display.path)).url;}
  async persistMedia(asset) {
    const path=asset.path.replace(/^\//,'');
    const file=asset.fileObj||asset.file;
    const content=encoded(new Uint8Array(await file.arrayBuffer()));
    const result=await this.call('/content/save',{path,sha:this.versions.get(path)||null,content});
    this.versions.set(path,result.sha);
    return {id:result.sha,path,name:file.name,size:file.size,displayURL:URL.createObjectURL(file)};
  }
  async deleteFiles(paths) {
    for(const path of paths){
      if(!this.versions.get(path)){
        const item=await this.call('/content/file?path='+encodeURIComponent(path));this.versions.set(path,item.sha);
      }
      await this.call('/content/delete',{path,sha:this.versions.get(path)});this.versions.delete(path);
    }
  }
}

window.CMS.registerBackend('blog-email',EmailBackend);
