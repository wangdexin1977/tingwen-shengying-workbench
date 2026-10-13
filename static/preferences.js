// 浏览器缓存提供即时恢复，本机配置文件提供跨浏览器和重启后的恢复。
const preferenceIds=['showTitle','musicEnabled','subtitleEnabled','audioOnly','audioProvider','localBackend',
  'omniVoice','omniExpression','speechSpeed','speechSilence','speechSeed','speechTokens','speechTemperature',
  'speechSteps','speechGuidance','imageMode','provider','model','drawthingsPort','titleFont','titleColor','titleSize',
  'subtitleFont','subtitleColor','subtitleSize','subtitlePosition','musicTrack','musicLoop','musicVolume','queueScheduledOutput'];
const numericPreferences=new Set(['speechSpeed','speechSilence','speechSeed','speechTokens','speechTemperature',
  'speechSteps','speechGuidance','drawthingsPort','titleSize','subtitleSize','subtitlePosition','musicLoop','musicVolume']);
let restoringPreferences=true,preferencesTimer=null,preferencesQueue=Promise.resolve(),preferenceRevision=0;
$('generate').disabled=true;$('savePreferences').disabled=true;
function capturePreferences(){
  const controls={edgeVoice:voice};
  for(const id of preferenceIds){const input=$(id);controls[id]=input.type==='checkbox'?input.checked:numericPreferences.has(id)?Number(input.value):input.value;}
  let profiles={};try{profiles=JSON.parse(localStorage.getItem(speechKey)||'{}');}catch{}
  return {controls,mode:$('longMode').getAttribute('aria-pressed')==='true'?'long':'short',speech_profiles:profiles};
}
function cachePreferences(settings){
  const c=settings.controls;
  try{
    localStorage.setItem('shengying-all-preferences',JSON.stringify(settings));
    localStorage.setItem('shengying-creation-mode',settings.mode);
    localStorage.setItem(speechKey,JSON.stringify(settings.speech_profiles));
    localStorage.setItem('shengying-workbench-voice',c.edgeVoice);
    localStorage.setItem(omniStorageKey,JSON.stringify({audio_provider:c.audioProvider,omnivoice_settings:{voice_id:c.omniVoice,expression:c.omniExpression,backend:c.localBackend}}));
    localStorage.setItem('shengying-workbench-image-mode',c.imageMode);
    localStorage.setItem('shengying-workbench-image-provider',c.provider);
    localStorage.setItem(titleStyleStorageKey,JSON.stringify({font:c.titleFont,color:c.titleColor,size:c.titleSize}));
    localStorage.setItem(subtitleStyleStorageKey,JSON.stringify({enabled:c.subtitleEnabled,font:c.subtitleFont,color:c.subtitleColor,size:c.subtitleSize,position:c.subtitlePosition}));
    localStorage.setItem(musicStorageKey,JSON.stringify({enabled:c.musicEnabled,track_id:c.musicTrack,volume:c.musicVolume,loop_seconds:c.musicLoop}));
  }catch{}
}
function persistPreferences(){
  clearTimeout(preferencesTimer);
  const settings=capturePreferences(),revision=++preferenceRevision;
  cachePreferences(settings);$('settingsSaveStatus').textContent='正在自动保存配置…';
  // 串行写入，快速连续调整时旧请求不会覆盖新值。
  preferencesQueue=preferencesQueue.catch(()=>{}).then(()=>api('/api/preferences',settings)).then(()=>{
    if(revision===preferenceRevision)$('settingsSaveStatus').textContent='配置已自动保存';
  }).catch(error=>{if(revision===preferenceRevision)$('settingsSaveStatus').textContent='配置保存失败：'+error.message;});
  return preferencesQueue;
}
function schedulePreferences(){
  if(restoringPreferences)return;
  cachePreferences(capturePreferences());
  clearTimeout(preferencesTimer);$('settingsSaveStatus').textContent='正在自动保存配置…';
  preferencesTimer=setTimeout(persistPreferences,180);
}
async function restorePreferences(settings){
  restoringPreferences=true;
  const c=settings.controls;cachePreferences(settings);
  if(c.edgeVoice)selectVoice(c.edgeVoice);
  for(const id of preferenceIds){if(!(id in c))continue;const input=$(id);if(input.type==='checkbox')input.checked=c[id];else input.value=String(c[id]);}
  restoreSpeechProfile();
  // 当前模型最后编辑的参数按快照恢复。
  for(const id of Object.values(speechControls))if(id in c)$(id).value=String(c[id]);
  $('speechSpeedValue').textContent=Number($('speechSpeed').value).toFixed(2)+'×';
  creationMode(settings.mode==='long',true);
  restoreTitleSettings({font:c.titleFont,color:c.titleColor,size:c.titleSize});
  restoreSubtitles({enabled:c.subtitleEnabled,font:c.subtitleFont,color:c.subtitleColor,size:c.subtitleSize,position:c.subtitlePosition});
  restoreMusic({enabled:c.musicEnabled,track_id:c.musicTrack,volume:c.musicVolume,loop_seconds:c.musicLoop});
  await Promise.all([loadOmniVoices(c.omniVoice),loadMusic(c.musicTrack)]);
  if('omniVoice' in c)$('omniVoice').value=[...$('omniVoice').options].some(o=>o.value===c.omniVoice)?c.omniVoice:'';
  sourceUI();audioSourceUI();updateAudioOnly();updateTitlePreview();updateSubtitleUI();updateMusicUI();
  restoringPreferences=false;
}
async function initializePreferences(){
  try{
    const response=await api('/api/preferences');
    let settings=response.preferences;
    if(!settings){
      try{settings=JSON.parse(localStorage.getItem('shengying-all-preferences')||'null');}catch{}
      if(!settings){settings=capturePreferences();settings.controls.showTitle=false;
        // 保留已明确保存的音乐选择；没有保存记录时默认关闭。
        settings.controls.musicEnabled=musicPreferences().enabled;
        settings.controls.omniVoice=audioPreferences().omnivoice_settings?.voice_id||settings.controls.omniVoice;
      }
    }
    await restorePreferences(settings);
    await persistPreferences();
  }catch(error){restoringPreferences=false;$('settingsSaveStatus').textContent='配置恢复失败：'+error.message;}
  finally{$('generate').disabled=false;$('savePreferences').disabled=false;}
}
for(const id of preferenceIds){$(id).addEventListener('input',schedulePreferences);$(id).addEventListener('change',schedulePreferences);}
for(const button of voiceButtons)button.addEventListener('click',schedulePreferences);
const originalChangeMusic=changeMusic;
changeMusic=()=>{originalChangeMusic();schedulePreferences();};
const originalRememberAudioOptions=rememberAudioOptions;
rememberAudioOptions=()=>{originalRememberAudioOptions();schedulePreferences();};
for(const id of ['shortMode','longMode','subtitleTop','subtitleMiddle','subtitleBottom','addMusic','addOmniVoice'])$(id).addEventListener('click',()=>setTimeout(schedulePreferences,0));
const originalClearWorkspace=clearWorkspaceUI;
clearWorkspaceUI=()=>{originalClearWorkspace();
  // 清空只清除稿件与素材；不覆盖用户默认参数。
  let cached;try{cached=JSON.parse(localStorage.getItem('shengying-all-preferences')||'null');}catch{}
  if(cached){for(const id of ['showTitle','audioOnly','drawthingsPort','model'])if(id in cached.controls){const input=$(id);if(input.type==='checkbox')input.checked=cached.controls[id];else input.value=String(cached.controls[id]);}creationMode(cached.mode==='long',true);updateTitlePreview();}
};
$('clearWorkspace').onclick=async()=>{if(preferencesTimer)await persistPreferences();else await preferencesQueue;window.location.reload();};
$('savePreferences').onclick=persistPreferences;
// 待初始 pageshow 清空结束后再恢复完整配置，避免启动时旧默认值覆盖快照。
if(document.readyState==='complete')initializePreferences();else window.addEventListener('load',initializePreferences,{once:true});
