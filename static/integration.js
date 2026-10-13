// 同一稿件、独立音色库、统一音视频任务；原有控件及操作完整保留。
const speechControls = {speed:'speechSpeed', interval_silence:'speechSilence', seed:'speechSeed',
  max_text_tokens:'speechTokens', temperature:'speechTemperature', omnivoice_num_steps:'speechSteps',
  omnivoice_guidance_scale:'speechGuidance'};
const speechKey='shengying-speech-profiles';
let currentBackend=$('localBackend').value;
function saveSpeechProfile(){
  try{const profiles=JSON.parse(localStorage.getItem(speechKey)||'{}');profiles[currentBackend]=speechParameters();localStorage.setItem(speechKey,JSON.stringify(profiles));}catch{}
  $('speechSpeedValue').textContent=Number($('speechSpeed').value).toFixed(2)+'×';
  rememberAudioOptions();
}
function restoreSpeechProfile(){
  let profile={};try{profile=JSON.parse(localStorage.getItem(speechKey)||'{}')[$('localBackend').value]||{};}catch{}
  const defaults={speed:1,interval_silence:250,seed:42,max_text_tokens:120,temperature:.8,omnivoice_num_steps:32,omnivoice_guidance_scale:2};
  for(const [key,id] of Object.entries(speechControls))$(id).value=profile[key]??defaults[key];
  currentBackend=$('localBackend').value;
  $('speechSpeedValue').textContent=Number($('speechSpeed').value).toFixed(2)+'×';
  document.querySelectorAll('[data-index-param]').forEach(el=>el.hidden=!currentBackend.startsWith('IndexTTS'));
  document.querySelectorAll('[data-omni-param]').forEach(el=>el.hidden=!currentBackend.includes('OmniVoice'));
  $('omniExpression').disabled=currentBackend==='IndexTTS 2.5'||currentBackend==='Fish Audio S2 Pro'||currentBackend==='CosyVoice 3';
  if($('omniExpression').disabled)$('omniExpression').value='follow_reference';
}
for(const id of Object.values(speechControls))$(id).addEventListener('input',saveSpeechProfile);
$('localBackend').onchange=()=>{restoreSpeechProfile();rememberAudioOptions();};
const savedBackend=audioPreferences().omnivoice_settings?.backend;
if([...$('localBackend').options].some(o=>o.value===savedBackend))$('localBackend').value=savedBackend;
restoreSpeechProfile();

let manualTitle=false;
const originalTextInput=$('text').oninput;
$('text').oninput=()=>{
  originalTextInput();
  audioSourceUI();
  if(!manualTitle||!$('title').value.trim()){
    $('title').value=$('text').value.replace(/\s+/g,' ').trim().slice(0,30);
    $('title').oninput();
  }
};
$('title').addEventListener('input',()=>manualTitle=true);
$('showTitle').onchange=()=>updateTitlePreview();

// 首屏只显示日常操作；标题、字幕细调与音乐保留在可展开面板。
const left=document.querySelector('#mainWorkspace > div');
const dock=document.createElement('div');dock.id='generateDock';
const generate=$('generate');dock.append(generate);const dockHint=document.createElement('small');dockHint.id='generateHint';dock.append(dockHint);left.append(dock);
const queueDraftActions=document.createElement('div');queueDraftActions.className='row';
for(const [id,label] of [['queueSaveDraft','保存待制作项目'],['queueOneClick','一键生成当前稿件 MP4']]){const button=document.createElement('button');button.id=id;button.textContent=label;queueDraftActions.append(button);}dock.append(queueDraftActions);
const tools=$('mp4Tools');
const advanced=document.createElement('details');advanced.id='videoAdvanced';
const summary=document.createElement('summary');summary.textContent='高级画面设置 · 标题样式 / 字幕 / 背景音乐';advanced.append(summary);
let node=tools.querySelector('h3:not(.live-preview h3)');
// 通过“视频标题样式”标题定位，保持实时预览和成品入口常驻。
node=[...tools.children].find(el=>el.tagName==='H3'&&el.textContent==='视频标题样式');
const tail=tools.querySelector('#showVideoFiles').parentElement;
const subtitleLabel=$('subtitleEnabled').parentElement;
while(node&&node!==tail){const next=node.nextElementSibling;advanced.append(node);node=next;}
tools.insertBefore($('musicPanel'),tail);tools.insertBefore(subtitleLabel,tail);tools.insertBefore(advanced,tail);
const titleLabel=$('showTitle').parentElement;const titleNodes=[];
for(let el=titleLabel;el&&el!==document.querySelector('label[for=text]');el=el.nextElementSibling)titleNodes.push(el);
for(const el of titleNodes)tools.insertBefore(el,tools.querySelector('.live-preview'));
tools.querySelector('h2').textContent='画面预览与设置';

function creationMode(long,restore=false){
  $('shortMode').classList.toggle('active',!long);$('longMode').classList.toggle('active',long);
  $('shortMode').setAttribute('aria-pressed',String(!long));$('longMode').setAttribute('aria-pressed',String(long));
  if(!restore){$('audioOnly').checked=false;if(long){$('audioProvider').value='omnivoice';rememberAudioOptions();}}
  $('generateHint').textContent=long?'长篇音频、配图与背景音乐，共同生成完整 MP4。':'生成后先试听并确认画面，再合成 MP4。';
  updateAudioOnly();
  try{localStorage.setItem('shengying-creation-mode',long?'long':'short');}catch{}
}
function updateAudioOnly(){
  const only=$('audioOnly').checked;$('pictureStage').hidden=only;
  $('generate').textContent=only?'生成 MP3 · 开始朗读':'生成 MP3 与画面素材';
  $('generateHint').textContent=only?'音频完成后可下载，或添加照片继续制作 MP4。':'生成后先试听并确认画面，再合成 MP4。';
}
const originalSourceUI=sourceUI;
sourceUI=()=>{originalSourceUI();updateAudioOnly();};
const originalAudioSourceUI=audioSourceUI;
function updateCloneVoiceSummary(){
  const choice=$('omniVoice').selectedOptions[0];
  $('currentCloneVoice').textContent=$('omniVoice').value?choice.text.split(' · ')[0]:'请选择克隆音色';
  $('cloneModelSummary').textContent=$('localBackend').value+' · '+$('omniExpression').selectedOptions[0].text;
  $('voiceParametersSummary').textContent='语音参数 · '+Number($('speechSpeed').value).toFixed(2)+'× · 停顿 '+$('speechSilence').value+' ms';
}
audioSourceUI=()=>{originalAudioSourceUI();updateCloneVoiceSummary();$('textHint').textContent=$('audioProvider').value==='omnivoice'?'本机单次合成最多 100000 个有效字符，长文自动分批；更长书籍可在专业工作区用文档队列分卷制作。':'全文提交，长文自动分段；文案框内可上下滚动。';};
for(const id of ['speechSpeed','speechSilence'])$(id).addEventListener('input',updateCloneVoiceSummary);
audioSourceUI();
$('shortMode').onclick=()=>creationMode(false);$('longMode').onclick=()=>creationMode(true);$('audioOnly').onchange=updateAudioOnly;
try{creationMode(localStorage.getItem('shengying-creation-mode')==='long',true);}catch{creationMode(false);}
$('voiceSearch').oninput=()=>{const query=$('voiceSearch').value.trim().toLowerCase();for(const option of $('omniVoice').options)option.hidden=Boolean(option.value&&!option.text.toLowerCase().includes(query));};

const importedDocuments=[];
let documentImportBusy=false;
function fileBase64(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('文件读取失败'));reader.readAsDataURL(file);});}
function renderDocuments(){
  $('documentQueuePanel').hidden=!importedDocuments.length;
  $('documentQueue').replaceChildren();
  importedDocuments.forEach((doc,index)=>{
    const card=document.createElement('div');card.className='document-card';
    const heading=document.createElement('strong');heading.textContent=(index+1)+'. '+doc.filename;card.append(heading);
    const actions=document.createElement('div');actions.className='row';
    for(const [label,delta] of [['上移',-1],['下移',1]]){const button=document.createElement('button');button.textContent=label;button.disabled=index+delta<0||index+delta>=importedDocuments.length;button.onclick=()=>{[importedDocuments[index],importedDocuments[index+delta]]=[importedDocuments[index+delta],importedDocuments[index]];renderDocuments();};actions.append(button);}
    const remove=document.createElement('button');remove.textContent='移出列表';remove.onclick=()=>{importedDocuments.splice(index,1);renderDocuments();};actions.append(remove);card.append(actions);
    const queueChapterButton=document.createElement('button');queueChapterButton.textContent='保存所选章节到队列';queueChapterButton.onclick=()=>saveBookChapters(doc);actions.append(queueChapterButton);
    for(const [label,selected] of [['全部选中',true],['全部取消选中',false]]){
      const button=document.createElement('button');button.textContent=label;
      button.onclick=()=>{doc.chapters.forEach(chapter=>chapter.selected=selected);renderDocuments();};actions.append(button);
    }
    const chapters=document.createElement('div');chapters.className='chapters';
    doc.chapters.forEach(chapter=>{const label=document.createElement('label');const input=document.createElement('input');input.type='checkbox';input.checked=chapter.selected!==false;input.onchange=()=>chapter.selected=input.checked;label.append(input,document.createTextNode(' '+chapter.title+' · '+chapter.text.length+' 字'));chapters.append(label);});card.append(chapters);$('documentQueue').append(card);
  });
}
$('importDocuments').onclick=()=>$('documentFiles').click();
$('documentFiles').onchange=async()=>{
  if(documentImportBusy)return;documentImportBusy=true;$('importDocuments').disabled=true;
  const failures=[];
  try{for(const file of $('documentFiles').files){
    $('documentStatus').textContent='正在本机解析 '+file.name+'…';
    try{if(file.size>40*1024*1024)throw new Error('主工作区支持 40 MB 以内文件，请在专业工作区导入大文件');const result=await api('/api/documents/import',{filename:file.name,data:await fileBase64(file)});importedDocuments.push(result.document);renderDocuments();}
    catch(error){failures.push(file.name+'：'+error.message);}
  }
  $('documentStatus').textContent='已载入 '+importedDocuments.length+' 份文档。'+(failures.length?failures.join('；'):'请勾选章节，整合到稿件后可清洗、试听。');
  if(importedDocuments.length)$('documentQueuePanel').open=true;
  }finally{documentImportBusy=false;$('importDocuments').disabled=false;$('documentFiles').value='';}
};
$('combineDocuments').onclick=()=>{
  const parts=[];for(const doc of importedDocuments)for(const chapter of doc.chapters)if(chapter.selected!==false){const text=chapter.text.trim();if(text)parts.push(chapter.include_title&&chapter.title&&!text.startsWith(chapter.title)?chapter.title+'\n\n'+text:text);}
  if(!parts.length){$('documentStatus').textContent='请至少选择一个有效章节。';return;}
  $('text').value=parts.join('\n\n');$('text').oninput();$('documentStatus').textContent='已按顺序整合 '+parts.length+' 个章节；可继续编辑或清洗。';
};

function workspace(which){
  $('mainWorkspace').hidden=which!=='main';$('creationBanner').hidden=which!=='main';$('expertWorkspace').hidden=which!=='expert';$('audioProducts').hidden=which!=='products';
  for(const [id,name] of [['mainTab','main'],['expertTab','expert'],['productsTab','products']]){$(id).classList.toggle('active',which===name);$(id).setAttribute('aria-selected',String(which===name));}
}
$('mainTab').onclick=()=>workspace('main');
let expertStarting=false;
$('expertTab').onclick=async()=>{
  workspace('expert');if(expertStarting||$('expertFrame').src)return;expertStarting=true;
  $('expertStatus').textContent='正在启动专业朗读工作区，首次需要约十几秒…';
  try{const result=await api('/api/expert/start',{});let ready=!result.starting;
    for(let i=0;i<60&&!ready;i++){await new Promise(resolve=>setTimeout(resolve,1000));if($('expertWorkspace').hidden)break;ready=(await api('/api/expert/state')).ready;}
    if(!ready)throw new Error('专业工作区尚未就绪，请稍后重新点击；详细诊断保存在 expert.log。');
    $('expertFrame').src=result.url;$('expertFrame').hidden=false;$('expertStatus').textContent='已连接独立专业工作区。语音生成与主工作区共用任务锁，请在一个工作区完成后再开始另一项。';await loadExpertOutputs();
  }catch(error){$('expertStatus').textContent=error.message;}finally{expertStarting=false;}
};
function outputRow(item,expert=false){
  const video=item.kind==='video';
  const row=document.createElement('div');row.className='product-entry';const title=document.createElement('strong');title.textContent=(video?'MP4 · ':'音频 · ')+(item.filename||item.title||'朗读音频');row.append(title);
  const player=document.createElement(video?'video':'audio');player.controls=true;player.preload='none';player.src=item.url;row.append(player);
  const download=document.createElement('a');download.className='button';download.textContent=video?'下载 MP4':'下载音频';download.href=item.url+(item.url.includes('?')?'&':'?')+'download=1';row.append(download);
  const reveal=document.createElement('button');reveal.textContent='在 Finder 中定位';reveal.onclick=async()=>{try{
    if(expert)await api('/api/expert/audio/'+item.key+'/reveal',{});
    else if(video)await api('/api/videos/'+item.job_id+'/reveal',{filename:item.filename});
    else await api('/api/jobs/'+item.id+'/reveal',{kind:'audio'});
  }catch(error){$('audioProductsStatus').textContent=error.message;}};row.append(reveal);
  if(item.path){const path=document.createElement('p');path.className='archive-path';path.style.width='100%';path.textContent='保存位置：'+item.path;row.append(path);}
  if(video){row.append(createPublishButton(item.job_id,item.filename));return row;}
  const send=document.createElement('button');send.textContent='继续制作 MP4';send.onclick=async()=>{
    send.disabled=true;
    try{
      if(!expert){workspace('main');select(item.id);render(await api('/api/jobs/'+item.id));$('imageAdjustments').open=true;$('imageAdjustments').scrollIntoView({behavior:'smooth'});return;}
      const response=await fetch(item.url);if(!response.ok)throw new Error('无法读取音频成品');const file=new File([await response.blob()],item.filename,{type:'audio/'+item.filename.split('.').pop()});const transfer=new DataTransfer();transfer.items.add(file);$('externalAudio').files=transfer.files;$('externalAudio').dispatchEvent(new Event('change'));workspace('main');$('externalMediaPanel').open=true;$('externalMediaPanel').scrollIntoView({behavior:'smooth'});$('externalMediaStatus').textContent='已载入专业语音成品。请选择照片，再点击“载入音频和照片”。';
    }catch(error){(expert?$('expertStatus'):$('audioProductsStatus')).textContent=error.message;}finally{send.disabled=false;}
  };row.append(send);return row;
}
async function loadExpertOutputs(){const result=await api('/api/expert/outputs');$('expertOutputs').replaceChildren(...result.outputs.map(item=>outputRow(item,true)));}
$('refreshExpertOutputs').onclick=()=>loadExpertOutputs().catch(error=>$('expertStatus').textContent=error.message);
async function loadAudioProducts(){
  try{
    const [audio,videos,expert]=await Promise.all([api('/api/products/audio'),api('/api/videos'),api('/api/expert/outputs')]);
    $('audioProductsStatus').textContent='共 '+(audio.products.length+expert.outputs.length)+' 个音频成品、'+videos.videos.length+' 个 MP4；试听、播放、下载或直接定位文件。';
    const rows=[...videos.videos.map(item=>outputRow({...item,kind:'video',url:item.media_url})),...audio.products.map(item=>outputRow(item)),...expert.outputs.map(item=>outputRow(item,true))];
    $('audioProductsList').replaceChildren(...rows);
  }catch(error){$('audioProductsStatus').textContent=error.message;}
}
$('productsTab').onclick=()=>{workspace('products');loadAudioProducts();};$('refreshAudioProducts').onclick=loadAudioProducts;
$('openFiles').onclick=$('productsTab').onclick;
$('openProductsRoot').onclick=async()=>{try{await api('/api/videos/reveal-root',{});}catch(error){$('audioProductsStatus').textContent=error.message;}};
const originalOpenVideos=$('openVideos').onclick;
$('openVideos').onclick=()=>{workspace('main');originalOpenVideos();};
window.addEventListener('pageshow',updateAudioOnly);

function createPublishButton(jobId,filename){
  const button=document.createElement('button');button.textContent='发布到今日头条';button.className='publish-button';
  button.onclick=()=>prepareToutiao(jobId,filename,button);return button;
}
let toutiaoPoll=null;
function showToutiaoStatus(result){
  $('publishStatus').textContent=result.message;
  $('publishPath').textContent='视频位置：'+result.path;
  $('copyPublishPath').onclick=async()=>{try{await navigator.clipboard.writeText(result.path);$('publishStatus').textContent='视频完整路径已复制，可在选择文件窗口按 Command+Shift+G 粘贴。';}catch{ $('publishStatus').textContent='无法自动复制，请选中下方完整路径复制。';}};
  $('revealPublishVideo').onclick=()=>api('/api/videos/'+result.path.split('/').slice(-2)[0]+'/reveal',{filename:result.filename}).catch(error=>$('publishStatus').textContent=error.message);
}
async function prepareToutiao(jobId,filename,button){
  button.disabled=true;
  try{const result=await api('/api/videos/'+jobId+'/publish-toutiao',{filename});
    $('publishPanel').hidden=false;$('publishTitle').value=result.title;showToutiaoStatus(result);
    $('publishPanel').scrollIntoView({behavior:'smooth',block:'start'});
    clearInterval(toutiaoPoll);
    toutiaoPoll=setInterval(async()=>{try{const current=await api('/api/toutiao-publish/'+result.id);showToutiaoStatus(current);if(!['starting','waiting_login','waiting_input','uploading'].includes(current.state))clearInterval(toutiaoPoll);}catch(error){clearInterval(toutiaoPoll);$('publishStatus').textContent=error.message;}},2000);
  }catch(error){showError(error.message);}finally{button.disabled=false;}
}
const originalRender=render;
render=j=>{originalRender(j);$('publishCurrentVideo').hidden=!j.video_path;};
$('publishCurrentVideo').onclick=()=>{if(job?.video_path)prepareToutiao(job.id,job.video_path.split('/').pop(),$('publishCurrentVideo'));};
$('copyPublishTitle').onclick=async()=>{try{await navigator.clipboard.writeText($('publishTitle').value);$('publishStatus').textContent='标题已复制，可在今日头条粘贴。';}catch{$('publishTitle').focus();$('publishTitle').select();$('publishStatus').textContent='请按 Command+C 复制标题。';}};
