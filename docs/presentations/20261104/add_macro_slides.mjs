const ROOT='/Users/tone/Documents/github/PyFireCA/docs/presentations/20261104/';
function shape(s,x,y,w,h,fill,line='none',width=0){return s.shapes.add({geometry:'rect',position:{left:x,top:y,width:w,height:h},fill,line:{fill:line,width,style:'solid'}});}
function text(s,t,x,y,w,h,z=23,c='#26332F',b=false){let q=shape(s,x,y,w,h,'none');q.text=t;q.text.style={typeface:'PingFang SC',fontSize:z,color:c,bold:b,insets:0,autoFit:'none'};return q;}
export default async function addMacro({presentation}){
 if(presentation.slides.items.length!==12)throw new Error('Expected existing 12-page deck, avoid duplicate additions');
 const cases=[{id:'batuan',name:'岜团',source:'B0062桥侧源',area:'6969.7 → 5756.5',n:13},{id:'pingyan',name:'平岩',source:'B0164鼓楼源',area:'12786.3 → 10023.7',n:14}];
 for(const v of cases){
  const s=presentation.slides.add({width:1280,height:720});s.background.fill='#F7F3EA';
  text(s,`${v.name}：全村尺度的火灾传播与防火线位置`,58,43,1168,70,37,'#26332F',true);
  text(s,`${v.source}  ·  起火后60分钟  ·  局部屋面0.5 m  ·  全村共同视窗`,65,119,1150,38,22,'#61716A');
  s.images.add({path:ROOT+`assets/${v.id}_macro_fire.png`,position:{left:55,top:165,width:1175,height:390},fit:'contain',alt:`${v.name}全村无干预与既有方案火灾传播对照`});
  const legend=[['0—20 min','#DFAD4D'],['20—40 min','#E47740'],['40—60 min','#A82E42']];
  legend.forEach((a,i)=>{shape(s,76+i*183,576,20,20,a[1]);text(s,a[0],106+i*183,573,151,29,20);});
  shape(s,639,576,20,20,'none','#087D89',2);text(s,'遗产核心',670,573,143,29,20);
  text(s,'┄ 干预位置',836,573,165,29,20);text(s,'★ 起火源',1040,573,160,29,20,'#70449D');
  text(s,`累计引燃屋面：${v.area} m²；核心联合结局：失效 → 保护`,75,620,1135,42,23,'#167D83',true);
  text(s,'来源：2026-11-03归档模拟；灰线为全部建筑，浅绿为地表分类背景，色点为引燃屋面元胞',58,676,1115,21,13.5,'#61716A');text(s,String(v.n),1191,675,41,24,15,'#61716A');
  s.speakerNotes.text=`本页是${v.name}完整村落尺度的分析图，不截取核心附近细节。比较同一历史固定源${v.source}、相同精确25m²起火多边形和同一共同布局下的无干预/既有方案，完整运行3600s，展示0.5m屋面实现；无新CA。两图保持相同覆盖全村建筑范围的地理视窗、比例尺和朝向。底图来自既有5m背景材质分类，含RGB推断，不是新增地表调查。暖色点为屋面物理元胞首次引燃时间段，尺寸仅用于可视化，不表示整栋烧毁；未把地表受火误计为屋面受火。青边遗产核心，黑色虚线为选中干预轮廓，紫星为固定源；各核心完整逐项和双尺度结果见第11页与归档。累计屋面面积${v.area}m²，核心联合保护是此情景内60分钟结果，不代表整村火灾停止或通用风险。岜团既有清单B0061/B0071/B0076；平岩B0119/B0165/B0167/B0177/B0190。来源docs/results/heritage-followup-20261103/summary.json、各evaluation下layout.npz和final-state.npz；绘图脚本prepare_macro_maps.py与macro-map-evidence.json。`;
 }
 await presentation.render({slide:[12,13]});return {slides:presentation.slides.items.length,added:[13,14]};
}
