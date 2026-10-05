# TypeScript + React + Vite

## 판별 신호

- `package.json`의 `devDependencies`에 `vite`와 `@vitejs/plugin-react`(또는
  `@vitejs/plugin-react-swc`)가 있다.
- `dependencies`에 `react`, `react-dom`.
- 레포 루트 또는 서브디렉터리(모노레포의 `frontend/`, `web/` 등)에
  `vite.config.ts`(또는 `.js`)가 있다. 매니페스트가 레포 루트에 없으면
  `stacks[].root`를 그 서브디렉터리로 잡는다.
- `tsconfig.json`이 있으면 TypeScript, 없고 `.jsx`만 있으면 JavaScript로
  기록한다 (language 필드).
- CRA(`react-scripts`)나 Next.js(`next` 의존성 + `next.config.*`)는 다른
  판별 신호이므로 이 플레이북으로 다루지 않는다.

## 엔트리포인트

- `main` : `vite.config.ts`의 `build.rollupOptions.input` 또는 프로젝트
  루트의 `index.html`이 가리키는 `<script type="module" src="...">` 파일
  (보통 `src/main.tsx`).
- `screen_router` : 라우팅 라이브러리 설정 지점.
  - `react-router-dom` v6 이하: `<Routes>`/`<Route>` JSX가 있는 파일.
  - `react-router-dom` v6.4+/v7 data router: `createBrowserRouter(...)` 호출부.
  - 라우팅 라이브러리가 없으면 (SPA 아님) 이 kind는 생략하고 `unknowns`에
    "라우팅 방식 미확인"으로 남긴다.
- API 호출부(백엔드와 짝지을 지점)는 이 단계의 entrypoint가 아니다.
  `axios.create(...)`나 `fetch` 래퍼가 있는 파일 경로만 `modules`의 `role`에
  적어두면, 뒤 단계의 api-contract-mapper가 그 파일부터 찾는다.

## 모듈 경계

- 관례상 `src/pages`(또는 `src/routes`, `src/views`)가 화면 단위 모듈이다.
  각 하위 디렉터리 또는 각 파일 하나를 module로 등록한다.
- `src/components`는 화면에 종속되지 않은 공용 컴포넌트면 별도 module로,
  특정 화면 전용이면 그 화면 module에 포함시키고 별도로 만들지 않는다.
- `src/store`(Redux/Zustand 등 전역 상태)와 `src/services`(API 클라이언트)는
  여러 화면이 공유하므로 독립 module로 등록하고, 화면 module들이
  `depends_on`으로 가리키게 한다.
- `src/utils`, `src/types`처럼 순수 유틸/타입 전용 디렉터리는 module로
  등록하지 않는다 (의존 관계 그래프에 노이즈만 추가한다). 필요하면
  개별 파일을 다른 module의 evidence로만 인용한다.

## 외부 의존성 설정 위치

- `.env`, `.env.production`, `.env.development` 등의 `VITE_` 접두사 변수.
  Vite는 `VITE_`로 시작하는 변수만 클라이언트에 노출하므로, 이 접두사가
  붙지 않은 변수는 빌드 도구(서버 사이드) 설정으로 분류한다.
- API 베이스 URL이 `.env`에 있으면 `kind: http_api`, config_evidence는
  그 `.env` 라인.
- `vercel.json`/`netlify.toml` 등의 배포 설정에 있는 `rewrites`/`redirects`가
  실제 백엔드 주소를 담고 있는 경우가 많다 — `.env`보다 이쪽이 진짜 값일 수
  있으니 둘 다 확인하고 다르면 `unknowns`에 남긴다.

## 빌드/실행/테스트 명령 확인 위치

- `package.json`의 `scripts` 객체. 보통 `dev`(run), `build`(build),
  `preview`(run, 프로덕션 빌드 미리보기), `lint`/`test`(test)로 매핑한다.
  스크립트가 없는 명령 종류는 비워두고 unknowns에 넣지 않는다 (없는 게
  정상일 수 있음 — 프론트에 test script가 없는 경우가 흔하다).
- `package-lock.json`이 있으면 `package_manager: npm`, `yarn.lock`이면
  `yarn`, `pnpm-lock.yaml`이면 `pnpm`.

## API 계약 단서

### caller: 클라이언트 인스턴스와 baseURL

- 인스턴스 정의부를 먼저 전부 찾는다. 검색 패턴:
  - `axios.create(` — 가장 흔하다. 반환값을 담은 변수/export 이름이
    인스턴스 이름이다.
  - `ky.create(`, `ky.extend(`, `wretch(` 등 다른 클라이언트 라이브러리의
    팩토리 호출. `package.json`의 `dependencies`에서 어떤 HTTP 라이브러리를
    쓰는지 먼저 확인하면 검색 패턴이 정해진다.
  - `fetch`를 감싼 자체 래퍼 함수 (`fetch(` 호출을 포함하고 경로 인자를
    받는 함수). 래퍼 안에서 접두 경로를 붙이면 그게 baseURL이다.
  - OpenAPI 등에서 생성된 클라이언트 (`generated/`, `__generated__/`,
    `openapi/` 디렉터리, 파일 머리의 "auto-generated" 주석). 생성 코드의
    기본 경로 설정(`BASE`, `basePath`, `baseUrl` 필드)이 baseURL이다.
- 인스턴스는 보통 `src/api`, `src/services`, `src/lib`, `src/utils` 아래에
  모여 있지만, 서비스(도메인)별 파일마다 인스턴스를 따로 만드는 패턴도
  흔하다. 디렉터리를 가정하지 말고 스택 루트 전체에서 검색한다.
- 인스턴스 없이 `axios.get(...)`(기본 인스턴스)이나 `fetch(...)`를 직접
  부르는 코드는 암묵적 클라이언트로 등록한다. 기본 인스턴스에
  `axios.defaults.baseURL = ...`을 대입하는 코드가 있으면 그 값이
  baseURL이다.
- baseURL이 적히는 형태:
  - 문자열 리터럴: `baseURL: '/api/auth'` → 그대로 확정.
  - 환경변수: `import.meta.env.VITE_*` (구버전 코드에는 `process.env.*`).
    값은 스택 루트의 `.env`, `.env.development`, `.env.production`,
    `.env.local`에서 찾는다. 커밋되지 않은 파일(`.gitignore` 대상)에만
    있으면 값을 알 수 없는 것이다.
  - 환경변수 + 경로 조합: `` `${import.meta.env.VITE_API_URL}/auth` ``,
    `VITE_API_URL + '/auth'`. 환경변수 값을 모르면 전체가 미확정이지만,
    뒤에 붙는 고정 경로(`/auth`)는 `base_url_note`에 적어 둔다.
  - 기본값이 붙은 형태: `import.meta.env.VITE_API_URL || 'http://localhost:8080'`,
    `?? '/api'`. 기본값은 확정값이 아니다.
  - 상수/설정 모듈 참조: `baseURL: API_BASE_URL`, `config.apiUrl`. 정의를
    따라간다.
- 경로를 바꾸는 것:
  - 개발 프록시: `vite.config.*`의 `server.proxy`. 키(예: `'/api'`)는
    프록시 대상 접두 경로이고, `rewrite: (path) => path.replace(...)`가
    있으면 백엔드에 도달하는 경로가 호출부와 달라진다 (예: `/api`를
    떼고 전달). `rewrite`가 없으면 경로는 그대로다. `modifiers`에
    `kind: proxy`로 기록한다. 배포 설정(`vercel.json`/`netlify.toml`의
    `rewrites`, nginx 설정)에 같은 종류의 재작성이 있을 수 있고 개발
    프록시와 다르면 `unknowns`에 남긴다.
  - 인터셉터: `<인스턴스>.interceptors.request.use(`. 대부분 인증 헤더만
    붙이지만 `config.url`이나 `config.baseURL`을 건드리면 경로가 바뀐다.
    `interceptors.response.use(` 안에서 토큰 재발급 엔드포인트를 직접
    호출하는 경우가 많다 — 이것도 호출부다.
  - 공통 래퍼: 인스턴스를 받아 `get/post`를 다시 내보내는 함수나 훅.
    호출부 검색 때 래퍼 이름으로도 찾아야 한다.

### caller: 호출부

- 인스턴스 이름으로 검색한다: `<인스턴스>.get(`, `.post(`, `.put(`,
  `.patch(`, `.delete(`, `.request(`, 그리고 함수처럼 부르는
  `<인스턴스>({ url, method })`. 메서드 이름이 HTTP 메서드다.
  `.request(`와 함수 호출 형태는 `method` 옵션을 보고, 옵션이 없으면 GET.
- `fetch(url, { method })`는 `method`가 없으면 GET.
- 인스턴스가 다른 이름으로 import되는 경우(`import api from`, `import
  { client as http }`)가 있으니 인스턴스 정의 파일을 import하는 파일을
  먼저 모으고 그 안에서 호출을 찾는다.
- 호출부는 보통 API 모듈 함수 안에 있고 컴포넌트는 그 함수를 부른다.
  계약의 evidence는 **HTTP 호출이 일어나는 줄**이다 (그 함수를 쓰는
  컴포넌트나 `useQuery`/`useMutation` 줄이 아니다).
- 경로가 상수 객체에 모여 있는 패턴(`ENDPOINTS.LOGIN`, `API_PATHS.user(id)`)은
  그 정의를 따라가 경로를 확정한다.
- `EventSource(`, `WebSocket(`, `navigator.sendBeacon(`, `<form action>`,
  `<a href>`/`window.location`으로 백엔드 경로에 직접 가는 것(파일
  다운로드, OAuth 리다이렉트 시작점)도 호출부다. 인스턴스를 거치지
  않으므로 baseURL이 붙지 않는다는 점에 주의한다.

### callee: 라우트 정의

해당 없음. 이 스택은 HTTP 라우트를 제공하지 않는다 (화면 라우팅은
`screen_router`이고 API 계약의 callee가 아니다).

### 경로 파라미터와 쿼리 표기

- 경로 파라미터는 템플릿 리터럴의 `${...}`로 들어간다:
  `` `/users/${id}` `` → `/users/{}`. 문자열 연결(`'/users/' + id`)도 같다.
  `name_caller`에는 끼워 넣은 식을 그대로 적는다 (`id`, `user.id`).
- 세그먼트 전체가 아니라 일부만 끼워 넣거나(`` `/files/${name}.pdf` ``),
  끼워 넣는 값이 경로 여러 세그먼트일 수 있는 경우(`` `/${path}` ``)는
  단순 치환이 아니다. `inferred`로 다루거나 `unknowns`에 남긴다.
- 쿼리 스트링이 붙는 형태: 옵션 객체의 `params: { ... }`(axios),
  `searchParams: { ... }`(ky), 경로 문자열 안의 `?key=${v}`,
  `new URLSearchParams({ ... })`를 경로 뒤에 붙이는 코드. 키 이름을
  `query_params`에 옮긴다. `params`에 변수 하나를 통째로 넘기면
  (`params: filter`) 키를 알 수 없으니 `note`에 그렇게 적는다.
