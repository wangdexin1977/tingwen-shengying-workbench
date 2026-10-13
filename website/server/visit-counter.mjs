const incrementSQL = `INSERT INTO site_visits (key,total) VALUES ('total',1)
ON CONFLICT(key) DO UPDATE SET total=site_visits.total+1
RETURNING total,started_at`;
const readSQL = "SELECT total,started_at FROM site_visits WHERE key='total'";
const jsonHeaders={'Cache-Control':'no-store','X-Robots-Tag':'noindex'};
const dbSession = env => {if(!env.DB)throw new Error('Missing counter database');return env.DB.withSession('first-primary');};
export async function handleContentRequest(request,env,contents){
 const url=new URL(request.url),method=request.method;
 if(url.pathname==='/api/visits'){
  if(!['GET','HEAD'].includes(method))return new Response(null,{status:405,headers:{Allow:'GET, HEAD',...jsonHeaders}});
  try{const row=await dbSession(env).prepare(readSQL).first();return Response.json({total:row?.total??0,started_at:row?.started_at??null,metric:'successful_content_requests',includes:['human','ai','search'],history_available:false},{headers:jsonHeaders});}
  catch{return Response.json({total:null,error:'temporarily_unavailable'},{status:503,headers:jsonHeaders});}
 }
 if(!['GET','HEAD'].includes(method))return null;
 let pathname=url.pathname;
 if(pathname==='/index.html')return new Response(null,{status:308,headers:{Location:'/', 'Cache-Control':'no-store'}});
 if(/^\/guide\/(product|technical)-\d{2}\.html$/.test(pathname)&&contents[pathname.slice(0,-5)])return new Response(null,{status:308,headers:{Location:pathname.slice(0,-5)+url.search, 'Cache-Control':'no-store'}});
 if(contents[pathname+'/'])return new Response(null,{status:308,headers:{Location:pathname+'/'+url.search, 'Cache-Control':'no-store'}});
 const entry=contents[pathname];
 if(!entry)return null;
 const counted=method==='GET'&&entry.counted;
 let row=null;
 if(counted){try{row=await dbSession(env).prepare(incrementSQL).first();}catch{console.error('Visit counter unavailable');}}
 let body=entry.body;
 if(entry.home)body=body.replace('>统计加载中</strong>',`>${row?new Intl.NumberFormat('zh-CN').format(row.total)+' 次':'暂不可用'}</strong>`);
 return new Response(method==='HEAD'?null:body,{status:entry.status||200,headers:{'Content-Type':entry.type,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
}
