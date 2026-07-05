""" The dashboard single-page HTML (served inline so it bundles with no template path). """

DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mahjong Copilot Dashboard</title>
<style>
  :root{
    --bg:#0f1220; --card:#181c2e; --card2:#20263e; --line:#2b3350;
    --tx:#e6e9f5; --mut:#8b93b5; --acc:#6ea8fe;
    --g1:#f7c948; --g2:#c7cdd8; --g3:#e08a4b; --g4:#e05a6b; --ok:#3ddc84; --bad:#e05a6b;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--tx);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,"PingFang SC","Microsoft YaHei",sans-serif;}
  header{display:flex;flex-wrap:wrap;align-items:center;gap:10px 18px;
    padding:14px 18px;background:linear-gradient(90deg,#1b2138,#141828);border-bottom:1px solid var(--line);
    position:sticky;top:0;z-index:5}
  header h1{font-size:18px;margin:0;font-weight:700}
  header .sp{flex:1}
  .pill{font-size:12px;padding:3px 9px;border-radius:999px;border:1px solid var(--line);background:var(--card2);color:var(--mut)}
  .pill.on{color:#0c1020;background:var(--ok);border-color:var(--ok);font-weight:600}
  .pill.off{opacity:.5}
  .live{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--mut)}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--bad);transition:background .3s}
  .dot.up{background:var(--ok);box-shadow:0 0 8px var(--ok)}
  main{max-width:1100px;margin:0 auto;padding:16px;display:grid;gap:16px;
    grid-template-columns:1fr 1fr}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
  .card h2{margin:0 0 12px;font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut)}
  .wide{grid-column:1 / -1}
  .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:10px}
  .stat{background:var(--card2);border-radius:10px;padding:10px 12px}
  .stat .v{font-size:26px;font-weight:700;line-height:1.1}
  .stat .k{font-size:11px;color:var(--mut);margin-top:2px}
  .bars{display:flex;gap:8px;align-items:flex-end;height:90px;margin-top:6px}
  .bar{flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;height:100%;justify-content:flex-end}
  .bar .fill{width:100%;border-radius:6px 6px 0 0;min-height:3px;transition:height .4s}
  .bar .lab{font-size:11px;color:var(--mut)}
  .bar .cnt{font-size:13px;font-weight:600}
  .seats{display:flex;flex-direction:column;gap:6px}
  .seat{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:9px;background:var(--card2)}
  .seat.me{background:#25406b;border:1px solid var(--acc)}
  .seat .nm{width:70px;color:var(--mut);font-size:13px}
  .seat .sc{margin-left:auto;font-variant-numeric:tabular-nums;font-weight:600}
  .seat .rc{color:var(--g4);font-weight:700;font-size:12px}
  .hand{font-size:34px;line-height:1.35;letter-spacing:2px;word-break:break-all}
  .tsumo{color:var(--acc)}
  .guide{background:var(--card2);border-radius:10px;padding:12px;margin-top:10px}
  .guide .act{font-size:22px;font-weight:700}
  .opt{display:flex;align-items:center;gap:8px;margin-top:8px}
  .opt .ot{width:64px;font-size:15px}
  .opt .track{flex:1;height:8px;background:#0c1020;border-radius:6px;overflow:hidden}
  .opt .ofill{height:100%;background:var(--acc)}
  .opt .ow{width:52px;text-align:right;color:var(--mut);font-size:12px}
  .spark{display:flex;flex-wrap:wrap;gap:3px}
  .sq{width:15px;height:15px;border-radius:3px;font-size:9px;color:#0c1020;display:flex;align-items:center;justify-content:center;font-weight:700}
  .log{max-height:260px;overflow:auto;font-size:13px}
  .log .row{display:flex;gap:10px;padding:5px 0;border-bottom:1px solid var(--line)}
  .log .t{color:var(--mut);white-space:nowrap;font-variant-numeric:tabular-nums}
  .muted{color:var(--mut)}
  .err{color:var(--bad);font-size:13px;margin-top:8px}
  a{color:var(--acc)}
  @media(max-width:760px){main{grid-template-columns:1fr}.hand{font-size:28px}}
</style>
</head>
<body>
<header>
  <h1>🀄 Mahjong Copilot</h1>
  <span class="pill" id="rank">–</span>
  <span class="sp"></span>
  <span class="pill" id="p-model">model</span>
  <span class="pill off" id="p-play">Autoplay</span>
  <span class="pill off" id="p-rec">Record</span>
  <span class="pill off" id="p-hud">Overlay</span>
  <span class="live"><span class="dot" id="dot"></span><span id="uptime">–</span></span>
</header>
<main>
  <section class="card wide">
    <h2>Session</h2>
    <div class="stats" id="sess"></div>
    <div style="margin-top:14px">
      <div class="muted" style="font-size:12px;margin-bottom:4px">Placement distribution</div>
      <div class="bars" id="bars"></div>
    </div>
  </section>

  <section class="card">
    <h2>Current Game</h2>
    <div id="game"></div>
  </section>

  <section class="card">
    <h2>AI Guidance</h2>
    <div id="guide"><div class="muted">No pending recommendation.</div></div>
  </section>

  <section class="card">
    <h2>Placement History</h2>
    <div class="spark" id="spark"></div>
    <div class="muted" id="pts" style="margin-top:10px;font-size:13px"></div>
  </section>

  <section class="card">
    <h2>Rank History</h2>
    <div id="rankh" class="log"></div>
  </section>

  <section class="card wide">
    <h2>Event Log</h2>
    <div class="log" id="log"></div>
  </section>
</main>
<script>
const WIND={E:"East 東",S:"South 南",W:"West 西",N:"North 北"};
const PC=["var(--g1)","var(--g2)","var(--g3)","var(--g4)"];
const ORD=["1st","2nd","3rd","4th"];
function fmtDur(s){s=s|0;const h=s/3600|0,m=(s%3600)/60|0,x=s%60;return (h?h+"h ":"")+(m||h?m+"m ":"")+x+"s";}
function ts(t){const d=new Date(t*1000);return d.toLocaleTimeString();}
function el(id){return document.getElementById(id);}
function pill(id,on,label){const e=el(id);e.textContent=label;e.className="pill "+(on?"on":"off");}

async function tick(){
  let d;
  try{ d=await (await fetch("/api/state",{cache:"no-store"})).json(); }
  catch(e){ el("dot").classList.remove("up"); return; }
  el("dot").classList.add("up");
  render(d);
}

function render(d){
  const st=d.status||{}, se=d.session||{}, g=d.game||{}, ac=se.account||{};
  // header
  el("rank").textContent = (ac.nickname? ac.nickname+"  ":"") + (ac.rank||"Rank ?") + (ac.level_score!=null? "  "+ac.level_score+"pt":"");
  el("uptime").textContent = "up "+fmtDur(se.uptime_sec||0);
  el("p-model").textContent = (st.model_type||"?")+(st.model_loaded?" ✓":" ✗");
  el("p-model").className="pill "+(st.model_loaded?"on":"off");
  pill("p-play",st.autoplay,"Autoplay"); pill("p-rec",st.recording,"Record"); pill("p-hud",st.overlay,"Overlay");

  // session stat cards
  const cards=[
    ["Games", se.games_played??0],
    ["1st place", se.first_place??0],
    ["1st rate", se.first_rate!=null?(se.first_rate*100).toFixed(1)+"%":"–"],
    ["Avg place", se.avg_placement??"–"],
    ["Top / Low", (se.wins??0)+" / "+(se.losses??0)],
    ["Net points", (se.total_points>0?"+":"")+(se.total_points??0)],
    ["Rank pts", (se.total_grading>0?"+":"")+(se.total_grading??0)],
  ];
  el("sess").innerHTML=cards.map(c=>`<div class="stat"><div class="v">${c[1]}</div><div class="k">${c[0]}</div></div>`).join("");

  // placement bars
  const pl=se.placements||[0,0,0,0], mx=Math.max(1,...pl);
  el("bars").innerHTML=pl.map((c,i)=>`<div class="bar"><div class="cnt">${c}</div>`+
    `<div class="fill" style="height:${(c/mx*100)|0}%;background:${PC[i]}"></div>`+
    `<div class="lab">${ORD[i]}</div></div>`).join("");

  // current game
  const gm=el("game");
  if(!g.in_game){ gm.innerHTML=`<div class="muted">Not in a game.</div>`; }
  else if(!g.started){ gm.innerHTML=`<div class="muted">Game starting…</div>`; }
  else{
    const nseat=(g.mode==="3P")?3:4;
    let seats="";
    for(let i=0;i<nseat;i++){
      const me=i===g.my_seat, rc=(g.player_reached&&g.player_reached[i])?`<span class="rc">RIICHI</span>`:"";
      const sc=(g.scores&&g.scores[i]!=null)?g.scores[i]:"–";
      seats+=`<div class="seat ${me?"me":""}"><span class="nm">${me?"You":"Seat "+i}</span>${rc}<span class="sc">${sc}</span></div>`;
    }
    const stat=g.calculating?`<span class="pill">🧮 calculating</span>`:(g.syncing?`<span class="pill">⏳ syncing</span>`:"");
    gm.innerHTML=
      `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
         <span class="pill on">${g.mode||"?"}</span>
         <span class="pill">${WIND[g.bakaze]||g.bakaze||"?"} ${g.kyoku||""}</span>
         <span class="pill">${g.honba||0} honba</span>${stat}</div>
       <div class="seats">${seats}</div>
       <div style="margin-top:12px" class="muted" style="font-size:12px">Your hand</div>
       <div class="hand">${g.my_tehai_unicode||"–"}<span class="tsumo"> ${g.my_tsumohai_unicode||""}</span></div>`;
  }

  // guide
  const gd=el("guide");
  if(g.guide){
    let opts=(g.guide.options||[]).map(o=>{
      const w=(o.weight*100);
      return `<div class="opt"><span class="ot">${o.tile}</span>`+
        `<span class="track"><span class="ofill" style="width:${Math.min(100,w)}%"></span></span>`+
        `<span class="ow">${w.toFixed(1)}%</span></div>`;
    }).join("");
    gd.innerHTML=`<div class="guide"><div class="act">${g.guide.action}</div>${opts}</div>`;
  } else { gd.innerHTML=`<div class="muted">No pending recommendation.</div>`; }

  // placement history sparkline
  const ph=se.placement_history||[];
  el("spark").innerHTML = ph.length? ph.map(p=>`<span class="sq" style="background:${PC[p-1]||"#555"}">${p}</span>`).join("") : `<span class="muted">No games yet.</span>`;
  const pts=se.points_history||[];
  const grd=se.grading_history||[];
  let ptline = pts.length? ("Points per game: "+pts.map(p=>(p>0?"+":"")+p).join(", ")) : "";
  if(grd.length){ ptline += (ptline?"  •  ":"")+"Rank pts: "+grd.map(p=>(p>0?"+":"")+p).join(", "); }
  el("pts").textContent = ptline;

  // rank history
  const rh=se.rank_history||[];
  el("rankh").innerHTML = rh.length? rh.slice().reverse().map(r=>
    `<div class="row"><span class="t">${ts(r.time)}</span><span>${r.rank}${r.level_score!=null?"  ("+r.level_score+"pt)":""}</span></div>`).join("")
    : `<div class="muted">No rank data yet.</div>`;

  // event log
  const lg=se.log||[];
  el("log").innerHTML = lg.length? lg.slice().reverse().map(e=>
    `<div class="row"><span class="t">${ts(e.time)}</span><span>${e.text}</span></div>`).join("")
    : `<div class="muted">No events yet.</div>`;

  // errors
  if(st.error){ el("game").insertAdjacentHTML("beforeend",`<div class="err">⚠ ${st.error}</div>`); }
}

tick(); setInterval(tick,1000);
</script>
</body>
</html>"""
