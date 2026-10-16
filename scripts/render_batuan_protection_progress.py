"""Show honest in-progress results while the frozen study is computing."""

import argparse

# ruff: noqa: E501, RUF001
from pathlib import Path

HTML = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>岜团村90分钟优化 · 计算中</title><style>body{background:#101c25;color:#e5edf2;font:16px/1.7 system-ui;margin:0}main{max-width:1200px;margin:auto;padding:32px}p{color:#bbcbd5}table{border-collapse:collapse;width:100%;background:#172833}td,th{text-align:left;padding:14px;border-bottom:1px solid #354958}progress{width:140px;accent-color:#62dcc7}a,.safe{color:#62dcc7}.wait{color:#f0ce91}.scroll{overflow:auto}</style><main><p>PYFIRECA / BATUAN / LIVE EXPERIMENT</p><h1>90分钟民居与风雨桥联合保护</h1><p id="status">读取冻结协议与运行记录…</p><p>目标：所有训练情景的民居和桥均不引燃，再减少实际植被清理面积。每类独立搜索；下表为尚未完成、尚未经过新留出和参数扰动验证的中间结果。</p><div class="scroll"><table><thead><tr><th>区域</th><th>线形</th><th>方案请求</th><th>训练零引燃方案</th><th>当前最好清理面积</th></tr></thead><tbody id="rows"></tbody></table></div><p>训练起火点T1=(160,151)、T2=(161,150)，时限均为90分钟。验证前冻结全部选线；新留出V1=(163,151)、V2=(163,149)，另作引燃温升降低5%的独立敏感性检验。</p><p>真实折线与多带组合不预设优于直线。当前静风、无飞火，村外/村内为建筑20m缓冲代理区，施工边界与材料仍待调查校准。</p><p><a href="protocol.json">冻结协议</a> · <a href="report.json">运行检查点</a> · <a href="protection-coverage.json">保护对象覆盖核对</a></p></main><script>
const zones={outer_proxy:'村外代理区',inner_proxy:'村内近邻区'},families={straight:'单直线',polyline:'单折线',multiple:'多条带组合'};
const less=(a,b)=>{for(let i=0;i<a.length;i++){if(a[i]!=b[i])return a[i]<b[i];}return false;};
async function update(){try{let report=await(await fetch('report.json',{cache:'no-store'})).json();if(report.status==='completed'){location.reload();return;}
let protocol=report.protocol,rows=[];document.querySelector('#status').textContent=report.status==='searching'?'正在独立搜索六组布局；每15秒自动更新。':'六组搜索已结束，正在冻结选线后的验证与全图复核。';
for(const [z,zt] of Object.entries(zones))for(const [f,ft] of Object.entries(families)){let records=[];try{let text=await(await fetch(`${z}-${f}-history.jsonl`,{cache:'no-store'})).text();let unique=new Map();for(let line of text.split('\\n')){try{let r=JSON.parse(line);if(r.event==='evaluation')unique.set(r.plan_id,r);}catch{}}records=[...unique.values()];}catch{}
let eligible=records.filter(r=>r.status==='ok'&&r.plan.paths.length>0),safe=eligible.filter(r=>r.effectiveness[0]===0);let best=eligible.reduce((b,r)=>!b||less([...r.effectiveness,r.cleared_area_m2],[...b.effectiveness,b.cleared_area_m2])?r:b,null),cap=protocol.configs[f].max_evaluations;
rows.push(`<tr><td>${zt}</td><td>${ft}</td><td><progress max="${cap}" value="${records.length}"></progress> ${records.length}/${cap}</td><td>${safe.length}</td><td class="${safe.length?'safe':'wait'}">${safe.length?Math.min(...safe.map(r=>r.cleared_area_m2)).toFixed(2)+' m²':best?'尚未达标；最差引燃 '+best.effectiveness[0]+' 个结构':'等待评价'}</td></tr>`);}
document.querySelector('#rows').innerHTML=rows.join('');}catch(e){document.querySelector('#status').textContent='等待下一份完整检查点…';}}
update();setInterval(update,15000);
</script></html>"""

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("study", type=Path)
    study = p.parse_args().study
    if (study / "index.html").exists():
        raise FileExistsError("preserve the existing page")
    (study / "index.html").write_text(HTML)
