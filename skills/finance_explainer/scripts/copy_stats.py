import re,glob,json,os,collections,sys
sys.path.insert(0,'.')
from vtt2txt import parse
meta={}
for line in open('meta_all.tsv',encoding='utf-8'):
    p=line.rstrip('\n').split('\t')
    if len(p)>=7: meta[p[0]]=dict(date=p[1],dur=float(p[2]) if p[2]!='NA' else 0,views=int(p[3]) if p[3].isdigit() else 0,likes=int(p[4]) if p[4].isdigit() else 0,comments=int(p[5]) if p[5].isdigit() else 0,title=p[6])
PHRASES=['你看','对吧','咱们','咱','那你说','你说','好 那','重点来了','其实','就是说','举个例子','比如说','简单来说','换句话说','说白了','你想','我觉得','我估计','当然','不过','所以说','也就是说','问题来了','注意','那么','怎么说呢','有意思的是','有意思','你猜','没错','是不是','为什么呢','好了','欢迎','下期','评论区','小Lin','小林','一口气','硬核','干货','不拉不拉','这里','这个','那个','啊','呢','吧','嘛','哈']
rows=[];agg=collections.Counter();totchars=0;totsec=0
for f in sorted(glob.glob('subs/*.vtt')):
    vid=os.path.basename(f).split('.')[0]
    cues=parse(f)
    if len(cues)<20: continue
    text=''.join(t for s,t in cues)
    chars=len(re.sub(r'[\s\W_]','',text))
    span=cues[-1][0]-cues[0][0]
    m=meta.get(vid,{})
    c=collections.Counter({p:text.count(p) for p in PHRASES})
    agg.update(c); totchars+=chars; totsec+=span
    q=text.count('？')+text.count('?')+len(re.findall(r'(吗|呢)(?=[ ，。]|$)',text))
    nums=len(re.findall(r'\d+(?:\.\d+)?',text))
    en=len(re.findall(r'[A-Za-z]{3,}',text))
    # 开场：直到出现"今天/这期/咱们就来"之类的引入句所花的秒数
    intro=None
    for s,t in cues:
        if re.search(r'(今天|这期|这次)(咱们|我们|咱|就)?.{0,6}(聊|讲|说|了解|来)',t): intro=s;break
    rows.append(dict(id=vid,title=m.get('title',''),date=m.get('date',''),views=m.get('views',0),dur=m.get('dur',0),
        cps=round(chars/span,2) if span else 0,q_per_min=round(q/span*60,2),num_per_min=round(nums/span*60,2),en_per_min=round(en/span*60,2),
        intro_s=round(intro,1) if intro is not None else None, nini=round(c['你看']/span*60,2)))
json.dump(rows,open('copy_rows.json','w'),ensure_ascii=False,indent=0)
print('videos',len(rows),'avg chars/sec',round(totchars/totsec,2))
print('per-10min phrase freq:')
print(', '.join(f'{p}:{agg[p]/totsec*600:.1f}' for p,_ in sorted(agg.items(),key=lambda x:-x[1])[:45]))
import statistics as st
for k in ['cps','q_per_min','num_per_min','en_per_min','intro_s']:
    v=[r[k] for r in rows if r[k] is not None]
    print(k,'median',st.median(v),'p25',sorted(v)[len(v)//4],'p75',sorted(v)[len(v)*3//4])
