import ast,pathlib,re
def reason(s,expr):
    if 'AssetDisposal' in expr or 'AssetReservation' in expr:
        return "disposal and reservation evidence includes cancelled and soft-deleted rows; asset scope narrows visibility"
    if 'ObjectChange' in expr:
        return "audit evidence is read independent of the active tenant scope"
    if 'user_model' in expr or 'get_user_model' in expr:
        return "actor reload for re-authorization must not depend on the ambient tenant"
    if 'StatusLabel' in expr:
        return "historical status labels resolve even after soft deletion"
    if '/type_library/' in s:
        return "library apply checks identities across soft-deleted rows and the command authorizes scope itself"
    if '/api/' in s or '/forms/' in s:
        return "post-save re-read of the row being persisted must not depend on soft-delete state or ambient scope"
    return "command locks and re-reads the row regardless of soft-delete state; the service authorizes scope itself"
tot=0
for p in sorted(pathlib.Path('assets').rglob('*.py')):
    s=p.as_posix()
    if not re.search(r'/(views?|forms?|serializers?|services?)(/|\.py)',s) or '/tests/' in s: continue
    raw=p.read_bytes().decode('utf-8'); crlf='\r\n' in raw
    src=raw.replace('\r\n','\n'); tree=ast.parse(src); lines=src.split('\n')
    par={}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n): par[c]=n
    ins={}
    for n in ast.walk(tree):
        if isinstance(n,ast.Attribute) and n.attr in('_base_manager','all_objects'):
            st=n
            while not isinstance(st,ast.stmt): st=par[st]
            if isinstance(st,(ast.FunctionDef,ast.ClassDef)):
                # class-level attribute within a class body: statement is ClassDef header -> use own statement
                st2=n
                while not isinstance(st2,ast.stmt) or isinstance(st2,(ast.ClassDef,ast.FunctionDef)):
                    st2=par[st2]
                st=st2
            ins.setdefault(st.lineno,(st,reason(s,ast.unparse(n.value))))
    if not ins: continue
    for ln in sorted(ins,reverse=True):
        st,r=ins[ln]
        ind=re.match(r'\s*',lines[ln-1]).group(0)
        txt=f"{ind}# unscoped: {r}"
        if len(txt)>118:
            cut=txt.rfind(' ',0,116)
            block=[txt[:cut],f"{ind}# "+txt[cut+1:]]
        else: block=[txt]
        lines[ln-1:ln-1]=block
    tot+=len(ins); print(s,len(ins))
    out='\n'.join(lines)
    if crlf: out=out.replace('\n','\r\n')
    p.write_bytes(out.encode('utf-8'))
print(tot)
