// Scope matches the navigation change. The project has no ESLint configuration.
const {spawnSync}=require('node:child_process');
const path=require('node:path');
const sources=['subscriptions.js','dashboard.js','smooth-navigation.js'].map(name=>path.resolve(__dirname,'../src/server_network_assist/subscription_admin_ui',name));
sources.push(path.resolve(__dirname,'../src/server_network_assist/client_ui/client.js'));
for(const file of sources){
 const result=spawnSync(process.execPath,['--check',file],{stdio:'inherit'});
 if(result.status!==0)process.exit(result.status||1);
}
for(const [entry,args] of [
 ['typescript/lib/tsc.js',['--noEmit','--project','tsconfig.app.json']],
 ['prettier/bin/prettier.cjs',['--check','../src/server_network_assist/subscription_admin_ui/smooth-navigation.js','../src/server_network_assist/subscription_admin_ui/smooth-navigation.css','src/app/smooth-navigation.ts']]
]){
 const result=spawnSync(process.execPath,[require.resolve(entry),...args],{cwd:__dirname,stdio:'inherit'});
 if(result.status!==0)process.exit(result.status||1);
}
console.log('Navigation syntax, application types and shared-source formatting passed.');
