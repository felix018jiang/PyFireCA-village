from pathlib import Path
import os,json,importlib.util
P=Path(__file__).resolve().parent;R=P.parents[2];os.environ['MPLCONFIGDIR']=str(P/'.mpl-cache')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import ListedColormap,BoundaryNorm
from matplotlib.patches import Polygon as Patch
import numpy as np
from shapely.ops import unary_union
from shapely.geometry import shape
font_manager.fontManager.addfont('/System/Library/Fonts/STHeiti Medium.ttc');plt.rcParams.update({'font.family':font_manager.FontProperties(fname='/System/Library/Fonts/STHeiti Medium.ttc').get_name(),'axes.unicode_minus':False,'font.size':16})
spec=importlib.util.spec_from_file_location('followup',R/'scripts/summarize_heritage_followup_20261103.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def poly(ax,g,**k):
 for p in [g] if g.geom_type=='Polygon' else g.geoms:ax.add_patch(Patch(np.asarray(p.exterior.coords),**k))
receipts=[]
for vid,fire in [('batuan','bridge-B0062-known'),('pingyan','drum-B0164-late-ignition')]:
 v=m.load_village(vid,R/'runs/research');geos=v['geometries'];cores=[x['building_id'] for x in v['inventory']['targets']];plan=next(x for x in v['experiment']['config']['plans'] if x['plan_id']=='selected')
 x0,y0,x1,y1=unary_union(list(geos.values())).bounds;pad=.045*max(x1-x0,y1-y0);bounds=[x0-pad,x1+pad,y0-pad,y1+pad]
 case=R/('runs/research/heritage-geometry-20261023-r2/case' if vid=='batuan' else 'runs/research/pingyan-physical-core-priority-20261029/case');manifest=json.loads((case/'manifest.json').read_text());grid=manifest['grid'];mat=np.load(case/'material_index.npy');bg=np.zeros_like(mat);bg[np.isin(mat,[1,7])]=1;bg[np.isin(mat,[4,6])]=2;bg[mat==5]=3
 extent=(grid['x_min_m'],grid['x_min_m']+grid['width']*grid['cell_size_m'],grid['y_max_m']-grid['height']*grid['cell_size_m'],grid['y_max_m'])
 fig,axs=plt.subplots(1,2,figsize=(13.7,5.6));fig.subplots_adjust(left=.01,right=.99,bottom=.01,top=.91,wspace=.025)
 numbers=[]
 for arm,ax,label in zip(['baseline','selected'],axs,['无干预','既有方案'],strict=True):
  ax.imshow(bg,extent=extent,origin='upper',cmap=ListedColormap(['#EEEDE7','#E4EBCB','#D5E1D2','#EEEAE2']),vmin=0,vmax=3,interpolation='nearest',zorder=0)
  for g in geos.values():poly(ax,g,facecolor='#EEEAE2',edgecolor='#8F9C92',linewidth=.5,zorder=1)
  rec=v['records'][(fire,arm,.5)];mask=rec['roofs']&np.isfinite(rec['ignition'])&(rec['ignition']<=3600);xy=rec['layout']['xy_m'][mask]
  ax.scatter(xy[:,0],xy[:,1],c=rec['ignition'][mask],s=np.clip(rec['area'][mask]*.5,1.2,16),marker='s',cmap=ListedColormap(['#DFAD4D','#E47740','#A82E42']),norm=BoundaryNorm([0,1200,2400,3600.001],3),linewidths=0,zorder=3,rasterized=True)
  for core in cores:
   poly(ax,geos[core],fill=False,edgecolor='#087D89',linewidth=1.7,zorder=5);p=geos[core].representative_point();ax.annotate(core,(p.x,p.y),xytext=(7,7),textcoords='offset points',fontsize=13,color='#005F69',fontweight='bold',bbox={'facecolor':'white','alpha':.8,'edgecolor':'none','pad':1},zorder=7)
  if arm=='selected':
   for bid in plan['demolished_ids']:poly(ax,geos[bid],fill=False,edgecolor='#26332F',linewidth=1.6,linestyle='--',zorder=6)
  for source,f in v['experiment']['problem']['fires'][fire]['footprints'].items():
   g=shape(f['geometry']);p=g.representative_point();ax.scatter([p.x],[p.y],s=100,marker='*',color='#70449D',edgecolors='white',linewidths=.5,zorder=8)
  ax.set(xlim=bounds[:2],ylim=bounds[2:],aspect='equal',title=label);ax.axis('off')
  dx=bounds[1]-bounds[0];dy=bounds[3]-bounds[2];lx=bounds[0]+.065*dx;ly=bounds[2]+.06*dy;length=100
  ax.plot([lx,lx+length],[ly,ly],color='#26332F',lw=3);ax.text(lx+length/2,ly+.02*dy,'100 m',ha='center',fontsize=14,color='#26332F');ax.annotate('N',xy=(bounds[1]-.065*dx,bounds[3]-.08*dy),xytext=(bounds[1]-.065*dx,bounds[3]-.2*dy),ha='center',fontsize=17,arrowprops={'arrowstyle':'->','color':'#26332F','lw':2})
  numbers.append({'arm':arm,'cumulative_roof_m2':float(np.sum(rec['area'][mask])),'core_ignition':rec['curves']['checkpoints'][-1]['core_joint_failure']})
 out=P/'assets'/f'{vid}_macro_fire.png';fig.savefig(out,dpi=180,bbox_inches='tight',pad_inches=.04);plt.close(fig);receipts.append({'village':vid,'fire_id':fire,'scale_m':.5,'horizon_s':3600,'full_building_extent':True,'statistics':numbers,'asset':str(out)})
(P/'macro-map-evidence.json').write_text(json.dumps({'new_CA_runs':0,'maps':receipts},ensure_ascii=False,indent=2)+'\n');print(json.dumps(receipts,ensure_ascii=False))
