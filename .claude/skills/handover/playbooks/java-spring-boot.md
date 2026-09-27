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
