// 页面只保留预览与选择编号，大批原图的冻结副本由本机服务保存。
let randomPhotoSelection=null,randomPhotoPending=null,randomPhotoRevision=0;
function randomPhotoCount(){
  const count=Number($('randomPhotoCount').value);
  if(!Number.isInteger(count)||count<1||count>50)throw new Error('随机图片数量必须为 1–50 的整数');
  return count;
}
function randomPhotoPreviewURL(){return randomPhotoSelection?.photos[0]?'data:image/png;base64,'+randomPhotoSelection.photos[0].preview_data:'';}
function renderRandomPhotos(){
  const photos=randomPhotoSelection?.photos||[];
  $('randomPhotoPreview').hidden=!photos.length;
  $('randomPhotoPreview').replaceChildren(...photos.map((photo,index)=>{
    const card=document.createElement('figure'),image=document.createElement('img'),caption=document.createElement('figcaption');
    card.className='photo-card';
    image.src='data:image/png;base64,'+photo.preview_data;image.alt=photo.photo_name;
    caption.textContent=(index+1)+' · '+photo.photo_name;card.append(image,caption);return card;
  }));
  $('randomPhotoSelectionStatus').textContent=randomPhotoSelection?.message||'尚未抽取图片';
  updateTitlePreview();
}
function clearRandomPhotos(){randomPhotoRevision++;randomPhotoSelection=null;renderRandomPhotos();}
async function refreshRandomPhotoFolder(){
  try{
    const info=await api('/api/random-photos');
    $('randomPhotoFolder').textContent=info.directory;
    $('randomPhotoLibraryStatus').textContent='文件夹中有 '+info.candidate_count+' 个图片文件；抽取时检查可用性。';
  }catch(error){$('randomPhotoLibraryStatus').textContent=error.message;}
}
function chooseRandomPhotos(){
  if(randomPhotoPending)return randomPhotoPending;
  const count=randomPhotoCount(),revision=++randomPhotoRevision;
  $('reshuffleRandomPhotos').disabled=true;$('randomPhotoCount').disabled=true;
  $('randomPhotoSelectionStatus').textContent='正在随机抽取并检查图片…';
  randomPhotoPending=(async()=>{
    try{
      const result=await api('/api/random-photos/select',{count});
      if(revision!==randomPhotoRevision||count!==randomPhotoCount())throw new Error('图片设置已改变，请重新随机选择');
      randomPhotoSelection=result;renderRandomPhotos();return result;
    }catch(error){
      // 抽样失败时清除旧选择，避免将旧素材当作本次抽样结果提交。
      if(revision===randomPhotoRevision){randomPhotoSelection=null;renderRandomPhotos();$('randomPhotoSelectionStatus').textContent=error.message;}
      throw error;
    }finally{randomPhotoPending=null;$('reshuffleRandomPhotos').disabled=false;$('randomPhotoCount').disabled=false;}
  })();
  return randomPhotoPending;
}
async function randomPhotoPayload(){
  const count=randomPhotoCount();
  // 重新抽取进行中时等待新结果，不能继续提交上一次的预览。
  if(randomPhotoPending)await randomPhotoPending;
  if(!randomPhotoSelection||randomPhotoSelection.requested_count!==count)await chooseRandomPhotos();
  return {image_mode:'upload',random_photo_selection:randomPhotoSelection.selection_id};
}
$('randomPhotoCount').oninput=clearRandomPhotos;
$('reshuffleRandomPhotos').onclick=async()=>{try{await chooseRandomPhotos();showError('');}catch(error){showError(error.message);}};
$('refreshRandomPhotoFolder').onclick=refreshRandomPhotoFolder;
$('openRandomPhotoFolder').onclick=async()=>{try{await api('/api/random-photos/open',{});await refreshRandomPhotoFolder();showError('');}catch(error){showError(error.message);}};
$('imageMode').addEventListener('change',()=>{updateTitlePreview();if($('imageMode').value==='random')refreshRandomPhotoFolder();});
const clearWithoutRandomPhotos=clearWorkspaceUI;
clearWorkspaceUI=()=>{clearRandomPhotos();clearWithoutRandomPhotos();};
refreshRandomPhotoFolder();
