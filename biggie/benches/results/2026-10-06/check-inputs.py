import subprocess,hashlib,json,os,csv,io
from pathlib import Path
out=Path('target/biggie-evidence')
binary='target/release/biggie'
env={**os.environ,'LC_ALL':'C','TZ':'UTC','CLIS_LOG_LEVEL':'off'}
results=[]
def run(args,binary=binary):
    p=subprocess.run([binary,*args],capture_output=True,env=env,check=True)
    assert not p.stderr,p.stderr
    return p.stdout
def save(name,data):
    (out/name).write_bytes(data)
    results.append(dict(name=name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
args=['-','--seed','42','-n','1000000']
before=run(args,'/private/tmp/biggie-before-b3b80d2')
after=run(args)
assert before==after
lines=after.splitlines()
assert len(lines)==1000000
for line in lines:
    words=line.split(b' ')
    assert 7<=len(words)<=14
    assert all(2<=len(w)<=11 and w.isalnum() for w in words)
save('ascii.txt',after)
records=run(['records','-','-r','miss','-r','Hit','-r','','-p','2','-c','10000'])
assert records==b'miss\nmiss\nHit\nHit\n\n\n'*10000
save('records.txt',records)
fields=run(['fields','-','-n','100000','-F','csv','-d',',','-s','42'])
rows=list(csv.reader(io.StringIO(fields.decode())))
assert len(rows)==100000 and all(len(row)==3 for row in rows)
assert all(2<=len(cell)<=11 and cell.isalnum() for row in rows for cell in row)
save('fields.csv',fields)
raw=run(['bytes','-','-b','16777216','-s','42'])
assert len(raw)==16777216 and raw==run(['bytes','-','-b','16777216','-s','42'])
save('bytes.bin',raw)
text=run(['text','-','-n','100000','-A','ACGT','-s','42'])
assert len(text.splitlines())==100000 and set(text)<=set(b'ACGT \n')
save('dna.txt',text)
run(['pair','-l',str(out/'left.txt'),'-r',str(out/'right.txt'),'-a','10000','-j','10000','-b','10000','-c','2'])
left=(out/'left.txt').read_bytes();right=(out/'right.txt').read_bytes()
l=left.splitlines();r=right.splitlines()
assert len(l)==len(r)==40000 and l==sorted(l) and r==sorted(r)
from collections import Counter
lc,rc=Counter(l),Counter(r)
assert sum((lc&rc).values())==20000 and sum((lc-rc).values())==sum((rc-lc).values())==20000
save('left.txt',left);save('right.txt',right)
(out/'checksums.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
