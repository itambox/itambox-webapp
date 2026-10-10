import ast,pathlib
out=[]
for p in pathlib.Path('assets').rglob('*.py'):
    s=p.as_posix()
    if '/tests/' in s or '/migrations/' in s: continue
    src=p.read_text(encoding='utf-8'); tree=ast.parse(src); lines=src.splitlines()
    par={}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n): par[c]=n
    for n in ast.walk(tree):
        if isinstance(n,ast.Attribute) and n.attr in('_base_manager','all_objects'):
            st=n
            while not isinstance(st,ast.stmt): st=par[st]
            e=st.end_lineno if not hasattr(st,'body') else st.body[0].lineno-1
            seg='\n'.join(lines[st.lineno-1:e])
            out.append((s,n.lineno,ast.unparse(n.value)+'.'+n.attr,'deleted_at__isnull=True' in seg, 'deleted_at' in seg))
for o in sorted(out): print(o)
print(len(out))
