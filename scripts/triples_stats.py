import json, os
total_n = total_e = 0
for n in ['01_局部解剖学','02_组织学与胚胎学','03_生理学','04_医学微生物学','05_病理学','06_传染病学','07_病理生理学']:
    p = f'data/triples/{n}.json'
    d = json.load(open(p))
    nodes = edges = 0
    if isinstance(d, list):
        for c in d:
            nodes += len(c.get('nodes', []))
            edges += len(c.get('edges', []))
    elif isinstance(d, dict):
        nodes = len(d.get('nodes', []))
        edges = len(d.get('edges', []))
    total_n += nodes; total_e += edges
    print(f'{n}: nodes={nodes} edges={edges}')
print(f'TOTAL: nodes={total_n} edges={total_e}')
d = json.load(open('data/triples/03_生理学.json'))
print('---type:', type(d).__name__, 'len:', len(d))
sample = d[0] if isinstance(d, list) else list(d.items())[0]
print(json.dumps(sample, ensure_ascii=False)[:600])
