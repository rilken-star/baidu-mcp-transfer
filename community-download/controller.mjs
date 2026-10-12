import fs from 'node:fs';
import path from 'node:path';
import { Client } from './project/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from './project/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';
const base=path.dirname(new URL(import.meta.url).pathname);
const job=JSON.parse(fs.readFileSync(path.join(base,'job.json'),'utf8'));
const source=job.source;
const dest=job.destination;
for(const p of [source,dest])if(typeof p!=='string'||!p.startsWith('/')||p==='/'||path.posix.normalize(p)!==p||p.includes('\0'))throw Error('INVALID_JOB_PATH');
const excludedNames=new Set((job.exclude_directories??[]).map(x=>String(x).toLowerCase()));
const excludedExtensions=new Set((job.exclude_extensions??[]).map(x=>'.'+String(x).replace(/^\./,'').toLowerCase()));
function excluded(p){return path.posix.relative(source,p).split('/').some(x=>excludedNames.has(x.toLowerCase()));}
function excludedFile(p){return excluded(p)||excludedExtensions.has(path.posix.extname(p).toLowerCase());}
const smoke=process.argv.includes('--smoke');
let token=smoke?'offline-placeholder':JSON.parse(fs.readFileSync(0,'utf8')).access_token;
if(typeof token!=='string'||!token.trim()||token.length>8192)process.exit(2);
const transport=new StdioClientTransport({command:process.execPath,args:[path.join(base,'scoped-server.mjs')],cwd:base,stderr:'ignore',env:{PATH:path.join(base,'runtime/bin')+':/usr/bin:/bin',BAIDU_ACCESS_TOKEN:token,BAIDU_LOCAL_ROOT:dest}});
token=undefined;
const client=new Client({name:'baidu-directory-download',version:'1.0.0'});
const progress={phase:'initializing',files:0,bytes:0,verified_files:0,verified_bytes:0,pending:0,pid:process.pid};
let currentPart;
function save(){
 const p={...progress,updated_at:new Date().toISOString()};
 if(currentPart&&fs.existsSync(currentPart))p.current_bytes=fs.statSync(currentPart).size;
 fs.writeFileSync(path.join(base,'progress.tmp'),JSON.stringify(p,null,2),{mode:0o600});
 fs.renameSync(path.join(base,'progress.tmp'),path.join(base,'progress.json'));
}
async function call(name,args){
 const r=await client.callTool({name,arguments:args},undefined,{timeout:24*60*60*1000});
 if(r.isError)throw Error('MCP_TOOL_FAILED');
 const t=r.content.find(c=>c.type==='text');
 if(!t)throw Error('MCP_RESPONSE_MISSING');
 return JSON.parse(t.text);
}
let timer;
try{
 await client.connect(transport);
 const tools=await client.listTools();
 if(tools.tools.length!==2||!tools.tools.some(t=>t.name==='baidu_download_file'))throw Error('MCP_TOOLS_INVALID');
 if(smoke){
  if(!excludedFile(source+'/blocked.JPG')&&excludedExtensions.has('.jpg'))throw Error('EXTENSION_GUARD_FAILED');
  const denied=await client.callTool({name:'baidu_list_files',arguments:{dir:'/outside-authorized-scope'}});
  const deniedDownload=await client.callTool({name:'baidu_download_file',arguments:{fsId:'1',localPath:'/outside-authorized-scope',overwrite:false}});
  if(!denied.isError||!deniedDownload.isError)throw Error('SCOPE_GUARD_FAILED');
  console.log('MCP_HANDSHAKE_AND_SCOPED_DOWNLOAD_TOOLS_OK');await client.close();process.exit(0);
 }
 save();timer=setInterval(save,5000);
 progress.phase='listing';save();
 const files=[];const dirs=[source];const seen=new Set();
 while(dirs.length){
  const dir=dirs.shift();if(seen.has(dir))throw Error('DUPLICATE_DIRECTORY');seen.add(dir);
  for(let start=0;;start+=1000){
   const result=await call('baidu_list_files',{dir,start,limit:1000,order:'name',showempty:true});
   if(!Array.isArray(result.list))throw Error('INVALID_LIST');
   for(const f of result.list){
    if(typeof f.path!=='string'||path.posix.dirname(f.path)!==dir||path.posix.normalize(f.path)!==f.path)throw Error('INVALID_REMOTE_PATH');
    if(excluded(f.path))continue;
    if(f.isdir===1)dirs.push(f.path);
    else if(f.isdir===0&&Number.isSafeInteger(f.size)&&f.size>=0){if(!excludedFile(f.path))files.push({path:f.path,id:String(f.fs_id),size:f.size});}
    else throw Error('INVALID_REMOTE_METADATA');
   }
   if(result.list.length<1000)break;
  }
 }
 if(new Set(files.map(f=>f.path)).size!==files.length)throw Error('DUPLICATE_REMOTE_FILE');
 progress.files=files.length;progress.bytes=files.reduce((n,f)=>n+f.size,0);progress.pending=files.length;
 fs.mkdirSync(dest,{recursive:true});
 if(fs.realpathSync(dest)!==dest)throw Error('DESTINATION_SYMLINK');
 for(const dir of seen){const p=path.join(dest,path.posix.relative(source,dir));fs.mkdirSync(p,{recursive:true});if(fs.realpathSync(p)!==p)throw Error('DESTINATION_SYMLINK');}
 let missing=0;
 for(const f of files){const target=path.join(dest,path.posix.relative(source,f.path));
  if(fs.existsSync(target)){const st=fs.lstatSync(target);if(!st.isFile()||st.isSymbolicLink()||st.size!==f.size)throw Error('EXISTING_FILE_CONFLICT');}
  else {if(fs.existsSync(target+'.part'))throw Error('EXISTING_PARTIAL_FILE');missing+=f.size;}
 }
 const free=fs.statfsSync(dest);progress.missing_bytes=missing;progress.free_bytes=free.bavail*free.bsize;
 if(missing>progress.free_bytes-1024*1024*1024)throw Error('INSUFFICIENT_SPACE');
 progress.phase='downloading';save();
 for(const f of files){
  const target=path.join(dest,path.posix.relative(source,f.path));
  if(fs.existsSync(target)){
   const s=fs.lstatSync(target);if(!s.isFile()||s.isSymbolicLink()||s.size!==f.size)throw Error('EXISTING_FILE_CONFLICT');
  }else{
   currentPart=target+'.part';if(fs.existsSync(currentPart))throw Error('EXISTING_PARTIAL_FILE');
   const r=await call('baidu_download_file',{fsId:f.id,localPath:currentPart,overwrite:false});
   if(r.bytesWritten!==f.size||fs.statSync(currentPart).size!==f.size)throw Error('DOWNLOAD_SIZE_MISMATCH');
   fs.linkSync(currentPart,target);fs.unlinkSync(currentPart);currentPart=undefined;
  }
  progress.verified_files++;progress.verified_bytes+=f.size;progress.pending--;save();
 }
 for(const f of files){const s=fs.lstatSync(path.join(dest,path.posix.relative(source,f.path)));if(!s.isFile()||s.isSymbolicLink()||s.size!==f.size)throw Error('FINAL_SIZE_MISMATCH');}
 progress.phase='complete';save();
}catch(e){
 progress.phase='needs_attention';progress.error=/^[A-Z_]+$/.test(e?.message??'')?e.message:'TRANSFER_FAILED';save();process.exitCode=1;
}finally{if(timer)clearInterval(timer);await client.close().catch(()=>{});}
