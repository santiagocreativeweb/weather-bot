#!/usr/bin/env python3
# scripts/city_js.py — JS de cliente para city.html / cities.html (separado de city_pages.py para
# no pelear con heredocs/comillas). CITY_JS renderiza el dashboard de UNA ciudad desde
# window.__CITIES_DATA (mapa CARTO, timeline v2, charts, auto-refresh); INDEX_JS el grid del indice.
# El timeline v2 (pedido Santiago 2026-07-16): precios en % (0.365->36.5), lineas de freeze 24h/48h,
# colores claros (top-1 verde / top-2 amarillo / top-3 naranja / resto gris), toggle grafico/tabla,
# gear para elegir buckets, slider-cursor sobre el eje X.

SHARED_JS = r"""
var DATA=(window.__CITIES_DATA||{cities:{},index:[],generated:''});
function qs(k){return new URLSearchParams(location.search).get(k);}
function esc(s){var d=document.createElement('div');d.textContent=(s==null?'':s);return d.innerHTML;}
function ts2ar(t){var d=new Date((t-3*3600)*1000);function f(x){return(x<10?'0':'')+x;}
  return f(d.getUTCDate())+'/'+f(d.getUTCMonth()+1)+' '+f(d.getUTCHours())+':'+f(d.getUTCMinutes());}
var PICKICO=['🎯','🥈','🥉'],PICKCLS=['top1','top2','top3'];
function pickHtml(top){return (top||[]).map(function(l,i){return '<span class="'+PICKCLS[i]+'">'+PICKICO[i]+esc(l)+'</span>';}).join('  ');}
function autoRefresh(cb){
  fetch('cities_data.js?t='+Date.now(),{cache:'no-store'}).then(function(r){return r.text();})
   .then(function(t){(0,eval)(t);DATA=window.__CITIES_DATA;cb&&cb();})
   .catch(function(){try{location.reload();}catch(e){}});
}
"""

CITY_JS = SHARED_JS + r"""
(function(){
  var codes=Object.keys(DATA.cities);
  if(!codes.length){document.getElementById('cbody').innerHTML='<p class="none">Sin datos.</p>';return;}
  var code=qs('city'); if(!DATA.cities[code])code=codes[0];
  var sel=document.getElementById('citysel');
  DATA.index.forEach(function(it){if(DATA.cities[it.code]){var o=document.createElement('option');o.value=it.code;o.textContent=it.city+' · '+it.code;sel.appendChild(o);}});
  sel.value=code;
  sel.addEventListener('change',function(){code=sel.value;history.replaceState(null,'','city.html?city='+code);render();});
  var tlState={mode:'graph',hours:48,cursor:null,hidden:{}},tlChart=null,obsChart=null,cmap=null;

  function card(l,b,sub,cls){return '<div class="scard '+(cls||'')+'"><div class="lbl">'+l+'</div><div class="big">'+b+'</div><div class="sub">'+sub+'</div></div>';}
  function statCards(C){var s=C.stats,c=[];
    c.push(card('✅ aciertos exactos',s.n?(s.ex+'/'+s.n):'—',s.n?(Math.round(100*s.ex/s.n)+'% de '+s.n):'sin resueltos'));
    c.push(card('🟡 acierto top-2',s.n?(Math.round(100*s.t2/s.n)+'%'):'—',s.n?(s.t2+'/'+s.n+' intentos'):'sin resueltos','y'));
    if(s.same)c.push(card('🌡 temperatura',s.tnow+C.deg,'la actual ES la máxima registrada hasta el momento'));
    else{if(s.tmax!=null)c.push(card('🔺 máx registrada hoy',s.tmax+C.deg,'estación de resolución'));
         if(s.tnow!=null)c.push(card('🌡 temperatura actual',s.tnow+C.deg,C.weather.ico+' '+C.weather.txt));}
    var pk=C.picks[0],pko=pk?(pk.p24||pk.p48||pk.prelim):null;
    c.push(card('🔒 pick de hoy',pko?('<span style="color:var(--pick)">'+esc(pko.top[0]||'—')+'</span>'):'—',pk?(pk.p24?'fijado 04:30 local':(pk.p48?'fijado 48h antes':'preliminar')):'se fija 04:30 local'));
    if(s.pos)c.push(card('🏆 estabilidad','#'+s.pos,'de '+s.total+' ciudades'));
    return '<div class="sgrid">'+c.join('')+'</div>';
  }
  function picksBox(C){
    /* [2026-07-17, pedido Santiago] AMBOS picks por dia (24h Y 48h) en lineas separadas */
    if(!C.picks.length)return '';
    var rows=C.picks.map(function(p){
      var L=['<div class="pickrow"><div style="color:var(--mut);font-weight:700">'+esc(p.date)+'</div>'];
      if(p.p24)L.push('<div style="margin:3px 0 0">🔒 <b>24h</b> · μ '+p.p24.mu+C.deg+'<br>&nbsp;&nbsp;'+pickHtml(p.p24.top)+'</div>');
      if(p.p48)L.push('<div style="margin:3px 0 0">⏳ <b>48h</b> · μ '+p.p48.mu+C.deg+'<br>&nbsp;&nbsp;'+pickHtml(p.p48.top)+'</div>');
      if(p.prelim)L.push('<div style="margin:3px 0 0">◷ preliminar · μ '+p.prelim.mu+C.deg+'<br>&nbsp;&nbsp;'+pickHtml(p.prelim.top)+'</div>');
      L.push('</div>');return L.join('');}).join('');
    return '<div class="panelbox"><h4>🔒 Picks 24h / 48h — 🎯 exacto · 🥈 top-2 · 🥉 top-3</h4>'+rows+
      '<p class="subt" style="margin:6px 0 0">24h = fijado 04:30 local (lo que se opera). 48h = fijado un día antes (mejor precio de entrada; lo mide el tab 48hs de Estadísticas).</p></div>';
  }
  function mktBox(C){
    return C.markets.map(function(m){
      var rows=m.rows.map(function(r){var cls=[r.cls,(r.dead?'dead':'')].filter(Boolean).join(' ');
        return '<tr class="'+cls+'"><td>'+esc(r.lab)+'</td><td class="num">'+r.mid.toFixed(2)+'</td><td class="num">'+(r.pbot!=null?Math.round(r.pbot*100)+'%':'—')+'</td><td class="num">'+(r.edge!=null?(r.edge>=0?'+':'')+r.edge:'—')+'</td></tr>';}).join('');
      var mut=(m.mu!=null?('μ <b>'+m.mu+C.deg+'</b> σ '+m.sg+' '+(m.frozen?'🔒':'◷')):'sin predicción');
      var lmt=(m.live_max!=null?' · máx en vivo: <b>'+m.live_max+C.deg+'</b>':'');
      var win=(m.winner?'<p class="subt">🏁 ganó <b>'+esc(m.winner)+'</b></p>':'');
      return '<div class="panelbox"><h4>🎯 Mercado '+m.head+' — '+esc(m.date)+'</h4>'+
        '<p class="subt" style="margin:0 0 8px">'+mut+lmt+' · <a href="'+m.url+'" target="_blank">Polymarket ↗</a> · <a href="'+m.wu+'" target="_blank">'+(C.code==='HKO'?'HKO ↗':'WU ↗')+'</a></p>'+
        (rows?('<table class="ct"><thead><tr><th>rango</th><th>mercado</th><th>p bot</th><th>Δ¢</th></tr></thead><tbody>'+rows+'</tbody></table>'):'<p class="subt">sin mercado.</p>')+win+'</div>';
    }).join('');
  }
  function pwsBox(C){
    var rows=C.pws.map(function(p){return '<tr><td>'+esc(p.id)+'</td><td class="num">'+p.km.toFixed(1)+'</td><td class="num">'+(p.bias>=0?'+':'')+p.bias.toFixed(2)+'</td><td class="num">'+p.std.toFixed(2)+'</td><td class="num">'+(p.now!=null?p.now.toFixed(1)+C.deg:'—')+'</td></tr>';}).join('');
    var est=(C.est!=null?'<p class="subt" style="margin:8px 0 0">estimado del sensor oficial AHORA: <b style="color:var(--live);font-size:16px">'+C.est+C.deg+'</b> = mediana(PWS − bias)</p>':'');
    return '<div class="panelbox"><h4>🗺 Estación + PWS (mapa CARTO)</h4><div id="citymap"></div>'+est+
      (rows?('<table class="ct" style="margin-top:10px"><thead><tr><th>pws</th><th>km</th><th>bias</th><th>σ</th><th>ahora</th></tr></thead><tbody>'+rows+'</tbody></table>'):'<p class="subt">sin PWS</p>')+'</div>';
  }
  function perfBox(C){
    function tb(arr,src){return arr.map(function(r){return '<tr><td>'+r.m+'</td><td class="num">'+src+'</td><td class="num">'+r.hits+'/'+r.n+'</td><td class="num">'+Math.round(r.rate*100)+'%</td><td class="num">'+(r.mae!=null?r.mae:'—')+'</td></tr>';}).join('');}
    var rows=tb(C.models.vivo,'vivo')+tb(C.models.retro,'retro'); if(!rows)return '';
    return '<div class="panelbox"><h4>🧪 Qué modelo acierta acá</h4><table class="ct"><thead><tr><th>modelo</th><th>fuente</th><th>exactos</th><th>%</th><th>MAE</th></tr></thead><tbody>'+rows+'</tbody></table><p class="subt" style="margin:6px 0 0">los que más pesan son los que mejor aciertan en ESTA ciudad.</p></div>';
  }
  function histBox(C){
    /* [2026-07-17] columna pick 48h (con su propio resultado) + boton para re-consultar Gamma */
    if(!C.history.length)return '';
    var IC={'EXACTO':['✅','g-ex'],'TOP-2':['✅','g-t2'],'TOP-3':['🔶','g-t3'],'PERDIDA':['❌','g-bad']};
    var rows=C.history.map(function(r){var ic=IC[r.niv]||['⏳',''];
      var i48=r.niv48?(IC[r.niv48]||['⏳','']):null;
      var c48=(r.pick48!=null)?(esc(r.pick48)+(i48?(' <span class="gv '+i48[1]+'">'+i48[0]+'</span>'):'')):'—';
      return '<tr><td>'+esc(r.date)+'</td><td>'+esc(r.pick)+'</td><td>'+c48+'</td><td>'+esc(r.win)+'</td><td><span class="gv '+ic[1]+'">'+ic[0]+' '+(r.niv||'pend.')+'</span></td></tr>';}).join('');
    return '<div class="panelbox"><h4>🗓 Historial — pick 24h y 48h</h4><table class="ct"><thead><tr><th>fecha</th><th>pick 24h 🔒</th><th>pick 48h ⏳</th><th>ganó</th><th>resultado</th></tr></thead><tbody>'+rows+'</tbody></table>'+
      '<div style="margin-top:8px"><button class="chip" id="histref">🔄 Actualizar resultados</button> <span class="subt" id="histrefmsg"></span></div>'+
      '<p class="subt" style="margin:4px 0 0">resultado = pick 24h (KPI oficial); el ✅/❌ junto al pick 48h es su propio resultado (acumula desde 16/07).</p></div>';
  }
  function wireHist(){
    var hb=document.getElementById('histref'); if(!hb)return;
    hb.addEventListener('click',function(){
      var m=document.getElementById('histrefmsg');
      if(location.protocol==='file:'){m.textContent='abrí la página servida por el dashboard (http) para actualizar en vivo';return;}
      m.textContent='actualizando resultados… (~1 min)'; hb.disabled=true;
      fetch('/action?do=results',{method:'POST'}).then(function(r){
        var ct=(r.headers.get('content-type')||'');
        if(!r.ok||ct.indexOf('application/json')<0)throw new Error('serví la página con el dashboard (--serve) para usar el refresh');
        return r.json();
      }).then(function(j){m.textContent=j.msg||'listo';hb.disabled=false;autoRefresh(function(){render();});})
        .catch(function(e){m.textContent=''+(e.message||e);hb.disabled=false;});
    });
  }
  function tlBox(C){
    if(!C.tl||!C.tl.labels.length)return '';
    var opts=C.tl.labels.map(function(l){return '<label><input type="checkbox" data-b="'+esc(l)+'" checked> '+esc(l)+'</label>';}).join('');
    return '<div class="panelbox" style="position:relative"><h4>⏱ Timeline del mercado — precios en %</h4>'+
      '<div class="tlbar"><button class="chip on" data-m="graph">📊 Gráfico</button><button class="chip" data-m="table">📋 Tabla</button>'+
      '<span style="width:8px"></span><button class="chip on" data-h="24">24 hs</button><button class="chip" data-h="48">48 hs</button>'+
      '<span class="sp"></span><button class="chip" id="tlgear">⚙ Buckets</button></div>'+
      '<div class="gearpop hidden" id="tlgearpop" style="right:16px">'+opts+'</div>'+
      '<div class="tllegend"><span><i style="background:var(--pick)"></i>🎯 exacto (top-1)</span>'+
      '<span><i style="background:var(--t2)"></i>🥈 top-2</span><span><i style="background:var(--t3)"></i>🥉 top-3</span>'+
      '<span><i style="background:#5b6b7d"></i>otros</span><span><i style="background:var(--live)"></i>🔒 freeze 24h/48h</span></div>'+
      '<div id="tlgraph"><div class="chartbox"><canvas id="tlchart"></canvas></div>'+
      '<input type="range" id="tlrange" min="0" value="0"><div class="tlcursor" id="tlcursor"></div></div>'+
      '<div id="tltable" class="hidden"></div></div>';
  }

  var freezePlugin={id:'frz',afterDraw:function(ch){
    var d=ch.$tl; if(!d)return; var a=ch.chartArea,ctx=ch.ctx;
    function xpix(i){return a.left+(i/(d.n-1))*(a.right-a.left);}
    function vline(i,color,lbl){if(i<0||i>d.n-1)return;var px=xpix(i);
      ctx.save();ctx.strokeStyle=color;ctx.lineWidth=1.5;ctx.setLineDash([5,4]);
      ctx.beginPath();ctx.moveTo(px,a.top);ctx.lineTo(px,a.bottom);ctx.stroke();
      ctx.setLineDash([]);ctx.fillStyle=color;ctx.font='10px monospace';ctx.fillText(lbl,px+3,a.top+11);ctx.restore();}
    vline(d.i24,'#ffc24a','🔒24h');vline(d.i48,'rgba(255,194,74,.6)','⏳48h');
    if(d.cursor!=null){var px=xpix(d.cursor);ctx.save();ctx.strokeStyle='#e8f0f7';ctx.lineWidth=1;
      ctx.setLineDash([2,3]);ctx.beginPath();ctx.moveTo(px,a.top);ctx.lineTo(px,a.bottom);ctx.stroke();ctx.restore();}
  }};
  function sliceInfo(C){var nAll=C.tl.times.length,keep=tlState.hours*2+1,i0=Math.max(0,nAll-keep);return {i0:i0,times:C.tl.times.slice(i0),n:C.tl.times.length-i0};}
  function idxOf(times,ep){var n=times.length,best=-1,bd=1e18;for(var i=0;i<n;i++){var dd=Math.abs(times[i]-ep);if(dd<bd){bd=dd;best=i;}}return (ep<times[0]-1800||ep>times[n-1]+1800)?-1:best;}
  function buildTL(C){
    var tl=C.tl,si=sliceInfo(C),i0=si.i0,times=si.times,n=si.n,labels=times.map(ts2ar),top=tl.top||[];
    var PAL=['#5b6b7d','#7b8fa3','#4a7fb0','#6a5b8f','#8f6a5b','#5b8f7a'],pi=0,ds=[];
    tl.labels.forEach(function(lab){ if(tlState.hidden[lab])return;
      var color,w=1.6;
      if(lab===top[0]){color='#25e6a4';w=2.4;}else if(lab===top[1]){color='#ffd23e';w=2.2;}
      else if(lab===top[2]){color='#ff9142';w=2.2;}else{color=PAL[pi%PAL.length];pi++;}
      ds.push({label:lab,data:tl.series[lab].slice(i0).map(function(v){return v==null?null:+(v*100).toFixed(1);}),
        borderColor:color,backgroundColor:color,borderWidth:w,pointRadius:0,pointHitRadius:6,spanGaps:true,tension:.15});});
    var muv=tl.mu.slice(i0);
    if(muv.some(function(v){return v!=null;}))ds.push({label:'μ bot',data:muv,borderColor:'#25e6a4',borderDash:[6,4],borderWidth:1.5,pointRadius:0,spanGaps:true,yAxisID:'y2'});
    if(tlChart)tlChart.destroy();
    var el=document.getElementById('tlchart'); if(!el)return;
    tlChart=new Chart(el,{type:'line',data:{labels:labels,datasets:ds},plugins:[freezePlugin],
      options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
        plugins:{legend:{labels:{boxWidth:9,boxHeight:9,font:{size:10}}},
          tooltip:{backgroundColor:'#0e151d',borderColor:'#2b3f52',borderWidth:1,callbacks:{label:function(c){return c.dataset.label+': '+(c.parsed.y==null?'—':(c.dataset.yAxisID==='y2'?c.parsed.y+C.deg:c.parsed.y+'%'));}}}},
        scales:{x:{offset:false,ticks:{maxTicksLimit:9,maxRotation:0,font:{size:9}}},
          y:{min:0,max:100,title:{display:true,text:'prob %'},ticks:{font:{size:9}}},
          y2:{position:'right',grid:{display:false},title:{display:true,text:'μ '+C.deg},ticks:{font:{size:9}}}}}});
    tlChart.$tl={n:n,i24:idxOf(times,tl.frz),i48:idxOf(times,tl.frz48),cursor:tlState.cursor};
    var rg=document.getElementById('tlrange'); rg.max=n-1; if(tlState.cursor==null||tlState.cursor>n-1)tlState.cursor=n-1; rg.value=tlState.cursor;
    updateCursor(C);
  }
  function updateCursor(C){
    var si=sliceInfo(C),i=+document.getElementById('tlrange').value,top=C.tl.top||[];
    tlState.cursor=i; if(tlChart){tlChart.$tl.cursor=i;tlChart.draw();}
    var parts=[]; top.forEach(function(lab,k){if(!lab)return;var v=C.tl.series[lab]?C.tl.series[lab][si.i0+i]:null;parts.push(PICKICO[k]+' '+esc(lab)+' <b>'+(v==null?'—':(v*100).toFixed(1)+'%')+'</b>');});
    var mu=C.tl.mu[si.i0+i];
    document.getElementById('tlcursor').innerHTML='🕐 <b>'+ts2ar(si.times[i])+' AR</b> · '+(parts.join(' · ')||'sin datos')+(mu!=null?(' · μ '+mu+C.deg):'');
  }
  function buildTable(C){
    var tl=C.tl,si=sliceInfo(C),i0=si.i0,nAll=tl.times.length;
    var labs=tl.labels.filter(function(l){return !tlState.hidden[l];});
    var head='<tr><th>hora AR</th>'+labs.map(function(l){return '<th>'+esc(l)+'</th>';}).join('')+'<th>μ</th></tr>';
    var rows='';
    for(var i=nAll-1;i>=i0;i--){
      var cells=labs.map(function(l){var v=tl.series[l]?tl.series[l][i]:null;return '<td>'+(v==null?'—':(v*100).toFixed(1))+'</td>';}).join('');
      rows+='<tr><td>'+ts2ar(tl.times[i])+'</td>'+cells+'<td>'+(tl.mu[i]==null?'—':tl.mu[i])+'</td></tr>';
    }
    document.getElementById('tltable').innerHTML='<div class="tltabwrap"><table class="tltab"><thead>'+head+'</thead><tbody>'+rows+'</tbody></table></div><p class="subt" style="margin:6px 0 0">precios en % (0.365→36.5). μ en '+C.deg+'.</p>';
  }
  function wireTL(C){
    document.querySelectorAll('.tlbar [data-m]').forEach(function(b){b.addEventListener('click',function(){
      document.querySelectorAll('.tlbar [data-m]').forEach(function(x){x.classList.remove('on');});b.classList.add('on');tlState.mode=b.dataset.m;
      document.getElementById('tlgraph').classList.toggle('hidden',tlState.mode!=='graph');
      document.getElementById('tltable').classList.toggle('hidden',tlState.mode!=='table');
      if(tlState.mode==='graph')buildTL(C);else buildTable(C);});});
    document.querySelectorAll('.tlbar [data-h]').forEach(function(b){b.addEventListener('click',function(){
      document.querySelectorAll('.tlbar [data-h]').forEach(function(x){x.classList.remove('on');});b.classList.add('on');tlState.hours=+b.dataset.h;tlState.cursor=null;
      if(tlState.mode==='graph')buildTL(C);else buildTable(C);});});
    var gp=document.getElementById('tlgearpop');
    document.getElementById('tlgear').addEventListener('click',function(e){e.stopPropagation();gp.classList.toggle('hidden');});
    document.addEventListener('click',function(){gp.classList.add('hidden');});
    gp.addEventListener('click',function(e){e.stopPropagation();});
    gp.querySelectorAll('input[data-b]').forEach(function(c){c.addEventListener('change',function(){tlState.hidden[c.dataset.b]=!c.checked;if(tlState.mode==='graph')buildTL(C);else buildTable(C);});});
    document.getElementById('tlrange').addEventListener('input',function(){updateCursor(C);});
  }
  function drawMap(C){
    var el=document.getElementById('citymap'); if(!el||!window.L)return;
    if(cmap){cmap.remove();cmap=null;}
    cmap=L.map('citymap',{scrollWheelZoom:false}).setView([C.lat,C.lon],12);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{attribution:'© OSM · © CARTO',subdomains:'abcd',maxZoom:19}).addTo(cmap);
    var star=L.divIcon({className:'',html:'<div style="font-size:22px;text-shadow:0 0 8px #ffc24a">★</div>',iconSize:[22,22],iconAnchor:[11,11]});
    L.marker([C.lat,C.lon],{icon:star}).addTo(cmap).bindTooltip('<b>'+C.code+'</b> · resolución',{className:'pwstip'});
    var pts=[[C.lat,C.lon]];
    (C.pws||[]).forEach(function(p){if(!p.lat)return;pts.push([p.lat,p.lon]);
      L.circleMarker([p.lat,p.lon],{radius:8,color:'#42c9ff',weight:1.5,fillColor:'#42c9ff',fillOpacity:.55}).addTo(cmap).bindTooltip('<b>'+p.id+'</b><br>'+(p.now!=null?('ahora '+p.now.toFixed(1)+'°<br>'):'')+'bias '+(p.bias>=0?'+':'')+p.bias.toFixed(2)+' · σ '+p.std.toFixed(2)+' · '+p.km.toFixed(1)+'km',{className:'pwstip'});
      var t=L.divIcon({className:'',html:'<div style="color:#8fe3ff;font:10px monospace;text-shadow:0 1px 2px #000;transform:translate(-50%,-190%);white-space:nowrap">'+(p.now!=null?p.now.toFixed(1)+'°':'')+'</div>',iconSize:[0,0]});
      L.marker([p.lat,p.lon],{icon:t,interactive:false}).addTo(cmap);});
    if(pts.length>1)cmap.fitBounds(pts,{padding:[34,34]});
  }
  function drawObs(C){
    var el=document.getElementById('histchart'); if(!el||!window.Chart)return;
    var labels=C.obs.map(function(o){return o.x.slice(5).split('-').reverse().join('/');});
    var pmap={};(C.picks30||[]).forEach(function(p){pmap[p.x]=p.y;});
    if(obsChart)obsChart.destroy();
    obsChart=new Chart(el,{type:'line',data:{labels:labels,datasets:[
      {label:'obs real',data:C.obs.map(function(o){return o.y;}),borderColor:'#42c9ff',backgroundColor:'rgba(66,201,255,.12)',borderWidth:2,pointRadius:2,fill:true,spanGaps:true},
      {label:'pick congelado (μ)',data:C.obs.map(function(o){return pmap[o.x]!=null?pmap[o.x]:null;}),borderColor:'#25e6a4',backgroundColor:'#25e6a4',borderWidth:0,pointRadius:4,showLine:false}]},
      options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
        plugins:{legend:{labels:{boxWidth:10}},tooltip:{backgroundColor:'#0e151d',borderColor:'#2b3f52',borderWidth:1}},
        scales:{x:{ticks:{maxTicksLimit:12,maxRotation:0,font:{size:9}}},y:{title:{display:true,text:C.deg}}}}});
  }
  function render(){
    var C=DATA.cities[code]; if(!C){document.getElementById('cbody').innerHTML='<p class="none">Ciudad no encontrada</p>';return;}
    document.getElementById('ctitle').innerHTML='🏙 '+esc(C.city)+' · '+C.code;
    document.getElementById('clinks').innerHTML='<a href="cities.html">← ciudades</a><a href="'+C.markets[0].url+'" target="_blank">Polymarket ↗</a><a href="'+C.markets[0].wu+'" target="_blank">'+(C.code==='HKO'?'HKO ↗':'WU ↗')+'</a><a href="https://www.windy.com/'+C.lat.toFixed(3)+'/'+C.lon.toFixed(3)+'" target="_blank">Windy ↗</a>';
    document.getElementById('cgen').innerHTML='🕒 '+DATA.generated+' (AR) · '+esc(C.country)+' · '+esc(C.cont)+' · '+C.resol;
    document.getElementById('cbody').innerHTML=statCards(C)+tlBox(C)+
      '<div class="cols"><div class="col">'+mktBox(C)+picksBox(C)+'</div><div class="col">'+pwsBox(C)+perfBox(C)+histBox(C)+'</div></div>'+
      '<div class="panelbox"><h4>📈 Últimos 30 días — obs real vs pick congelado</h4><div class="chartbox"><canvas id="histchart"></canvas></div></div>';
    tlState={mode:'graph',hours:48,cursor:null,hidden:{}};
    drawMap(C);
    if(C.tl&&C.tl.labels.length){buildTL(C);wireTL(C);}
    wireHist();
    drawObs(C);
  }
  render();
  var refN=90;
  setInterval(function(){if(!document.getElementById('autoref').checked||document.hidden)return;autoRefresh(function(){if(DATA.cities[code])render();});},refN*1000);
  setInterval(function(){var t=document.getElementById('reftxt');if(t)t.textContent=(document.getElementById('autoref').checked?'cada '+refN+'s':'pausado');},1000);
})();
"""

INDEX_JS = SHARED_JS + r"""
(function(){
  var TCOL={FUERTE:['🟢','var(--fin)'],MEDIA:['🟡','var(--t2)'],DEBIL:['🔴','var(--red)']};
  var cont='all',q='';
  function draw(){
    document.getElementById('idxgen').innerHTML='🕒 '+DATA.generated+' AR';
    var conts=[];DATA.index.forEach(function(it){if(conts.indexOf(it.cont)<0)conts.push(it.cont);});conts.sort();
    document.getElementById('idxfilters').innerHTML='<button class="chip on" data-f="all">Todas ('+DATA.index.length+')</button>'+
      conts.map(function(c){return '<button class="chip" data-f="'+c+'">'+c+'</button>';}).join('')+
      '<input type="search" id="csearch" placeholder="buscar ciudad, país o ICAO…" style="background:var(--s2);color:var(--ink);border:1px solid var(--bd);border-radius:6px;padding:6px 10px;font-size:12px;margin-left:auto;min-width:210px"><span class="count" id="ccount"></span>';
    document.getElementById('cigrid').innerHTML=DATA.index.map(function(it){
      var tc=TCOL[it.tier]||['·','var(--base)'];
      var track=it.n?('<b style="color:var(--fc)">'+it.ex+'/'+it.n+'</b> exactos · '+it.t2+'/'+it.n+' top-2'):'<span style="color:var(--mut)">sin track aún</span>';
      var best=it.best?('<div class="ci-model">🏅 mejor modelo: <b>'+esc(it.best[0])+'</b> '+Math.round(it.best[1]*100)+'% (n='+it.best[2]+')</div>'):'';
      var picks=(it.picks||[]).map(function(p){
        var L=[];
        if(p.p24)L.push('<div class="ci-pk"><span class="d">'+esc(p.date)+' 🔒24h</span> '+pickHtml(p.p24.top)+'</div>');
        if(p.p48)L.push('<div class="ci-pk"><span class="d">'+esc(p.date)+' ⏳48h</span> '+pickHtml(p.p48.top)+'</div>');
        if(p.prelim)L.push('<div class="ci-pk"><span class="d">'+esc(p.date)+' ◷</span> '+pickHtml(p.prelim.top)+'</div>');
        return L.join('');}).join('');
      var pbox=picks?('<div class="ci-picks">'+picks+'</div>'):'';
      return '<a class="ci-card" href="city.html?city='+it.code+'" style="--tcol:'+tc[1]+'" data-cont="'+it.cont+'" data-q="'+esc((it.city+' '+it.code+' '+it.country).toLowerCase())+'"><div class="ci-top"><div><div class="ci-name">'+esc(it.city)+'</div><div class="ci-sub">'+it.code+' · '+esc(it.country)+' · '+it.cont+'</div></div><span style="font-size:15px">'+tc[0]+'</span></div><div class="ci-track">'+track+'</div>'+best+pbox+'</a>';
    }).join('');
    document.querySelectorAll('.chip[data-f]').forEach(function(b){b.addEventListener('click',function(){document.querySelectorAll('.chip[data-f]').forEach(function(x){x.classList.remove('on');});b.classList.add('on');cont=b.dataset.f;apply();});});
    var s=document.getElementById('csearch');s.value=q;s.addEventListener('input',function(){q=s.value.trim().toLowerCase();apply();});
    apply();
  }
  function apply(){var n=0;document.querySelectorAll('.ci-card').forEach(function(c){var ok=(cont==='all'||c.dataset.cont===cont)&&(!q||c.dataset.q.indexOf(q)>=0);c.style.display=ok?'':'none';if(ok)n++;});
    document.getElementById('ccount').textContent=n+' ciudades';document.getElementById('cnone').style.display=n?'none':'';}
  draw();
  setInterval(function(){if(document.hidden)return;autoRefresh(function(){draw();});},120000);
})();
"""
