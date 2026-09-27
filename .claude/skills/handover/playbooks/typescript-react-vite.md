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
