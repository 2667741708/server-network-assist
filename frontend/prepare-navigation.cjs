// Angular assets must be inside its workspace. Keep one authored source and
// refresh these generated copies before build/start, without extra dependencies.
const fs = require('node:fs');
const path = require('node:path');
const source = path.resolve(__dirname, '../src/server_network_assist/subscription_admin_ui');
const target = path.resolve(__dirname, 'public');
fs.mkdirSync(target, {recursive:true});
for (const name of ['smooth-navigation.js','smooth-navigation.css']) {
  fs.copyFileSync(path.join(source,name),path.join(target,name));
}
