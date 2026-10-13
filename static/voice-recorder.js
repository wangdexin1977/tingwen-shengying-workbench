// 参考人声仅在用户点击保存时上传到本机，不自动录音或上传。
let voiceReferenceFile=null,voiceReferenceURL=null,voiceRecorder=null,voiceStream=null;
let voiceRecordingTimer=null,voiceRecordingStarted=0,voiceRecordingPending=false;
function releaseVoiceMicrophone(){voiceStream?.getTracks().forEach(track=>track.stop());voiceStream=null;clearInterval(voiceRecordingTimer);voiceRecordingTimer=null;}
function resetVoiceReference(){
  voiceReferenceFile=null;$('omniVoiceFile').value='';
  $('voiceRecordingPreview').pause();$('voiceRecordingPreview').removeAttribute('src');$('voiceRecordingPreview').hidden=true;
  if(voiceReferenceURL)URL.revokeObjectURL(voiceReferenceURL);voiceReferenceURL=null;
  $('downloadVoiceRecording').hidden=true;$('discardVoiceRecording').hidden=true;
}
function chooseVoiceReference(file){
  if(!file||!file.size)throw new Error('录音为空，请重新录制或选择音频。');
  if(file.size>20*1024*1024)throw new Error('参考音频请控制在 20 MB 以内。');
  resetVoiceReference();voiceReferenceFile=file;voiceReferenceURL=URL.createObjectURL(file);
  $('voiceRecordingPreview').src=voiceReferenceURL;$('voiceRecordingPreview').hidden=false;
  $('downloadVoiceRecording').href=voiceReferenceURL;$('downloadVoiceRecording').download=file.name;
  $('downloadVoiceRecording').hidden=false;$('discardVoiceRecording').hidden=false;
  $('voiceRecordingStatus').textContent='已载入 '+file.name+'，可试听；填写名称后点击“命名并保存音色”。';
}
function voiceRecordingControls(recording){
  $('startVoiceRecording').disabled=recording;$('stopVoiceRecording').hidden=!recording;
  for(const id of ['uploadVoiceRecording','discardVoiceRecording','omniVoiceFile','addOmniVoice'])$(id).disabled=recording;
}
$('startVoiceRecording').onclick=async()=>{
  if(voiceRecordingPending||voiceRecorder?.state==='recording')return;
  if(!navigator.mediaDevices?.getUserMedia||typeof MediaRecorder==='undefined'){
    $('voiceRecordingStatus').textContent='当前浏览器不支持麦克风录音。请用 Chrome / Safari 打开本机工作台，或点击“上传已有录音”。';return;
  }
  voiceRecordingPending=true;voiceRecordingControls(true);$('stopVoiceRecording').disabled=true;
  try{
    voiceStream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true},video:false});
    const type=['audio/webm;codecs=opus','audio/mp4','audio/ogg;codecs=opus'].find(value=>MediaRecorder.isTypeSupported(value));
    voiceRecorder=type?new MediaRecorder(voiceStream,{mimeType:type}):new MediaRecorder(voiceStream);
    const chunks=[];let size=0,failed=false;
    voiceRecorder.ondataavailable=event=>{if(event.data.size){chunks.push(event.data);size+=event.data.size;if(size>=20*1024*1024&&voiceRecorder.state==='recording')voiceRecorder.stop();}};
    voiceRecorder.onerror=()=>{failed=true;releaseVoiceMicrophone();voiceRecordingControls(false);$('voiceRecordingStatus').textContent='录音中断，请检查麦克风后重试；已选择的参考音频保留。';};
    voiceRecorder.onstop=()=>{
      releaseVoiceMicrophone();voiceRecordingControls(false);if(failed)return;
      try{const mime=voiceRecorder.mimeType||type||'audio/webm';const extension=mime.includes('mp4')?'m4a':mime.includes('ogg')?'ogg':'webm';chooseVoiceReference(new File(chunks,'我的录音-'+Date.now()+'.'+extension,{type:mime}));}
      catch(error){$('voiceRecordingStatus').textContent=error.message;}
    };
    voiceRecorder.start(1000);$('stopVoiceRecording').disabled=false;voiceRecordingStarted=Date.now();
    $('voiceRecordingStatus').textContent='正在录音 · 0 秒。建议朗读 10–30 秒，完成后点击停止录音。';
    voiceRecordingTimer=setInterval(()=>{const seconds=Math.floor((Date.now()-voiceRecordingStarted)/1000);$('voiceRecordingStatus').textContent='正在录音 · '+seconds+' 秒 / 最多 180 秒';if(seconds>=180&&voiceRecorder.state==='recording')voiceRecorder.stop();},500);
  }catch(error){
    releaseVoiceMicrophone();voiceRecordingControls(false);
    $('voiceRecordingStatus').textContent=error.name==='NotAllowedError'?'未获得麦克风权限。请在浏览器允许麦克风后重试，或上传已有录音。':error.name==='NotFoundError'?'未找到麦克风，请连接麦克风或上传已有录音。':'无法开始录音，请检查麦克风是否被占用，或上传已有录音。';
  }finally{voiceRecordingPending=false;}
};
$('stopVoiceRecording').onclick=()=>{if(voiceRecorder?.state==='recording'){voiceRecordingControls(true);$('stopVoiceRecording').disabled=true;voiceRecorder.stop();releaseVoiceMicrophone();}};
$('uploadVoiceRecording').onclick=()=>$('omniVoiceFile').click();
$('omniVoiceFile').onchange=()=>{const file=$('omniVoiceFile').files?.[0];if(!file)return;try{chooseVoiceReference(file);}catch(error){$('voiceRecordingStatus').textContent=error.message;}};
$('discardVoiceRecording').onclick=()=>{resetVoiceReference();$('voiceRecordingStatus').textContent='录音已清除。可重新录音或上传；已保存的音色不受影响。';};
$('addOmniVoice').onclick=async()=>{
  const button=$('addOmniVoice');button.disabled=true;
  try{
    const file=voiceReferenceFile||$('omniVoiceFile').files?.[0],name=$('omniVoiceName').value.trim();
    if(!name||!file)throw new Error('请填写音色名称，并录音或上传参考音频。');
    const audio_data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('音频读取失败'));reader.readAsDataURL(file);});
    const result=await api('/api/omnivoice/voices',{name,filename:file.name,audio_data,reference_text:$('omniReferenceText').value});
    await loadOmniVoices(result.voice_id);rememberAudioOptions();
    $('omniVoice').dispatchEvent(new Event('change',{bubbles:true}));
    $('omniVoiceStatus').textContent=result.created?'音色“'+name+'”已保存并选中，可用于生成 MP3。':'这段录音已保存过，已选中已有音色；可在专业音色库修改名称。';
    $('voiceRecordingStatus').textContent='保存完成。可继续试听录音，或开始录制下一个音色。';
  }catch(error){$('omniVoiceStatus').textContent=error.message;$('voiceRecordingStatus').textContent=error.message;}
  finally{button.disabled=false;}
};
window.addEventListener('pagehide',()=>{if(voiceRecorder?.state==='recording')voiceRecorder.stop();releaseVoiceMicrophone();});
