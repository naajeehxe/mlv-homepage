# MLV Lab 홈페이지 빌더 (Notion → 정적 사이트)

Notion의 **"MLV Lab homepage DB"** 아래 10개 데이터베이스를 읽어서 홈페이지(정적 HTML 1개 + 이미지)를
자동으로 만들어 주는 스크립트입니다. GitHub Actions에 올려 두면 **6시간마다 자동으로 다시 빌드**되고,
Actions 탭의 **Run workflow** 버튼으로 즉시 반영할 수도 있습니다.

```
Notion DB ──(fetch_notion.py, Notion API)──▶ build/site_data.json + site/img/
          ──(render.py, template.html)─────▶ site/index.html   ──▶ GitHub Pages (hyunwoojkim.com)
```

## 폴더 구성

| 파일 | 역할 |
|---|---|
| `config.json` | 데이터베이스 ID, 이미지 크기 등 설정 |
| `fetch_notion.py` | Notion API로 10개 DB를 읽어 `build/site_data.json`과 이미지를 만듦 |
| `render.py` | `template.html`에 데이터를 넣어 `site/index.html` 생성 |
| `template.html` | 홈페이지 디자인(HTML/CSS/JS). 디자인 수정은 여기서 |
| `snapshot/` | 2026-09-02 기준 Google Sites에서 가져온 이미지 250장 + 데이터 스냅샷. Notion에 파일이 없을 때 대체용 |
| `.github/workflows/deploy.yml` | GitHub Actions: 스케줄/수동 빌드 + GitHub Pages 배포 |

## 처음 한 번만 하는 설정 (약 20분)

1. **Notion 통합(integration) 만들기**
   notion.so/profile/integrations → *New integration* → 워크스페이스 선택, 이름 `MLV homepage builder`,
   권한은 *Read content*만 → 생성 후 **Internal Integration Secret** 복사 (`ntn_…` 또는 `secret_…`).
2. **DB에 통합 연결하기**
   Notion에서 "MLV Lab homepage DB" 페이지 우측 상단 `…` → *Connections* → 방금 만든 통합 추가.
   (상위 페이지에 연결하면 아래 10개 DB에 모두 적용됩니다.)
3. **GitHub 저장소 만들기**
   이 폴더 전체를 새 저장소(예: `mlvlab/homepage`)에 push. Settings → Secrets and variables → Actions →
   *New repository secret*: 이름 `NOTION_TOKEN`, 값은 1번의 시크릿.
4. **GitHub Pages 켜기**
   Settings → Pages → Source: **GitHub Actions**. Actions 탭에서 *Build site from Notion and deploy* →
   *Run workflow*. 몇 분 뒤 `https://<계정>.github.io/<저장소>/`에서 확인.
5. **도메인 연결 (선택, 기존 사이트는 그대로 두고 나중에)**
   저장소 루트에 `CNAME` 파일을 만들고 `www.hyunwoojkim.com` 한 줄 작성 → Settings → Pages → Custom domain에 같은 값 입력.
   도메인 등록 업체(현재 Google Sites에 연결된 곳)에서 `www` CNAME 레코드를 `<계정>.github.io`로 바꾸면 전환 완료.
   바꾸기 전까지 Google Sites는 그대로 서비스되므로 안전합니다.

## 로컬에서 미리 보기

```bash
pip install -r requirements.txt
python render.py --snapshot          # Notion 없이 스냅샷으로 site/index.html 생성
open site/index.html                 # 브라우저로 열기

export NOTION_TOKEN=ntn_xxx          # 실제 Notion 데이터로 빌드
python fetch_notion.py && python render.py
```

## 평소 운영 방법

* 내용 수정은 **Notion에서만** 합니다. (논문 추가, 뉴스, 사람, 사진, 영상, 공고 문구…)
  → 최대 6시간 안에 자동 반영. 급하면 GitHub Actions → *Run workflow*.
* 행을 잠시 숨기고 싶으면 `Show on Site` 체크를 끕니다. 삭제하지 않아도 됩니다.
* 표시 순서는 `Order` 숫자입니다(Publications는 큰 숫자가 위, Photos/Highlights는 작은 숫자가 앞).
* **이미지**: 각 DB의 `… File` 필드(Photo File, Thumbnail File, Image File)에 파일을 직접 올리는 것을 권장합니다.
  Google Sites 이미지 주소(`… URL` 필드)는 만료될 수 있어서, 빌더는 `File → URL → snapshot/ 복사본` 순서로 사용합니다.
* 디자인/문구 배치를 바꾸려면 `template.html`을 수정하고 push하면 자동으로 재배포됩니다.

## 필드 이름이 바뀌었을 때

`fetch_notion.py`는 Notion 속성 이름으로 값을 읽습니다 (`prop(p, 'Paper Link')` 등).
Notion에서 열 이름을 바꾸면 스크립트의 해당 이름도 같이 바꿔 주세요. `Key`(Site Content)와 `Show on Site`, `Order`는 바꾸지 않는 것이 좋습니다.
