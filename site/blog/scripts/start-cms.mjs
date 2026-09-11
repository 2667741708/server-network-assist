import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const repo = fileURLToPath(new URL('../../../', import.meta.url));
const server = fileURLToPath(new URL('../node_modules/decap-server/dist/index.js', import.meta.url));
const child = spawn(process.execPath, [server], {
  cwd: repo, stdio: 'inherit',
  env: { ...process.env, MODE: 'git', BIND_HOST: '127.0.0.1', GIT_REPO_DIRECTORY: process.env.GIT_REPO_DIRECTORY || repo },
});
child.on('exit', code => process.exit(code ?? 1));
