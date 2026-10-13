let productionQueue=null,queuePoll=null,replacementProject='';
const queueStateNames={waiting:'待制作',running:'正在制作',audio_ready:'MP3 已完成 · 可继续 MP4',done:'MP4 已完成',failed:'失败 · 保留待重试',interrupted:'已中断 · 素材保留'};
function queueSettings(){return {voice,...audioOptions(),title:currentTitle(),title_enabled:$('showTitle').checked,
  title_settings:titleSettings(),subtitle_settings:subtitleSettings(),music_settings:musicSettings()};}
async function queuePictures(){
  if($('imageMode').value==='upload'&&selectedLocalPhotos.length)return readPhotos(selectedLocalPhotos.map(item=>item.file));
  if($('imageMode').value==='url'&&$('photoUrl').value.trim())return {photo_url:$('photoUrl').value.trim()};
  if(job?.image_state==='ready')return {source_job_id:job.id,image_version:job.image_version};
  return {};
}
async function saveQueueProjects(projects){
  const pictures=await queuePictures();
  const result=await api('/api/queue/projects',{projects,settings:queueSettings(),audio_only:$('audioOnly').checked,...pictures});
  renderProductionQueue(result.queue);return result.ids;
}
async function saveCurrentDraft(start=false){
  const button=start?$('queueOneClick'):$('queueSaveDraft');button.disabled=true;showError('');
  try{
    const text=$('text').value.trim();if(!text)throw new Error('请先填写文案；书籍可直接保存所选章节到队列。');
    const ids=await saveQueueProjects([{text,title:currentTitle()}]);
    if(start)renderProductionQueue(await api('/api/queue/start',{output:'mp4',ids}));
    showQueueWorkspace();$('queueStatus').textContent=start?'已保存当前稿件并开始自动生成 MP4。':'文案、配图和参数已保存，可继续准备下一项。';
  }catch(error){showError(error.message);}finally{button.disabled=false;}
}
async function saveBookChapters(document){
  showError('');
  try{
    const projects=document.chapters.filter(chapter=>chapter.selected!==false).map(chapter=>{
      const content=chapter.text.trim();const text=chapter.include_title&&chapter.title&&!content.startsWith(chapter.title)?chapter.title+'\n\n'+content:content;
      return {name:document.title+' · '+chapter.title,text,title:$('showTitle').checked?text.replace(/\s+/g,' ').trim().slice(0,30):''};
    });
    if(!projects.length)throw new Error('请至少选择一个章节');
    await saveQueueProjects(projects);showQueueWorkspace();$('queueStatus').textContent='已将 '+projects.length+' 个章节分别保存为待制作项目。';
  }catch(error){showError(error.message);}
}
function showQueueWorkspace(){workspace('queue');$('queueWorkspace').hidden=false;loadProductionQueue();}
const originalWorkspace=workspace;
workspace=which=>{originalWorkspace(which);$('queueWorkspace').hidden=which!=='queue';$('queueTab').classList.toggle('active',which==='queue');};
function renderProductionQueue(data){
  productionQueue=data;
  if(data.scheduled_at&&!$('queueStartAt').value)$('queueStartAt').value=new Date((data.scheduled_at+8*3600)*1000).toISOString().slice(0,16);
  const status=data.running?(data.paused?'已暂停 · ':'正在按顺序制作 · '):'';
  $('queueStatus').textContent=status+data.message+(data.scheduled_at?' · 预约 '+new Date(data.scheduled_at*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'');
  $('queuePause').hidden=!data.running||data.paused;$('queueResume').hidden=!data.running||!data.paused;
  $('queueStop').hidden=!data.running;$('queueCancelSchedule').hidden=!data.scheduled_at;
  for(const id of ['queueRunMp4','queueRunMp3','queueSchedule'])$(id).disabled=data.running||!data.projects.length;
  $('queueProjects').replaceChildren(...data.projects.map((project,index)=>{
    const card=document.createElement('div');card.className='document-card';const heading=document.createElement('strong');heading.textContent=(index+1)+'. '+project.name;card.append(heading);
    const detail=document.createElement('p');detail.textContent=(queueStateNames[project.state]||project.state)+' · '+project.characters+' 字 · '+project.images+' 张照片';card.append(detail);
    if(project.error){const error=document.createElement('p');error.style.color='#b91c1c';error.textContent=project.error;card.append(error);}
    if(project.job){const j=project.job;const progress=document.createElement('p');progress.textContent=[j.audio_message,j.video_state!=='idle'?j.video_message:''].filter(Boolean).join(' · ');card.append(progress);}
    const actions=document.createElement('div');actions.className='row';
    for(const [label,delta] of [['上移',-1],['下移',1]]){const button=document.createElement('button');button.textContent=label;button.disabled=data.running||index+delta<0||index+delta>=data.projects.length;button.onclick=async()=>{const order=data.projects.map(p=>p.id);[order[index],order[index+delta]]=[order[index+delta],order[index]];try{renderProductionQueue(await api('/api/queue/reorder',{order}));}catch(error){$('queueStatus').textContent=error.message;}};actions.append(button);}
    const preview=document.createElement('button');preview.textContent='查看保存稿件';preview.onclick=async()=>{try{const saved=await api('/api/queue/projects/'+project.id);$('queueDraftText').textContent=saved.text;$('queueDraftPanel').hidden=false;}catch(error){$('queueStatus').textContent=error.message;}};actions.append(preview);
    const changeImage=document.createElement('button');changeImage.textContent='添加 / 更换照片';changeImage.disabled=data.running;changeImage.onclick=()=>{replacementProject=project.id;$('queueReplaceFiles').click();};actions.append(changeImage);
    if(['failed','interrupted','audio_ready','waiting'].includes(project.state)){const retry=document.createElement('button');retry.textContent=project.state==='audio_ready'?'继续生成 MP4':'制作此项 MP4';retry.disabled=data.running;retry.onclick=async()=>{try{renderProductionQueue(await api('/api/queue/start',{output:'mp4',ids:[project.id]}));}catch(error){$('queueStatus').textContent=error.message;}};actions.append(retry);}
    const remove=document.createElement('button');remove.textContent='移出队列';remove.disabled=data.running;remove.onclick=async()=>{try{renderProductionQueue(await api('/api/queue/remove',{id:project.id}));}catch(error){$('queueStatus').textContent=error.message;}};actions.append(remove);
    if(project.job){for(const [kind,label,path] of [['audio','MP3',project.job.audio_path],['video','MP4',project.job.video_path]])if(path){const download=document.createElement('a');download.className='button';download.textContent='下载 '+label;download.href='/api/jobs/'+project.job_id+'/media/'+kind+'?download=1';actions.append(download);const reveal=document.createElement('button');reveal.textContent='定位 '+label;reveal.onclick=()=>api('/api/jobs/'+project.job_id+'/reveal',{kind}).catch(error=>$('queueStatus').textContent=error.message);actions.append(reveal);const location=document.createElement('p');location.className='archive-path';location.textContent=label+'：'+path;card.append(location);}}
    card.append(actions);return card;
  }));
}
async function loadProductionQueue(){try{renderProductionQueue(await api('/api/queue'));}catch(error){$('queueStatus').textContent=error.message;}}
$('queueTab').onclick=showQueueWorkspace;$('queueSaveDraft').onclick=()=>saveCurrentDraft();$('queueOneClick').onclick=()=>saveCurrentDraft(true);
for(const [id,output] of [['queueRunMp4','mp4'],['queueRunMp3','mp3']])$(id).onclick=async()=>{try{renderProductionQueue(await api('/api/queue/start',{output}));}catch(error){$('queueStatus').textContent=error.message;}};
for(const [id,action] of [['queuePause','pause'],['queueResume','resume'],['queueStop','stop'],['queueCancelSchedule','cancel-schedule']])$(id).onclick=async()=>{try{renderProductionQueue(await api('/api/queue/'+action,{}));}catch(error){$('queueStatus').textContent=error.message;}};
$('queueSchedule').onclick=async()=>{try{const when=new Date($('queueStartAt').value+'+08:00').getTime()/1000;if(!Number.isFinite(when))throw new Error('请选择开始日期和时间');renderProductionQueue(await api('/api/queue/schedule',{scheduled_at:when,output:$('queueScheduledOutput').value}));}catch(error){$('queueStatus').textContent=error.message;}};
$('queueRefresh').onclick=loadProductionQueue;
$('queueReplaceFiles').onchange=async()=>{try{const photos=await readPhotos([...$('queueReplaceFiles').files]);renderProductionQueue(await api('/api/queue/projects/'+replacementProject+'/images',photos));}catch(error){$('queueStatus').textContent=error.message;}finally{$('queueReplaceFiles').value='';}};
queuePoll=setInterval(()=>{if(productionQueue?.running||productionQueue?.scheduled_at||!$('queueWorkspace').hidden)loadProductionQueue();},1500);
loadProductionQueue();
