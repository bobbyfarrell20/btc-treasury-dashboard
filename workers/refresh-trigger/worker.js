// Cloudflare Worker: lets the dashboard Refresh button start update.yml without any token in the page.
// Secret GITHUB_TOKEN (fine-grained, this repo only, Actions: read & write) is set with `wrangler secret put GITHUB_TOKEN`.
const ORIGIN='https://bobbyfarrell20.github.io', REPO='bobbyfarrell20/btc-treasury-dashboard';
const cors={'Access-Control-Allow-Origin':ORIGIN,'Access-Control-Allow-Methods':'POST, OPTIONS','Access-Control-Allow-Headers':'Content-Type'};
let last=0;
export default {async fetch(req,env){
  if(req.method==='OPTIONS')return new Response(null,{headers:cors});
  if(req.method!=='POST'||req.headers.get('Origin')!==ORIGIN)return new Response('forbidden',{status:403,headers:cors});
  if(Date.now()-last<120e3)return new Response(JSON.stringify({ok:true,throttled:true}),{headers:{...cors,'Content-Type':'application/json'}});
  const r=await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/update.yml/dispatches`,{method:'POST',
    headers:{Authorization:`Bearer ${env.GITHUB_TOKEN}`,Accept:'application/vnd.github+json','User-Agent':'dashboard-refresh','Content-Type':'application/json'},body:JSON.stringify({ref:'main'})});
  if(r.ok)last=Date.now();
  return new Response(JSON.stringify({ok:r.ok,status:r.status}),{status:r.ok?200:502,headers:{...cors,'Content-Type':'application/json'}});
}};
