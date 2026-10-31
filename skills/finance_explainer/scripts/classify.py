"""逐帧画面分类：A=真人全景 AO=真人+叠加卡片 ST=图表舞台(人缩小进角落或不在) B=空镜/B-roll/梗图"""
import subprocess,sys,json,numpy as np
src=sys.argv[1]; fps=2; W,H=96,54
raw=subprocess.run(['ffmpeg','-loglevel','error','-i',src,'-vf',f'fps={fps},scale={W}:{H}','-f','rawvideo','-pix_fmt','rgb24','-'],capture_output=True).stdout
fr=np.frombuffer(raw,np.uint8).reshape(-1,H,W,3).astype(np.float32)
n=len(fr)
# 参考 A-roll：先取全片每帧，找出"与最多帧相似"的帧当中心（演播室机位占比最大）
small=fr[:, ::3, ::3].reshape(n,-1)
idx=np.linspace(0,n-1,min(n,300)).astype(int)
S=small[idx]
D=np.abs(S[:,None,:]-S[None,:,:]).mean(-1)
center=idx[np.argmax((D<18).sum(1))]
# 用与中心相似的帧求中位数，去掉手势影响
sim=[i for i in range(n) if np.abs(small[i]-small[center]).mean()<18]
ref=np.median(fr[sim[:400]],axis=0)
# 只看背景区域（去掉中间人物 & 底部字幕）做匹配
mask=np.ones((H,W),bool); mask[:,int(W*.33):int(W*.67)]=False; mask[int(H*.82):,:]=False
def hsv_purple(f):
    r,g,b=f[...,0],f[...,1],f[...,2]
    return ((b>140)&(r>60)&(g<110)&(b-g>60))
lab=[]
for i in range(n):
    f=fr[i]
    m=(np.abs(f-ref).max(-1)<40)[mask].mean()
    pur=hsv_purple(f).mean()
    if pur>0.18: c='ST'
    elif m>0.70: c='A'
    elif m>0.35: c='AO'
    else: c='B'
    lab.append(c)
# 平滑：去掉单帧抖动
for i in range(1,n-1):
    if lab[i-1]==lab[i+1]!=lab[i]: lab[i]=lab[i-1]
segs=[];s=0
for i in range(1,n+1):
    if i==n or lab[i]!=lab[s]:
        segs.append((s/fps,i/fps,lab[s])); s=i
tot=n/fps
share={k:round(sum(e-b for b,e,c in segs if c==k)/tot*100,1) for k in ['A','AO','ST','B']}
lens={k:[e-b for b,e,c in segs if c==k] for k in share}
out={'video':src,'duration':tot,'share_pct':share,
     'mean_seg_s':{k:round(float(np.mean(v)),1) if v else 0 for k,v in lens.items()},
     'segments_per_min':round(len(segs)/tot*60,1),
     'longest_A_s':max(lens['A']) if lens['A'] else 0,
     'segs':segs}
json.dump(out,open(src.rsplit('.',1)[0]+'.segs.json','w'),ensure_ascii=False)
print(json.dumps({k:v for k,v in out.items() if k!='segs'},ensure_ascii=False))
