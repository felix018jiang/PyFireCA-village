// Reusable callbacks for the saved, viewer-bound project presentation.
// Run through artifactSession.run; this module does not construct a separate deck.
const ROOT='/Users/tone/Documents/github/PyFireCA';
const A=ROOT+'/docs/presentations/20261104/assets/';
const C={paper:'#F7F3EA',ink:'#26332F',teal:'#167D83',fire:'#C95B37',muted:'#61716A',pale:'#E5ECE5',gold:'#DBA24E',white:'#FFFFFF',dark:'#202A29'};
const SOURCE={
 P:'src/pyfireca/village/physics_v2.py; src/pyfireca/village/physics_v3.py; src/pyfireca/village/solver_v3.py',
 R:'src/pyfireca/planning/roof_thermal.py; src/pyfireca/village/flame_contact_v3.py',
 A:'src/pyfireca/planning/building_search.py; src/pyfireca/planning/failure_cuts.py; src/pyfireca/planning/engines/roof_search.py',
 E:'docs/HERITAGE_EXACT_EIGHT_COMPLETION_20261024.md',
 B:'docs/HERITAGE_TRIPLE_VALIDATION_20261024.md',
 T:'docs/HERITAGE_B0063_ADDBACK_20261024.md; docs/HERITAGE_POSITION_CONTROLS_20261024.md',
 F:'docs/HERITAGE_FULL_SEARCH_AND_PINGYAN_20261029.md',
 X:'docs/HERITAGE_PINGYAN_COUNT_EXTENSION_20261030.md',
 D:'docs/HERITAGE_DURATION_POSITION_RESULTS_20261103.md; docs/results/heritage-followup-20261103/summary.json',
 M:'runs/batuan-contact-20261016/profiles.synthetic.yml; examples/research/heritage/batuan-strategy-materials.json',
};
function rect(s,x,y,w,h,fill,line='none',lw=0,geometry='rect'){
 return s.shapes.add({geometry,position:{left:x,top:y,width:w,height:h},fill,line:{fill:line,width:lw,style:'solid'}});
}
function text(s,t,x,y,w,h,size=25,color=C.ink,bold=false,align='left'){
 const q=rect(s,x,y,w,h,'none');q.text=t;q.text.style={typeface:'PingFang SC',fontSize:size,color,bold,alignment:align,verticalAlignment:'top',insets:0,autoFit:'none',wrap:'square'};return q;
}
function image(s,name,x,y,w,h){return s.images.add({path:A+name+'.png',position:{left:x,top:y,width:w,height:h},fit:'contain',alt:name});}
function arrow(s,x,y,w=42,h=22,color=C.teal,vertical=false){return rect(s,x,y,w,h,color,'none',0,vertical?'downArrow':'rightArrow');}
function footer(s,n,src){text(s,src,58,673,1120,23,14,C.muted);text(s,String(n).padStart(2,'0'),1193,673,40,24,15,C.muted,false,'right');}
function base(p,n,title,src,notes){const s=p.slides.add({width:1280,height:720});s.background.fill=C.paper;text(s,title,58,43,1168,72,38,C.ink,true);footer(s,n,src);s.speakerNotes.text=notes;return s;}
function heading(s,t,x,y,w=510,size=27,color=C.teal){text(s,t,x,y,w,48,size,color,true);}
function statement(s,t,y=603,color=C.teal){rect(s,58,y,1165,47,color);text(s,t,75,y+9,1130,35,23,C.white,true);}
function rows(s,items,x,y,w,gap=102,size=24){items.forEach((item,i)=>{text(s,String(i+1).padStart(2,'0'),x,y+i*gap,48,44,30,C.fire,true);text(s,item,x+65,y+i*gap,w-65,gap-11,size);});}
function table(s,values,x,y,w,h,widths,font=22){
 const tb=s.tables.add({rows:values.length,columns:values[0].length,left:x,top:y,width:w,height:h,columnWidths:widths,values});
 tb.borders.assign({fill:'#D1DAD3',width:1,style:'solid'});
 const all=tb.cells.block({row:0,column:0,rowCount:values.length,columnCount:values[0].length});
 all.assign({textStyle:{typeface:'PingFang SC',fontSize:font,color:C.ink},margins:{top:11,bottom:8,left:13,right:13},anchor:'middle'});
 for(let r=0;r<values.length;r++)for(let c=0;c<values[0].length;c++){
  const cell=tb.getCell(r,c);cell.fill=r===0?C.dark:(r%2?C.white:C.pale);
  if(r===0)cell.text.style={typeface:'PingFang SC',fontSize:font,bold:true,color:C.white};
 }
 return tb;
}
function chart(s,categories,series,x,y,w,h,max,title=''){
 return s.charts.add('bar',{position:{left:x,top:y,width:w,height:h},title,categories,series,barOptions:{direction:'column',grouping:'clustered',gapWidth:72},hasLegend:series.length>1,legend:{position:'bottom',overlay:false,textStyle:{fontSize:17,fill:C.ink}},xAxis:{textStyle:{fontSize:18,fill:C.ink},line:{fill:'#B2BDB5',width:1}},yAxis:{min:0,max,textStyle:{fontSize:16,fill:C.muted},majorGridlines:{fill:'#D8DFD8',width:1}},dataLabels:{showValue:true,position:'outEnd',numberFormatCode:'0.##',textStyle:{fontSize:20,fill:C.ink,bold:true}},chartFill:'none',chartLine:{fill:'none',width:0},plotAreaFill:'none'});
}
function miniCell(s,x,y,label,fill){rect(s,x,y,113,91,fill);text(s,label,x+5,y+32,103,44,22,fill===C.pale?C.ink:C.white,true,'center');}

function make(p,n){let s;
switch(n){
case 2:
 s=base(p,n,'研究要回答的三个问题','项目定位与评价目标',`研究聚焦少数民族传统村落应用，不以算法绝对原创为叙事目标。现阶段研究对象为侗族村落中的鼓楼、风雨桥和周边普通建筑。\n来源：${SOURCE.F}; ${SOURCE.D}`);
 rows(s,['火灾从哪里起、怎样在木质建筑群中扩散，\n核心建筑何时受到引燃威胁？','在保护鼓楼与风雨桥的条件下，\n哪些普通建筑位置应优先形成空间分隔？','找到的方案在新起火落点、替代位置和\n更长观察期内，是否仍能保持保护？'],72,169,1060,126,29);
 statement(s,'当前交付：模拟过程、选址方法、两村方案和可复核的实验结果');break;
case 3:
 s=base(p,n,'研究背景：木构聚落中的文化遗产保护','背景：UNESCO Dong Villages；NIST建筑分隔实验',`侗族聚落沿河、坡地组织，鼓楼、风雨桥与民居构成日常文化空间。UNESCO描述杉木吊脚楼和部分连通檐廊。不能据此断言本案例每栋的实测构造。研究将木构建筑作为等效可燃对象。\n背景来源：https://whc.unesco.org/en/tentativelists/5813/ （预备名单条目，不称已列入世界遗产）；https://www.nist.gov/publications/nist-outdoor-structure-separation-experiments-nosse-wind 。NIST间距数字不直接移用于侗寨。`);
 image(s,'batuan_B0073',658,142,545,427);
 heading(s,'保护与空间干预需要同时考虑',67,147,560);
 rows(s,['木构民居和公共建筑形成相邻的\n可燃空间，热量可在建筑间传递','鼓楼、风雨桥具有公共文化价值，\n保护目标应落实到具体建筑','大范围清空会改变村落格局，\n因此研究少量位置干预的效果'],68,216,550,110,25);
 text(s,'岜团鼓楼与周边干预位置｜实际标注几何',715,581,480,33,19,C.muted);
 break;
case 4:
 s=base(p,n,'两个真实村落，采用同一数据和评价流程','[F/X] 两村统一接入与平岩续轮结果',`统计单元为建筑标注对象，不等同产权房屋或住户。岜团333对象，其中2核心、331普通候选；平岩950对象，其中3核心、947普通候选。影像与建筑轮廓是真实空间数据，燃烧材料沿用冻结的合成等效参数。\n来源：${SOURCE.F}; ${SOURCE.X}; ${SOURCE.M}`);
 image(s,'batuan_overview',70,134,505,324);image(s,'pingyan_overview',701,134,505,324);
 heading(s,'岜团村',83,459);heading(s,'平岩村',710,459);
 text(s,'333 个标注对象\n2 处核心：鼓楼、风雨桥\n331 个普通建筑候选',83,510,490,118,25);
 text(s,'950 个标注对象\n3 处核心：鼓楼、两座风雨桥\n947 个普通建筑候选',710,510,490,118,25);
 break;
case 5:
 s=base(p,n,'从村落数据到防火线方案','方法流程：数据—模拟—搜索—确认',`输入包括配准影像、建筑轮廓、地形、材料绑定、核心清单和候选资格。输出包括干预清单、地图、逐核心保护结果、代价账本和搜索界限。\n来源：docs/HERITAGE_REUSABLE_STRATEGY_METHOD.md; ${SOURCE.F}; ${SOURCE.D}`);
 const steps=[['数据准备','影像 / 轮廓 / 地形\n材料 / 核心 / 资格'],['火灾评价','独立屋面热状态\n辐射 / 接触 / 引燃'],['位置搜索','数量分层 + 面积下界\n条件反例 + CA复算'],['效果确认','新起火落点\n位置对照 / 延时检验']];
 steps.forEach((a,i)=>{let x=65+i*301;rect(s,x,220,267,186,i===2?C.teal:C.pale);text(s,a[0],x+16,242,235,49,31,i===2?C.white:C.ink,true);text(s,a[1],x+16,311,237,88,24,i===2?C.white:C.ink);if(i<3)arrow(s,x+274,292,23,24);});
 text(s,'输出',70,477,120,45,29,C.fire,true);text(s,'防火分隔位置与清单  ·  传播图与到火时间  ·  最优性界限与验证记录',190,478,1023,77,28);
 statement(s,'搜索器提出方案，火灾模拟器给出保护判定');break;
case 6:
 s=base(p,n,'元胞不是“整栋建筑共用一个温度”','[R] roof_thermal.py：独立屋面组件与面积分配',`完整背景采用5 m母网格；指定源、核心和干预对象局部屋面采用1 m与0.5 m实现。按建筑轮廓与网格交集形成屋面组件，分别保存温度、水分、燃料和时钟。同一母格内不同建筑不共享温度，存储1×N数组不定义地理邻接。质量、热容量和能量等按不重叠覆盖面积分配。\n来源：${SOURCE.R}; ${SOURCE.F}`);
 heading(s,'空间离散示意',68,147,500);
 for(let r=0;r<3;r++)for(let c=0;c<3;c++)rect(s,82+c*109,215+r*109,109,109,r===1&&c===1?'#C9D7CE':'none','#ADBDB2',1);
 rect(s,206,340,34,48,C.fire);rect(s,247,333,45,62,C.teal);text(s,'房屋A',198,416,100,40,20,C.fire,true);text(s,'核心B',294,346,100,44,20,C.teal,true);
 text(s,'同一5 m母格内，仍可有不同热状态',70,574,540,44,23,C.muted);
 rows(s,['完整背景保留；局部屋面按\n1 m / 0.5 m两种尺度计算','屋面片元独立保存状态，\n相邻建筑不会在0秒被同步点燃','共享屋面物理燃料只计一次；\n几何位置决定接触与辐射关系'],620,177,590,124,25);
 break;
case 7:
 s=base(p,n,'每个元胞保存状态，也保存连续物理量','[P] solver_v3.py：V3State与状态更新',`可燃元胞状态为UNBURNED、HEATING、BURNING、BURNED_OUT；非可燃地表为INERT，域外为OUTSIDE。受热状态可随冷却返回UNBURNED，因此图中给出回退说明。燃烧后表面温度有效掩膜关闭，不能用存储温度解释火焰温度。\n来源：${SOURCE.P}`);
 [['未燃',C.pale],['受热',C.gold],['燃烧',C.fire],['燃尽',C.muted]].forEach((a,i)=>{miniCell(s,106+i*294,185,a[0],a[1]);if(i<3)arrow(s,235+i*294,220,118,23);});
 text(s,'升温',262,265,135,42,21,C.muted);text(s,'达到点燃阈值',553,265,180,42,21,C.muted);text(s,'燃料耗尽',855,265,165,42,21,C.muted);
 table(s,[['连续变量','对应含义'],['T、mᵥ、m_f','有效表面温度、受热水分、剩余可热解燃料'],['τ、Q̄、E','燃烧龄期、区间平均热释放率、累计释放能量'],['t_ign、t_out','首次引燃时间、燃尽时间']],83,360,1110,230,[295,815],22);
 text(s,'受热元胞可以冷却；直接起火源由预设事件初始化',85,611,1100,36,23,C.teal,true);break;
case 8:
 s=base(p,n,'燃烧公式：点燃后采用分段热释放率曲线','[P/M] physics_v2.py；冻结等效建筑参数',`τ=t−t_ign。燃烧曲线为二次增长、恒定平台、线性衰减，曲线结束后归零。当前建筑参数t_g=120s、t_s=395.36s、t_d=300s，峰值300kW/m²活动面积。每个片元从自身点燃时刻开启时钟。此处描述预设HRR，而非求解火焰化学反应、氧供和流体温度。\n来源：${SOURCE.P}; ${SOURCE.M}`);
 image(s,'eq_hrr',64,174,1152,210);image(s,'hrr_curve',653,405,540,218);
 text(s,'τ：元胞点燃后的燃烧龄期\nQ̇：热释放率（W）\nQ̇max：峰值热释放率',81,423,530,130,26);
 text(s,'增长120 s  /  平台395.36 s  /  衰减300 s',80,576,550,44,22,C.teal,true);break;
case 9:
 s=base(p,n,'燃料与能量闭合，限制每个元胞能释放多少热','[P/M] 有限燃料与HRR曲线积分',`m_dry为初始干燃料，f_pyro为可热解比例0.6，H=16.26MJ/kg；m_remain表示剩余可热解干燃料。每步准确积分分段曲线并以剩余燃料能量为上限，再由Δm=ΔE/H扣除。1m²覆盖屋面的等效干燃料18kg，初始可用能量175.608MJ。等效参数并非当地木构实测。\n来源：${SOURCE.P}; ${SOURCE.M}`);
 image(s,'eq_energy',67,162,1140,213);
 heading(s,'1 m²等效屋面的能量账本',79,421,630);
 table(s,[['干燃料','可热解比例','有效燃烧热','可用能量'],['18 kg','0.60','16.26 MJ/kg','175.608 MJ']],77,478,1115,116,[245,245,300,325],25);
 text(s,'曲线积分与可用燃料能量一致；相邻元胞各自启动燃烧时钟',80,619,1120,34,23,C.teal,true);break;
case 10:
 s=base(p,n,'温度由表面净热流推进','[P] net_surface_heat_flux_W_m2',`q_rad为入射辐射功率除以元胞覆盖面积，α是吸收率；q_contact为接触传热功率除以覆盖面积。h_a^eff=h_a(1−f_contact)，避免接触面积同时按环境对流散热。εσ是向环境的净再辐射，k_b为等效背面导热系数。这里的T是未燃/受热表面温度，不是燃烧后的火焰温度。\n来源：${SOURCE.P}`);
 image(s,'eq_heat',65,154,1155,165);
 const heat=[['吸收辐射','α q″rad',C.fire],['接触传热','q″contact',C.fire],['环境对流','−h_eff ΔT',C.teal],['表面再辐射','−εσ(T⁴−T∞⁴)',C.teal],['背面导热','−k_b ΔT',C.teal]];
 heat.forEach((v,i)=>{let x=70+i*237;rect(s,x,392,214,132,C.pale);text(s,v[0],x+10,407,194,38,24,v[2],true,'center');text(s,v[1],x+7,463,200,45,23,C.ink,false,'center');});
 statement(s,'相邻元胞的热输入可以叠加，散热过程同时参与计算');break;
case 11:
 s=base(p,n,'点燃前，输入能量先分配给升温与水分蒸发','[P] advance_surface_heating_and_evaporation',`正净热流下采用事件分段：湿态升至373.15K；维持沸点并蒸发受热水分；水耗尽后继续提高干态表面温度。冷却时温度不低于环境且不会恢复水分。图示为机制，不是某一村落的实测温度曲线。m_heated''=2kg/m²，c=1500J/(kgK)，初始受热水分=0.2kg/m²。\n来源：${SOURCE.P}; ${SOURCE.M}`);
 [['湿态升温','T < 373.15 K','干物质与水共同吸热'],['水分蒸发','T ≈ 373.15 K','能量用于汽化潜热'],['干态升温','mᵥ ≈ 0','继续升温至点燃阈值']].forEach((v,i)=>{let x=72+i*405;rect(s,x,171,350,153,C.pale);text(s,v[0],x+17,190,315,40,29,C.teal,true);text(s,v[1],x+17,241,315,34,25);text(s,v[2],x+17,285,315,34,21,C.muted);if(i<2)arrow(s,x+360,232,32,25);});
 image(s,'eq_dryheat',78,382,1125,159);text(s,'水汽化潜热 Lᵥ = 2.2564 MJ/kg；温度按K计算',81,591,1100,46,25,C.teal,true);break;
case 12:
 s=base(p,n,'元胞之间的辐射：距离衰减与面积加权求积','[R/P] 面积积分辐射图与多源热量相加',`选定源/核心等屋面用0.5m求积，其他未涉及选定屋面的边保留原代表点核。λ是源/受体求积点面积份额，合计分别为1。d包含地形和有效高度。求积对水平距离≤15m的点对计入；ε_r=5m近场正则化，κ=0.001m^-1。该15m为计算范围，不解释成安全间距。每源权重和≤1，避免分配超过出射辐射能量。\n来源：${SOURCE.R}; ${SOURCE.P}; docs/HERITAGE_AREA_RADIATION_20261023.md`);
 image(s,'eq_rad',68,164,1145,175);
 miniCell(s,107,430,'燃烧源1',C.fire);miniCell(s,107,546,'燃烧源2',C.gold);arrow(s,257,468,264,28,C.fire);arrow(s,257,570,264,28,C.gold);miniCell(s,570,484,'受热元胞',C.teal);
 text(s,'χ：辐射份额，当前等效建筑为0.4\n权重由真实地理位置与面积决定\n多个燃烧元胞的辐射功率在受体相加',772,421,435,179,24);
 break;
case 13:
 s=base(p,n,'近场接触传热：几何接触和能量上限共同约束','[R] roof_contact_operator；FlameContactOperator',`屋面接触边要求同母格或四面邻格且几何具有正长度公共边界。正间隙不会因数组邻接形成接触。φ包含有效接触比例、几何、高度覆盖、Q/Qmax活动权重，并按受体总面积约束缩放，再按各源非辐射预算缩放。T_g=1100K、h_c=50W/(m²K)、接触比例0.5为等效合成输入。\n来源：${SOURCE.R}`);
 image(s,'eq_contact',64,156,1155,163);
 rows(s,['几何门控：必须满足真实接触关系，\n正间隙不自动产生接触传热边','受体上限：多个源的接触作用面积\n合计不超过受体可覆盖面积','源能量上限：接触传出的总功率\n不超过源的非辐射释放功率'],83,365,1090,84,24);
 break;
case 14:
 s=base(p,n,'点燃判定：热状态满足条件，才转入燃烧','[P] solver_v3.py：newly_ignited判据',`实际布尔判据为susceptible & burnable_mask & piloted_ignition_mask & next_fuel>fuel_tolerance & next_temperature>=ignition_temperature。susceptible包含UNBURNED与HEATING。水分影响温度更新，不另增加一个代码中不存在的独立点火概率。直接起火事件按源协议初始化，其余元胞由热判据点燃。研究中T_ign=520K是等效参数，非所有木材通用点燃温度。\n来源：${SOURCE.P}; ${SOURCE.M}`);
 image(s,'eq_ign',67,170,1149,180);
 table(s,[['条件','实现含义'],['状态与材料','尚未燃烧，属于可燃且允许引燃的区域'],['剩余燃料','超过燃料数值容差'],['表面温度','更新后 T ≥ T_ign；本批等效建筑 T_ign = 520 K']],81,389,1110,211,[270,840],22);
 text(s,'核心任一屋面组件首次引燃，即判该核心失效',83,620,1090,34,25,C.fire,true);break;
case 15:
 s=base(p,n,'固定起火区，保证方案之间可以比较','[B/F/D] 25 m²点火与源保留协议',`历史搜索/确认使用预先固定的建筑优先序：源被拆时顺延到第一栋保留建筑，仍真实起火。源不变时同多边形、同预算。2026-11-03比较为了单独检验位置效果，所有比较臂都保留该批固定源，因此没有替换。两尺度是同一次火灾的数值实现，不是独立抽样。\n来源：${SOURCE.B}; ${SOURCE.F}; docs/HERITAGE_DURATION_POSITION_PROTOCOL_20261103.md`);
 rect(s,90,181,430,299,C.pale);for(let r=0;r<5;r++)for(let c=0;c<6;c++)rect(s,112+c*61,202+r*48,60,47,'none','#B0BDB3',1);rect(s,221,287,119,96,C.fire);text(s,'冻结点火面\n25 m²',230,308,114,76,25,C.white,true,'center');
 rows(s,['同源对照：点火建筑、多边形、\n初始燃料和能量预算保持一致','搜索时若拆掉预定源，按冻结序列\n顺延到保留源，并单列实际源变化','延时位置对照保留全部固定源；\n1 m与0.5 m复用相同点火几何'],607,179,590,123,24);
 text(s,'图为示意，点火区由实际屋面几何生成',92,505,480,70,22,C.muted);
 statement(s,'起火条件、材料和共同布局固定，位置对照才有可解释性');break;
case 16:
 s=base(p,n,'防火线在模型中如何实现','[R/F] 屋面清空与共享燃料保留',`干预单元是建筑标注对象组成的整栋方案。可独立清空区域被置为惰性地表，燃料、水分、能量和传热掩膜清零，重建接触与辐射图；任何未拆归属的共享屋面仍保留。保留建筑参数不随同格清空改变。模型仍允许辐射跨空地，因此拆除不等于绝热墙。\n来源：${SOURCE.R}; ${SOURCE.F}`);
 heading(s,'清空可移除燃料',77,153,490);miniCell(s,90,244,'起火建筑',C.fire);miniCell(s,261,244,'候选位置',C.muted);miniCell(s,432,244,'核心建筑',C.teal);arrow(s,207,275,47,23,C.fire);arrow(s,378,275,47,23,C.fire);text(s,'清空后',83,386,180,47,28,C.teal,true);miniCell(s,90,447,'燃烧源',C.fire);rect(s,261,447,113,91,'none',C.muted,2);text(s,'无燃料',267,477,101,44,22,C.muted,true,'center');miniCell(s,432,447,'核心',C.teal);arrow(s,211,560,214,20,C.gold);
 text(s,'仍可能跨空地辐射',166,589,385,39,22,C.fire,true);
 rows(s,['行动对象决定在哪些位置清空\n可独立移除的屋面燃料','共享保留部分单列账本，\n不把名义面积等同实际清空面积','通过CA复算判断是否仍引燃核心，\n不能只凭几何断开宣布保护'],672,183,536,126,24);break;
case 17:
 s=base(p,n,'位置优化：先满足保护，再最少干预','[A/F] 数量优先、同数量面积次优先',`x_i=1表示清空合格普通建筑对象i，x_i=0表示保留。a_i是标注建筑名义轮廓面积。目标按词典序比较(栋数,名义面积)，不是未经声明的加权总分。I_{c,s,h}=0表示核心c在情景s、尺度h和时窗T内没有任一片元引燃。设计搜索T=1200s；60分钟批次只检验固定方案，未产生新时长的全局最优证明。费用、住户未知，不把面积称货币成本。\n来源：${SOURCE.A}; ${SOURCE.F}; ${SOURCE.D}`);
 image(s,'eq_objective',71,164,1136,169);
 table(s,[['层次','具体要求'],['保护约束','全部指定核心 × 全部设计情景 × 双尺度均未引燃'],['第一目标','拆除标注对象数量最少'],['第二目标','同数量下，名义轮廓面积之和最小']],81,381,1110,220,[240,870],23);
 text(s,'核心禁拆；实施资格、真实经济成本另由输入资料核定',82,618,1120,36,23,C.teal,true);break;
case 18:
 s=base(p,n,'选址算法：数量分层的分支定界与条件反例剪枝','[A] building_search.py + failure_cuts.py',`当前实现是模拟约束下的数量分层分支定界。不是强化学习或遗传算法。初始可行解必须在当前共同布局重新核验，构成上界；对各k层懒展开，利用可采纳下界和有条件失效证据剪枝；未知或预算耗尽保留界限。\n来源：${SOURCE.A}; ${SOURCE.F}; ${SOURCE.X}`);
 const algo=[['01','冻结问题与情景','核验初始可行解，得到上界'],['02','按0、1、2…栋分层','同层按可达最低面积扩展'],['03','检查界限与条件反例','可合法排除则剪枝，否则调用CA'],['04','更新结果与停止判定','返回方案、上下界和未决范围']];
 algo.forEach((v,i)=>{let y=151+i*118;text(s,v[0],75,y+6,75,59,42,C.fire,true);text(s,v[1],184,y+4,460,47,29,C.teal,true);text(s,v[2],688,y+8,500,72,25);if(i<3)arrow(s,100,y+63,20,43,C.muted,true);});
 break;
case 19:
 s=base(p,n,'层内按面积下界展开，不一次生成全部组合','[A] ordered_demolition_sets：优先队列与可采纳下界',`P为当前已选拆除前缀，R为剩余候选，k为本层固定栋数。用P面积+补齐剩余名额的最小面积给出可采纳下界，最佳优先队列懒展开。只有同数量下与可行面积上界比较。图中的A/B/C是演示候选，不是村落建筑ID，也非新的实验结果。\n来源：${SOURCE.A}`);
 image(s,'eq_bound',72,154,1136,151);
 text(s,'演示：固定选2个位置，候选面积为 A=10、B=20、C=40',78,341,1130,46,26,C.muted);
 [['A+B','下界 / 完整面积 30'],['A+C','下界 / 完整面积 50'],['B+C','下界 / 完整面积 60']].forEach((v,i)=>{rect(s,82+i*398,425,346,118,i===0?C.teal:C.pale);text(s,v[0],100+i*398,443,310,38,30,i===0?C.white:C.ink,true);text(s,v[1],100+i*398,494,310,39,23,i===0?C.white:C.ink);if(i<2)arrow(s,438+i*398,468,27,22);});
 statement(s,'面积下界决定检查顺序；保护可行性仍由CA确定');break;
case 20:
 s=base(p,n,'评价器用三种状态控制搜索','[A/P] ProtectionCheck：通过、失效、未知',`一个方案面对4个设计火源家族×2尺度，最多8项。任何一次有效核心引燃足以否决方案，允许提前停止剩余可行性检查；全部要求完成才能通过。输入、资源错误或未完成记为unknown，阻止最优认证。缓存以完整物理问题SHA、方案、情景绑定，不同布局不能仅按建筑ID复用。\n来源：${SOURCE.A}; ${SOURCE.F}`);
 table(s,[['状态','判定依据','搜索动作'],['通过','全部要求完成，核心均未引燃','更新可行上界'],['失效','任一有效情景出现核心引燃','否决方案，保存反例'],['未知','输入无效、资源或预算停止、未完成','保留未决，阻止认证']],76,170,1130,300,[160,600,370],23);
 heading(s,'减少重复计算的两项措施',79,519,640,28);
 text(s,'失败早停：一个反例即可否决\n精确缓存：相同完整物理请求直接复用',81,566,1105,77,25);
 break;
case 21:
 s=base(p,n,'失败保持剪枝：只继承证书覆盖的失效条件','[A] cold_removal_certificate 与 FailureCutStore',`A是已失败方案的拆除集合，H为证书保守要求保留的影响对象集合。保留全部已燃节点、所有可能受热节点、接触邻接、实际源，并保护可能影响全局时间步的冷节点。若D保留A的全部拆除，且未拆H内任何对象，额外只删除证书外的冷对象，则在当前透明无风固定几何核下继承相同失败。相应必要约束为Σ_A(1−x_i)+Σ_H x_i≥1。不是“多拆必然更安全”的单调假设。\n来源：${SOURCE.A}; ${SOURCE.F}`);
 image(s,'eq_cut',75,155,1134,155);
 heading(s,'证书保守记录的内容',83,351,640);
 rows(s,['失效前全部燃烧对象与可能受热对象','接触连接、共享归属和实际起火源','会影响全局时间步的冷节点；\n证书外对象须具有精确零入热'],82,411,1100,71,25);
 text(s,'有效反例减少要计算的组合；它不预测新的温度轨迹',82,622,1120,35,23,C.teal,true);break;
case 22:
 s=base(p,n,'八候选基准：256个子集形成完整证明账本','[E] 八候选最优性验证（1200 s设计问题）',`8候选共有256子集。64有效失败=零拆1+单栋8+两栋28+更小面积三栋27；1完整通过；同数量面积界排除28；数量界排除163；更优未知0。得到3栋451.240456m²的小候选完整词典序最优。此证书不扩成331候选面积最优。新增60搜索CA，复用48父检查，共108设计检查，并有独立恢复。\n来源：${SOURCE.E}`);
 table(s,[['子集分类','数量','依据'],['有效失败','64','每项有有效CA反例'],['完整通过','1','原三栋完成全部8项'],['同数量面积界排除','28','面积不优于可行三栋'],['数量界排除','163','四栋及以上不改善数量目标'],['未决更优组合','0','证明闭合']],80,155,1120,341,[480,160,480],23);
 text(s,'3 栋',86,541,236,80,59,C.teal,true);text(s,'451.240 m²',391,541,510,80,53,C.teal,true);text(s,'小候选内：数量与面积最优',85,626,1100,29,24,C.ink,true);break;
case 23:
 s=base(p,n,'扩展到全候选后，分别报告数量与面积界限','[F/X] 全候选搜索与平岩17对补齐',`岜团331候选，排除0/1/2栋共54947组合：零拆1、单栋331、两栋54615。三栋可行，因此最少数量3；同数量面积仍未完成，177.814102为未决下界非实现方案。平岩947候选，17对补齐后0/1/2栋448879全部排除，当前五栋可行，所以3≤k*≤5；三/四栋分支预算停止，五栋面积也未证明最优。所有数量证明均针对原1200s设计协议。\n来源：${SOURCE.F}; ${SOURCE.X}`);
 table(s,[['指标','岜团','平岩'],['全部普通候选','331','947'],['两栋组合数','54,615','447,931'],['已排除更少数量组合','54,947（0/1/2栋）','448,879（0/1/2栋）'],['最少数量界','3 ≤ k* ≤ 3','3 ≤ k* ≤ 5'],['当前可行方案','3栋 / 451.240 m²','5栋 / 995.481 m²']],78,158,1126,350,[448,339,339],24);
 statement(s,'岜团数量最优已证明；平岩已有可行方案，数量最优仍未闭合');break;
case 24:
 s=base(p,n,'岜团方案：桥侧一处，鼓楼周边两处','[F] 岜团三栋方案与面积账本',`选中B0061、B0071、B0076。名义451.240456m²；实际清空443.841969m²；共享保留7.398487m²。图按原标注几何绘制：暖色为干预对象轮廓，青色为核心。它显示位置，不意味着实际建筑具备拆除资格。完整资格与产权、成本尚需现场核定。\n来源：${SOURCE.F}; docs/results/heritage-full-batuan-20261029/strategy/demolition-list.csv`);
 image(s,'batuan_B0073',63,142,549,411);image(s,'batuan_B0120',667,142,549,411);
 text(s,'鼓楼 B0073｜B0071 + B0076',92,563,525,45,26,C.teal,true);text(s,'风雨桥 B0120｜B0061',698,563,497,45,26,C.teal,true);
 text(s,'名义干预 451.240 m²   /   实际清空 443.842 m²   /   共享保留 7.398 m²',82,622,1120,35,23,C.muted);break;
case 25:
 s=base(p,n,'新落点重复确认：三栋方案18次均保护双核心','[B] 18次分层抽样 × 3方案 × 双尺度 = 108份模拟',`方案冻结后，用两组新种子抽取建筑及屋面落点；背景、近鼓楼、近桥各6次。每方案18火灾抽样，双尺度36评价，三方案108。基准联合保护10/18，单拆B0061为16/18，三栋18/18；近鼓楼失败2/6、2/6、0/6；近桥6/6、0/6、0/6；背景均0/6。等量分层不是自然火灾发生概率。三栋峰温514.030K仍表示受热，未评价结构损伤。\n来源：${SOURCE.B}`);
 chart(s,['无干预','仅拆B0061','三栋方案'],[{name:'联合保护抽样数',values:[10,16,18],fill:C.teal,points:[{idx:0,fill:C.muted},{idx:1,fill:C.gold},{idx:2,fill:C.teal}]}],69,160,654,389,20);
 table(s,[['核心失效','无干预','单栋','三栋'],['近鼓楼','2/6','2/6','0/6'],['近桥','6/6','0/6','0/6'],['背景','0/6','0/6','0/6']],759,189,454,264,[169,95,95,95],21);
 text(s,'三栋峰值表面温度\n鼓楼 514.030 K',789,510,400,89,24,C.teal,true);
 text(s,'观察1200 s；双尺度按任一引燃合并；18次是抽样数，108份是模拟数',82,621,1120,36,23,C.muted);break;
case 26:
 s=base(p,n,'定向与替代位置试验，解释三栋方案为何有效','[T] B0063补齐、逐栋回加和位置对照',`B0063六个新屋面落点，两臂双尺度24份，同源同预算；基准6/6桥失效，三栋6/6双核心保护。逐栋保留B0061、B0071或B0076均有有效反例，其中源替换需单列。另六比较情景（4新定向落点+2旧压力点）、五臂双尺度共60份；表中列四个三栋布局。后续全候选数量证明另列，不能从逐栋回加单独推出全局最优。\n来源：${SOURCE.T}; docs/HERITAGE_EXPERIMENT_REPORT_20261030.md`);
 text(s,'B0063补充确认',79,147,600,44,29,C.teal,true);text(s,'6/6',80,204,226,83,62,C.teal,true);text(s,'三栋方案保护两核心\n无干预6次均引燃桥',329,209,831,83,27);
 table(s,[['三栋布局','名义面积 m²','失效情景'],['B0061 + B0071 + B0076','451.240','0/6'],['B0063 + B0071 + B0076','308.495','1/6'],['B0061 + B0067 + B0077','453.597','2/6'],['B0063 + B0067 + B0071','452.026','3/6']],78,330,1126,246,[720,220,186],21);
 text(s,'逐栋保留均找到失效反例；相近面积并不保证同等核心保护',81,618,1120,35,23,C.fire,true);break;
case 27:
 s=base(p,n,'平岩迁移：同一方法接入更大的村落','[F/X] 947候选、3处核心、当前五栋方案',`当前五栋B0119+B0165+B0167+B0177+B0190：桥B0035侧1栋、鼓楼4栋，另一座桥B0036附近无新增拆除。名义995.481072m²，实际清空985.607836m²。五栋来自预声明种子并经物理核验成为上界，不称优化器已经自动发现全局最优。最少数量3—5。\n来源：${SOURCE.F}; ${SOURCE.X}`);
 image(s,'pingyan_B0166',66,145,574,413);image(s,'pingyan_B0035',663,145,550,413);
 text(s,'鼓楼 B0166｜4栋位置组合',85,574,560,43,26,C.teal,true);text(s,'风雨桥 B0035｜B0119',690,574,515,43,26,C.teal,true);
 text(s,'名义干预 995.481 m²；当前可行上界5栋，最少数量界3—5栋',83,624,1120,32,23,C.muted);break;
case 28:
 s=base(p,n,'平岩新落点确认：冻结五栋在4次抽样中均通过','[F] 4新落点 × 2方案 × 双尺度 = 16份模拟',`这4次冻结后抽样未进入方案重选：背景B0076、桥B0035近邻B0120、桥B0036压力源B0039、鼓楼近邻B0164。无干预仅B0164在1m下1169.36s引燃鼓楼，另一尺度未引燃；五栋全部通过。1200s确认与2026-11-03共同布局不同，因此旧1169.36与新1168.36不能当作同一轨迹。\n来源：${SOURCE.F}; ${SOURCE.D}`);
 chart(s,['无干预','五栋方案'],[{name:'联合保护抽样数',values:[3,4],fill:C.teal,points:[{idx:0,fill:C.muted},{idx:1,fill:C.teal}]}],72,175,490,344,5);
 table(s,[['新起火位置','基准','五栋'],['背景 B0076','通过','通过'],['近桥 B0120','通过','通过'],['桥侧压力 B0039','通过','通过'],['近鼓楼 B0164','失效','通过']],597,173,608,330,[355,126,127],22);
 text(s,'基准鼓楼在1 m下于1169.36 s引燃；五栋方案双尺度均未引燃',81,585,1125,60,25,C.teal,true);break;
case 29:
 s=base(p,n,'60分钟后续批次：固定同源，专门比较位置与时长','[D] 2026-11-03冻结协议与完整24份结果',`两村各2历史固定源×3方案×2尺度=24份完整3600s评价，检查点1200/2400/3600从同一完整轨迹提取，失败也继续模拟。四源是历史压力回放，新独立随机抽样0；三臂同布局、同源多边形、同预算。位置对照由求解前几何匹配产生，匹配名义面积而非真实经济成本。\n来源：docs/HERITAGE_DURATION_POSITION_PROTOCOL_20261103.md; ${SOURCE.D}`);
 table(s,[['村落','固定起火建筑','比较方案','完整评价'],['岜团','B0077 / B0062','无干预 / 既有 / 位置对照','12份'],['平岩','B0164 / B0120','无干预 / 既有 / 位置对照','12份']],76,165,1130,211,[175,330,440,185],23);
 text(s,'20 min',101,447,210,72,45,C.muted,true);arrow(s,332,468,200,25);text(s,'40 min',572,447,210,72,45,C.gold,true);arrow(s,812,468,195,25);text(s,'60 min',1028,447,210,72,45,C.fire,true);
 statement(s,'24份模拟均运行到3600 s；三个检查点不作为三次独立火灾');break;
case 30:
 s=base(p,n,'岜团延时结果：既有位置保护保持，对照出现后续失效','[D] 核心首燃时间，按1 m / 0.5 m列示',`B0077源：基准和位置对照鼓楼857/906s引燃，既有方案到3600s双核心未引燃。B0062源：基准桥989.36/1046s引燃，鼓楼3281.08/3332.08s后续引燃；对照桥1249.36/1409.36s引燃；既有均未引燃。桥侧对照在1200s通过，但到2400/3600s失效。\n来源：${SOURCE.D}; docs/results/heritage-followup-20261103/core-checkpoints-dual-scale.csv`);
 table(s,[['火源 / 方案','鼓楼首燃 s','桥首燃 s'],['B0077 / 无干预','857 / 906','未引燃'],['B0077 / 既有','未引燃','未引燃'],['B0077 / 对照','857 / 906','未引燃'],['B0062 / 无干预','3281.08 / 3332.08','989.36 / 1046'],['B0062 / 既有','未引燃','未引燃'],['B0062 / 对照','未引燃','1249.36 / 1409.36']],76,155,1130,366,[415,357,358],22);
 statement(s,'桥侧对照：20分钟尚未引燃，约20分49秒 / 23分29秒后引燃');break;
case 31:
 s=base(p,n,'岜团桥侧传播形态：观察后续到火的位置差异','[D] B0062同源；局部屋面0.5 m；三臂共同视窗',`真实地理坐标上绘制首次引燃的屋面节点。三臂视窗完全相同，按冻结几何确定。黄色0—20min，橙色20—40min，深红40—60min；青色为桥B0120，虚线为干预轮廓，紫色为点火区。图为累计到火，不是终点活动燃烧或整栋损毁。对应1m结果见首燃表。\n来源：${SOURCE.D}; docs/presentations/20261104/prepare_assets.py`);
 image(s,'batuan_bridge_morph',48,140,1187,421);
 [['0—20 min',C.gold],['20—40 min','#E47D48'],['40—60 min','#AA3441']].forEach((v,i)=>{rect(s,82+i*290,582,24,24,v[1]);text(s,v[0],118+i*290,579,235,38,23);});text(s,'青色：核心\n虚线：干预',986,577,218,68,21,C.teal,true);
 break;
case 32:
 s=base(p,n,'平岩延时结果：两组布局均保护核心，保留尺度差异','[D] 本轮共同布局结果；“未引燃”均观察至3600 s',`鼓楼源B0164基准鼓楼1168.36/1251s引燃，两干预均保护三核心。桥源B0120基准桥B0035在1m下1564s引燃，0.5m至3600s未引燃；两干预全部保护。桥B0036全部未引燃。基准桥源传播面积1m2259.07、0.5m113.13m²，不能声称尺度已收敛。\n来源：${SOURCE.D}`);
 table(s,[['火源 / 方案','鼓楼首燃 s','桥B0035首燃 s'],['B0164 / 无干预','1168.36 / 1251','未引燃'],['B0164 / 既有','未引燃','未引燃'],['B0164 / 对照','未引燃','未引燃'],['B0120 / 无干预','未引燃','1564 / 未引燃'],['B0120 / 既有','未引燃','未引燃'],['B0120 / 对照','未引燃','未引燃']],76,155,1130,366,[415,357,358],22);
 statement(s,'按预设规则：任一尺度引燃即失效；两座桥和鼓楼分别记录');break;
case 33:
 s=base(p,n,'平岩有效布局之间，外围传播面积仍有差别','[D] B0164鼓楼侧；60分钟累计引燃屋面面积',`既有布局名义995.481072m²、对照995.124930m²（−0.0358%）；实际清空985.607836与974.454232m²（−1.1316%），后者有7.89636m²内部轮廓重叠。两布局本批均保护核心，屋面引燃面积既有10290.21/10023.68，对照8754.95/8502.88m²，约少15%。这不替换全部原设计情景的最优结论，也不把屋面面积称全栋损失。\n来源：${SOURCE.D}; docs/results/heritage-followup-20261103/intervention-areas.csv`);
 chart(s,['1 m','0.5 m'],[{name:'既有方案',values:[10290.21,10023.68],fill:C.teal},{name:'位置对照',values:[8754.95,8502.88],fill:C.fire}],71,151,780,417,12000);
 text(s,'约15%',907,193,303,93,62,C.fire,true);text(s,'累计引燃屋面面积减少\n两布局均保护三核心',910,309,302,115,26);
 text(s,'比较匹配名义面积\n实际清空面积另列',909,483,300,86,23,C.muted);
 text(s,'面积为物理屋面覆盖累计，不表示整栋烧毁；此结论对应鼓楼侧固定源',82,619,1126,35,22,C.muted);break;
case 34:
 s=base(p,n,'平岩鼓楼局部：共同的保护结局，不同的传播形态','[D] B0164同源；0.5 m；全部核心判定见表',`三臂共用由源建筑、最近核心和核心邻近行动对象定义的几何视窗，未用传播结果选窗。图展示B0166核心局部，但统计仍包含全部3核心。图中基准鼓楼20min后引燃；两布局鼓楼未引燃，普通屋面传播形态不同。\n来源：${SOURCE.D}; docs/presentations/20261104/prepare_assets.py`);
 image(s,'pingyan_drum_morph',46,140,1192,422);
 [['0—20 min',C.gold],['20—40 min','#E47D48'],['40—60 min','#AA3441']].forEach((v,i)=>{rect(s,82+i*290,582,24,24,v[1]);text(s,v[0],118+i*290,579,235,38,23);});text(s,'青色：鼓楼\n虚线：干预',986,577,218,68,21,C.teal,true);
 break;
case 35:
 s=base(p,n,'核心受到保护时，外围传播仍可继续','[D] 同源面积—时间曲线；屋面覆盖面积物理单计',`左为岜团B0062桥侧源，右为平岩B0164鼓楼源。均使用归档完整3600s数据，30s展示采样；实线0.5m、虚线1m，灰基准、青既有、橙对照。两图的纵轴范围按各自结果设定。核心保护约束与外围面积过程是不同指标，不能把二者混为全村火灾停止。面积为累计引燃屋面覆盖，含初始点火区，共享节点只计一次，不是整栋损毁。\n来源：${SOURCE.D}; docs/results/heritage-followup-20261103/roof-ignition-curves.csv`);
 heading(s,'岜团 · B0062桥侧源',78,143,555,27);heading(s,'平岩 · B0164鼓楼源',690,143,555,27);
 image(s,'batuan_curve',58,194,564,342);image(s,'pingyan_curve',664,194,564,342);
 [['无干预',C.muted],['既有方案',C.teal],['位置对照',C.fire]].forEach((v,i)=>{rect(s,83+i*260,566,27,10,v[1]);text(s,v[0],125+i*260,551,210,40,23);});
 text(s,'实线 0.5 m\n虚线 1 m',1012,547,202,70,23,C.muted);text(s,'核心到火时间、累计引燃范围与终点燃烧状态分别记录',81,620,1120,35,24,C.teal,true);break;
case 36:
 s=base(p,n,'目前形成的研究成果与下一阶段问题','阶段结论：方法、方案和实验；保留规律提炼为后续问题',`汇报不提出适用于所有传统木构村落的最终线宽、距离或方向规律。现阶段已有两村可运行的数据—模拟—选址—验证链；岜团原设计条件下最少数量3，平岩当前五栋可行、界3—5；两村固定方案60min检验完成。下一步统一60min情景并扩大源位置，检验有效位置共有空间特征；用独立火灾或观测校验材料与传播，不把软件恢复当现场验证。\n来源：${SOURCE.F}; ${SOURCE.X}; ${SOURCE.D}`);
 heading(s,'已取得的成果',79,158,520);
 rows(s,['独立屋面受热—点燃—燃烧模拟','数量分层搜索与可核验的剪枝界限','两村位置方案、重复确认及60分钟对照'],80,222,1128,88,28);
 heading(s,'下一阶段的问题',79,514,650);
 text(s,'扩大起火位置覆盖；量化有效布局的空间特征；\n结合独立观测检验传播过程，再讨论可推广的设计原则',82,565,1110,78,27);
 break;
case 37:
 s=base(p,n,'附录｜当前等效建筑与传热参数','[M/P/R] 参数用于本研究情景，均按冻结输入保留',`以下为合成等效建筑与接触设定，不是岜团/平岩逐栋现场测量值。热参数两村一致。直接源25m²，初始可热解能量约4.3902GJ。背景含草、树等效燃料，与建筑不同。接触Tg并非模型预测的火焰温度。\n来源：${SOURCE.M}; ${SOURCE.P}; ${SOURCE.R}; runs/research/heritage-geometry-20261023-r2/case/manifest.json`);
 table(s,[['参数','当前设定','参数','当前设定'],['干燃料载荷','18 kg/m²','点燃阈值','520 K'],['可热解比例','0.60','燃烧热','16.26 MJ/kg'],['含水率（干基）','0.10','有效受热干质量','2 kg/m²'],['辐射份额 / 吸收率','0.40 / 0.90','发射率','0.85'],['气体温度 / 接触系数','1100 K / 50 W/(m²·K)','辐射范围 / 正则化','15 m / 5 m'],['环境 / 背面导热系数','10 / 2 W/(m²·K)','风 / 飞火','无风 / 未启用']],76,153,1130,365,[315,250,300,265],21);
 text(s,'相邻建筑的高度和材料仍是等效设定；15 m是计算范围',82,600,1120,49,24,C.teal,true);break;
case 38:
 s=base(p,n,'附录｜每一时间步的更新顺序','[P] solver_v3.py；最大步长1 s并受热稳定条件约束',`时间步取最大步长、剩余观察时长、下一点火/采样、热稳定限制与最近燃尽事件的最小值；无风无飞火本批不含飞火事件。先根据当前燃烧元胞积分能量，计算辐射和接触，再推进受体受热/水分，再更新燃料、状态和时间戳。新点燃元胞在后续步释放燃烧热。\n来源：${SOURCE.P}`);
 const code=['1 处理到期的直接点火事件；确定可受热与燃烧集合','2 取 Δt = min(最大步长、事件间隔、热稳定、燃尽等限制)','3 对燃烧元胞积分HRR，扣除燃料并分配辐射/接触热','4 对未燃/受热元胞更新净热流、水分与表面温度','5 检查点燃判据；更新燃尽状态及首次引燃/燃尽时间','6 保存诊断，进入下一时间步或达到观察终点'];
 code.forEach((t,i)=>text(s,t,82,164+i*69,1128,54,25,i===4?C.fire:C.ink,i===4));
 break;
case 39:
 s=base(p,n,'附录｜重复实验与验收分别支撑不同判断','归档实验汇总；完整模拟数不等于独立抽样数',`各冻结批次分别列示，不合并成独立样本总数。18次随机确认108CA；B0063定向6次24CA；位置对照6情景×5臂×2尺度60CA；平岩新落点4次16CA；60分钟历史源回放4固定火源24CA，新独立抽样0。2026-11-03另2次原生复现；缓存恢复0新CA。审计全部24份有效达3600s。共同无歧义普通对象岜团143/331、平岩298/947，不能改用全部标注数作分母。\n来源：${SOURCE.B}; ${SOURCE.T}; ${SOURCE.F}; ${SOURCE.D}`);
 table(s,[['实验批次','火源/情景','完整模拟','主要作用'],['岜团随机确认','18次分层抽样','108','冻结方案的新落点保护'],['岜团B0063补齐','6次定向落点','24','补足桥侧同源覆盖'],['岜团位置对照','6个比较情景','60','检验替代布局'],['平岩新落点确认','4次抽样','16','跨村方案确认'],['两村60分钟对照','4处历史固定源','24','延时与同源位置效果']],76,153,1130,366,[333,255,160,382],21);
 statement(s,'核对源码、点火面、质量/能量、面积账本与原生复现');break;
case 40:
 s=base(p,n,'附录｜主要证据与复现入口','本汇报资料截至2026-11-03；完整路径和链接列于各页备注',`本汇报使用已有归档，不追加CA，不调整材料。图表源为docs/results/heritage-followup-20261103等。具体源码：${SOURCE.P}; ${SOURCE.R}; ${SOURCE.A}。数据与报告：${SOURCE.B}; ${SOURCE.T}; ${SOURCE.E}; ${SOURCE.F}; ${SOURCE.X}; ${SOURCE.D}。外部背景：https://whc.unesco.org/en/tentativelists/5813/ ; https://www.nist.gov/publications/nist-outdoor-structure-separation-experiments-nosse-wind 。汇报构建与资产清单位于docs/presentations/20261104。Spaces Page: page_efc598a9edf48191ab8eca406efa8f7b`);
 heading(s,'代码中的方法',78,152,590);
 text(s,'燃烧与热平衡：physics_v2 / physics_v3\n状态推进与点燃：solver_v3\n屋面与传热图：roof_thermal / flame_contact_v3\n组合搜索：building_search / failure_cuts',80,215,1120,163,24);
 heading(s,'归档实验',78,406,590);
 text(s,'岜团18次随机确认、定向与位置对照（2026-10-24）\n全候选搜索与平岩迁移（2026-10-29至30）\n两村60分钟同源位置对照（2026-11-03）',80,466,1120,128,25);
 text(s,'演讲备注包含逐页数据来源、公式定义与适用范围',80,620,1120,35,24,C.teal,true);break;
default:throw new Error('No slide definition for '+n);
}
return s;
}

export default async function buildBatch({presentation,input}){
 const added=[];
 for(const n of input.slides){const s=make(presentation,n);added.push({number:n,id:s.id});}
 return {added,slideCount:presentation.slides.items.length};
}

export async function repair({presentation,input}){
 // Replacement preserves the ordering of every unchanged slide.
 const ids=(await presentation.inspect({select:'$.slides[*]',include:['id']})).records.map(r=>r.id);
 for(const n of input.slides){const old=ids[n-1];const fresh=make(presentation,n);const inspection=await presentation.inspect({select:'$.slides[*]',include:['id']});const newId=inspection.records.at(-1).id;presentation.delete(old);ids[n-1]=newId;}
 presentation.slides.reorder(ids);
 return {replaced:input.slides,slideCount:presentation.slides.items.length};
}
