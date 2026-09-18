const $ = (id) => document.getElementById(id);
const canvas = $("mapCanvas");
const ctx = canvas.getContext("2d");
const stateColors = {
  0: [113, 129, 140, 115], 1: [0, 0, 0, 0], 2: [244, 185, 66, 190],
  3: [255, 61, 22, 235], 4: [53, 43, 39, 225], 254: [123, 74, 134, 225], 255: [0, 0, 0, 0],
};
const stateLabels = {0:"不燃",1:"未燃",2:"受热",3:"燃烧",4:"燃尽",254:"未知",255:"域外"};
let data = null;
let frame = 0;
let playing = false;
let selected = null;
let lastTick = 0;
let accumulated = 0;
const layers = {image:true, buildings:true, grid:false};
let layerMode = "state";

async function loadJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`无法读取 ${path} (${response.status})`);
  return response.json();
}

async function loadBinary(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`无法读取 ${path} (${response.status})`);
  return response.arrayBuffer();
}

async function init() {
  try {
    const [manifest, grid, imageRef, buildings] = await Promise.all([
      loadJson("manifest.json"), loadJson("grid.json"), loadJson("image_ref.json"), loadJson("buildings.json"),
    ]);
    if (!["village-viewer-1","village-viewer-2"].includes(manifest.schema_version)) throw new Error("不支持的回放版本");
    const [stateBuffer, fuelBuffer, materialBuffer, image] = await Promise.all([
      loadBinary(manifest.state.path), loadBinary(manifest.fuel.path), loadBinary(manifest.material.path),
      loadImage(manifest.background),
    ]);
    const temperatureBuffer = manifest.temperature ? await loadBinary(manifest.temperature.path) : null;
    const hrrBuffer = manifest.hrr ? await loadBinary(manifest.hrr.path) : null;
    const buildingCells = manifest.building_cells ? await loadJson(manifest.building_cells) : null;
    const cells = grid.height * grid.width;
    if (stateBuffer.byteLength !== manifest.frame_count * cells) throw new Error("状态文件长度不正确");
    if (fuelBuffer.byteLength !== manifest.frame_count * cells * 4) throw new Error("燃料文件长度不正确");
    if (materialBuffer.byteLength !== cells * 4) throw new Error("材质文件长度不正确");
    data = {
      manifest, grid, imageRef, buildings, image,
      states: new Uint8Array(stateBuffer),
      fuel: new Float32Array(fuelBuffer),
      material: new Int32Array(materialBuffer),
      temperature: temperatureBuffer ? new Float32Array(temperatureBuffer) : null,
      hrr: hrrBuffer ? new Float32Array(hrrBuffer) : null,
      buildingCells,
    };
    for (const option of $("layerMode").options) if (option.value !== "state") option.disabled = !data[option.value];
    $("timeline").max = manifest.frame_count - 1;
    $("mapBadge").textContent = `${grid.width} × ${grid.height} 元胞 · ${grid.cell_size_m} m`;
    $("runStatus").textContent = manifest.model;
    $("loading").hidden = true;
    resize();
    render();
  } catch (error) {
    $("loading").textContent = `载入失败：${error.message}`;
    $("loading").classList.add("error");
  }
}

function loadImage(path) {
  return new Promise((resolve, reject) => {
    const image = new Image(); image.onload = () => resolve(image); image.onerror = () => reject(new Error("底图损坏")); image.src = path;
  });
}

function viewport() {
  const {width, height} = data.grid;
  const scale = Math.min(canvas.width / width, canvas.height / height) * 0.92;
  return {scale, x:(canvas.width-width*scale)/2, y:(canvas.height-height*scale)/2};
}

function render() {
  if (!data) return;
  const v = viewport();
  ctx.clearRect(0,0,canvas.width,canvas.height);
  ctx.save(); ctx.translate(v.x,v.y); ctx.scale(v.scale,v.scale);
  if (layers.image) drawImageLayer();
  if (layerMode === "state") drawStateLayer(); else drawScalarLayer(layerMode);
  if (layers.buildings) drawBuildings();
  if (layers.grid && v.scale > 7) drawGrid();
  if (selected) { ctx.strokeStyle="#fff7d6"; ctx.lineWidth=2/v.scale; ctx.strokeRect(selected.col,selected.row,1,1); }
  ctx.restore();
  updateReadouts();
}

function scalarColor(value, minimum, maximum, logScale) {
  const scaled = logScale ? Math.log1p(Math.max(0,value))/Math.log1p(Math.max(maximum,1e-12)) : (value-minimum)/Math.max(maximum-minimum,1e-12);
  const t=Math.max(0,Math.min(1,scaled));
  if (t<=0) return [0,0,0,0];
  const stops=[[0.0,53,34,70],[0.45,255,75,31],[1.0,255,242,168]];
  const pair=t<.45?[stops[0],stops[1]]:[stops[1],stops[2]], u=(t-pair[0][0])/(pair[1][0]-pair[0][0]);
  return [Math.round(pair[0][1]+u*(pair[1][1]-pair[0][1])),Math.round(pair[0][2]+u*(pair[1][2]-pair[0][2])),Math.round(pair[0][3]+u*(pair[1][3]-pair[0][3])),215];
}

function drawScalarLayer(kind) {
  const {width,height}=data.grid, spec=data.manifest[kind], values=data[kind];
  const image=ctx.createImageData(width,height), offset=frame*width*height;
  for(let i=0;i<width*height;i++) image.data.set(scalarColor(values[offset+i],spec.minimum,spec.maximum,spec.scale==="log1p"),i*4);
  const buffer=document.createElement("canvas"); buffer.width=width; buffer.height=height;
  buffer.getContext("2d").putImageData(image,0,0); ctx.save(); ctx.imageSmoothingEnabled=false; ctx.drawImage(buffer,0,0); ctx.restore();
}

function drawImageLayer() {
  const [a,b,c,d,e,f] = data.imageRef.affine_pixel_to_world;
  const g = data.grid;
  ctx.save();
  ctx.transform(a/g.cell_size_m, -d/g.cell_size_m, b/g.cell_size_m, -e/g.cell_size_m,
    (c-g.x_min_m)/g.cell_size_m, (g.y_max_m-f)/g.cell_size_m);
  ctx.globalAlpha=.78; ctx.drawImage(data.image,0,0); ctx.restore();
}

function drawStateLayer() {
  const {width,height} = data.grid;
  const image = ctx.createImageData(width,height);
  const offset = frame*width*height;
  for (let i=0;i<width*height;i++) {
    const color=stateColors[data.states[offset+i]] || [255,0,255,255];
    image.data.set(color,i*4);
  }
  const buffer=document.createElement("canvas"); buffer.width=width; buffer.height=height;
  buffer.getContext("2d").putImageData(image,0,0);
  ctx.save(); ctx.imageSmoothingEnabled=false; ctx.drawImage(buffer,0,0); ctx.restore();
}

function drawBuildings() {
  const g=data.grid; ctx.lineWidth=1.25/viewport().scale;
  for (const building of data.buildings.buildings) for (const polygon of building.polygons) {
    const status=buildingState(building.building_id), color=stateColors[status]||stateColors[1];
    ctx.strokeStyle=status===1?"rgba(255,245,210,.9)":`rgba(${color[0]},${color[1]},${color[2]},.95)`;
    ctx.beginPath(); traceRing(polygon.exterior,g); for (const ring of polygon.holes) traceRing(ring,g); ctx.stroke();
  }
}
function buildingState(buildingId) {
  const cells=data.buildingCells?.buildings?.[buildingId]; if(!cells)return 1;
  const offset=frame*data.grid.width*data.grid.height; let sawBurned=false, sawHeating=false;
  for(const cell of cells){const state=data.states[offset+cell];if(state===3)return 3;if(state===2)sawHeating=true;if(state===4)sawBurned=true;}
  return sawHeating?2:(sawBurned?4:1);
}
function traceRing(ring,g) { ring.forEach(([x,y],i) => {const gx=(x-g.x_min_m)/g.cell_size_m, gy=(g.y_max_m-y)/g.cell_size_m; i?ctx.lineTo(gx,gy):ctx.moveTo(gx,gy);}); }
function drawGrid() { const g=data.grid; ctx.strokeStyle="rgba(255,255,255,.15)"; ctx.lineWidth=.5/viewport().scale; ctx.beginPath(); for(let x=0;x<=g.width;x++){ctx.moveTo(x,0);ctx.lineTo(x,g.height);} for(let y=0;y<=g.height;y++){ctx.moveTo(0,y);ctx.lineTo(g.width,y);} ctx.stroke(); }

function updateReadouts() {
  const {manifest,grid,states}=data, cells=grid.width*grid.height, offset=frame*cells;
  const seconds=manifest.sample_times_s[frame];
  $("timeReadout").textContent=manifest.time_display_unit==="min"?`${(seconds/60).toFixed(2)} min`:`${seconds.toFixed(2)} s`;
  $("frameReadout").textContent=`${frame+1} / ${manifest.frame_count}`; $("timeline").value=frame;
  let burning=0, ignited=0; for(let i=0;i<cells;i++){const s=states[offset+i]; if(s===3)burning++; if(s===3||s===4)ignited++;}
  $("burningCount").textContent=burning; $("ignitedCount").textContent=ignited;
  let burningBuildings=0, affectedBuildings=0;
  for(const id of Object.keys(data.buildingCells?.buildings||{})){const status=buildingState(id);if(status===3)burningBuildings++;if(status!==1)affectedBuildings++;}
  $("burningBuildingCount").textContent=burningBuildings; $("affectedBuildingCount").textContent=affectedBuildings;
  if (selected) showSelection();
}

function showSelection() {
  const g=data.grid, i=selected.row*g.width+selected.col, offset=frame*g.width*g.height+i;
  $("emptySelection").hidden=true; $("cellDetails").hidden=false;
  $("cellPosition").textContent=`${selected.row}, ${selected.col}`; $("cellId").textContent=i;
  $("cellState").textContent=stateLabels[data.states[offset]]; $("cellMaterial").textContent=data.material[i];
  $("cellFuel").textContent=`${(data.fuel[offset]*100).toFixed(1)}%`;
  $("cellTemperature").textContent=data.temperature?`${data.temperature[offset].toFixed(1)} K`:"—";
  $("cellHrr").textContent=data.hrr?`${data.hrr[offset].toFixed(1)} W`:"—";
  const building=findBuilding(selected); $("cellBuilding").textContent=building||"—"; $("cellBuildingState").textContent=building?stateLabels[buildingState(building)]:"—";
}

function findBuilding(cell) {
  const g=data.grid, x=g.x_min_m+(cell.col+.5)*g.cell_size_m, y=g.y_max_m-(cell.row+.5)*g.cell_size_m;
  for(const building of data.buildings.buildings) for(const polygon of building.polygons) if(pointInRing(x,y,polygon.exterior) && !polygon.holes.some(r=>pointInRing(x,y,r))) return building.building_id;
  return null;
}
function pointInRing(x,y,ring){let inside=false;for(let i=0,j=ring.length-1;i<ring.length;j=i++){const [xi,yi]=ring[i],[xj,yj]=ring[j];if(((yi>y)!==(yj>y))&&(x<(xj-xi)*(y-yi)/(yj-yi)+xi))inside=!inside;}return inside;}

function resize(){const rect=canvas.getBoundingClientRect(), ratio=window.devicePixelRatio||1;canvas.width=Math.round(rect.width*ratio);canvas.height=Math.round(rect.height*ratio);render();}
canvas.addEventListener("click",event=>{if(!data)return;const rect=canvas.getBoundingClientRect(),ratio=window.devicePixelRatio||1,v=viewport();const col=Math.floor(((event.clientX-rect.left)*ratio-v.x)/v.scale),row=Math.floor(((event.clientY-rect.top)*ratio-v.y)/v.scale);if(row>=0&&row<data.grid.height&&col>=0&&col<data.grid.width){selected={row,col};render();}});
window.addEventListener("resize",resize);

function setFrame(value){frame=Math.max(0,Math.min(data.manifest.frame_count-1,value));render();}
$("timeline").addEventListener("input",e=>{playing=false;setFrame(Number(e.target.value));});
$("jumpStart").onclick=()=>setFrame(0); $("stepBack").onclick=()=>setFrame(frame-1); $("stepForward").onclick=()=>setFrame(frame+1);
$("playPause").onclick=()=>{playing=!playing;$("playPause").textContent=playing?"Ⅱ":"▶";};
for(const [id,key] of [["toggleImage","image"],["toggleBuildings","buildings"],["toggleGrid","grid"]]) $(id).onchange=e=>{layers[key]=e.target.checked;render();};
$("layerMode").onchange=e=>{layerMode=e.target.value;const scalar=layerMode!=="state", spec=data?.manifest[layerMode];$("scalarLegend").hidden=!scalar;document.querySelector(".legend").hidden=scalar;if(scalar&&spec){$("scalarMaximum").textContent=`${spec.maximum.toFixed(1)} ${spec.unit}`;$("scalarMinimum").textContent=`${spec.minimum.toFixed(1)} ${spec.unit}`;}render();};
function tick(now){if(playing&&data){if(!lastTick)lastTick=now;accumulated+=(now-lastTick)*Number($("speed").value);if(accumulated>=500){setFrame(frame+1);accumulated=0;if(frame===data.manifest.frame_count-1){playing=false;$("playPause").textContent="▶";}}}lastTick=now;requestAnimationFrame(tick);} requestAnimationFrame(tick);
init();
