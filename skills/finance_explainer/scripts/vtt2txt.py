import re,sys,json
def parse(p):
    out=[];cur=None
    for line in open(p,encoding='utf-8'):
        line=line.strip()
        m=re.match(r'(\d+):(\d+):(\d+)\.(\d+) --> ',line) or re.match(r'(\d+):(\d+)\.(\d+) --> ',line)
        if '-->' in line:
            ts=line.split(' --> ')[0]; parts=ts.split(':')
            s=sum(float(x)*60**i for i,x in enumerate(reversed(parts)))
            cur=[s,'']; out.append(cur); continue
        if cur is not None and line and not line.startswith(('WEBVTT','Kind','Language')):
            cur[1]+=re.sub(r'<[^>]+>','',line)
    return [(s,t) for s,t in out if t]
if __name__=='__main__':
    for s,t in parse(sys.argv[1]):
        print(f"{int(s//60):02d}:{s%60:05.2f} {t}")
