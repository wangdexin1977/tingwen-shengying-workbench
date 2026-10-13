import hashlib,json,re,concurrent.futures,urllib.request,xml.etree.ElementTree as ET,subprocess,tempfile
from pathlib import Path
from html.parser import HTMLParser
root=Path(__file__).resolve().parent.parent
origin=json.loads((root/'site.json').read_text())['origin']
urls=[e.text for e in ET.fromstring((root/'static-output/sitemap.xml').read_text()).iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
class Page(HTMLParser):
 def __init__(self):super().__init__();self.h1=0;self.title='';self.intitle=False;self.description='';self.robots='';self.canonical=''
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if tag=='h1':self.h1+=1
  if tag=='title':self.intitle=True
  if tag=='meta' and a.get('name')=='description':self.description=a.get('content','')
  if tag=='meta' and a.get('name')=='robots':self.robots=a.get('content','')
  if tag=='link' and a.get('rel')=='canonical':self.canonical=a.get('href','')
 def handle_endtag(self,tag):
  if tag=='title':self.intitle=False
 def handle_data(self,t):
  if self.intitle:self.title+=t
def get(url,ua='Mozilla/5.0 TingwenPublicVerification/1.0'):
 with tempfile.TemporaryDirectory() as tmp:
  f=Path(tmp)/'body';h=Path(tmp)/'headers'
  result=subprocess.run(['curl','-sS','-L','--max-time','25','-A',ua,'-D',str(h),'-o',str(f),'-w','%{http_code}\n%{url_effective}',url],capture_output=True,text=True,check=True)
  lines=result.stdout.splitlines();headers={}
  for line in h.read_text().splitlines():
   if line.startswith('HTTP/'):headers={}
   elif ':' in line:
    k,v=line.split(':',1);headers[k.strip().title()]=v.strip()
  return f.read_bytes(),headers,lines[1],int(lines[0])
def check(url):
 body,headers,final,status=get(url);text=body.decode('utf-8');p=Page();p.feed(text)
 assert status==200 and p.h1==1,(url,status,p.h1)
 assert 'wzqy2019' in text and 'noindex' not in p.robots,(url,'missing contact or noindex')
 assert 'noindex' not in headers.get('X-Robots-Tag','').lower(),(url,headers)
 assert p.canonical==url and final==url,(url,p.canonical,final)
 assert p.title and p.description,(url,'metadata missing')
 for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>',text,re.S):json.loads(raw)
 return {'url':url,'status':status,'final_url':final,'h1':p.h1,'title':p.title,'description':p.description,'canonical':p.canonical,'x_robots_tag':headers.get('X-Robots-Tag'),'visible_contact':True}
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
 pages=[]
 for future in concurrent.futures.as_completed([pool.submit(check,u) for u in urls]):
  pages.append(future.result())
  if len(pages)%10==0:print('已验证公开页面：'+str(len(pages)),flush=True)
assert len(set(p['title'] for p in pages))==len(pages),'duplicate titles'
assert len(set(p['description'] for p in pages))==len(pages),'duplicate descriptions'
key=(root/'seo/indexnow-key.txt').read_text().strip()
resources=[]
for route in ['/robots.txt','/sitemap.xml','/llms.txt','/llms-full.txt','/product-facts.json','/'+key+'.txt','/assets/social.png',*['/assets/cases/'+c['id']+'.jpg' for c in json.loads((root/'src/cases.json').read_text())],'/assets/yunjian.mp3','/downloads/product.html','/downloads/technical.html','/downloads/shengying-specification.md','/downloads/shengying-specification.html','/downloads/shengying-api.json','/specification-source.json','/assets/diagrams/shengying-architecture.svg']:
 body,h,final,status=get(origin+route);assert status==200 and len(body)>0,(route,status,len(body))
 if route=='/'+key+'.txt':assert body.decode().strip()==key
 if route=='/downloads/shengying-specification.md':assert hashlib.sha256(body).digest()==hashlib.sha256((root.parent/'docs/产品规格说明书.md').read_bytes()).digest()
 if route=='/downloads/shengying-api.json':assert json.loads(body)==json.loads((root.parent/'docs/接口与参数清单.json').read_text())
 resources.append({'path':route,'status':status,'bytes':len(body),'content_type':h.get('Content-Type')})
bots=[]
for ua in ['Googlebot','bingbot','OAI-SearchBot']:
 for route in ['/robots.txt','/facts/','/contact/']:
  body,h,final,status=get(origin+route,ua);assert status==200 and len(body)>0
  if route!='/robots.txt':assert b'wzqy2019' in body
  bots.append({'user_agent':ua,'path':route,'status':status})
report={'passed':True,'anonymous_access':True,'indexed':False,'page_count':len(pages),'unique_titles':True,'unique_descriptions':True,'pages':pages,'resources':resources,'user_agent_checks':bots,'web_search_fetch_limit':'Web search open工具目前未能直接获取新域名；匿名HTTP及三种爬虫UA请求通过，不代表实际爬虫访问或收录。'}
(root/'qa/public-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'passed':True,'anonymous_pages':len(pages),'resources':len(resources),'crawler_ua_requests':len(bots),'redirects':[(p['url'],p['final_url']) for p in pages if p['url']!=p['final_url']]},ensure_ascii=False))
