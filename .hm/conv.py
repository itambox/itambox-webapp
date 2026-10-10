import ast,pathlib,re
SOFT={'Asset','AssetType','Category','Manufacturer','AssetRole','Depreciation','Tag','AssetRequest','Tenant'}
n_total=0
for p in sorted(pathlib.Path('assets').rglob('*.py')):
    s=p.as_posix()
    if not re.search(r'/(views?|forms?|serializers?|services?)(/|\.py)',s) or '/tests/' in s: continue
    raw=p.read_bytes().decode('utf-8'); src=raw.replace('\r\n','\n'); tree=ast.parse(src); lines=src.split('\n')
    par={}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n): par[c]=n
    edits=[]
    for n in ast.walk(tree):
        if isinstance(n,ast.Attribute) and n.attr in('_base_manager','all_objects') and isinstance(n.value,ast.Name) and n.value.id in SOFT:
            st=n
            while not isinstance(st,ast.stmt): st=par[st]
            e=st.end_lineno if not hasattr(st,'body') else st.body[0].lineno-1
            seg='\n'.join(lines[st.lineno-1:e])
            m=n.value.id
            ok='deleted_at__isnull=True' in seg
            if m=='Tenant' and ('.none()' in seg or 'filter(pk=current_tenant' in seg): ok=True
            if ok: edits.append((n.lineno,n.col_offset,m,n.attr))
    if not edits: continue
    for ln,col,m,attr in sorted(edits,reverse=True):
        l=lines[ln-1]; old=f'{m}.{attr}'
        assert l[col:col+len(old)]==old,(s,ln,l)
        lines[ln-1]=l[:col]+f'{m}.objects'+l[col+len(old):]
    n_total+=len(edits); print(s,len(edits))
    out='\n'.join(lines)
    if '\r\n' in raw: out=out.replace('\n','\r\n')
    p.write_bytes(out.encode('utf-8'))
print(n_total)
