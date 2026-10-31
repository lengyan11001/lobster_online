"""多连接分片下载：每个文件切 N 段并行，单段失败自动续传重试；全部完成后解码校验"""
import subprocess, sys, os, concurrent.futures as cf
N = 6
def size(url):
    out = subprocess.run(['curl', '-sSI', '--http1.1', '--max-time', '30', url], capture_output=True, text=True).stdout
    for l in out.splitlines():
        if l.lower().startswith('content-length'): return int(l.split(':')[1])
    raise RuntimeError('no length')
def part(url, path, a, b):
    for _ in range(30):
        have = os.path.getsize(path) if os.path.exists(path) else 0
        if have >= b - a + 1: return True
        r = subprocess.run(['curl', '-sS', '--http1.1', '--max-time', '60', '-r', f'{a + have}-{b}', url], capture_output=True)
        with open(path, 'ab') as f: f.write(r.stdout)
    return os.path.getsize(path) >= b - a + 1
def fetch(tid, url):
    n = size(url); step = (n + N - 1) // N
    rngs = [(i * step, min(n - 1, (i + 1) * step - 1)) for i in range(N)]
    with cf.ThreadPoolExecutor(N) as ex:
        ok = list(ex.map(lambda ir: part(url, f'.p{tid}_{ir[0]}', *ir[1]), enumerate(rngs)))
    with open(f'renoise-{tid}.mp4', 'wb') as out:
        for i in range(N):
            p = f'.p{tid}_{i}'; out.write(open(p, 'rb').read()[: rngs[i][1] - rngs[i][0] + 1]); os.remove(p)
    bad = subprocess.run(['ffmpeg', '-v', 'error', '-i', f'renoise-{tid}.mp4', '-f', 'null', '-'], capture_output=True, text=True).stderr.strip()
    return tid, n, all(ok), 'OK' if not bad else 'BAD ' + bad[:60]
jobs = [l.split() for l in open('urls.txt') if l.strip()]
for j in jobs:
    for f in os.listdir('.'):
        if f.startswith(f'.p{j[0]}_'): os.remove(f)
with cf.ThreadPoolExecutor(4) as ex:
    for r in ex.map(lambda j: fetch(*j), jobs): print(*r, flush=True)
