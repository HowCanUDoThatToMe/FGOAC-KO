# 설치기 코드의 MIT 적용 범위

2026-10-26부터 아래 자체 설치기 소스에 MIT License를 적용합니다. GitHub의 `installer/`와 배포 ZIP의 `engine/`에서 같은 이름의 파일을 뜻합니다.

- `common.py`
- `launcher_compat.py`
- `engine.py`
- `native_label_support.py`
- `offline_config.py`
- `offline_support.py`
- `offline_ui.py`
- `platform_launch.py`
- `process_guard.py`
- `process_identity.py`
- `launcher_profiles.py`
- `launcher_analysis.ps1`
- `network_compat.py`
- `network_launcher.py`
- `network_analysis.ps1`
- `FGO_KoNetwork.ps1`
- `FGO_KoServerConfig.py`
- `FGO_KoServerCleanup.ps1`
- `runtime_variants.py`
- `first_start_compat.py`

v1.0.3에서 추가한 위 런처 분석·네트워크 보조 소스에도 같은 MIT 고지를 적용합니다. 프로파일 JSON은 실행 구조 검사에 사용하는 메타데이터이며 원본 게임 스크립트 전체를 포함하지 않습니다.

MIT 전문은 이 폴더의 `LICENSE`와 배포 ZIP의 `engine/licenses/FGOAC-KO-Installer-MIT.txt`에 있습니다. 배포본과 공개 소스는 파일 해시로 대조할 수 있습니다. 이 코드에는 AI 도구 OpenAI Codex를 사용했습니다.

MIT는 위 코드의 상업적 이용·수정·재배포·판매도 허용합니다. 프로젝트 제작자가 수익·후원·기부를 받지 않는다는 운영 원칙은 MIT에 상업 이용 금지 조건을 추가하는 뜻이 아닙니다. 재배포할 때 저작권 표시와 MIT 허락 고지를 유지해야 합니다.

이 MIT 적용 범위에는 게임 프로그램·게임 스크립트 원본·원문·이미지·음악·캐릭터·번역·이미지 수정본·차분 자료·글꼴·네이티브 DLL·Python 등 동봉 실행 환경과 외부 구성품이 포함되지 않습니다. 설치기가 사용자의 원본을 바탕으로 만드는 파일 전체에 MIT를 새로 부여하는 것도 아닙니다. 이들의 권리와 라이선스는 별도입니다.

원작 콘텐츠에 대한 새 이용허락을 부여하지 않으며, 외부 코드의 기존 저작권·MIT·OFL·Python 등의 고지는 그대로 유지합니다. 구성품별 내용은 저장소의 `docs/THIRD_PARTY_NOTICES_KO.md`와 배포 ZIP의 `engine/licenses`를 확인하세요.

이 폴더는 공개 검토용 소스입니다. 게임 데이터와 실행 환경을 포함한 독립 실행 패키지가 아니므로, 일반 이용자는 GitHub Releases의 설치 ZIP을 사용하세요.

참고: [MIT 공식 전문](https://opensource.org/license/mit), [GitHub 라이선스 안내](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository).

 v2.0.0에서 추가한 배포 계열 선택·최초 설정 보완 소스에도 같은 MIT 고지를 적용합니다.
