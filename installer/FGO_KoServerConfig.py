"""Private, owned ARTEMiS session configuration; Python 3.10 standard library."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_path(path):
    path = Path(os.path.abspath(path))
    for entry in (path, *path.parents):
        if entry.is_symlink() or (entry.exists() and getattr(entry.stat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('NETWORK_CONFIG_REPARSE_POINT')
    return path


PORT_FIELDS = {('server','port'):'http', ('allnet','port'):'http',
               ('billing','port'):'billing', ('aimedb','port'):'aime',
               ('database','port'):'database'}


def core_rows(raw):
    """A bounded flat-scalar reader for the reviewed network fields only."""
    lines=raw.decode('utf-8-sig').splitlines(keepends=True)
    seen=set(); section=None; result={}
    relevant={'server','allnet','billing','aimedb','database'}
    for index,line in enumerate(lines):
        top=re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*):[ \t]*(?:#.*)?(?:\r?\n)?',line)
        if top:
            section=top[1]
            if section in relevant and section in seen:
                raise ValueError('NETWORK_CONFIG_SHAPE_UNSUPPORTED')
            seen.add(section)
        elif line and not line[0].isspace() and not line.startswith('#'):
            section=None
        match=re.fullmatch(r'(  )([A-Za-z_][A-Za-z0-9_]*):( *)([^\r\n]*)(\r?\n)?',line)
        if match and section in relevant:
            key=(section,match[2])
            if key in result:
                raise ValueError('NETWORK_CONFIG_SHAPE_UNSUPPORTED')
            result[key]=(index,match)
    return lines,result


def port_value(value):
    if isinstance(value,bool) or not re.fullmatch(r'[0-9]{1,5}',str(value)) or not 1 <= int(value) <= 65535:
        raise ValueError('NETWORK_PORT_INVALID')
    return int(value)


def settings_from_core(raw, ports=None):
    _,rows=core_rows(raw)
    standalone=rows.get(('allnet','standalone'))
    if standalone and standalone[1][4].split('#',1)[0].strip().lower() not in ('false','no','off','0'):
        raise ValueError('NETWORK_SERVICE_LAYOUT_UNSUPPORTED')
    result={}
    for (section,key),name in PORT_FIELDS.items():
        if (section,key) not in rows:
            if section=='allnet': continue
            raise ValueError('NETWORK_CONFIG_FIELDS_MISSING')
        scalar=rows[(section,key)][1][4].split('#',1)[0].strip().strip('"\'')
        value=port_value(scalar)
        if section!='allnet':result[name]=value
    if ports is not None:
        if not isinstance(ports,dict) or set(ports)-{'http','billing','aime','database'}:
            raise ValueError('NETWORK_PORT_INVALID')
        for name in ('http','billing','aime'):
            if name not in ports:raise ValueError('NETWORK_PORT_INVALID')
            result[name]=port_value(ports[name])
        # Database storage belongs to the existing local server. Never silently
        # select another database port/account store from launcher settings.
        if 'database' in ports and port_value(ports['database'])!=result['database']:
            raise ValueError('NETWORK_DATABASE_PORT_CONFLICT')
    if len(set(result.values()))!=4:
        raise ValueError('NETWORK_PORT_CONFLICT')
    return dict(host='local',address='192.168.100.1',**result)


def render_core(raw, ports=None):
    """Edit three known scalar fields, preserving all other bytes and settings.

    This deliberately accepts only the reviewed flat YAML field structure,
    never evaluates YAML tags/aliases and never rewrites other fields.
    """
    lines,rows=core_rows(raw)
    fields={('server','listen_address'):'127.0.0.1',
            ('server','hostname'):'"192.168.100.1"',
            ('aimedb','listen_address'):'127.0.0.1'}
    if ports is not None:
        settings=settings_from_core(raw,ports)
        fields.update({key:str(settings[name]) for key,name in PORT_FIELDS.items() if key in rows})
    for identity,replacement in fields.items():
        if identity not in rows:raise ValueError('NETWORK_CONFIG_FIELDS_MISSING')
        index,match=rows[identity]
        if any(token in match[4] for token in ('&','*','!','{','[','|','>')):
            raise ValueError('NETWORK_CONFIG_SHAPE_UNSUPPORTED')
        lines[index]=match[1]+match[2]+': '+replacement+(match[5] or '')
    return (b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b'')+''.join(lines).encode('utf-8')


def prepare(source, state, ports=None):
    source=safe_path(source); state=safe_path(state)
    if not source.is_dir() or not state.is_dir():
        raise ValueError('NETWORK_CONFIG_LAYOUT_INVALID')
    entries=list(source.iterdir())
    if len(entries)>128:
        raise ValueError('NETWORK_CONFIG_LIMIT')
    data={}
    for path in entries:
        safe_path(path)
        if path.name=='ownership.json' or not path.is_file() or path.stat().st_size>8*1024*1024:
            raise ValueError('NETWORK_CONFIG_SHAPE_UNSUPPORTED')
        data[path.name]=path.read_bytes()
        if sum(map(len,data.values()))>32*1024*1024:
            raise ValueError('NETWORK_CONFIG_LIMIT')
    if 'core.yaml' not in data:
        raise ValueError('NETWORK_CONFIG_FIELDS_MISSING')
    settings=settings_from_core(data['core.yaml'],ports)
    originals={name:sha(raw) for name,raw in data.items()}
    data['core.yaml']=render_core(data['core.yaml'],{k:settings[k] for k in ('http','billing','aime','database')})
    if set(p.name for p in source.iterdir())!=set(data) or any(
            sha(safe_path(source/name).read_bytes())!=expected for name,expected in originals.items()):
        raise ValueError('NETWORK_SOURCE_CONFIG_CHANGED')
    folder=state/('.ko-network-'+uuid.uuid4().hex)
    folder.mkdir()
    created={}
    try:
        for name,raw in data.items():
            with (folder/name).open('xb') as stream:stream.write(raw)
            created[name]=sha(raw)
        manifest={'format':2,'owner':'FGO_KoNetwork','files':created,'settings':settings}
        with (folder/'ownership.json').open('x',encoding='utf-8') as stream:json.dump(manifest,stream)
    except BaseException:
        # Never recursively delete a partly written or externally changed tree.
        for name,expected in created.items():
            path=safe_path(folder/name)
            if path.is_file() and sha(path.read_bytes())==expected:path.unlink()
        if not any(folder.iterdir()):folder.rmdir()
        raise
    return folder


def cleanup(folder, manifest_sha256):
    folder=safe_path(folder)
    if not re.fullmatch(r'\.ko-network-[0-9a-f]{32}',folder.name):
        raise ValueError('NETWORK_CONFIG_OWNER_INVALID')
    manifest_raw=(folder/'ownership.json').read_bytes()
    if sha(manifest_raw)!=manifest_sha256:
        raise ValueError('NETWORK_CONFIG_CHANGED_PRESERVED')
    manifest=json.loads(manifest_raw)
    if manifest.get('format') not in (1,2) or manifest.get('owner')!='FGO_KoNetwork':
        raise ValueError('NETWORK_CONFIG_OWNER_INVALID')
    files=manifest['files']
    if set(path.name for path in folder.iterdir())!=set(files)|{'ownership.json'}:
        raise ValueError('NETWORK_CONFIG_CHANGED_PRESERVED')
    for name,expected in files.items():
        if Path(name).name!=name or name in ('.','..'):
            raise ValueError('NETWORK_CONFIG_OWNER_INVALID')
        path=safe_path(folder/name)
        if not path.is_file() or sha(path.read_bytes())!=expected:
            raise ValueError('NETWORK_CONFIG_CHANGED_PRESERVED')
    for name in files:
        (folder/name).unlink()
    (folder/'ownership.json').unlink()
    folder.rmdir()


if __name__=='__main__':
    try:
        if len(sys.argv) in (4,5) and sys.argv[1]=='prepare':
            folder=prepare(sys.argv[2],sys.argv[3],json.loads(sys.argv[4]) if len(sys.argv)==5 else None)
            manifest=(folder/'ownership.json').read_bytes()
            print(json.dumps({'folder':str(folder),'manifest_sha256':sha(manifest),'settings':json.loads(manifest)['settings']}))
        elif len(sys.argv) in (3,4) and sys.argv[1]=='settings':
            core=safe_path(sys.argv[2])
            if core.stat().st_size>8*1024*1024:raise ValueError('NETWORK_CONFIG_LIMIT')
            print(json.dumps(settings_from_core(core.read_bytes(),json.loads(sys.argv[3]) if len(sys.argv)==4 else None)))
        elif len(sys.argv)==4 and sys.argv[1]=='cleanup':
            cleanup(sys.argv[2],sys.argv[3])
        else:
            raise ValueError('NETWORK_CONFIG_ARGUMENT_INVALID')
    except Exception as error:
        # No source text, private path or credential details in diagnostics.
        print(str(error) if isinstance(error,ValueError) else 'NETWORK_CONFIG_IO_FAILED',file=sys.stderr)
        sys.exit(1)
