import { McpServer } from './project/node_modules/@modelcontextprotocol/sdk/dist/esm/server/mcp.js';
import { StdioServerTransport } from './project/node_modules/@modelcontextprotocol/sdk/dist/esm/server/stdio.js';
import { loadConfig } from './project/dist/config.js';
import { BaiduNetdiskClient } from './project/dist/baiduClient.js';
import { registerBaiduTools } from './project/dist/tools.js';
import path from 'node:path';
import fs from 'node:fs';
const base=path.dirname(new URL(import.meta.url).pathname);
const job=JSON.parse(fs.readFileSync(path.join(base,'job.json'),'utf8'));
const source=job.source;
const dest=job.destination;
for(const p of [source,dest])if(typeof p!=='string'||!p.startsWith('/')||p==='/'||path.posix.normalize(p)!==p||p.includes('\0'))throw Error('INVALID_JOB_PATH');
const excludedNames=new Set((job.exclude_directories??[]).map(x=>String(x).toLowerCase()));
const excludedExtensions=new Set((job.exclude_extensions??[]).map(x=>'.'+String(x).replace(/^\./,'').toLowerCase()));
function excluded(p){return path.posix.relative(source,p).split('/').some(x=>excludedNames.has(x.toLowerCase()));}
function excludedFile(p){return excluded(p)||excludedExtensions.has(path.posix.extname(p).toLowerCase());}
const destination=dest;
const config=loadConfig({...process.env,BAIDU_LOCAL_ROOT:destination});
const client=new BaiduNetdiskClient({config,tokenProvider:{getAccessToken:async()=>config.accessToken}});
const server=new McpServer({name:'scoped-baidu-download',version:'1.0.0'});
const known=new Map();
function inside(p,root){return p===root||p.startsWith(root+'/');}
registerBaiduTools({registerTool(name,definition,handler){
 if(!['baidu_list_files','baidu_download_file'].includes(name))return;
 server.registerTool(name,definition,async args=>{
  try{
   if(name==='baidu_list_files'){
    if(excluded(args.dir)||!inside(args.dir,source)||path.posix.normalize(args.dir)!==args.dir)throw Error('scope');
    const result=await handler(args);
    for(const c of result.content??[]){if(c.type!=='text')continue;const raw=JSON.parse(c.text);
     for(const f of raw.list??[]){if(f.isdir===0&&inside(f.path,source)&&path.posix.dirname(f.path)===args.dir)known.set(String(f.fs_id),f.path);}}
    return result;
   }
   const remote=known.get(String(args.fsId));
   if(!remote||excludedFile(remote)||args.overwrite||args.localPath!==path.join(destination,path.posix.relative(source,remote))+'.part')throw Error('scope');
   return await handler(args);
  }catch(e){
   const code=typeof e?.code==='number'||typeof e?.code==='string'?String(e.code).replace(/[^a-zA-Z0-9_-]/g,'').slice(0,40):'REQUEST_FAILED';
   return {isError:true,content:[{type:'text',text:JSON.stringify({error:code})}]};
  }
 });
}},{config,client});
await server.connect(new StdioServerTransport());
