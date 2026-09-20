import { spawn,spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
const python=process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python';
if(!existsSync(python)){console.error('Set up Python first: python -m venv .venv, then install backend/requirements.txt. See README.md.');process.exit(1);}
const development=process.argv.includes('--dev');
if(!development){
 for(const args of [['node_modules/typescript/bin/tsc','-b'],['node_modules/vite/bin/vite.js','build']]){
  const build=spawnSync(process.execPath,args,{stdio:'inherit'});
  if(build.status!==0){process.exit(build.status??1);}
 }
}
const children=[spawn(python,['-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8011'],{stdio:'inherit'}),spawn(process.execPath,['node_modules/vite/bin/vite.js',...(development?[]:['preview']),'--host','127.0.0.1'],{stdio:'inherit'})];
let stopping=false;
function stop(){if(stopping)return;stopping=true;for(const child of children)child.kill();}
for(const child of children){child.on('error',error=>{console.error(error.message);stop();process.exitCode=1;});child.on('exit',code=>{if(!stopping){stop();process.exitCode=code??1;}});}
process.on('SIGINT',stop);process.on('SIGTERM',stop);
console.log('\nPowerShift workspace: http://127.0.0.1:5180\nAPI documentation: http://127.0.0.1:8011/docs\n');
