import build from './build_session.mjs';
const ROOT='/Users/tone/Documents/github/PyFireCA';
const C={ink:'#26332F',teal:'#167D83',fire:'#C95B37',paper:'#F7F3EA',muted:'#61716A'};
function tx(s,t,x,y,w,h,z=24,c=C.ink,b=false){let q=s.shapes.add({geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});q.text=t;q.text.style={typeface:'PingFang SC',fontSize:z,color:c,bold:b,insets:0,autoFit:'none'};return q;}
function table(s,values,x,y,w,h,widths,font=22){let t=s.tables.add({rows:values.length,columns:values[0].length,left:x,top:y,width:w,height:h,columnWidths:widths,values});t.borders.assign({fill:'#D1DAD3',width:1});t.cells.block({row:0,column:0,rowCount:values.length,columnCount:values[0].length}).assign({textStyle:{typeface:'PingFang SC',fontSize:font,color:C.ink},margins:{top:10,bottom:8,left:12,right:12},anchor:'middle'});for(let r=0;r<values.length;r++)for(let c=0;c<values[0].length;c++){let a=t.getCell(r,c);a.fill=r===0?'#202A29':r%2?'#FFFFFF':'#E5ECE5';if(r===0)a.text.style={fontSize:font,bold:true,color:'#FFFFFF'};}return t;}
function base(p,title,src,notes){let s=p.slides.add({width:1280,height:720});s.background.fill=C.paper;tx(s,title,58,43,1168,75,37,C.ink,true);tx(s,src,58,673,1120,23,14,C.muted);s.speakerNotes.text=notes;return s;}
export default async function compact({presentation}){
 presentation.slides.keep([0]);
 await build({presentation,input:{slides:[3,8,10,14,17,18,21,23,25]}});
 let q=await presentation.inspect({select:'$.slides[9].elements[?(@.kind == "table")]',include:['id']});if(q.records.length)presentation.delete(q.records.map(r=>r.id));
 let s=presentation.slides.getItem(9);
 table(s,[['补充确认','基准保护','方案保护'],['岜团B0063\n6个定向落点','0/6','6/6'],['平岩\n4个新落点','3/4','4/4']],750,189,462,264,[206,128,128],21);
 tx(s,'均为冻结方案后的确认',767,466,425,32,22,C.muted);
 s.speakerNotes.text+='\n补充表：岜团B0063六个同源新点，两臂双尺度24份，基准0/6保护、三栋6/6；平岩四新点两臂双尺度16份，基准3/4、五栋4/4。来源docs/HERITAGE_B0063_ADDBACK_20261024.md及HERITAGE_FULL_SEARCH_AND_PINGYAN_20261029.md。';
 s=base(presentation,'60分钟同源对照：两村既有方案均保持核心保护','来源：2026-11-03固定比较24份；时间按1 m / 0.5 m列示', '两村各2固定历史源×3臂×双尺度=24份完整3600s模拟，新独立抽样0。表为任一核心首次引燃时间，未引燃表示完整观察3600s。岜团桥侧基准后续鼓楼3281.08/3332.08s引燃。平岩桥侧基准1m失效而0.5m未引燃，按任一尺度失效合并。两村既有方案均通过，但不把旧20min数量/面积证书扩为60min最优性。来源docs/HERITAGE_DURATION_POSITION_RESULTS_20261103.md及docs/results/heritage-followup-20261103/summary.json。');
 table(s,[['村落 / 固定火源','无干预首燃 s','既有方案','位置对照首燃 s'],['岜团 / 鼓楼侧B0077','857 / 906','未引燃','857 / 906'],['岜团 / 桥侧B0062','989.36 / 1046','未引燃','1249.36 / 1409.36'],['平岩 / 鼓楼侧B0164','1168.36 / 1251','未引燃','未引燃'],['平岩 / 桥侧B0120','1564 / 未引燃','未引燃','未引燃']],74,163,1133,340,[331,283,206,313],22);
 tx(s,'岜团桥侧位置对照在20分钟时尚未失效，随后约21—23分钟引燃核心',80,542,1120,71,25,C.fire,true);
 tx(s,'源、多边形、能量和共同布局固定；全部24份达到3600 s并通过验收',80,621,1120,34,22,C.muted);
 s=base(presentation,'传播形态：有效位置组合仍可产生不同的外围受火','来源：平岩B0164同源，0.5 m；累计到火位置，不等同整栋损毁','图中三臂共同视窗，黄色0—20min、橙20—40、深红40—60；青色鼓楼B0166，虚线干预。统计包含全部3核心。两干预方案均保护核心；60min累计引燃屋面面积1m既有10290.21、对照8754.95m²，0.5m10023.68、8502.88m²，约少15%。名义面积995.481/995.125m²，实际清空985.608/974.454m²，对照约少1.13%实际清空；匹配名义面积。来源docs/HERITAGE_DURATION_POSITION_RESULTS_20261103.md。当前成果为方法、两村方案和条件验证；后续扩大火源、提取空间特征与独立观测校验，不在本汇报给出最终通用设置规律。');
 s.images.add({path:ROOT+'/docs/presentations/20261104/assets/pingyan_drum_morph.png',position:{left:48,top:139,width:1188,height:353},fit:'contain',alt:'平岩鼓楼侧三臂累计传播形态'});
 tx(s,'黄色：0—20 min   橙色：20—40 min   深红：40—60 min',83,501,1110,38,22,C.muted);
 tx(s,'两布局均保护核心；鼓楼侧情景中，对照的累计引燃屋面面积约少15%',81,555,1120,63,27,C.teal,true);
 tx(s,'下一步：扩大起火位置覆盖，量化有效布局共同特征，再检验可推广性',81,628,1120,30,22,C.muted);
 // Replace the old long-deck folios without changing evidence objects.
 for(let i=1;i<presentation.slides.items.length;i++){
  const query=await presentation.inspect({select:`$.slides[${i}].elements[?(@.kind == "textbox")]`,include:['id','position','text']});
  let found=false;for(const r of query.records){if(r.position?.left>=1180 && r.position?.top>=660){presentation.resolve(r.id).text=String(i+1).padStart(2,'0');found=true;}}
  if(!found)tx(presentation.slides.getItem(i),String(i+1).padStart(2,'0'),1193,673,40,24,15,C.muted);
 }
 return {slides:presentation.slides.items.length};
}
