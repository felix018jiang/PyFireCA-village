// Pure replay transformations. Raw component arrays are never changed.
export const componentLabels = {
  surface_dead: "树下细死燃料", upper_fuel: "树木上部燃料",
  combustible_base: "建筑可燃基部", upper_combustible: "建筑上部可燃部位", whole: "单层燃料",
};
export const componentLabel = (id) => Object.hasOwn(componentLabels, id) ? componentLabels[id] : id;
export function combinedState(states) {
  let heating=false, burned=false, unburned=false, inert=false, unknown=false;
  for (const s of states) {
    if (s===3) return 3;
    heating ||= s===2; burned ||= s===4 || s===5; unburned ||= s===1 || s===2 || s===5;
    inert ||= s===0; unknown ||= s===254;
  }
  return burned && unburned ? 5 : heating ? 2 : burned ? 4 : unburned ? 1 : unknown ? 254 : inert ? 0 : 255;
}
export function validateReplay(raw, cells, frames, times) {
  const nodes=raw.ids.length;
  if (!Number.isSafeInteger(cells) || cells<=0 || !Number.isSafeInteger(frames) || frames<=0 || nodes<=0)
    throw new Error("部位回放维度不正确");
  for (const key of ["cellId","burnable","initialFuel","initialDry","initialMoisture","ignitionTime","burnoutTime","ignitionCause"])
    if (raw[key]?.length!==nodes) throw new Error(`部位静态数组长度不正确：${key}`);
  for (const key of ["states","fuel","temperature","temperatureValid","hrr","surface_water"])
    if (raw[key]?.length!==frames*nodes) throw new Error(`部位动态数组长度不正确：${key}`);
  if (times.length!==frames || times.some((t,i)=>!Number.isFinite(t)||t<0||(i>0&&t<=times[i-1])))
    throw new Error("部位采样时间不正确");
  const identities=new Set();
  for (let n=0;n<nodes;n++) {
    const c=raw.cellId[n], id=raw.ids[n], key=JSON.stringify([c,id]);
    if (!Number.isInteger(c)||c<0||c>=cells||typeof id!=="string"||!id||identities.has(key))
      throw new Error("部位索引或编号不正确");
    identities.add(key);
    if (![0,1].includes(raw.burnable[n])) throw new Error("部位可燃掩膜不正确");
    if (!Number.isFinite(raw.initialFuel[n])||raw.initialFuel[n]<0||!Number.isFinite(raw.initialDry[n])||raw.initialDry[n]<0)
      throw new Error("部位初始燃料质量不正确");
    if (raw.burnable[n] && (!Number.isFinite(raw.initialMoisture[n])||raw.initialMoisture[n]<0))
      throw new Error("部位初始含水率不正确");
  }
  return raw;
}
export function aggregateReplay(raw, cells, frames, mode=null) {
  if (mode!==null&&!raw.ids.includes(mode)) throw new Error("不存在所选燃料部位");
  const count=cells*frames, nodes=raw.ids.length;
  const result={
    states:new Uint8Array(count).fill(255), fuel:new Float32Array(count),
    temperature:new Float32Array(count).fill(NaN), temperatureValid:new Uint8Array(count),
    hrr:new Float32Array(count), surface_water:new Float32Array(count),
    initialMoisture:new Float32Array(cells).fill(NaN), presence:new Uint8Array(cells),
    ignitionTime:new Float64Array(cells).fill(NaN), ignitionCause:new Uint8Array(cells),
    burnoutTime:new Float64Array(cells).fill(NaN),
  };
  const byCell=Array.from({length:cells},()=>[]);
  for(let n=0;n<nodes;n++) if(mode===null||raw.ids[n]===mode) byCell[raw.cellId[n]].push(n);
  for(let c=0;c<cells;c++) {
    const selected=byCell[c]; if(!selected.length) continue;
    result.presence[c]=1;
    let initial=0, dry=0, water=0;
    const combustible=selected.filter(n=>raw.burnable[n]);
    for(const n of combustible) {
      initial+=raw.initialFuel[n]; dry+=raw.initialDry[n];
      if(raw.initialDry[n]>0) water+=raw.initialDry[n]*raw.initialMoisture[n];
      const t=raw.ignitionTime[n];
      if(Number.isFinite(t)&&(!Number.isFinite(result.ignitionTime[c])||t<result.ignitionTime[c])) {
        result.ignitionTime[c]=t; result.ignitionCause[c]=raw.ignitionCause[n];
      }
    }
    if(dry>0) result.initialMoisture[c]=water/dry;
    if(combustible.length&&combustible.every(n=>Number.isFinite(raw.burnoutTime[n])))
      result.burnoutTime[c]=Math.max(...combustible.map(n=>raw.burnoutTime[n]));
    for(let f=0;f<frames;f++) {
      const dest=f*cells+c, source=f*nodes;
      result.states[dest]=combinedState(selected.map(n=>raw.states[source+n]));
      let remaining=0;
      for(const n of combustible) {
        const i=source+n;
        remaining+=raw.fuel[i]*raw.initialFuel[n];
        result.hrr[dest]+=raw.hrr[i]; result.surface_water[dest]+=raw.surface_water[i];
        if(raw.temperatureValid[i]&&(raw.states[i]===1||raw.states[i]===2)) {
          result.temperature[dest]=result.temperatureValid[dest]?Math.max(result.temperature[dest],raw.temperature[i]):raw.temperature[i];
          result.temperatureValid[dest]=1;
        }
      }
      result.fuel[dest]=initial>0?remaining/initial:0;
    }
  }
  return result;
}
export const occurredBy = (time, now) => Number.isFinite(time) && time<=now;

export function renderComponentCards(container, raw, cell, frame, now, mode) {
  container.replaceChildren();
  const labels={0:"不燃",1:"未燃",2:"受热",3:"燃烧",4:"燃尽",254:"未知",255:"域外"};
  for(let n=0;n<raw.ids.length;n++) {
    if(raw.cellId[n]!==cell||!raw.burnable[n]) continue;
    const i=frame*raw.ids.length+n;
    const article=document.createElement("article"); article.className="component-card";
    if(mode!==null&&raw.ids[n]!==mode) article.classList.add("muted-component");
    const title=document.createElement("h3"); title.textContent=componentLabel(raw.ids[n]); article.append(title);
    const state=document.createElement("span"); state.className=`component-state state-${raw.states[i]}`;
    state.textContent=labels[raw.states[i]]; article.append(state);
    const metrics=[
      ["初始含水率（干基）",`${(raw.initialMoisture[n]*100).toFixed(2)}%`],
      ["剩余可燃燃料",`${(raw.fuel[i]*raw.initialFuel[n]).toFixed(3)} kg`],
      ["有效受热层剩余水",`${raw.surface_water[i].toFixed(3)} kg`],
      ["未燃表面温度",raw.temperatureValid[i]?`${raw.temperature[i].toFixed(1)} K`:"未计算"],
      ["末次计算步平均 HRR",`${(raw.hrr[i]/1000).toFixed(2)} kW`],
      ["首次点燃",occurredBy(raw.ignitionTime[n],now)?`${(raw.ignitionTime[n]/60).toFixed(2)} min`:"尚未点燃"],
    ];
    const dl=document.createElement("dl");
    for(const [label,value] of metrics) { const dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=label;dd.textContent=value;dl.append(dt,dd); }
    article.append(dl); container.append(article);
  }
  if(!container.children.length) container.textContent="此格没有可燃燃料部位。";
}
