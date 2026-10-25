"""Local-only package checks. No network client or automatic download fallback."""
import os
from pathlib import Path
from common import child, digest, no_links, read


def cancelled(marker):
    if marker and Path(marker).exists():
        raise InterruptedError('사용자가 작업을 취소했습니다.')


def local_path(value):
    text = str(value)
    if '://' in text or text.startswith(('\\\\', '//')):
        raise ValueError('오프라인 설치는 PC의 로컬 폴더만 사용합니다.')
    path = Path(value).absolute()
    no_links(path)
    if os.name == 'nt':
        import ctypes
        get_type = ctypes.windll.kernel32.GetDriveTypeW
        get_type.argtypes = [ctypes.c_wchar_p]
        get_type.restype = ctypes.c_uint
        if get_type(path.anchor) == 4:
            raise ValueError('네트워크 드라이브는 지원하지 않습니다. 로컬 디스크에 압축을 풀어 주세요.')
    return path


def verify_bundle(package, cancel=None):
    package = local_path(package)
    manifest = read(child(package, 'offline-manifest.json'))
    if manifest.get('format') != 1 or manifest.get('mode') != 'offline-only':
        raise ValueError('오프라인 배포 명세가 올바르지 않습니다.')
    from offline_config import RELEASE_NAME, RELEASE_SHA256
    if manifest.get('release_name') != RELEASE_NAME or manifest.get('release_sha256') != RELEASE_SHA256:
        raise ValueError('설치 엔진과 번역 자료 버전이 다릅니다.')
    files = manifest.get('engine_files')
    if not isinstance(files, dict) or not files:
        raise ValueError('설치 엔진 파일 목록이 없습니다.')
    for rel, expected in files.items():
        cancelled(cancel)
        if not rel.startswith('engine/'):
            raise ValueError('설치 엔진 검증 범위를 벗어났습니다.')
        path = child(package, rel)
        if not path.is_file() or digest(path) != expected:
            raise ValueError('설치 엔진 파일 누락 또는 손상: ' + rel)
    release = child(package, RELEASE_NAME)
    if not release.is_file() or digest(release) != RELEASE_SHA256:
        raise ValueError('번역 자료 명세가 누락되었거나 손상되었습니다.')
    return release
