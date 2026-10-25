# 크레딧·제3자 고지

> 2026-10-25 오프라인 r002 구성과 대조한 고지. 이 요약은 각 라이선스의 전문을 대신하지 않는다. 언급된 제작자는 이 패치를 승인하거나 보증한 것으로 표시하지 않는다.

## 공통 고지 문안

Fate/Grand Order Arcade와 관련한 게임 프로그램·원문·이미지·음악·캐릭터·명칭의 권리는 각 권리자에게 있습니다. 이 프로젝트는 공식 제품이나 공식 한국어판이 아닙니다.

이 프로젝트는 비상업적·비영리 팬 프로젝트로, 판매 수익·광고 수익·유료 서비스 대가·후원금·기부금을 포함해 어떤 형태의 금전적 이익도 받지 않습니다. 이 운영 원칙은 아래 제3자 라이선스가 부여하는 권리를 바꾸거나 제3자 콘텐츠에 새 라이선스를 부여하지 않습니다.

한국어 번역·이미지 수정 및 통합, 직접 제공한 개념예장 이미지, 사용자 플레이 확인과 오류 제보에 참여한 분들께 감사드립니다. 공개할 참여자 표기명은 본인의 동의와 실제 기여 범위를 확인해 추가합니다.

**권리자 또는 그 대리인으로부터 DMCA 통지 등 저작권 침해 신고나 게시 중단 요청을 받으면, 해당 자료를 내리고 배포를 중단하겠습니다.** 이 방침은 권리자의 권리를 존중하기 위한 운영 원칙이며, 이용허락이나 적법성·면책을 보증하는 문구가 아닙니다.

## AI 사용 공개

**이 패치는 AI 도구인 OpenAI Codex를 사용해 제작했습니다.** 번역·코드 작성·자료 통합·검증 보조·문서 작성에 Codex를 활용했으며, 일부 이미지 작업에는 AI 이미지 생성 도구도 사용했습니다. 사용자가 직접 준비한 이미지와 직접 확인한 플레이 결과도 함께 반영했습니다. 모든 문장·이미지·기능을 사람이 전수 검수했다는 뜻은 아니며, AI가 만든 결과에는 오역이나 오류가 남아 있을 수 있습니다.

Codex와 기타 도구의 언급은 도구 제공사의 공식 참여·승인·후원이나 원작에 대한 권리 허락을 뜻하지 않습니다.

## 도구와 구성요소

**[PhotoCraft](https://github.com/storytold/photocraft)** — 이미지 편집·크기 조정·투명도 확인·저장 및 PNG 내보내기에 사용한 오픈소스 도구입니다. 작업 기록에서 v0.15.1 사용을 확인했습니다. ArtCraft Team 및 PhotoCraft contributors의 프로젝트이며, 해당 버전은 [MIT](https://github.com/storytold/photocraft/blob/v0.15.1/LICENSE-MIT) 또는 [Apache-2.0](https://github.com/storytold/photocraft/blob/v0.15.1/LICENSE-APACHE) 라이선스를 선택할 수 있습니다. 여기서는 제작 도구 사용 사실을 밝히며 PhotoCraft 프로그램 자체를 패치에 동봉하지 않습니다.

**Cloud23333** — 로컬 플랫폼과 현지화 도구 관련 선행 작업. 무료 배포나 크레딧 표기를 재배포 허락으로 해석하지 않습니다. 플랫폼·현지화 도구 전체는 이 패치의 배포 대상으로 승인되지 않았습니다.

**FGOAC scooby contributors** — 프로젝트의 FARC 구현은 `Hanhua/Archive.cs`에서 파생되었다는 출처 기록이 있습니다. 명시적 MIT 적용 코드가 포함되는 경우 `Copyright (c) 2026 FGOAC scooby contributors`와 MIT 전문을 보존합니다. [상위 프로젝트](https://github.com/githubuser420x/FGOAC-scooby) · [라이선스](https://github.com/githubuser420x/FGOAC-scooby/blob/master/LICENSE). 이전 권리자 코드와 게임 자료의 권리는 별도입니다.

**Noto 기반 글꼴** — `FGOAC Korean Test`는 Noto CJK 한국어 글꼴에 기반한 파생 글꼴이며, Noto Sans·Symbols·Math 계열의 보완 및 고지 기록이 있습니다. 실제 사용 글리프와 각 출처의 고지를 보존합니다. 아래는 읽은 글꼴·OFL 자료의 권리 표기입니다.

- © 2014–2021 Adobe, Reserved Font Name: Source
- Copyright 2022 Google LLC. All Rights Reserved.
- Copyright 2022 The Noto Project Authors — latin-greek-cyrillic, symbols, math의 해당 고지

SIL OFL 1.1 전문을 동봉하고 파생 글꼴 명칭을 유지합니다. 글꼴 자체에 적용되는 OFL을 이미지·게임 전체의 재배포 허락으로 확장하지 않습니다. [OFL 공식 전문](https://openfontlicense.org/open-font-license-official-text/)

**Python Software Foundation 및 기여자** — Python 3.10.11 기반 실행 환경. 실제 포함한 Python 배포본의 `LICENSE.txt` 전체를 유지해야 합니다. 그 파일의 PSF·역사적 라이선스와 포함 구성요소의 개별 고지를 적용합니다. [v3.10.11 라이선스](https://github.com/python/cpython/blob/v3.10.11/LICENSE)

Python 구성 변경 요약: 기존 실행 파일·DLL을 복사하고, 표준 라이브러리 중 개발·시험용 일부 디렉터리를 제외하여 `python310.zip`으로 묶었습니다. 경로 설정 파일과 패치 실행 스크립트를 추가했습니다. Python 실행 파일·DLL과 포함된 표준 라이브러리 소스는 원본을 유지했습니다. [구성품별 고지](RUNTIME_COMPONENTS_KO.md)와 [런타임 파일 해시](RUNTIME_FILES.json)를 함께 제공합니다.

**Tcl/Tk 및 기여자** — Tkinter 설치 화면에 사용하는 런타임. 실제 제공 버전의 저작권 고지와 `license.terms` 전문 및 하위 구성요소 고지를 유지합니다. [공식 라이선스](https://www.tcl-lang.org/software/tcltk/license.html)

**Zig contributors / MinGW-w64 contributors** — 한국어 표시 보조 DLL의 빌드·런타임 구성. r002의 `engine/licenses`에 `Zig-MIT.txt`, `MinGW-w64-COPYING.txt`, `NATIVE_LABEL_NOTICE.txt`를 동봉했습니다. 설치 시 관리 도구에도 함께 복사됩니다. 소스 미동봉 구성에 맞춰 네이티브 DLL 안내 문구를 바로잡았습니다. 실제 링크 부분에 별도 라이선스가 있으면 그 조건도 적용합니다.

**Python에 포함된 추가 라이브러리와 Microsoft 런타임** — OpenSSL 1.1.1t, Tcl/Tk 8.6.12, SQLite 3.40.1 및 Python에 포함된 추가 라이브러리와 VC 런타임의 적용 고지를 [구성품 표](RUNTIME_COMPONENTS_KO.md)에 구분했습니다. Python 배포본 LICENSE의 Windows 추가 조건, Python 문서의 통합 구성품 고지, Tcl/Tk·XZ 고지를 함께 동봉하며 모든 구성품을 PSF나 MIT라고 표시하지 않습니다.

## 라이선스 전문 보관

이 저장소의 [licenses](../licenses/)에는 확인한 제3자 고지 전문을 검토용으로 보관합니다. 저장소에 고지를 추가하는 것과 최종 설치 ZIP에 고지를 동봉하는 것은 별개입니다. r001 ZIP에서 누락됐던 Zig·MinGW·네이티브 DLL 고지는 r002에 보완했습니다.

- [Noto 파생 글꼴 OFL](../licenses/NOTO-OFL.txt)
- [FGOAC scooby MIT](../licenses/FGOAC-scooby-MIT.txt)
- [Zig MIT](../licenses/Zig-MIT.txt)
- [MinGW-w64 COPYING](../licenses/MinGW-w64-COPYING.txt)
- [Python 3.10.11 배포본 LICENSE](../licenses/Python-3.10.11-LICENSE.txt)
- [Tk 8.6 license.terms](../licenses/Tk-8.6-license.terms)

도구의 출처·라이선스는 게임 번역·원작 이미지의 권리 허락을 대신하지 않습니다. 프로젝트 전체에 적용할 단일 라이선스를 새로 부여한 것이 아닙니다.
