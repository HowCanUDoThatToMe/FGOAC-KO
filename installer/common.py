"""Small shared installer primitives. No filesystem mutation on import."""
import hashlib
import json
import os
from pathlib import Path
import uuid


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def read(path):
    path = Path(path)
    try:
        return json.loads(path.read_text('utf-8-sig'))
    except json.JSONDecodeError as error:
        raise ValueError('JSON 형식 오류: %s (%s행 %s열). 설정 파일의 쉼표·따옴표를 확인하거나 정상 백업을 복원해 주세요.' %
                         (path.name, error.lineno, error.colno)) from None
    except UnicodeError:
        raise ValueError('문자 인코딩 오류: %s. UTF-8 형식의 정상 파일을 사용해 주세요.' % path.name) from None
    except FileNotFoundError:
        raise ValueError('필수 파일이 없습니다: %s. 압축을 모두 풀었는지, 선택한 게임 폴더가 맞는지 확인해 주세요.' % path.name) from None
    except PermissionError:
        raise ValueError('파일 읽기 권한이 없습니다: %s. 파일 접근 권한과 사용 중인 프로그램을 확인해 주세요.' % path.name) from None


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def put(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    no_links(path)
    tmp = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    with tmp.open('xb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save(path, value):
    put(path, encoded(value))


def no_links(path):
    path = Path(path).absolute()
    for p in (path, *path.parents):
        if p.is_symlink() or (p.exists() and getattr(p.lstat(), 'st_file_attributes', 0) & 1024):
            raise ValueError('Linked/reparse path is not supported: ' + str(p))


def child(root, relative):
    if not isinstance(relative, str) or '\\' in relative or ':' in relative:
        raise ValueError('Invalid relative path')
    parts = relative.split('/')
    if any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in parts):
        raise ValueError('Unsafe relative path')
    if any(p.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM' + str(i) for i in range(1, 10)), *('LPT' + str(i) for i in range(1, 10))} for p in parts):
        raise ValueError('Reserved Windows path')
    root = Path(root).absolute()
    result = root.joinpath(*parts)
    no_links(result)
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError('Path escaped root')
    return result


def inventory(root):
    result = {}
    for p in sorted(Path(root).rglob('*')):
        no_links(p)
        if p.is_file():
            result[p.relative_to(root).as_posix()] = digest(p)
    return result


def xor(a, b):
    return (int.from_bytes(a, 'little') ^ int.from_bytes(b[:len(a)], 'little')).to_bytes(len(a), 'little')
