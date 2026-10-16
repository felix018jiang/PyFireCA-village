import {adjacentFuelCounts, eventOccurredBy, scalarDisplaySpec, summarizeCoreFrame} from "./core-metrics.mjs";
const $ = (id) => document.getElementById(id);
const canvas = $("mapCanvas");
const ctx = canvas.getContext("2d");
const stateColors = {
  0: [113, 129, 140, 28], 1: [0, 0, 0, 0], 2: [244, 185, 66, 190],
  3: [255, 61, 22, 235], 4: [53, 43, 39, 225], 5: [111, 78, 48, 220], 254: [123, 74, 134, 225], 255: [0, 0, 0, 0],
};
const stateLabels = {0:"不燃",1:"未燃",2:"受热",3:"燃烧",4:"燃尽",254:"未知",255:"域外"};
let data = null;
let componentTools = null;
let componentMode = null;
let frame = 0;
let playing = false;
let selected = null;
let ignitionChoice = null;
let lastTick = 0;
let accumulated = 0;
const layers = {image:true, buildings:true, grid:false};
let layerMode = "state";
let frameLoader = null;
let pendingFrame = null;
let loadingFrame = false;
let mapZoom = 1, mapPanX = 0, mapPanY = 0;
let dragStart = null, dragged = false;
let materialDraft = null;

async function loadJson(path) {
  const response = await fetch(path, {cache:"no-store"});
  if (!response.ok) throw new Error(`无法读取 ${path} (${response.status})`);
  return response.json();
}

async function loadBinary(path) {
  const response = await fetch(path, {cache:"no-store"});
  if (!response.ok) throw new Error(`无法读取 ${path} (${response.status})`);
  return response.arrayBuffer();
}

async function loadSpecBuffer(spec) {
  if (!spec) return null;
  if (!spec.chunks) return loadBinary(spec.path);
  const buffers = await Promise.all(spec.chunks.map(async (chunk) => {
    const buffer = await loadBinary(chunk.path);
    if (buffer.byteLength !== chunk.byte_length) throw new Error(`分块长度不正确：${chunk.path}`);
    return buffer;
  }));
  const total = buffers.reduce((sum, buffer) => sum + buffer.byteLength, 0);
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const buffer of buffers) { merged.set(new Uint8Array(buffer), offset); offset += buffer.byteLength; }
  return merged.buffer;
}

async function loadOptionalBinary(spec) {
  return spec ? loadSpecBuffer(spec) : null;
}

async function init() {
  try {
    const [manifest, grid, imageRef, buildings] = await Promise.all([
      loadJson("manifest.json"), loadJson("grid.json"), loadJson("image_ref.json"), loadJson("buildings.json"),
    ]);
    if (manifest.schema_version === "village-background-1") {
      await initBackground(manifest, grid, imageRef, buildings);
      return;
    }
    if (!["village-viewer-1","village-viewer-2","village-viewer-3","village-component-viewer-1"].includes(manifest.schema_version)) throw new Error("不支持的回放版本");
    const frameLocal = !manifest.components && Boolean(manifest.state.chunks);
    let firstFrame = null;
    if (frameLocal) {
      const {createChunkFrameLoader} = await import("./chunk-replay.mjs");
      const specs = Object.fromEntries(["state","fuel","temperature","temperature_valid","hrr","surface_water"].filter(k=>manifest[k]).map(k=>[k,manifest[k]]));
      frameLoader = createChunkFrameLoader(specs,grid.width*grid.height,loadBinary);
      firstFrame = await frameLoader.load(0);
    }
    const dynamicBuffer = key => frameLocal ? Promise.resolve(firstFrame[key] || null) : loadOptionalBinary(manifest[key]);
    const [stateBuffer, fuelBuffer, materialBuffer, image] = await Promise.all([
      dynamicBuffer("state"), dynamicBuffer("fuel"), loadSpecBuffer(manifest.material),
      loadImage(manifest.background),
    ]);
    const [temperatureBuffer, temperatureValidBuffer, hrrBuffer, waterBuffer, landcoverBuffer, coverageBuffer, buildingBuffer, ignitionBuffer, burnoutBuffer, causeBuffer, elevationBuffer, elevationValidBuffer] = await Promise.all([
      dynamicBuffer("temperature"), dynamicBuffer("temperature_valid"),
      dynamicBuffer("hrr"), dynamicBuffer("surface_water"),
      loadOptionalBinary(manifest.landcover), loadOptionalBinary(manifest.coverage),
      loadOptionalBinary(manifest.building), loadOptionalBinary(manifest.ignition_time),
      loadOptionalBinary(manifest.burnout_time), loadOptionalBinary(manifest.ignition_cause),
      loadOptionalBinary(manifest.elevation), loadOptionalBinary(manifest.elevation_valid),
    ]);
    const buildingCells = manifest.building_cells ? await loadJson(manifest.building_cells) : null;
    const moistureBuffer = await loadOptionalBinary(manifest.initial_moisture);
    const initialFuelBuffer = manifest.components ? null : await loadOptionalBinary(manifest.initial_fuel);
    const evidenceBuffer = await loadOptionalBinary(manifest.material_evidence);
    const cells = grid.height * grid.width;
    const storageCells = manifest.components ? manifest.components.node_count : cells;
    if (evidenceBuffer && evidenceBuffer.byteLength !== cells) throw new Error("材质证据长度不正确");
    const storageFrames = frameLocal ? 1 : manifest.frame_count;
    if (stateBuffer.byteLength !== storageFrames * storageCells) throw new Error("状态文件长度不正确");
    if (fuelBuffer.byteLength !== storageFrames * storageCells * 4) throw new Error("燃料文件长度不正确");
    if (materialBuffer.byteLength !== cells * 4) throw new Error("材质文件长度不正确");
    if (moistureBuffer && moistureBuffer.byteLength !== cells * 4) throw new Error("初始含水率文件长度不正确");
    if (initialFuelBuffer && initialFuelBuffer.byteLength !== cells * 8) throw new Error("初始燃料文件长度不正确");
    if (manifest.schema_version === "village-viewer-3" && landcoverBuffer?.byteLength !== cells * 4) throw new Error("地类文件长度不正确");
    data = {
      manifest, grid, imageRef, buildings, image, frameLocal,
      states: new Uint8Array(stateBuffer),
      fuel: new Float32Array(fuelBuffer),
      material: new Int32Array(materialBuffer),
      temperature: temperatureBuffer ? new Float32Array(temperatureBuffer) : null,
      temperatureValid: temperatureValidBuffer ? new Uint8Array(temperatureValidBuffer) : null,
      hrr: hrrBuffer ? new Float32Array(hrrBuffer) : null,
      surface_water: waterBuffer ? new Float32Array(waterBuffer) : null,
      initialMoisture: moistureBuffer ? new Float32Array(moistureBuffer) : null,
      initialFuel: initialFuelBuffer ? new Float64Array(initialFuelBuffer) : null,
      material_evidence: evidenceBuffer ? new Uint8Array(evidenceBuffer) : null,
      landcover: landcoverBuffer ? new Int32Array(landcoverBuffer) : null,
      coverage: coverageBuffer ? new Float32Array(coverageBuffer) : null,
      building: buildingBuffer ? new Int32Array(buildingBuffer) : null,
      ignitionTime: ignitionBuffer ? new Float64Array(ignitionBuffer) : null,
      burnoutTime: burnoutBuffer ? new Float64Array(burnoutBuffer) : null,
      ignitionCause: causeBuffer ? new Uint8Array(causeBuffer) : null,
      elevation: elevationBuffer ? new Float32Array(elevationBuffer) : null,
      elevationValid: elevationValidBuffer ? new Uint8Array(elevationValidBuffer) : null,
      buildingCells,
    };
    if (manifest.components) {
      for (const key of ["landcover","coverage","building","initialMoisture"]) {
        if(data[key]?.length!==cells) throw new Error(`物理格静态数组长度不正确：${key}`);
      }
      await initComponentReplay();
    }
    for (const option of $("layerMode").options) {
      const available = ["state","background","material"].includes(option.value) || Boolean(data[option.value]);
      option.disabled = !available;
    }
    $("timeline").max = manifest.frame_count - 1;
    $("mapBadge").textContent = `${grid.width} × ${grid.height} 元胞 · ${grid.cell_size_m} m`;
    if (manifest.scenario?.place_name) document.querySelector("h1").textContent = `${manifest.scenario.place_name}火灾模拟`;
    if (manifest.scenario?.annotation_report?.bridge_count > 0) {
      $("burningBuildingCount").previousElementSibling.textContent = "燃烧建筑/桥";
      $("affectedBuildingCount").previousElementSibling.textContent = "受影响建筑/桥";
    }
    const scenarioLabel = manifest.scenario?.scenario_id ? ` · ${manifest.scenario.scenario_id}` : "";
    const vegetationLabel = manifest.scenario?.includes_nonbuilding_fuels ? " · 含建筑外可燃材料" : "";
    const elevationLabel = manifest.scenario?.elevation_physics_activated ? " · DEM 已参与计算" : "";
    const contactLabel = manifest.flame_contact ? " · 近场火焰接触换热" : "";
    const contactOverrides = manifest.flame_contact?.receiver_height_overrides_m || [];
    const filled = manifest.scenario?.hypothetically_filled_unknown_cell_count;
    if ($("unknownLegend")) $("unknownLegend").hidden=manifest.scenario?.unknown_domain_cell_count===0;
    if (manifest.scenario?.input_summary) {
      $("landcoverAssumptionNote").hidden=false;
      $("landcoverAssumptionNote").textContent=manifest.scenario.input_summary;
    }
    if (manifest.scenario?.place_name === "高友村" && Number.isInteger(filled) && filled > 0) {
      $("landcoverAssumptionNote").hidden = false;
      $("landcoverAssumptionNote").textContent = `${filled.toLocaleString()}格未辨明地表按案例假设补全。当前高友村演示将其设为不燃，可能切断燃料通路；需要实地核实。`;
    }
    if (contactOverrides.length) {
      $("contactReceiverNote").hidden = false;
      $("contactReceiverNote").textContent = `接触受热高度采用案例假设：${contactOverrides.map(([material,height]) => `${manifest.material?.labels?.[material] || `材质 ${material}`} ${height} m`).join("；")}。辐射高度独立保留。`;
    }
    const halfExtent = manifest.flame_contact?.receiver_vertical_half_extent_m || 0;
    if (halfExtent > 0) {
      $("contactReceiverNote").hidden = false;
      $("contactReceiverNote").textContent += ` 有效受体高度带采用±${halfExtent} m演示假设；接触热量按高度重叠比例计算。`;
    }
    if (manifest.flame_contact?.terrain_reference === "shared_face") {
      $("contactReceiverNote").hidden = false;
      $("contactReceiverNote").textContent += " 相邻可燃格按连续坡面的公共边计算接触；辐射保留DEM高差。该接触模型为演示假设。";
    }
    $("runStatus").textContent = `${manifest.model_label || manifest.model}${scenarioLabel}${vegetationLabel}${elevationLabel}${contactLabel}`;
    if (manifest.hrr?.temporal_meaning === "mean_over_last_solver_step_ending_at_sample") {
      $("hrrLabel").textContent = "上一步平均 HRR";
    }
    $("loading").hidden = true;
    await initSimulationControls(manifest);
    resize();
    render();
  } catch (error) {
    $("loading").textContent = `载入失败：${error.message}`;
    $("loading").classList.add("error");
  }
}

async function initComponentReplay() {
  const {manifest,grid}=data, spec=manifest.components;
  if (spec.schema_version!=="village-view-components-1") throw new Error("不支持的部位数据版本");
  componentTools=await import("./component-view.mjs");
  const [cellId,ids,initialFuel,initialDry,initialMoisture,burnable]=await Promise.all([
    loadSpecBuffer(spec.cell_id),loadJson(spec.ids),loadSpecBuffer(spec.initial_fuel),
    loadSpecBuffer(spec.initial_dry),loadSpecBuffer(spec.initial_moisture),loadSpecBuffer(spec.burnable),
  ]);
  if(ids.length!==spec.node_count) throw new Error("部位数与清单不一致");
  data.componentReplay=componentTools.validateReplay({
    ...Object.fromEntries(["states","fuel","temperature","temperatureValid","hrr","surface_water","ignitionTime","burnoutTime","ignitionCause"].map(k=>[k,data[k]])),
    ids,burnable:new Uint8Array(burnable),cellId:new Int32Array(cellId),initialFuel:new Float64Array(initialFuel),
    initialDry:new Float64Array(initialDry),initialMoisture:new Float32Array(initialMoisture),
  },grid.width*grid.height,manifest.frame_count,manifest.sample_times_s);
  document.body.classList.add("component-viewer");
  $("coreMetrics").hidden = true;
  Object.assign(stateLabels,{2:"有部位受热",3:"有部位燃烧",4:"所选部位燃尽",5:"部分燃尽"});
  $("componentPicker").hidden=false; $("partialLegend").hidden=false;
  $("moistureLabel").textContent="所选部位初始含水率（干基）";
  $("temperatureLabel").textContent="未燃部位最高温度";
  $("waterLabel").textContent="有效受热层剩余水";
  $("modelDescription").textContent="燃料部位独立计算；基部着火不代表整格或整栋建筑烧毁。图层与统计随所选部位切换。参数仍需独立校准。";
  $("burningBuildingCount").previousElementSibling.textContent="有燃烧部位的建筑";
  $("affectedBuildingCount").previousElementSibling.textContent="有受热或燃烧部位的建筑";
  const picker=$("componentMode");
  for(const id of new Set(ids)) { const option=document.createElement("option");option.value=JSON.stringify(id);option.textContent=componentTools.componentLabel(id);picker.append(option); }
  picker.onchange=()=>{componentMode=JSON.parse(picker.value);applyComponentMode();$("layerMode").dispatchEvent(new Event("change"));};
  applyComponentMode();
}
function applyComponentMode() {
  Object.assign(data,componentTools.aggregateReplay(data.componentReplay,data.grid.width*data.grid.height,data.manifest.frame_count,componentMode));
  for(const key of ["hrr","surface_water"]) {
    let maximum=0;for(const value of data[key]) maximum=Math.max(maximum,value);
    data.manifest[key]={...data.manifest[key],maximum};
  }
}

function loadImage(path) {
  if (!path) return Promise.resolve(null);
  return new Promise((resolve, reject) => {
    const image = new Image(); image.onload = () => resolve(image); image.onerror = () => reject(new Error("底图损坏"));
    const separator = path.includes("?") ? "&" : "?";
    image.src = `${path}${separator}v=20260922-satellite-background-2`;
  });
}

async function initBackground(manifest, grid, imageRef, buildings) {
  data = {manifest, grid, imageRef, buildings, image:await loadImage(manifest.background), backgroundOnly:true};
  layers.buildings = false;
  document.title = `${manifest.place_name} · 村火推演台`;
  document.querySelector("h1").textContent = `${manifest.place_name}火灾模拟`;
  $("timeReadout").textContent = "待建模";
  $("runStatus").textContent = "卫星底图已载入";
  $("modelDescription").textContent = `${manifest.place_name}底图已就绪。建筑、燃料和地形数据待准备，完成后可启用火灾模拟。`;
  const imagePixelSize=Math.abs(imageRef.affine_pixel_to_world[0]);
  $("mapBadge").textContent = `${imageRef.width_px} × ${imageRef.height_px} 像素 · ${imagePixelSize.toFixed(2)} 米/像素`;
  for (const element of [$("layerMode").closest("label"), $("toggleBuildings").closest("label"), $("toggleGrid").closest("label"), document.querySelector(".legend").closest("section"), $("coreMetrics"), document.querySelector(".transport")]) element.hidden = true;
  document.querySelector(".shell").style.gridTemplateRows = "84px 1fr";
  const inspector = document.querySelector(".inspector");
  inspector.replaceChildren();
  const heading = document.createElement("p"); heading.className="section-number"; heading.textContent="影像信息";
  const info = document.createElement("p"); info.className="control-help";
  info.textContent = `${manifest.place_name} · ${grid.crs} · ${imageRef.width_px} × ${imageRef.height_px} 像素。滚轮缩放，拖动平移，点击查询投影坐标。`;
  const selection = document.createElement("p"); selection.id="backgroundSelection"; selection.className="control-help";
  inspector.append(heading, info);
  document.querySelector(".notice").append(selection);
  if (manifest.mosaic) {
    const quality=document.createElement("p"); quality.className="control-help";
    quality.textContent=`高清拼接覆盖 ${(manifest.mosaic.high_resolution_fraction*100).toFixed(1)}%；原图补齐 ${(manifest.mosaic.fallback_fraction*100).toFixed(1)}%。补齐区域仍保留原图精度。`;
    document.querySelector(".notice").append(quality);
  }
  if (manifest.landcover_preview) {
    const spec=manifest.landcover_preview;
    const buffer=await loadBinary(spec.path);
    if (buffer.byteLength!==grid.width*grid.height) throw new Error("地表分类尺寸不匹配");
    data.landcover=new Uint8Array(buffer);
    data.landcoverCanvas=document.createElement("canvas");
    data.landcoverCanvas.width=grid.width; data.landcoverCanvas.height=grid.height;
    const context=data.landcoverCanvas.getContext("2d"), pixels=context.createImageData(grid.width,grid.height);
    for(let i=0;i<data.landcover.length;i++) {
      const code=data.landcover[i], color=spec.colors[code];
      if (!color) throw new Error(`未知分类代码：${code}`);
      pixels.data.set([parseInt(color.slice(1,3),16),parseInt(color.slice(3,5),16),parseInt(color.slice(5,7),16),code===255?0:115],i*4);
    }
    context.putImageData(pixels,0,0);
    const picker=$("layerMode"); picker.replaceChildren();
    for (const [value,label] of [["background","卫星原图"],["landcover","地表材质初步识别"]]) {
      const option=document.createElement("option"); option.value=value; option.textContent=label; picker.append(option);
    }
    picker.closest("label").hidden=false; picker.value="landcover"; layerMode="landcover";
    picker.closest("label").querySelector("span").textContent="查看图层";
    const legend=document.createElement("section");
    const title=document.createElement("p"); title.className="section-number"; title.textContent="地表分类 / RGB 推测"; legend.append(title);
    for(const [code,label] of Object.entries(spec.labels)) {
      if(code==="255") continue;
      const row=document.createElement("p"); row.className="control-help";
      const swatch=document.createElement("i"); swatch.className="swatch"; swatch.style.display="inline-block"; swatch.style.background=spec.colors[code]; swatch.style.marginRight="6px";
      row.append(swatch,document.createTextNode(`${label} · ${(100*(spec.class_counts[code]||0)/Math.max(1,spec.valid_cell_count)).toFixed(1)}%`)); legend.append(row);
    }
    document.querySelector(".left-rail").append(legend);
    $("modelDescription").textContent=`${manifest.place_name}整村地表已按 RGB 颜色初步分类。点击地图查询类别。树木与草地、道路与屋顶可能混淆；燃料量、建筑材质和地形待补充后再启用模拟。`;
    $("runStatus").textContent="底图与地表分类已载入";
    const report=document.createElement("a"); report.href=spec.report; report.textContent="查看分类依据与统计"; report.target="_blank"; inspector.append(report);
  }
  $("loading").hidden = true;
  resize();
}

function frameOffset() { return data.frameLocal ? 0 : frame*data.grid.width*data.grid.height; }
function viewport() {
  const {width, height} = data.grid;
  const scale = Math.min(canvas.width / width, canvas.height / height) * 0.92 * mapZoom;
  return {scale, x:(canvas.width-width*scale)/2 + mapPanX, y:(canvas.height-height*scale)/2 + mapPanY};
}

function render() {
  if (!data) return;
  const v = viewport();
  ctx.clearRect(0,0,canvas.width,canvas.height);
  ctx.save(); ctx.translate(v.x,v.y); ctx.scale(v.scale,v.scale);
  if (layers.image) drawImageLayer();
  if (data.backgroundOnly) {
    if (layerMode==="landcover" && data.landcoverCanvas) {
      ctx.save(); ctx.imageSmoothingEnabled=false; ctx.drawImage(data.landcoverCanvas,0,0); ctx.restore();
    }
    if (selected) {
      ctx.strokeStyle="#fff7d6"; ctx.lineWidth=2/v.scale;
      ctx.beginPath(); ctx.arc(selected.col+.5, selected.row+.5, 5/v.scale, 0, Math.PI*2); ctx.stroke();
      const g=data.grid;
      $("backgroundSelection").textContent=`投影坐标：E ${(g.x_min_m+(selected.col+.5)*g.cell_size_m).toFixed(2)} m，N ${(g.y_max_m-(selected.row+.5)*g.cell_size_m).toFixed(2)} m`;
      if(data.landcover) $("backgroundSelection").textContent+=`；地表：${data.manifest.landcover_preview.labels[data.landcover[selected.row*g.width+selected.col]]}（影像推测）`;
    }
    ctx.restore();
    return;
  }
  if (layerMode === "state") drawStateLayer();
  else if (layerMode === "material_evidence") drawEvidenceLayer();
  else if (layerMode === "material" || layerMode === "landcover") drawCategoricalLayer(layerMode);
  else if (layerMode !== "background") drawScalarLayer(layerMode);
  if (layers.buildings && layerMode !== "background") drawBuildings();
  if (layers.grid && layerMode !== "background" && v.scale > 7) drawGrid();
  if (materialDraft && location.pathname === "/") {
    ctx.strokeStyle="#38e8ec"; ctx.lineWidth=2/v.scale;
    for (const edit of materialDraft.edits) ctx.strokeRect(edit.col,edit.row,1,1);
  }
  if (selected) { ctx.strokeStyle="#fff7d6"; ctx.lineWidth=2/v.scale; ctx.strokeRect(selected.col,selected.row,1,1); }
  if (ignitionChoice) {
    ctx.strokeStyle="#ff3d16"; ctx.lineWidth=3/v.scale;
    ctx.beginPath(); ctx.arc(ignitionChoice.col+.5,ignitionChoice.row+.5,Math.max(3/v.scale,.8),0,Math.PI*2); ctx.stroke();
  }
  ctx.restore();
  updateReadouts();
}

function drawEvidenceLayer() {
  const {width,height}=data.grid, values=data.material_evidence;
  if (!values) return;
  const colors={0:[217,80,206,190],1:[75,153,90,100],2:[77,146,215,155],3:[56,232,236,190],255:[0,0,0,0]};
  const pixels=ctx.createImageData(width,height);
  for(let i=0;i<values.length;i++) pixels.data.set(colors[values[i]]||[0,0,0,0],i*4);
  const off=document.createElement("canvas");off.width=width;off.height=height;
  off.getContext("2d").putImageData(pixels,0,0);ctx.drawImage(off,0,0);
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
  const {width,height}=data.grid, spec=scalarDisplaySpec(kind,data.manifest[kind]), values=data[kind];
  if (!spec || !values) return;
  const image=ctx.createImageData(width,height);
  const dynamic = ["temperature","hrr","surface_water","fuel"].includes(kind);
  for(let i=0;i<width*height;i++) {
    const value=values[dynamic ? frameOffset()+i : i];
    const validMask = kind === "temperature" ? data.temperatureValid : kind === "elevation" ? data.elevationValid : null;
    const valid=kind==="fuel" ? (data.initialFuel ? data.initialFuel[i]>0 : ![0,254,255].includes(data.states[frameOffset()+i])) : validMask ? validMask[dynamic ? frameOffset()+i : i] : 1;
    image.data.set(valid ? scalarColor(value,spec.minimum,spec.maximum,spec.scale==="log1p") : [0,0,0,0],i*4);
  }
  const buffer=document.createElement("canvas"); buffer.width=width; buffer.height=height;
  buffer.getContext("2d").putImageData(image,0,0); ctx.save(); ctx.imageSmoothingEnabled=false; ctx.drawImage(buffer,0,0); ctx.restore();
}

function drawCategoricalLayer(kind) {
  const {width,height}=data.grid, values=data[kind];
  if (!values) return;
  const image=ctx.createImageData(width,height), palette=[[70,113,92,205],[191,133,62,205],[102,128,177,205],[158,91,119,205],[120,120,77,205],[86,150,145,205],[178,104,55,205],[120,94,155,205]];
  for(let i=0;i<width*height;i++) {
    const value=values[i];
    image.data.set(value < 0 ? [0,0,0,0] : palette[Math.abs(value)%palette.length],i*4);
  }
  const buffer=document.createElement("canvas"); buffer.width=width; buffer.height=height;
  buffer.getContext("2d").putImageData(image,0,0); ctx.save(); ctx.imageSmoothingEnabled=false; ctx.drawImage(buffer,0,0); ctx.restore();
}

function drawImageLayer() {
  if (!data.image) return;
  const [a,b,c,d,e,f] = data.imageRef.affine_pixel_to_world;
  const g = data.grid;
  ctx.save();
  ctx.transform(a/g.cell_size_m, -d/g.cell_size_m, b/g.cell_size_m, -e/g.cell_size_m,
    (c-g.x_min_m)/g.cell_size_m, (g.y_max_m-f)/g.cell_size_m);
  ctx.drawImage(data.image,0,0); ctx.restore();
}

function drawStateLayer() {
  const {width,height} = data.grid;
  const image = ctx.createImageData(width,height);
  const offset = frameOffset();
  for (let i=0;i<width*height;i++) {
    const state=data.states[offset+i];
    // Keep satellite detail visible; unknown remains queryable and shown in the evidence layer.
    const color=state===254 && layers.image ? [0,0,0,0] : stateColors[state] || [255,0,255,255];
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
  const offset=frameOffset();
  if (data.componentReplay) return componentTools.combinedState(cells.filter(c=>data.presence[c]).map(c=>data.states[offset+c]));
  let sawBurned=false, sawHeating=false;
  for(const cell of cells){const state=data.states[offset+cell];if(state===3)return 3;if(state===2)sawHeating=true;if(state===4)sawBurned=true;}
  return sawHeating?2:(sawBurned?4:1);
}
function traceRing(ring,g) { ring.forEach(([x,y],i) => {const gx=(x-g.x_min_m)/g.cell_size_m, gy=(g.y_max_m-y)/g.cell_size_m; i?ctx.lineTo(gx,gy):ctx.moveTo(gx,gy);}); }
function drawGrid() { const g=data.grid; ctx.strokeStyle="rgba(255,255,255,.15)"; ctx.lineWidth=.5/viewport().scale; ctx.beginPath(); for(let x=0;x<=g.width;x++){ctx.moveTo(x,0);ctx.lineTo(x,g.height);} for(let y=0;y<=g.height;y++){ctx.moveTo(0,y);ctx.lineTo(g.width,y);} ctx.stroke(); }

function updateReadouts() {
  const {manifest,grid,states}=data, cells=grid.width*grid.height, offset=frameOffset();
  const seconds=manifest.sample_times_s[frame];
  $("timeReadout").textContent=manifest.time_display_unit==="min"?`${(seconds/60).toFixed(2)} min`:`${seconds.toFixed(2)} s`;
  $("frameReadout").textContent=`${frame+1} / ${manifest.frame_count}`; $("timeline").value=frame;
  let burning=0, ignited=0; for(let i=0;i<cells;i++){const s=states[offset+i]; if(s===3)burning++; if(data.componentReplay ? componentTools.occurredBy(data.ignitionTime[i],seconds) : s===3||s===4)ignited++;}
  $("burningCount").textContent=burning; $("ignitedCount").textContent=ignited;
  if (!data.componentReplay) {
    const summary = summarizeCoreFrame(states, data.fuel, data.initialFuel, data.frameLocal ? 0 : frame, cells);
    $("heatingCount").textContent = summary.heating;
    $("burnedOutCount").textContent = summary.burnedOut;
    $("remainingFuelTotal").textContent = summary.remainingFuelKg === null ? "—" : `${summary.remainingFuelKg.toFixed(1)} kg`;
  }
  let burningBuildings=0, affectedBuildings=0;
  for(const id of Object.keys(data.buildingCells?.buildings||{})){const status=buildingState(id);if(status===3)burningBuildings++;if([2,3,4,5].includes(status))affectedBuildings++;}
  $("burningBuildingCount").textContent=burningBuildings; $("affectedBuildingCount").textContent=affectedBuildings;
  let burningVegetation=0, affectedVegetation=0;
  for(let i=0;i<cells;i++) {
    if(data.building && data.building[i] >= 0) continue;
    const s=states[offset+i];
    if(s===3) burningVegetation++;
    if(s===2||s===3||s===4||s===5) affectedVegetation++;
  }
  $("burningVegetationCount").textContent=data.building?burningVegetation:"—";
  $("affectedVegetationCount").textContent=data.building?affectedVegetation:"—";
  if (selected) showSelection();
}

function showSelection() {
  const g=data.grid, i=selected.row*g.width+selected.col, offset=frameOffset()+i;
  if (materialDraft) {
    const edit=materialDraft.edits.find(e=>e.row===selected.row&&e.col===selected.col);
    const label=c=>materialDraft.materials.find(m=>m.code===c)?.label || c;
    $("selectedDraftSummary").textContent=edit ? `所选格草稿：${data.material_evidence?.[i] === 0 && data.material[i] === 0 ? "待核实地表（当前不燃假设）" : label(data.material[i])} → ${label(edit.after)}；依据：${edit.reason}（用户建议，未核实）` : "所选格没有待应用修正";
  }
  $("emptySelection").hidden=true; $("cellDetails").hidden=false;
  $("cellPosition").textContent=`${selected.row}, ${selected.col}`; $("cellId").textContent=i;
  $("cellState").textContent=data.componentReplay && !data.presence[i] ? "此格无所选部位" : stateLabels[data.states[offset]];
  const materialCode=data.material[i], materialLabels=data.manifest.material.labels||{};
  $("cellMaterial").textContent=materialLabels[String(materialCode)]||materialCode;
  const evidenceLabel=data.material_evidence ? data.manifest.material_evidence.labels[data.material_evidence[i]] : "旧结果未记录";
  $("cellEvidence").textContent=evidenceLabel;
  if (data.material_evidence?.[i] === 3) $("cellMaterial").textContent=`${String(materialLabels[String(materialCode)]||materialCode).replace(/\([^)]*\)/g,"")}（人工修正建议）`;
  $("cellLandcover").textContent=data.landcover?((data.manifest.landcover.labels||{})[String(data.landcover[i])]||data.landcover[i]):"—";
  if (data.material_evidence?.[i] === 0 && materialCode === 0 && data.initialFuel?.[i] === 0) {
    $("cellMaterial").textContent="待核实地表（当前不燃假设）";
    $("cellLandcover").textContent="未知地类，暂按不燃处理";
  }
  $("cellCoverage").textContent=data.coverage?`${(data.coverage[i]*100).toFixed(1)}%`:"—";
  $("cellFuel").textContent=`${(data.fuel[offset]*100).toFixed(1)}%`;
  $("cellWater").textContent=data.surface_water?`${data.surface_water[offset].toFixed(3)} kg`:"—";
  const initialMoisture = data.initialMoisture?.[i];
  $("cellInitialMoisture").textContent = data.initialMoisture
    ? (Number.isFinite(initialMoisture) ? `${(initialMoisture * 100).toFixed(2)}%` : "不适用")
    : "旧结果未记录";
  $("cellTemperature").textContent=data.temperature && (!data.temperatureValid || data.temperatureValid[offset])?`${data.temperature[offset].toFixed(1)} K`:"未计算";
  $("cellElevation").textContent=data.elevation && (!data.elevationValid || data.elevationValid[i])?`${data.elevation[i].toFixed(1)} m`:"—";
  $("cellHrr").textContent=data.hrr?`${data.hrr[offset].toFixed(1)} W`:"—";
  const ignitionVisible = data.ignitionTime && eventOccurredBy(data.ignitionTime[i],data.manifest.sample_times_s[frame]);
  $("cellIgnition").textContent=ignitionVisible?`${(data.ignitionTime[i]/60).toFixed(2)} min`:"—";
  const burnoutVisible = data.burnoutTime && eventOccurredBy(data.burnoutTime[i],data.manifest.sample_times_s[frame]);
  $("cellBurnout").textContent = burnoutVisible ? `${(data.burnoutTime[i]/60).toFixed(2)} min` : "—";
  const causeLabels=data.manifest.ignition_cause?.labels||{};
  $("cellCause").textContent=data.ignitionCause && ignitionVisible?(causeLabels[String(data.ignitionCause[i])]||data.ignitionCause[i]):"—";
  if (data.componentReplay) {
    $("componentDetails").hidden=false;
    componentTools.renderComponentCards($("componentCards"),data.componentReplay,i,frame,data.manifest.sample_times_s[frame],componentMode);
  }
  if (data.componentReplay && !data.presence[i]) {
    for (const key of ["cellFuel","cellWater","cellHrr","cellTemperature"]) $(key).textContent="不适用";
  }
  const lifecycle = burnoutVisible ? `；燃尽于 ${$("cellBurnout").textContent}` : ignitionVisible ? `；点燃于 ${$("cellIgnition").textContent}` : "";
  $("selectedCellSummary").textContent = `所选格 ${selected.row}, ${selected.col}：${$("cellMaterial").textContent}；当前${$("cellState").textContent}；剩余燃料 ${$("cellFuel").textContent}${lifecycle}`;
  $("selectedCellSummary").hidden = false;
  if (data.material_evidence) $("selectedCellSummary").textContent += `；依据：${evidenceLabel}`;
  if (data.initialFuel && !data.componentReplay) {
    const neighbors=adjacentFuelCounts(data.initialFuel,g.width,g.height,selected.row,selected.col);
    $("selectedCellSummary").textContent += `；初始邻接可燃格：边邻 ${neighbors.face}/4、对角 ${neighbors.diagonal}/4（不保证点燃）`;
  }
  const building=findBuilding(selected);
  $("cellBuilding").textContent=building?.startsWith("index:") ? `建筑标注索引 ${building.slice(6)}` : building||"—";
  const structure=data.buildings.buildings.find(record=>record.building_id===building);
  if(structure?.properties?.source_label==="fengyuqiao") $("cellBuilding").textContent+="（风雨桥）";
  const status=building ? buildingState(building) : null;
  $("cellBuildingState").textContent=status===null ? "—" : data.componentReplay && status===255 ? "无所选部位" : stateLabels[status];
}

function findBuilding(cell) {
  const g=data.grid, x=g.x_min_m+(cell.col+.5)*g.cell_size_m, y=g.y_max_m-(cell.row+.5)*g.cell_size_m;
  for(const building of data.buildings.buildings) for(const polygon of building.polygons) if(pointInRing(x,y,polygon.exterior) && !polygon.holes.some(r=>pointInRing(x,y,r))) return building.building_id;
  if(data.componentReplay && data.building?.[cell.row*g.width+cell.col]>=0) return `index:${data.building[cell.row*g.width+cell.col]}`;
  return null;
}
function pointInRing(x,y,ring){let inside=false;for(let i=0,j=ring.length-1;i<ring.length;j=i++){const [xi,yi]=ring[i],[xj,yj]=ring[j];if(((yi>y)!==(yj>y))&&(x<(xj-xi)*(y-yi)/(yj-yi)+xi))inside=!inside;}return inside;}

function resize(){const rect=canvas.getBoundingClientRect(), ratio=window.devicePixelRatio||1;canvas.width=Math.round(rect.width*ratio);canvas.height=Math.round(rect.height*ratio);render();}
canvas.addEventListener("click",event=>{if(!data||dragged){dragged=false;return;}const rect=canvas.getBoundingClientRect(),ratio=window.devicePixelRatio||1,v=viewport();const col=Math.floor(((event.clientX-rect.left)*ratio-v.x)/v.scale),row=Math.floor(((event.clientY-rect.top)*ratio-v.y)/v.scale);if(row>=0&&row<data.grid.height&&col>=0&&col<data.grid.width){selected={row,col};render();}});
function zoomMap(factor, point=null) {
  if (!data) return;
  const old = viewport(), zoom = Math.max(1,Math.min(32,mapZoom*factor));
  const px = point?.[0] ?? canvas.width/2, py = point?.[1] ?? canvas.height/2;
  const focus = !point ? selected || ignitionChoice : null;
  const wx = focus ? focus.col+.5 : (px-old.x)/old.scale;
  const wy = focus ? focus.row+.5 : (py-old.y)/old.scale;
  mapZoom=zoom; mapPanX=0; mapPanY=0;
  const next=viewport(); mapPanX=px-next.x-wx*next.scale; mapPanY=py-next.y-wy*next.scale;
  if (zoom===1) {mapPanX=0;mapPanY=0;}
  render();
}
$("zoomIn").onclick=()=>zoomMap(2);
$("zoomOut").onclick=()=>zoomMap(.5);
$("fitMap").onclick=()=>{mapZoom=1;mapPanX=0;mapPanY=0;render();};
canvas.addEventListener("wheel",event=>{
  event.preventDefault();const rect=canvas.getBoundingClientRect(),ratio=window.devicePixelRatio||1;
  zoomMap(event.deltaY<0?1.25:.8,[(event.clientX-rect.left)*ratio,(event.clientY-rect.top)*ratio]);
},{passive:false});
canvas.addEventListener("pointerdown",event=>{if(event.button!==0)return;dragged=false;dragStart={x:event.clientX,y:event.clientY,panX:mapPanX,panY:mapPanY};canvas.setPointerCapture(event.pointerId);});
canvas.addEventListener("pointermove",event=>{
  if(!dragStart||!data)return;
  const dx=event.clientX-dragStart.x,dy=event.clientY-dragStart.y;
  if(Math.hypot(dx,dy)>3)dragged=true;
  if(dragged){const ratio=window.devicePixelRatio||1;mapPanX=dragStart.panX+dx*ratio;mapPanY=dragStart.panY+dy*ratio;render();}
});
canvas.addEventListener("pointerup",()=>{dragStart=null;});
canvas.addEventListener("pointercancel",()=>{dragStart=null;dragged=false;});
window.addEventListener("resize",resize);

async function initSimulationControls(manifest) {
  let config;
  try {
    const response = await fetch("/api/config", {cache:"no-store"});
    if (!response.ok) return;
    config = await response.json();
  } catch { return; }
  if (config.model && config.model!==manifest.model) return;
  if (manifest.components && (!config.component_ignition_selection || config.model!==manifest.model)) return;
  if (config.grid?.height!==data.grid.height || config.grid?.width!==data.grid.width) return;
  $("simulationControls").hidden = false;
  if (manifest.scenario?.material_corrections?.applied_to_simulation) {
    $("correctionReplayNote").hidden=false;
    $("startSimulation").disabled=true;
    $("useSelectedIgnition").disabled=true;
  }
  const source = manifest.components ? manifest.scenario?.ignition_events?.[0] || config.ignition : manifest.scenario?.ignition_source || config.ignition;
  ignitionChoice = {row:source.row, col:source.col};
  $("ignitionChoice").textContent = `起火点：第 ${source.row} 行，第 ${source.col} 列`;
  const duration = manifest.scenario?.end_time_s || config.end_time_s;
  $("durationMinutes").value = Number(Math.min(duration / 60, 500).toPrecision(12));
  if (!manifest.components && location.pathname === "/") {
    try {
      materialDraft = await loadJson("/api/material-draft");
      for (const material of materialDraft.materials) {
        const option = document.createElement("option"); option.value=material.code; option.textContent=material.label;
        $("draftMaterial").append(option);
      }
      $("materialDraftControls").hidden=false;
      $("applyDraftChoice").hidden=!config.material_draft_application;
      updateMaterialDraft();
    } catch { /* Drafts are optional on legacy/static servers. */ }
  }
  try {
    const response = await fetch("/api/runs", {cache:"no-store"});
    if (!response.ok) return;
    const {runs} = await response.json();
    const picker = $("savedRuns");
    for (const run of runs) {
      const option = document.createElement("option");
      option.value = run.url;
      option.textContent = `${run.initial ? "初始演示" : "保存结果"} · ${run.row},${run.col} · ${(run.end_time_s/60).toFixed(2)}分钟${run.initial ? "" : ` · ${new Date(run.saved_at*1000).toLocaleString()}`}${run.material_correction_count ? ` · 材质修正${run.material_correction_count}格` : ""}`;
      picker.append(option);
    }
    if (runs.length) {
      picker.value = runs.some(run=>run.url===location.pathname) ? location.pathname : runs[0].url;
      $("savedRunsPicker").hidden = false;
      $("openSavedRun").hidden = false;
    }
  } catch { /* History is optional; simulation controls remain usable. */ }
}

function updateMaterialDraft() {
  $("undoMaterialDraft").disabled=!materialDraft.can_undo;
  const edits=materialDraft.edits;
  $("materialDraftSummary").textContent=`已保存 ${edits.length} 格修正草稿 · 版本 ${materialDraft.revision} · 尚未应用到当前回放`;
  render();
}
async function changeMaterialDraft(action) {
  if (!materialDraft) return;
  if (action === "set" && !selected) { $("materialDraftSummary").textContent="请先选择原胞"; return; }
  const save=$("saveMaterialDraft"), undo=$("undoMaterialDraft"); save.disabled=true; undo.disabled=true;
  try {
    const payload={action,revision:materialDraft.revision};
    if (action === "set") Object.assign(payload,selected,{material:Number($("draftMaterial").value),reason:$("draftReason").value});
    const response=await fetch("/api/material-draft",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const result=await response.json();
    if (!response.ok) throw new Error(result.error || "草稿保存失败");
    materialDraft=result; updateMaterialDraft();
  } catch(error) { $("materialDraftSummary").textContent=error.message; }
  finally { save.disabled=false; undo.disabled=!materialDraft.can_undo; }
}
$("saveMaterialDraft").onclick=()=>changeMaterialDraft("set");
$("undoMaterialDraft").onclick=()=>changeMaterialDraft("undo");

$("openSavedRun").onclick = () => {
  const url = $("savedRuns").value;
  if (url === "/" || /^\/result\/[0-9a-f]{32}\/$/.test(url)) location.href = url;
};

$("useSelectedIgnition").onclick = () => {
  if (!selected || !data) {
    $("simulationMessage").textContent = "请先点击地图中的一个元胞";
    return;
  }
  const cellId = selected.row * data.grid.width + selected.col;
  const edit=$("applyMaterialDraft").checked ? materialDraft?.edits.find(e=>e.row===selected.row&&e.col===selected.col) : null;
  const templateId=edit ? data.material.findIndex((code,i)=>code===edit.after&&data.initialFuel?.[i]>0) : -1;
  const state = edit ? (templateId>=0 ? 1 : 0) : data.initialFuel ? (data.initialFuel[cellId] > 0 ? 1 : 0) : data.states[cellId];
  const componentBurnable = data.componentReplay?.cellId.some((cell,n)=>cell===cellId && data.componentReplay.burnable[n]);
  if (data.componentReplay ? !componentBurnable : state === 0 || state === 254 || state === 255) {
    $("simulationMessage").textContent = "该元胞不可燃，请选择建筑或植被格";
    return;
  }
  ignitionChoice = {...selected};
  $("ignitionChoice").textContent = `起火点：第 ${selected.row} 行，第 ${selected.col} 列`;
  $("simulationMessage").textContent = "起火点已选择";
  render();
};

$("startSimulation").onclick = async () => {
  const minutes = Number($("durationMinutes").value);
  if (!ignitionChoice || !Number.isFinite(minutes) || minutes < 1/60 || minutes > 500) {
    $("simulationMessage").textContent = "请选择起火点，并输入 1 秒至 500 分钟的时长";
    return;
  }
  const button = $("startSimulation"); button.disabled = true;
  $("simulationMessage").textContent = "正在提交模拟…";
  try {
    const payload={...ignitionChoice,end_time_s:minutes*60};
    if ($("applyMaterialDraft").checked && materialDraft) Object.assign(payload,{use_material_draft:true,draft_revision:materialDraft.revision});
    const response = await fetch("/api/run", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify(payload),
    });
    const submission = await response.json();
    if (!response.ok) throw new Error(submission.error || "提交失败");
    $("simulationMessage").textContent = "模拟计算中，完成后自动打开新回放…";
    while (true) {
      await new Promise(resolve => setTimeout(resolve, 1500));
      const status = await loadJson(`/api/jobs/${submission.job_id}`);
      if (status.status === "completed") { location.href = status.url; return; }
      if (status.status === "failed") throw new Error(status.error);
      if (Number.isFinite(status.progress_fraction)) $("simulationMessage").textContent = status.phase === "writing_results" ? "计算完成，正在保存回放…" : `模拟计算中 ${(status.progress_fraction*100).toFixed(0)}% · ${(status.simulated_time_s/60).toFixed(1)} 分钟`;
    }
  } catch (error) {
    $("simulationMessage").textContent = `模拟失败：${error.message}`;
    button.disabled = false;
  }
};

async function setFrame(value) {
  const target = Math.max(0,Math.min(data.manifest.frame_count-1,value));
  if (!frameLoader) { frame=target; render(); return; }
  pendingFrame = target;
  if (loadingFrame) return;
  loadingFrame = true;
  try {
    while (pendingFrame !== null) {
      const next = pendingFrame; pendingFrame = null;
      $("frameReadout").textContent = "载入帧…";
      const buffers = await frameLoader.load(next);
      if (pendingFrame !== null) continue;
      for (const [key,type] of [["state",Uint8Array],["fuel",Float32Array],["temperature",Float32Array],["temperature_valid",Uint8Array],["hrr",Float32Array],["surface_water",Float32Array]]) {
        if (buffers[key]) data[key==="state" ? "states" : key==="temperature_valid" ? "temperatureValid" : key] = new type(buffers[key]);
      }
      frame = next; render();
    }
  } catch (error) {
    $("frameReadout").textContent = "帧加载失败";
    $("simulationMessage").textContent = error.message;
  } finally { loadingFrame = false; }
}
function setPlaying(value) { playing=value; accumulated=0; $("playPause").textContent=playing?"Ⅱ":"▶"; }
$("timeline").addEventListener("input",e=>{setPlaying(false);setFrame(Number(e.target.value));});
$("jumpStart").onclick=()=>{setPlaying(false);setFrame(0);};
$("stepBack").onclick=()=>{setPlaying(false);setFrame(frame-1);};
$("stepForward").onclick=()=>{setPlaying(false);setFrame(frame+1);};
$("playPause").onclick=()=>{if(!playing&&frame===data.manifest.frame_count-1)setFrame(0);setPlaying(!playing);};
for(const [id,key] of [["toggleImage","image"],["toggleBuildings","buildings"],["toggleGrid","grid"]]) $(id).onchange=e=>{layers[key]=e.target.checked;render();};
$("layerMode").onchange=e=>{layerMode=e.target.value;if(data?.backgroundOnly){render();return;}const scalar=!['state','background','material','landcover','material_evidence'].includes(layerMode), spec=scalarDisplaySpec(layerMode,data?.manifest[layerMode]);$("scalarLegend").hidden=!scalar;document.querySelector(".legend").hidden=scalar||layerMode==="material_evidence"||layerMode==="background";$("evidenceLegend").hidden=layerMode!=="material_evidence";if(scalar&&spec){$("scalarMaximum").textContent=`${(spec.maximum*spec.legendMultiplier).toFixed(1)} ${spec.unit||''}`;$("scalarMinimum").textContent=`${(spec.minimum*spec.legendMultiplier).toFixed(1)} ${spec.unit||''}`;}render();};
function tick(now){if(playing&&data&&!loadingFrame){if(!lastTick)lastTick=now;accumulated+=(now-lastTick)*Number($("speed").value);if(accumulated>=500){setFrame(frame+1);accumulated=0;if(frame===data.manifest.frame_count-1){setPlaying(false);}}}lastTick=now;requestAnimationFrame(tick);} requestAnimationFrame(tick);
init();
