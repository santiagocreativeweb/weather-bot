
(function(){
  var tip=document.getElementById('viz-tooltip');
  document.addEventListener('mousemove',function(e){
    var t=e.target.closest('[data-tip]');
    if(!t){tip.style.opacity=0;return;}
    tip.textContent=t.getAttribute('data-tip');
    tip.style.left=Math.min(e.clientX+12,window.innerWidth-360)+'px';
    tip.style.top=(e.clientY+12)+'px';tip.style.opacity=1;
  });
  // reloj UTC-3 con segundos (punto 8)
  function clock(){
    var now=new Date(Date.now()-3*3600*1000);
    var p=function(n){return String(n).padStart(2,'0')};
    document.getElementById('viz-clock').textContent=
      p(now.getUTCHours())+':'+p(now.getUTCMinutes())+':'+p(now.getUTCSeconds())+
      ' - '+p(now.getUTCDate())+'/'+p(now.getUTCMonth()+1)+'/'+now.getUTCFullYear();
  }
  clock(); setInterval(clock,1000);
  // filtros combinables (punto 2) + calendario (punto 3)
  var sels=['f-cont','f-pais','f-ciudad','f-st','f-estado','f-conf'].map(function(i){return document.getElementById(i)});
  var fecha=document.getElementById('f-fecha');
  var reco=document.getElementById('f-reco'), pmax=document.getElementById('f-pmax');
  var reset=document.getElementById('f-reset'), count=document.getElementById('f-count');
  var attrs=['cont','pais','ciudad','st','estado','conf'];
  function apply(){
    var vals=sels.map(function(s){return s.value});
    var fv=fecha.value, shown=0, total=0;
    document.querySelectorAll('.card').forEach(function(c){
      total++;
      var ok=true;
      attrs.forEach(function(a,i){ if(vals[i] && c.dataset[a]!==vals[i]) ok=false; });
      if(fv && c.dataset.fecha!==fv) ok=false;
      // dias pasados: ocultos por defecto; visibles si el calendario los pide o Estado=Finalizado
      if(!fv && c.dataset.old==='1' && sels[4].value!=='fin') ok=false;
      if(reco.classList.contains('on') && c.dataset.reco!=='1') ok=false;
      if(pmax.classList.contains('on') && parseFloat(c.dataset.pmax)<0.40) ok=false;
      c.classList.toggle('hidden',!ok);
      if(ok) shown++;
    });
    document.querySelectorAll('.cont-lbl').forEach(function(cl){
      var g=cl.nextElementSibling;
      var any=g && g.querySelector('.card:not(.hidden)');
      cl.classList.toggle('hidden',!any); if(g)g.classList.toggle('hidden',!any);
    });
    document.querySelectorAll('h3.dia').forEach(function(h){
      var any=false, n=h.nextElementSibling;
      while(n && !n.matches('h3.dia')){ if(n.querySelector && n.querySelector('.card:not(.hidden)')) any=true; n=n.nextElementSibling; }
      h.classList.toggle('hidden',!any);
    });
    count.textContent=shown+' de '+total+' mercados';
    save();
  }
  function save(){
    try{ localStorage.setItem('wxbt-filters', JSON.stringify({
      s: sels.map(function(x){return x.value}), f: fecha.value,
      r: reco.classList.contains('on'), p: pmax.classList.contains('on')})); }catch(e){}
  }
  function restore(){
    try{
      var st=JSON.parse(localStorage.getItem('wxbt-filters')||'null');
      if(!st) return;
      (st.s||[]).forEach(function(v,i){ if(sels[i]) sels[i].value=v; });
      fecha.value=st.f||'';
      reco.classList.toggle('on',!!st.r); pmax.classList.toggle('on',!!st.p);
    }catch(e){}
  }
  sels.forEach(function(s){s.addEventListener('change',apply)});
  fecha.addEventListener('change',apply);
  [reco,pmax].forEach(function(ch){ch.addEventListener('click',function(){ch.classList.toggle('on');apply();})});
  reset.addEventListener('click',function(){
    sels.forEach(function(s){s.value=''});fecha.value='';
    [reco,pmax].forEach(function(c){c.classList.remove('on')});apply();
  });
  restore(); apply();
  // alertas por evento (#14): las cerradas viven en localStorage y se re-ocultan tras cada
  // morph/reload; el panel entero se esconde cuando no queda ninguna visible.
  function hideAlerts(){
    var hid=JSON.parse(localStorage.getItem('wxbt-alerts-closed')||'[]');
    var vis=0;
    document.querySelectorAll('.arow-al').forEach(function(r){
      var h=hid.indexOf(r.dataset.aid)>=0;
      r.classList.toggle('hidden',h); if(!h)vis++;
    });
    var b=document.getElementById('alerts-count'); if(b)b.textContent=vis?String(vis):'';
    var box=document.getElementById('alerts-box');
    if(box)box.classList.toggle('empty',vis===0);
  }
  hideAlerts();
  document.addEventListener('click',function(e){
    var c=e.target.closest('.aclose'); if(!c)return;
    var hid=JSON.parse(localStorage.getItem('wxbt-alerts-closed')||'[]');
    if(hid.indexOf(c.dataset.aid)<0)hid.push(c.dataset.aid);
    while(hid.length>300)hid.shift();
    localStorage.setItem('wxbt-alerts-closed',JSON.stringify(hid));
    hideAlerts();
  });
  // limpiar TODAS las alertas de una (pedido 2026-07-12): marca todas las visibles como cerradas
  document.addEventListener('click',function(e){
    var b=e.target.closest('#alerts-clear'); if(!b)return;
    var hid=JSON.parse(localStorage.getItem('wxbt-alerts-closed')||'[]');
    document.querySelectorAll('.arow-al:not(.hidden)').forEach(function(r){
      if(hid.indexOf(r.dataset.aid)<0)hid.push(r.dataset.aid);
    });
    while(hid.length>300)hid.shift();
    localStorage.setItem('wxbt-alerts-closed',JSON.stringify(hid));
    hideAlerts();
  });
  window.__wxbtApply = function(){ apply(); hideAudit(); hideAlerts(); };
  // [FIX 2026-07-15] endpoints /timeline y /action SOLO existen bajo el servidor propio del
  // dashboard (--serve). Servido por un http.server comun (o file://) devuelven HTML 404/501 y
  // el .json() reventaba con "Unexpected token '<'". wxJSON detecta el caso y tira un mensaje
  // claro y accionable en vez del error criptico.
  var WX_SERVE_MSG='Esta función necesita el dashboard corriendo con su propio servidor. '
    +'Arrancalo así:  python scripts/dashboard.py --watch --serve  '
    +'y abrí http://127.0.0.1:8765/live_dashboard.html (no el archivo suelto ni otro servidor).';
  function wxJSON(r){
    var ct=(r.headers && r.headers.get && r.headers.get('content-type'))||'';
    if(!r.ok || ct.indexOf('application/json')<0){ throw new Error(WX_SERVE_MSG); }
    return r.json();
  }
  // TIMELINE 24h por card (slider 30 min, hora UTC-3). Modal appendeado a <body>, FUERA de
  // .viz-root: el morph del --watch jamas lo toca, sobrevive refrescos.
  function tlOpen(st, fe){
    var m=document.getElementById('tl-modal');
    if(!m){
      m=document.createElement('div'); m.id='tl-modal';
      m.innerHTML='<div class="tl-box"><div class="tl-head"><span id="tl-title"></span>'
        +'<span class="tl-x" id="tl-x">✕</span></div><div id="tl-body"></div></div>';
      document.body.appendChild(m);
      m.addEventListener('click',function(e){ if(e.target===m) m.style.display='none'; });
      m.querySelector('#tl-x').addEventListener('click',function(){ m.style.display='none'; });
    }
    m.style.display='flex';
    document.getElementById('tl-title').textContent='⏱ '+st+' · '+fe;
    var body=document.getElementById('tl-body');
    body.textContent='cargando timeline de 24h…';
    if(location.protocol.indexOf('http')!==0){ body.textContent=WX_SERVE_MSG; return; }
    fetch('/timeline?st='+encodeURIComponent(st)+'&date='+encodeURIComponent(fe))
      .then(wxJSON)
      .then(function(j){ if(!j.ok){ body.textContent='sin datos: '+(j.msg||''); return; } tlRender(body,j,st); })
      .catch(function(e){ body.textContent=(e&&e.message)||(''+e); });
  }
  function tlRender(body, j, st){
    var n=j.times.length;
    // [2026-07-13] mercado PASADO = el ancla (ultimo precio real) esta >2h antes de ahora: el
    // extremo del slider es el CIERRE, no AHORA, y la Δ se mide contra el cierre.
    var isPast = (Date.now()/1000 - j.times[n-1]) > 7200;
    var anchorTxt = isPast ? 'cierre' : 'AHORA';
    body.innerHTML='<div class="tl-ctl"><input type="range" id="tl-sl" min="0" max="'+(n-1)+'" value="'+(n-1)+'" step="1">'
      +'<span class="tl-time" id="tl-time"></span></div>'
      +'<div class="tl-bot" id="tl-bot"></div><table id="tl-tab"></table>'
      +'<div class="tl-note">arrastra el slider: cada paso = 30 min · Δ = cuanto se movio el precio de ese momento al '+anchorTxt+' · '
      +j.city+(isPast?' · mercado ya resuelto: ventana = 24h antes del cierre':'')+'</div>';
    var sl=document.getElementById('tl-sl');
    function f2(x){return (x<10?'0':'')+x;}
    function draw(){
      var i=+sl.value;
      var t=new Date((j.times[i]-3*3600)*1000);   // epoch UTC -> mostrado como UTC-3
      document.getElementById('tl-time').textContent =
        f2(t.getUTCDate())+'/'+f2(t.getUTCMonth()+1)+' '+f2(t.getUTCHours())+':'+f2(t.getUTCMinutes())
        +(i===n-1?' AR · '+anchorTxt
                 :' AR · '+(((n-1-i)*30)/60).toFixed(1)+'h antes del '+anchorTxt);
      var mu=j.mu[i], rk=(j.ranks&&j.ranks[i])||[];
      var t2=rk[1], t3=rk[2];
      // [2026-07-13] marca del BLOQUEO: desde j.frz el pronostico esta FIJADO (mu clavado por el
      // server) — visible para verificar que nada se mueve despues del freeze.
      var frozen = j.frz && j.times[i] >= j.frz;
      var ftag = '';
      if (j.frz) {
        var ft = new Date((j.frz - 3*3600) * 1000);
        var fs = f2(ft.getUTCDate())+'/'+f2(ft.getUTCMonth()+1)+' '+f2(ft.getUTCHours())+':'+f2(ft.getUTCMinutes());
        ftag = frozen ? ' · <b style="color:#ffb020">🔒 FIJADO desde '+fs+' AR</b>'
                      : ' · <span style="color:#587085">se fija '+fs+' AR (04:30 local)</span>';
      }
      document.getElementById('tl-bot').innerHTML = ((mu==null)
        ? 'bot: sin prediccion registrada en ese momento'
        : 'bot predecia <b>'+mu.toFixed(1)+j.unit+'</b> → pick <b>'+j.pick[i]+'</b>'
          +(t2?'  ·  <span class="tl-y">top-2 '+t2+'</span>':'')
          +(t3?'  ·  <span class="tl-o">top-3 '+t3+'</span>':'')) + ftag;
      var rows='<tr><th></th><th>bucket</th><th>precio en ese momento</th><th class="num">$</th><th class="num">Δ→'+anchorTxt+'</th></tr>';
      j.labels.forEach(function(lab){
        var p=j.prices[lab][i], pn=j.prices[lab][n-1];
        var w=(p==null)?0:Math.max(2,Math.round(p*100));
        var dl=(p!=null&&pn!=null)?(((pn-p)>=0?'+':'')+Math.round((pn-p)*100)+'c'):'—';
        // marca del bot EN ESE MOMENTO: top-1 pick verde, top-2 amarillo, top-3 naranja
        var dot='', cls='';
        if(lab===rk[0]){dot='<span class="tl-dot g"></span>';cls='tl-r1';}
        else if(lab===rk[1]){dot='<span class="tl-dot y"></span>';cls='tl-r2';}
        else if(lab===rk[2]){dot='<span class="tl-dot o"></span>';cls='tl-r3';}
        rows+='<tr class="'+cls+'"><td>'+dot+'</td><td>'+lab+'</td>'
          +'<td><span class="track"><span class="fill" style="width:'+w+'%"></span></span></td>'
          +'<td class="num">'+(p==null?'—':p.toFixed(2))+'</td><td class="num">'+dl+'</td></tr>';
      });
      document.getElementById('tl-tab').innerHTML=rows;
    }
    sl.addEventListener('input',draw); draw();
  }
  document.addEventListener('click',function(e){
    var b=e.target.closest('.tlb'); if(!b)return;
    if(location.protocol.indexOf('http')!==0){
      if(qmsg){qmsg.className='qmsg err';qmsg.textContent='el timeline necesita el modo http (puerto 8765)';}
      return;
    }
    tlOpen(b.dataset.tlst, b.dataset.tlfe);
  });
  // tachito de auditoria: TOGGLE limpiar <-> mostrar todo. Las revisiones NUEVAS (corrida nueva,
  // ts distinto) siempre aparecen porque no estan en la lista de ocultas.
  var AC=document.getElementById('audit-clear');
  function hideAudit(){
    var hid=JSON.parse(localStorage.getItem('wxbt-audit-hidden')||'[]');
    document.querySelectorAll('.arow').forEach(function(r){
      r.classList.toggle('hidden', hid.indexOf(r.dataset.k)>=0);
    });
    if(AC) AC.textContent = hid.length ? '↺ mostrar todo' : '🗑 limpiar';
  }
  hideAudit();
  if(AC) AC.addEventListener('click',function(){
    var hid=JSON.parse(localStorage.getItem('wxbt-audit-hidden')||'[]');
    if(hid.length){ localStorage.setItem('wxbt-audit-hidden','[]'); }
    else { document.querySelectorAll('.arow:not(.hidden)').forEach(function(r){hid.push(r.dataset.k)});
           localStorage.setItem('wxbt-audit-hidden',JSON.stringify(hid)); }
    hideAudit();
  });
  // BOTONES RAPIDOS (#9): POST /action?do=X. Solo con --serve (http); si es archivo local, se
  // deshabilitan con un aviso. El mensaje de estado queda visible hasta la proxima accion.
  var isHttp = location.protocol.indexOf('http')===0;
  var qbtns = document.querySelectorAll('.qbtn[data-do]');
  var qmsg = document.getElementById('q-msg');
  if(!isHttp){
    qbtns.forEach(function(b){ b.disabled=true; });
    if(qmsg) qmsg.textContent='(abrí el dashboard vía http://…:8765 para usar los botones)';
  } else {
    qbtns.forEach(function(b){
      b.addEventListener('click',function(){
        var did=b.dataset.do;
        qbtns.forEach(function(x){x.classList.add('busy')});
        if(qmsg){qmsg.className='qmsg';qmsg.textContent='⏳ '+b.textContent.trim()+'…';}
        fetch('/action?do='+encodeURIComponent(did),{method:'POST'})
          .then(wxJSON)
          .then(function(j){
            if(qmsg){qmsg.className='qmsg '+(j.ok?'ok':'err');qmsg.textContent=(j.ok?'✓ ':'✗ ')+(j.msg||did);}
            // acciones que cambian el HTML: refrescar la vista al toque
            if(j.ok && (did==='regen'||did==='cache'||did==='forecasts'||did==='orderbook'||did==='live')){
              setTimeout(function(){ if(window.__wxbtReload)window.__wxbtReload(); },400);
            }
          })
          .catch(function(e){ if(qmsg){qmsg.className='qmsg err';qmsg.textContent='✗ '+((e&&e.message)||e);} })
          .then(function(){ qbtns.forEach(function(x){x.classList.remove('busy')}); });
      });
    });
  }
  // restaurar scroll tras un reload de --watch
  var sy=sessionStorage.getItem('wxbt-scroll');
  if(sy) window.scrollTo(0, parseInt(sy));
})();


(function(){
  var iv = parseInt((document.body.getAttribute('data-interval')||'0'),10);
  if(!iv){ return; }
  var total=iv,left=total;
  var el=document.getElementById('viz-countdown');
  function morph(a,b){
    if(a.children.length!==b.children.length||a.tagName!==b.tagName){a.replaceWith(b.cloneNode(true));return;}
    if(a.children.length===0){ if(a.textContent!==b.textContent){a.textContent=b.textContent;
      if(!(a.closest&&a.closest('[data-noanim]'))){a.classList.remove('chg');void a.offsetWidth;a.classList.add('chg');}}
      if(a.getAttribute('style')!==b.getAttribute('style'))a.setAttribute('style',b.getAttribute('style')||'');
      return;}
    for(var i=0;i<a.children.length;i++)morph(a.children[i],b.children[i]);}
  function pull(){ if(location.protocol.indexOf('http')!==0){
      sessionStorage.setItem('wxbt-scroll', String(window.scrollY)); location.reload(); return; }
    fetch(location.href,{cache:'no-store'}).then(function(r){return r.text()}).then(function(t){
      var nd=new DOMParser().parseFromString(t,'text/html');
      var nr=nd.querySelector('.viz-root'),or=document.querySelector('.viz-root');
      if(nr&&or){morph(or,nr);if(window.__wxbtApply)window.__wxbtApply();}}).catch(function(){}); }
  window.__wxbtReload=pull;
  function tick(){left--;if(el)el.textContent='refresco en '+Math.max(left,0)+'s';
    if(left>0)return; left=total; pull(); }
  setInterval(tick,1000);
})();
