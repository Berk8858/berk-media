var dirs=[];
var currentFilm=null;
var probeData=null;
var audioPriority=[];
var subPriority=[];

function loadDirs(){
  fetch('/api/dirs').then(function(r){return r.json()}).then(function(d){dirs=d});
}

function doSearch(){
  var q=document.getElementById('searchInput').value.trim();
  if(q.length<2)return;
  document.getElementById('results').innerHTML='<div class="loading">Aranıyor...</div>';
  document.getElementById('searchBtn').disabled=true;
  fetch('/api/search?q='+encodeURIComponent(q)).then(function(r){return r.json()}).then(function(data){
    renderResults(data);
    document.getElementById('searchBtn').disabled=false;
  }).catch(function(){
    document.getElementById('results').innerHTML='<div class="empty">Arama hatası</div>';
    document.getElementById('searchBtn').disabled=false;
  });
}

function renderResults(films){
  var c=document.getElementById('results');
  if(!films.length){c.innerHTML='<div class="empty">Sonuç bulunamadı</div>';return}
  var html='';
  for(var i=0;i<films.length;i++){
    var f=films[i];
    var posterSrc=f.poster?'/api/poster?url='+encodeURIComponent(f.poster):'';
    var posterHtml=posterSrc
      ?'<div class="poster-wrap"><img src="'+posterSrc+'" alt="'+f.title+'" loading="lazy" onerror="this.parentElement.innerHTML=\'<div class=no-poster>Poster yok</div>\'"></div>'
      :'<div class="poster-wrap"><div class="no-poster">Poster yok</div></div>';
    var safeUrl=f.url.replace(/'/g,"\\'");
    html+='<div class="card" onclick="showDetail(\''+safeUrl+'\')">';
    html+='<span class="source-tag">'+(f.source||'')+'</span>';
    html+=posterHtml;
    html+='<div class="info">';
    html+='<div class="title">'+f.title+'</div>';
    html+='<div class="meta">';
    if(f.imdb)html+='<span class="imdb">IMDB '+f.imdb+'</span>';
    if(f.quality)html+='<span class="quality">'+f.quality+'</span>';
    if(f.year)html+=f.year;
    html+='</div></div></div>';
  }
  c.innerHTML=html;
}

function showDetail(url){
  document.getElementById('modal').classList.add('active');
  document.getElementById('modalContent').innerHTML='<div class="loading">Yükleniyor...</div>';
  currentFilm=null;
  probeData=null;
  audioPriority=[];
  subPriority=[];

  fetch('/api/film?url='+encodeURIComponent(url)).then(function(r){return r.json()}).then(function(film){
    currentFilm=film;
    if(film.youtube_url||film.youtube_id){
      var ytUrl=film.youtube_url||('https://www.youtube.com/watch?v='+film.youtube_id);
      probeData={loading:true};
      renderDetail();
      fetch('/api/probe?url='+encodeURIComponent(ytUrl)).then(function(r){return r.json()}).then(function(pd){
        probeData=pd;
        if(!pd.error){
          if(pd.audio_formats&&pd.audio_formats.length){
            for(var i=0;i<pd.audio_formats.length;i++){
              var a=pd.audio_formats[i];
              if(a.language&&(a.language.indexOf('tur')>=0||a.language.indexOf('Türk')>=0)){
                audioPriority=[a.language];break;
              }
            }
            for(var i=0;i<pd.audio_formats.length;i++){
              var a=pd.audio_formats[i];
              if(a.language&&(a.language.indexOf('eng')>=0)){
                if(audioPriority.indexOf(a.language)<0)audioPriority.push(a.language);
                break;
              }
            }
          }
          if(pd.subtitles&&pd.subtitles.length){
            for(var i=0;i<pd.subtitles.length;i++){
              var s=pd.subtitles[i];
              if(s.language==='tr'||s.language.indexOf('tur')>=0){
                subPriority=[s.language];break;
              }
            }
            for(var i=0;i<pd.subtitles.length;i++){
              var s=pd.subtitles[i];
              if(s.language==='en'||s.language.indexOf('eng')>=0){
                if(subPriority.indexOf(s.language)<0)subPriority.push(s.language);
                break;
              }
            }
          }
        }
        renderDetail();
      }).catch(function(){
        probeData={error:'Video bilgisi alınamadı'};
        renderDetail();
      });
    }else{
      probeData={error:'no_youtube'};
      renderDetail();
    }
  }).catch(function(){
    document.getElementById('modalContent').innerHTML='<div class="empty">Detay yüklenemedi</div>';
  });
}

function renderDetail(){
  var f=currentFilm;
  if(!f||!f.title)return;

  var dirOpts='';
  for(var i=0;i<dirs.length;i++){
    dirOpts+='<option value="'+dirs[i].path+'">'+dirs[i].name+'</option>';
  }

  var html='<div class="detail">';
  html+='<div class="poster">';
  if(f.poster)html+='<img src="/api/poster?url='+encodeURIComponent(f.poster)+'" alt="'+f.title+'">';
  html+='</div>';
  html+='<div class="info">';
  html+='<h2>'+f.title+(f.year?' ('+f.year+')':'')+'</h2>';
  if(f.imdb)html+='<div class="year">IMDB: '+f.imdb+'</div>';
  if(f.duration)html+='<div class="year">'+f.duration+'</div>';
  if(f.description)html+='<div class="desc">'+f.description.substring(0,300)+'...</div>';
  if(f.director)html+='<div class="tags"><span class="tag">Yonetmen: '+f.director+'</span></div>';
  html+='</div></div>';

  // OPTIONS PANEL - HER ZAMAN GOSTERILIR
  html+='<div class="opts">';
  html+='<h3>Indirme Secenekleri</h3>';

  // Kaynak
  html+='<div class="opt-group"><label>Kaynak</label><select id="sourceSelect">';
  html+='<option value="fullhdfilmizlesene">FullHDFilmIzlesene</option>';
  html+='</select></div>';

  var probeOk=probeData&&!probeData.error&&!probeData.loading;
  var probeLoading=probeData&&probeData.loading;

  if(probeLoading){
    html+='<div class="info-msg" id="probeStatus">Video bilgisi aliniyor...</div>';
  }else if(probeOk){
    // Kalite
    if(probeData.video_formats&&probeData.video_formats.length){
      html+='<div class="opt-group"><label>Video Kalitesi</label><select id="qualitySelect">';
      for(var i=0;i<probeData.video_formats.length;i++){
        var v=probeData.video_formats[i];
        html+='<option value="'+v.format_id+'"'+(i===0?' selected':'')+'>'+v.label+' ('+v.ext+')</option>';
      }
      html+='</select></div>';
    }

    // Ses
    if(probeData.audio_formats&&probeData.audio_formats.length){
      html+='<div class="opt-group"><label>Ses Dosyasi (siralama icin tiklayin)</label><div class="chip-list">';
      for(var i=0;i<probeData.audio_formats.length;i++){
        var a=probeData.audio_formats[i];
        var idx=audioPriority.indexOf(a.language);
        html+='<span class="chip'+(idx>=0?' on':'')+'" data-lang="'+a.language+'" onclick="toggleAudio(this)">'+a.label;
        if(idx>=0)html+='<span class="ord">#'+(idx+1)+'</span>';
        html+='</span>';
      }
      html+='</div>';
      if(audioPriority.length){
        html+='<div class="pri-list">';
        for(var i=0;i<audioPriority.length;i++){
          html+='<div class="pri-item"><span class="num">'+(i+1)+'</span><span class="name">'+audioPriority[i]+'</span>';
          html+='<span class="btns"><span onclick="moveAudio('+i+',-1)">&#9650;</span><span onclick="moveAudio('+i+',1)">&#9660;</span><span onclick="removeAudio('+i+')">&times;</span></span></div>';
        }
        html+='</div>';
      }
      html+='</div>';
    }

    // Altyazi
    if(probeData.subtitles&&probeData.subtitles.length){
      var showSubs=[];
      for(var i=0;i<probeData.subtitles.length;i++){
        var s=probeData.subtitles[i];
        if(s.language==='tr'||s.language==='en'||s.language.indexOf('tur')>=0||s.language.indexOf('eng')>=0||subPriority.indexOf(s.language)>=0){
          showSubs.push(s);
        }
      }
      if(!showSubs.length)showSubs=probeData.subtitles.slice(0,10);
      html+='<div class="opt-group"><label>Altyazilar (siralama icin tiklayin)</label><div class="chip-list">';
      for(var i=0;i<showSubs.length;i++){
        var s=showSubs[i];
        var idx=subPriority.indexOf(s.language);
        html+='<span class="chip'+(idx>=0?' on':'')+'" data-lang="'+s.language+'" onclick="toggleSub(this)">'+s.label;
        if(idx>=0)html+='<span class="ord">#'+(idx+1)+'</span>';
        html+='</span>';
      }
      html+='</div>';
      if(subPriority.length){
        html+='<div class="pri-list">';
        for(var i=0;i<subPriority.length;i++){
          html+='<div class="pri-item"><span class="num">'+(i+1)+'</span><span class="name">'+subPriority[i]+'</span>';
          html+='<span class="btns"><span onclick="moveSub('+i+',-1)">&#9650;</span><span onclick="moveSub('+i+',1)">&#9660;</span><span onclick="removeSub('+i+')">&times;</span></span></div>';
        }
        html+='</div>';
      }
      html+='</div>';
    }
  }else if(probeData&&probeData.error==='no_youtube'){
    html+='<div class="info-msg">Bu film YouTube uzerinden yayinlanmiyor. Kaynak site uzerinden dogrudan indirme yapilacak.</div>';
  }else if(probeData&&probeData.error){
    html+='<div class="info-msg">Video bilgisi alinamadi. Dogrudan indirme denenecek.</div>';
  }

  // Format - HER ZAMAN
  html+='<div class="opt-group"><label>Kaydetme Formati</label><select id="formatSelect">';
  html+='<option value="mkv">MKV (varsayilan)</option>';
  html+='<option value="mp4">MP4</option>';
  html+='</select></div>';

  // Klasor - HER ZAMAN
  html+='<div class="opt-group"><label>Kayit Klasoru</label><select id="dirSelect">'+dirOpts+'</select></div>';
  html+='</div>';

  html+='<button class="dl-btn" onclick="startDownload()">Indir</button>';

  document.getElementById('modalContent').innerHTML=html;
}

function toggleAudio(el){
  var lang=el.getAttribute('data-lang');
  var idx=audioPriority.indexOf(lang);
  if(idx>=0)audioPriority.splice(idx,1);else audioPriority.push(lang);
  renderDetail();
}
function removeAudio(i){audioPriority.splice(i,1);renderDetail()}
function moveAudio(i,d){
  var n=i+d;
  if(n<0||n>=audioPriority.length)return;
  var tmp=audioPriority[i];audioPriority[i]=audioPriority[n];audioPriority[n]=tmp;
  renderDetail();
}
function toggleSub(el){
  var lang=el.getAttribute('data-lang');
  var idx=subPriority.indexOf(lang);
  if(idx>=0)subPriority.splice(idx,1);else subPriority.push(lang);
  renderDetail();
}
function removeSub(i){subPriority.splice(i,1);renderDetail()}
function moveSub(i,d){
  var n=i+d;
  if(n<0||n>=subPriority.length)return;
  var tmp=subPriority[i];subPriority[i]=subPriority[n];subPriority[n]=tmp;
  renderDetail();
}

function startDownload(){
  if(!currentFilm)return;
  var qualityEl=document.getElementById('qualitySelect');
  var formatEl=document.getElementById('formatSelect');
  var dirEl=document.getElementById('dirSelect');
  var format_id=qualityEl?qualityEl.value:'';
  var output_format=formatEl?formatEl.value:'mkv';
  var save_dir=dirEl?dirEl.value:(dirs.length?dirs[0].path:'/mnt/3tb/Medya/Movies');

  var video_url='';
  if(currentFilm.youtube_url||currentFilm.youtube_id){
    video_url=currentFilm.youtube_url||('https://www.youtube.com/watch?v='+currentFilm.youtube_id);
  }else{
    video_url=currentFilm.url;
  }

  var btn=document.querySelector('.dl-btn');
  btn.disabled=true;
  btn.textContent='Baslatiliyor...';

  fetch('/api/download',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({
      title:currentFilm.title+(currentFilm.year?' ('+currentFilm.year+')':''),
      url:video_url,
      format_id:format_id,
      audio_lang:audioPriority[0]||'',
      sub_langs:subPriority,
      output_format:output_format,
      save_dir:save_dir,
      poster:currentFilm.poster||''
    })
  }).then(function(r){return r.json()}).then(function(data){
    if(data.task_id){
      closeModal();
      pollDownload(data.task_id);
    }else{
      alert('Hata: '+JSON.stringify(data));
      btn.disabled=false;
      btn.textContent='Indir';
    }
  }).catch(function(e){
    alert('Baglanti hatasi: '+e);
    btn.disabled=false;
    btn.textContent='Indir';
  });
}

function pollDownload(taskId){
  var interval=setInterval(function(){
    fetch('/api/download/'+taskId).then(function(r){return r.json()}).then(function(d){
      renderDownloads();
      if(d.status==='completed'||d.status==='error'||d.status==='merge_error')clearInterval(interval);
    }).catch(function(){clearInterval(interval)});
  },2000);
}

function renderDownloads(){
  fetch('/api/downloads').then(function(r){return r.json()}).then(function(data){
    var c=document.getElementById('dlList');
    var keys=Object.keys(data);
    if(!keys.length){c.innerHTML='<div class="empty">Henuz indirme yok</div>';return}
    var html='';
    for(var i=0;i<keys.length;i++){
      var id=keys[i];
      var d=data[id];
      html+='<div class="dl-item"><div><div class="title">'+(d.title||id)+'</div>';
      if(d.progress)html+='<div class="progress-bar"><div class="fill" style="width:'+d.progress+'%"></div></div>';
      html+='</div><span class="status '+d.status+'">'+statusText(d.status)+'</span></div>';
    }
    c.innerHTML=html;
  }).catch(function(){});
}

function statusText(s){
  var map={starting:'Baslatiliyor',downloading:'Indiriliyor',downloading_hls:'HLS Indiriliyor',merging:'Birlestiriliyor',completed:'Tamamlandi',error:'Hata',merge_error:'Birlesme Hatasi'};
  return map[s]||s;
}

function closeModal(){document.getElementById('modal').classList.remove('active')}

document.getElementById('searchInput').addEventListener('keydown',function(e){if(e.key==='Enter')doSearch()});
document.addEventListener('keydown',function(e){if(e.key==='Escape')closeModal()});
loadDirs();
setInterval(renderDownloads,10000);
