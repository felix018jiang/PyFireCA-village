const C={paper:'#F7F3EA',ink:'#26332F',teal:'#167D83',fire:'#C95B37',muted:'#61716A',pale:'#E5ECE5',gold:'#DBA24E'};
const A='/Users/tone/Documents/github/PyFireCA/docs/presentations/20261104/assets/';
function rect(s,x,y,w,h,fill,line='none',lw=0,geometry='rect'){return s.shapes.add({geometry,position:{left:x,top:y,width:w,height:h},fill,line:{fill:line,width:lw,style:'solid'}});}
function tx(s,t,x,y,w,h,z=24,c=C.ink,b=false){const q=rect(s,x,y,w,h,'none');q.text=t;q.text.style={typeface:'PingFang SC',fontSize:z,color:c,bold:b,insets:0,autoFit:'none'};return q;}
function arrow(s,x,y,w=35,h=23){return rect(s,x,y,w,h,C.teal,'none',0,'rightArrow');}
function img(s,name,x,y,w,h){s.images.add({path:A+name+'.png',position:{left:x,top:y,width:w,height:h},fit:'contain',alt:name});}
function page(p,n,title,src){const s=p.slides.add({width:1280,height:720});s.background.fill=C.paper;tx(s,title,58,43,1168,72,37,C.ink,true);tx(s,src,58,673,1120,24,14,C.muted);tx(s,String(n).padStart(2,'0'),1193,673,40,24,15,C.muted);return s;}
function table(s,v,x,y,w,h,widths){const t=s.tables.add({rows:v.length,columns:v[0].length,left:x,top:y,width:w,height:h,columnWidths:widths,values:v});t.borders.assign({fill:'#CED8D0',width:1});t.cells.block({row:0,column:0,rowCount:v.length,columnCount:v[0].length}).assign({textStyle:{typeface:'PingFang SC',fontSize:22,color:C.ink},margins:{top:10,bottom:8,left:13,right:13},anchor:'middle'});for(let r=0;r<v.length;r++)for(let c=0;c<v[0].length;c++){let q=t.getCell(r,c);q.fill=r===0?'#202A29':r%2?'#FFFFFF':C.pale;if(r===0)q.text.style={fontSize:22,bold:true,color:'#FFFFFF'};}return t;}
export default async function revise({presentation}){
 const old=(await presentation.inspect({select:'$.slides[*]',include:['id']})).records.map(r=>r.id);
 if(old.length!==12)throw new Error('Expected the reviewed 12-slide deck');
 const oldNotes=[2,3,4].map(i=>presentation.slides.getItem(i).speakerNotes.text);
 let s=page(presentation,3,'燃烧、传热与点燃：每个元胞遵循同一物理链条','来源：physics_v2.py / solver_v3.py；变量与详细分段公式见演讲备注');
 tx(s,'燃烧放热',70,162,190,46,28,C.fire,true);img(s,'ca_burn',268,137,930,123);
 tx(s,'f(τ)：二次增长 → 平台 → 线性衰减；释放能量受剩余燃料约束',271,266,921,35,22,C.muted);
 tx(s,'受热升温',70,335,190,46,28,C.teal,true);img(s,'eq_heat',249,310,960,121);
 tx(s,'正热量依次用于湿态升温、水分蒸发和干态升温',271,439,922,35,22,C.muted);
 tx(s,'点燃转态',70,521,190,46,28,C.fire,true);img(s,'ca_ign',270,506,925,60);
 tx(s,'同时要求：尚未燃烧、可燃、允许引燃；本批等效阈值 T_ign = 520 K',271,581,925,62,22,C.muted);
 s.speakerNotes.text=oldNotes.join('\n\n')+'\n合并页保留原三页说明。f采用t_g=120s、t_s=395.36s、t_d=300s；Qmax密度300kW/m²活动面积。ΔE为分段积分和剩余可热解燃料能量的最小值；Δm=ΔE/H。温度是受热表面温度，520K是当前合成等效阈值，非所有木材通用常数。';
 s=page(presentation,4,'元胞自动机如何把村落转成可计算的空间','来源：roof_thermal.py；完整5 m背景与局部1 m / 0.5 m屋面；图为离散示意');
 tx(s,'① 配准与分类',72,147,340,45,29,C.teal,true);tx(s,'② 轮廓与网格求交',473,147,390,45,29,C.teal,true);tx(s,'③ 独立片元状态',902,147,330,45,29,C.teal,true);
 const colors=[['#A5BA9A','#A5BA9A','#CAD8A3','#D9DBD4'],['#CAD8A3','#D9DBD4','#D9DBD4','#D9DBD4'],['#CAD8A3','#D9DBD4','#A5BA9A','#A5BA9A']];
 for(let r=0;r<3;r++)for(let c=0;c<4;c++)rect(s,77+c*66,223+r*66,65,65,colors[r][c]);
 tx(s,'地表：草地 / 林木 / 硬化地表等',77,442,302,75,22,C.muted);arrow(s,379,309,50,26);
 for(let r=0;r<3;r++)for(let c=0;c<3;c++)rect(s,480+c*95,218+r*75,95,75,'none','#AEBFB1',1);
 rect(s,509,250,111,94,C.fire);rect(s,636,280,94,101,C.teal);
 for(let i=1;i<3;i++){rect(s,509+i*37,250,1,94,C.paper);rect(s,509,250+i*31,111,1,C.paper);rect(s,636+i*31,280,1,101,C.paper);rect(s,636,280+i*33,94,1,C.paper);}
 tx(s,'A',541,282,60,40,28,'#FFFFFF',true);tx(s,'B',667,312,60,40,28,'#FFFFFF',true);
 tx(s,'一栋房屋对应多个屋面片元\n同一母格内保留不同建筑身份',480,455,356,90,22,C.muted);arrow(s,804,309,48,26);
 [['屋面 A','T_A、m_A、水分',C.fire],['屋面 B','T_B、m_B、水分',C.teal],['地表 / 植被','各自的燃料和热状态',C.muted]].forEach((a,i)=>{let y=223+i*91;rect(s,892,y,311,70,C.pale);tx(s,a[0],904,y+9,290,29,23,a[2],true);tx(s,a[1],904,y+39,290,28,20);});
 tx(s,'CA的作用：离散真实空间 → 逐步更新燃烧状态 → 输出传播形态与核心首燃时间',75,591,1130,62,25,C.teal,true);
 s.speakerNotes.text='空间输入为配准TIFF、稳定ID建筑矢量、地表分类及DEM。按建筑轮廓与网格交集形成独立屋面组件；不同建筑即使处在同一个5m背景格，也不共享温度。指定源、核心和行动位置的局部屋面采用1m/0.5m两种离散实现，其余完整背景保留。质量、能量、有效受热质量等广延量按不重叠面积份额分配并守恒。重叠标注区域物理燃料只计一次；共享保留策略保留任何未拆所有者对应燃料。模型是屋面等效表示，并未逐根梁柱或逐层房间重建。图为概念示意，非新实验。来源src/pyfireca/planning/roof_thermal.py，docs/HERITAGE_UNIFIED_INPUT_OUTPUT_20261024.md。';
 s=page(presentation,5,'材质、建筑身份与规划标签分层进入元胞','来源：材质绑定清单、物理数组与遗产清单；燃烧参数为当前等效设定');
 table(s,[['空间对象','几何与类别来源','进入元胞的物理信息'],['建筑屋面','人工轮廓 + 建筑ID','覆盖面积、燃料、水分、温度与燃烧时钟'],['草地 / 农田','RGB分类及声明推断','草地等效燃料与热参数'],['林木 / 灌丛','RGB分类及声明推断','林木等效燃料与热参数'],['道路 / 水体等','分类或显式地表假设','按当前不燃配置处理']],73,151,1134,302,[230,380,524]);
 [['类别 / 材质ID','绑定参数库'],['燃料 / 含水率 / 热参数','决定受热和燃烧响应'],['规划标签另行指定','核心 / 候选 / 禁拆 → 搜索约束']].forEach((a,i)=>{let x=75+i*397;tx(s,a[0],x,493,365,38,24,i===2?C.fire:C.teal,true);tx(s,a[1],x,537,365,42,22,C.muted);if(i===0)arrow(s,x+357,518,29,22);});
 tx(s,'当前屋面使用统一等效建筑材质；影像提供空间线索，参数由声明材料库赋值',75,610,1135,47,23,C.teal,true);
 s.speakerNotes.text='图中分层示意数据流程，不表示所有标签串行决定物性。地表/材质ID绑定物理参数库，核心与候选标签另外约束保护与行动。建筑轮廓和遗产身份不等于实测燃烧物性。当前屋面继承统一synthetic_building等效材质：18kg/m²干燃料、干基含水0.1、可热解比例0.6、燃烧热16.26MJ/kg、受热干质量2kg/m²、阈值520K等；背景可用草、树、不燃等不同参数。平岩核心重叠几何按遗产优先规则保留，并保存修正依据。独立温度不意味着已完成逐栋不同木材/构造标定。空间邻接由实际几何、接触边界与辐射距离构建，不由1×N存储数组索引决定。候选清空后重建物理评价，因此CA充当优化器的保护判定器。来源examples/research/heritage/batuan-strategy-materials.json，src/pyfireca/planning/roof_thermal.py，docs/HERITAGE_UNIFIED_INPUT_OUTPUT_20261024.md。';
 const now=(await presentation.inspect({select:'$.slides[*]',include:['id']})).records.map(r=>r.id);const fresh=now.slice(-3);
 presentation.delete(old.slice(2,5));presentation.slides.reorder([...old.slice(0,2),...fresh,...old.slice(5)]);
 await presentation.render({slide:[2,3,4]});return {slides:presentation.slides.items.length,replaced:[3,4,5]};
}
