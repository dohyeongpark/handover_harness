# Java + Spring Boot

## 판별 신호

- `build.gradle`(또는 `build.gradle.kts`, `pom.xml`)에 플러그인/의존성으로
  `org.springframework.boot`가 있다.
- 매니페스트가 레포 루트에 없고 `backend/`, `server/` 같은 서브디렉터리에
  있으면 `stacks[].root`를 그 디렉터리로 잡는다. Gradle이면 `gradlew`가
  그 디렉터리에 함께 있는지로 서브프로젝트 루트인지 확인한다.
- `src/main/java`(또는 `src/main/kotlin`) 아래 `@SpringBootApplication`이
  붙은 클래스 하나가 있다 — 이 파일이 `manifest`가 아니라 `entrypoints`의
  `main`이다. `manifest`는 어디까지나 build.gradle/pom.xml.
- Gradle이면 `runtime_version`은 `build.gradle`의 `java { toolchain {
  languageVersion } }` 또는 `sourceCompatibility`에서, Maven이면
  `<properties><java.version>`에서 확인한다.

## 엔트리포인트

- `main` : `@SpringBootApplication` 클래스, `public static void main`.
- `http_route_root` : `@RestController` 또는 `@Controller`가 붙은 클래스.
  evidence는 클래스 선언부 + 클래스/메서드 레벨 `@RequestMapping`(또는
  `@GetMapping`/`@PostMapping` 등) 값. 클래스별로 하나씩 등록하고, 메서드
  단위 세부 라우트는 이 단계에서 나열하지 않는다 (api-contract-mapper의 몫).
- `scheduler` : `@Scheduled`가 붙은 메서드가 있는 클래스.
- `event_consumer` : `@KafkaListener`, `@RabbitListener`, `@EventListener`
  등이 붙은 메서드. 없으면 kind 자체를 생략한다.
- `cli` : Spring Boot 웹 프로젝트에는 보통 해당 없음. `CommandLineRunner`나
  `ApplicationRunner`를 구현한 빈이 있을 때만 등록한다.

## 모듈 경계

- 패키지 구조가 계층형(`controller/`, `service/`, `repository/`가 최상위)인지
  도메인형(`domain/<feature>/controller,service,repository`)인지 먼저
  확인한다 — 둘은 module 나누는 기준이 다르다.
  - 도메인형이면 `domain/<feature>` 디렉터리 하나가 module 하나.
  - 계층형이면 계층(`controller`/`service`/`repository`) 하나가 module
    하나이고, `depends_on`은 controller → service → repository 순서로 건다.
- 도메인 공통 로직을 모아둔 `global/`, `common/`, `config/` 패키지는 여러
  도메인 module이 공유하므로 독립 module로 등록하고, 그 도메인 module들이
  `depends_on`으로 가리키게 한다.
- 외부 연동 어댑터(`infra/`, `client/` 등 — 결제사, AI 서버 등 외부 API를
  감싼 클래스)는 그 자체로 module이며, `external_deps`와 연결되는 지점이다.

## 외부 의존성 설정 위치

- `src/main/resources/application.properties`(또는 `.yml`)와 프로파일별
  파일(`application-{profile}.properties`). `spring.profiles.active` 값으로
  어느 프로파일이 기본인지 확인하고, dev/prod 등 프로파일마다 값이 다르면
  각각 별도 evidence로 남긴다 (하나만 남기면 다른 프로파일의 의존성을
  놓친다).
- `spring.datasource.*` → `kind: database`. 드라이버 클래스명으로 실제
  제품(MySQL/PostgreSQL/H2 등)을 특정한다.
- `${ENV_VAR}` 형태로 값이 치환되는 키는 실제 값이 아니라 환경변수
  이름으로만 알 수 있다 — 그 환경변수의 실제 값이 어디 있는지는
  이 단계에서 모른다면 `unknowns`에 남긴다 (env-builder가 이어받는다).
- `spring.kafka.*`, `spring.data.redis.*`, `spring.cloud.aws.*` 처럼
  `spring.<모듈>.*` 접두사 프로퍼티가 있으면 그 모듈에 대응하는 외부
  의존성(queue/cache/storage)이 있다는 신호다.
- 외부 API URL이 커스텀 프로퍼티 키(`*.service.url`, `*.api.key` 등)로
  있으면 `kind: http_api`.

## 빌드/실행/테스트 명령 확인 위치

- Gradle: `./gradlew build`(build), `./gradlew bootRun`(run),
  `./gradlew test`(test)가 기본값이지만, `build.gradle`에 커스텀 태스크나
  `bootRun { args ... }` 설정이 있으면 그것을 우선한다.
- Maven: `mvn package`(build), `mvn spring-boot:run`(run), `mvn test`(test).
- `Dockerfile`이 있으면 컨테이너 기동 명령(`ENTRYPOINT`/`CMD`)도
  run_commands 후보다 — 특히 `-Dspring.profiles.active=` 같은 프로파일
  지정이 여기서만 드러나는 경우가 있으므로 `application.properties`의
  `spring.profiles.active`와 다르면 둘 다 남긴다.

## API 계약 단서

### caller: 클라이언트 인스턴스와 baseURL

이 스택이 caller가 되는 것은 다른 서비스를 호출할 때다 (`RestClient`,
`WebClient`, `RestTemplate`의 빈 정의, `@FeignClient`, `@HttpExchange`
인터페이스). baseURL은 빌더의 `.baseUrl(...)`이나 어노테이션의 `url`
속성에 있고, 대개 `${...}` 프로퍼티 치환이다. 현재 api-contract-mapper의
범위(프론트→백)에서는 수집하지 않는다.

### caller: 호출부

위와 같은 이유로 현재 범위에서는 해당 없음.

### callee: 라우트 정의

- 컨트롤러 클래스: `@RestController` 또는 `@Controller`가 붙은 클래스.
  라우트의 전체 경로는 **세 단계를 이어 붙인 것**이다:
  1. 전역 접두 경로 — `application.properties`/`.yml`의
     `server.servlet.context-path`, `spring.mvc.servlet.path`
     (WebFlux면 `spring.webflux.base-path`). 프로파일별 파일에서 값이
     다를 수 있다. `WebMvcConfigurer`의 `configurePathMatch`에서
     `addPathPrefix(...)`로 특정 컨트롤러 묶음에만 접두 경로를 붙이는
     경우도 있다. 없으면 접두 경로 없음.
  2. 클래스 레벨 `@RequestMapping("/...")` — 없을 수 있다.
  3. 메서드 레벨 `@GetMapping`, `@PostMapping`, `@PutMapping`,
     `@PatchMapping`, `@DeleteMapping`, `@RequestMapping`.
- 메서드 레벨 어노테이션에 경로가 없으면(`@PostMapping` 단독) 클래스 레벨
  경로가 곧 그 라우트의 경로다. 놓치기 쉽다.
- 경로는 `value` 또는 `path` 속성, 혹은 속성 이름 없이 첫 인자로 적힌다.
  배열(`{"/a", "/b"}`)이면 경로마다 라우트가 하나씩이다.
- HTTP 메서드: `@GetMapping` 등은 이름이 곧 메서드. `@RequestMapping`은
  `method = RequestMethod.X` 속성을 보고, 속성이 없으면 메서드 제한이
  없는 라우트(`ANY`)다.
- 경로가 문자열 상수(`ApiPaths.USERS`, `BASE + "/login"`)로 적혀 있으면
  상수 정의를 따라가 확정한다.
- 어노테이션이 컨트롤러가 구현하는 **인터페이스**에 붙어 있는 경우가 있다
  (API 스펙 인터페이스, OpenAPI 생성 코드). 컨트롤러 클래스에 매핑
  어노테이션이 안 보이면 `implements` 대상을 확인한다. evidence는 매핑이
  실제로 적힌 줄, `handler_symbol`은 구현 클래스의 메서드.
- `handler_symbol`은 `클래스명.메서드명`.
- 컨트롤러가 아닌 곳에서 생기는 라우트 — **프론트가 부르는데 컨트롤러에
  없다고 바로 caller_only로 단정하지 말고 여기를 먼저 확인한다:**
  - 보안 필터가 처리하는 경로: `SecurityFilterChain` 설정의
    `formLogin().loginProcessingUrl(...)`, `logout().logoutUrl(...)`,
    OAuth2 로그인의 `/oauth2/authorization/{registrationId}`와
    `/login/oauth2/code/{registrationId}`, 그리고 특정 경로에 반응하는
    커스텀 필터(`OncePerRequestFilter` 구현체, `AntPathRequestMatcher`).
    컨트롤러 없이 요청을 처리한다. callee로 등록하고 `callee.note`에
    "필터가 처리"라고 적는다.
  - 함수형 라우트: `RouterFunction<ServerResponse>` 빈에서
    `route().GET("/...", handler)` 형태.
  - Actuator: `spring-boot-starter-actuator` 의존성이 있으면
    `management.endpoints.web.base-path`(기본 `/actuator`) 아래 경로가
    생긴다. 노출 범위는 `management.endpoints.web.exposure.include`.
  - Spring Data REST: `spring-boot-starter-data-rest` 의존성과
    `@RepositoryRestResource`가 있으면 리포지토리에서 라우트가 자동
    생성된다. 정적으로 전부 나열하기 어려우니 `unknowns`에 남긴다.
  - 정적 리소스·뷰 컨트롤러(`addViewControllers`,
    `addResourceHandlers`)는 API 계약이 아니다.
- `@MessageMapping`(STOMP/WebSocket)은 HTTP 라우트가 아니다. 계약으로
  세지 않되, 프론트에 대응하는 호출부가 있으면 `unknowns`에 남긴다.

### 경로 파라미터와 쿼리 표기

- 경로 파라미터는 `{name}` 플레이스홀더다: `/users/{id}` → `/users/{}`.
  정규식이 붙은 형태 `{id:[0-9]+}`도 같은 파라미터 하나다 (`name_callee`는
  `id`).
- `name_callee`는 플레이스홀더 안의 이름을 쓴다. 핸들러 인자의
  `@PathVariable("id") Long userId`에서 변수 이름이 다르더라도
  플레이스홀더 이름이 기준이다.
- `*`, `**` 와일드카드가 들어간 경로(`/files/**`)는 단순 비교가 안 된다.
  접두 경로가 일치하는 호출부와는 `inferred`로 맞추고 note에 남긴다.
- 쿼리 파라미터는 핸들러 인자의 `@RequestParam`에서 찾는다. 이름은
  `value`/`name` 속성, 없으면 인자 이름. `required = false`나
  `defaultValue`가 있으면 선택 파라미터이니 `note`에 적는다.
- 어노테이션 없는 객체 인자나 `@ModelAttribute` 객체(검색 조건 DTO)는
  그 클래스의 필드가 쿼리 파라미터로 바인딩된다. `Pageable` 인자는
  `page`, `size`, `sort`를 받는다.
- `@RequestMapping(params = "...")`이나 `headers`/`consumes`/`produces`
  조건으로 같은 메서드·경로에 핸들러가 여럿일 수 있다. 호출부가 어느
  쪽에 해당하는지 확정되지 않으면 `ambiguous`다.

## 서비스 기능 단서

### 사용자 행동 단서

- 백엔드는 사용자 행동을 직접 보지 못하고 컨트롤러 메서드(핸들러)로만
  본다. 기능 경계는 프론트 화면 쪽 단서(프론트 플레이북)를 우선하고,
  백엔드 쪽은 "같은 기능에 속하는 핸들러를 묶는 근거"로 쓴다:
  - 한 컨트롤러 메서드가 호출하는 서비스 메서드(`@Service`)가 같으면 같은
    기능일 가능성이 높다.
  - 같은 서비스 메서드를 여러 핸들러가 공유하면(예: 관리자용/사용자용)
    별도 기능으로 쪼갤지 `grouping_note`에 판단 근거를 남긴다.
- 컨트롤러 메서드 하나가 곧 기능 하나는 아니다. 검증 요청(`/check-*`),
  목록 조회와 상세 조회처럼 같은 행동의 부분인 핸들러를 묶는다.
- 인증·인가 필터와 `@PreAuthorize`/`@Secured`는 기능이 아니라 접근 제어다.
  별도 기능으로 만들지 않는다.

### 사용자 행동이 아닌 시작점

화면이 부르지 않는 기능의 시작점은 아래와 같고, 각각 `trigger` 값이 정해진다.

- `@Scheduled` 메서드 → `trigger: scheduled`. 크론/고정 주기 표현식을 summary에
  적는다.
- `@KafkaListener`, `@RabbitListener`, `@EventListener`, `@TransactionalEventListener`
  메서드 → `trigger: event`.
- 컨트롤러 핸들러인데 api_contracts에서 caller가 없는 것(`callee_only`) →
  `trigger: external_call`. 외부 시스템이 부르는 웹훅·콜백일 수 있다
  (경로에 `webhook`, `callback`, `notify` 등이 들어 있으면 단서).
- `CommandLineRunner`/`ApplicationRunner` 빈 → 기동 시 한 번 도는 초기화.
  기능으로 볼지 애매하면 기능으로 만들지 말고 `unknowns`에 남긴다.
