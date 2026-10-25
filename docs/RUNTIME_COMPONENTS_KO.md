# 동봉 구성품과 고지

이 폴더의 고지는 배포 ZIP에 포함되며 설치 후에도 로컬 관리 도구의 `licenses` 폴더에 보존됩니다. 게임 전체·번역·이미지에 하나의 오픈소스 라이선스를 부여하는 문서가 아닙니다.

| 구성품 | 동봉 위치 | 적용 고지 |
| --- | --- | --- |
| CPython 3.10.11 / 표준 라이브러리 | `engine/runtime` | `Python-3.10.11-LICENSE.txt`, `Python-3.10.11-Doc-license.rst` |
| OpenSSL 1.1.1t | `runtime/DLLs/libssl-1_1.dll`, `libcrypto-1_1.dll`, `_ssl.pyd`, `_hashlib.pyd` | Python LICENSE의 OpenSSL 및 SSLeay 고지 |
| libffi | `runtime/DLLs/libffi-7.dll`, `_ctypes.pyd` | Python LICENSE의 libffi 고지 |
| Tcl/Tk 8.6.12 | `runtime/DLLs/tcl86t.dll`, `tk86t.dll`, `_tkinter.pyd`, `runtime/tcl` | Tcl·Tk `license.terms`와 Python LICENSE |
| bzip2·zlib·Expat·libmpdec 등 | Python DLL 및 확장 모듈 | Python LICENSE와 Python Doc license의 해당 구성품 고지 |
| liblzma (XZ 5.2.5) | `runtime/DLLs/_lzma.pyd` | `XZ-5.2.5-COPYING.txt`; liblzma 부분은 public domain |
| SQLite 3.40.1 | `runtime/DLLs/sqlite3.dll`, `_sqlite3.pyd` | SQLite는 public domain: https://sqlite.org/copyright.html |
| Microsoft VC 런타임 | `runtime/vcruntime140.dll`, `vcruntime140_1.dll` 및 Python 바이너리의 링크 코드 | Python LICENSE의 `Additional Conditions for this Windows binary build` |
| Noto 파생 글꼴 | 설치된 한국어 리소스 | `NOTO-OFL.txt` |
| 선행 Scooby MIT 적용 코드 | 해당 코드 부분 | `FGOAC-scooby-MIT.txt` |
| 한국어 표시 DLL의 Zig·MinGW 런타임 | 설치된 `native/fgoko-labels.dll` | `NATIVE_LABEL_NOTICE.txt`, `Zig-MIT.txt`, `MinGW-w64-COPYING.txt` |

런타임 파일별 SHA-256은 `RUNTIME_FILES.json`에 있습니다. Python 실행 파일·DLL은 원 배포본의 바이트를 유지합니다. 표준 라이브러리의 `.py` 파일은 ZIP으로 묶었고 개발용 디버그 파일·외부 패키지·일부 시험 자료·pip/IDLE은 제외했으며, 이 묶음 전용 모듈 검색 경로를 설정했습니다. Python 소스의 동작을 수정한 빌드가 아닙니다.

Microsoft 구성품을 사용·재배포할 때에는 동봉 Python LICENSE의 Windows 추가 조건을 따라야 합니다. Microsoft 고지를 변경하거나 Microsoft의 보증·승인을 암시해서는 안 되며, 해당 구성품은 Microsoft 플랫폼용입니다. 이 조건은 Python이나 프로젝트 전체에 대한 별도 독점 라이선스 부여가 아닙니다.

This product includes software developed by the OpenSSL Project for use in the OpenSSL Toolkit (https://www.openssl.org/).
This product includes cryptographic software written by Eric Young (eay@cryptsoft.com).
This product includes software written by Tim Hudson (tjh@cryptsoft.com).

출처: https://github.com/python/cpython/tree/v3.10.11 및 https://github.com/tcltk/tcl/tree/core-8-6-12 . 원 저작권자들의 보증·후원·승인을 뜻하지 않습니다.
