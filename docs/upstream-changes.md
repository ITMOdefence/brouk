# Что нужно исправить в существующем коде PUNos

> **Ни одна из описанных здесь правок не внесена.** Это перечень предложений к
> обсуждению с владельцами компонентов: репозиторий PUNos и репозитории
> сервисов этим документом не изменяются. Каждая правка затрагивает чужой
> контракт, поэтому вносить её можно только после согласования — порядок
> согласования описан в [`roadmap.md`](roadmap.md).

Брокер нельзя добавить «сбоку»: он требует изменений в четырёх чужих
компонентах. Ниже — каждое изменение с путём, причиной и признаком готовности.
Ссылки на строки соответствуют ветке `main` репозитория
[ITMOdefence/PUNos](https://github.com/ITMOdefence/PUNos) на 28 октября 2026
года.

Пометки: **BLOCKER** — без этого брокер не работает; **FIX** — существующий
дефект, который брокер делает опасным; **HARDENING** — усиление, не входящее
в v1.

| № | Компонент | Изменение | Класс |
| --- | --- | --- | --- |
| [U1](#u1) | `appserv` | Генерация `MessagingToken`, control-клиент `msgserv`, передача токена в environment | BLOCKER |
| [U2](#u2) | `appserv` | `instance_id` назначается до `process_start` | BLOCKER |
| [U3](#u3) | `appserv` | `control.instance_release` при обнаружении завершения | BLOCKER |
| [U4](#u4) | `appserv` | Привязка экземпляра к создавшему соединению | FIX |
| [U5](#u5) | `initserv` | `MsgControlToken`, регистрация `msgserv`, порядок и readiness | BLOCKER |
| [U6](#u6) | `appframework` | Модуль `messaging` и приём незапрошенных кадров | BLOCKER |
| [U7](#u7) | `PlatformRegistry` + генераторы | Секция `messaging`, согласованно в двух репозиториях | BLOCKER |
| [U8](#u8) | CI, initramfs, `/init-smoke` | Сборка, проверка и загрузочный smoke для `msgserv` | BLOCKER |
| [U9](#u9) | `appserv` | Список экземпляров не сокращается никогда | FIX |
| [U10](#u10) | `ipclib`, `libprocess`, `constants.h` | `SO_PEERCRED` и PID дочернего процесса | HARDENING |
| [U11](#u11) | `ipclib` | Безусловный `unlink()` чужого endpoint'а | HARDENING |
| [U12](#u12) | `appserv`, `initserv`, `logserv` (отдельные репозитории) | Обновление `vendor/PUNos` и `punos.ref` | BLOCKER |

---

## U1

**`appserv`: генерация токена, control-соединение с брокером, передача токена
ребёнку.** BLOCKER

Файлы: `System/SystemServices/appserv/src/service.c`,
`src/main.c`, `include/appserv/appserv.h`, `Makefile`.

Что сделать:

1. В `AppService` добавить control-клиента `msgserv` — по образцу уже
   существующего `AppLogger` (`src/logger.h`), который делает то же самое для
   `logserv`: неблокирующее клиентское соединение, `EventSourceId` в общем
   event loop'е, флаг `connected`, счётчик `outstanding`. Это готовый шаблон,
   изобретать ничего не нужно.
2. В `launch()` (`src/service.c:103`) до `process_start` сгенерировать
   `MessagingToken` через `random_fill()` и отправить
   `control.instance_register`.
3. Собрать для ребёнка environment: взять `app->environment` и добавить
   `PUNOS_MESSAGING_TOKEN=<64 hex-символа>`. `ProcessLaunch.environment` —
   массив `String` с отдельным `environment_count`
   (`System/SystemLibs/libprocess/include/sys/process.h:42`), поэтому нужна
   выделенная на время запуска копия массива длиной `count + 1`. Освободить
   её сразу после `process_start`: `libprocess` требует, чтобы строки были
   валидны только во время вызова.
4. В `Makefile` добавить `librandom` в зависимости.

Важное решение: **если `control.instance_register` не удался, `app.launch`
возвращает отказ и процесс не запускается.** Приложение без личности в брокере
не сможет ни подписаться, ни опубликовать, а молча запустить его хуже, чем
честно отказать. Это меняет контракт `app.launch`: появляется новая причина
отказа, не связанная с `process_start`.

Готово, когда: `app.launch` при недоступном `msgserv` возвращает ошибку и не
оставляет процесса-сироты; при доступном — ребёнок успешно выполняет bind.

## U2

**`appserv`: `instance_id` должен назначаться до `process_start`.** BLOCKER

Файл: `System/SystemServices/appserv/src/service.c:116` и `:123`.

Сейчас порядок такой:

```c
error = process_start(&spec, &item->process);   /* строка 116 */
if (error != CORE_OK) { ... }
item->id = service->next_id++;                  /* строка 123 */
```

То есть идентификатор появляется **после** запуска процесса. Для брокера это
гонка: регистрировать экземпляр нужно до `process_start` (иначе ребёнок
успеет подключиться и получит `CORE_PERMISSION_DENIED`), а в регистрации уже
нужен `instance_id`.

Что сделать: назначить `item->id = service->next_id++` до `process_start`.
Это безопасно — `next_id` просто счётчик, а контракт и так гарантирует лишь
уникальность в пределах запуска сервиса, без непрерывности. Неудачный запуск
израсходует один идентификатор; это допустимо и даже полезно: сейчас
`record(service, "launch_failed", item, error)` (`:119`) журналирует
`launch_failed` с `id == 0`, то есть отказы запуска в журнале неразличимы.
После правки у каждого отказа будет свой идентификатор.

При неудачном `process_start` необходимо отменить регистрацию в брокере
(`control.instance_release`), иначе в `InstanceTable` останется запись с
токеном, которым никто не воспользуется.

Готово, когда: тест на отказ `process_start` подтверждает, что запись в
брокере снята, а идентификаторы в журнале различимы.

## U3

**`appserv`: освобождение экземпляра в брокере.** BLOCKER

Файл: `System/SystemServices/appserv/src/service.c:201` (`app_service_poll`),
плюс `app_service_shutdown`.

В `app_service_poll()` при `finished == true` сейчас выполняется только
`item->state = APP_EXITED; record(...)`. Нужно добавить отправку
`control.instance_release` — иначе `InstanceTable` брокера будет расти всё
время работы системы, а токен завершённого экземпляра останется действующим.

Учесть: брокер узнаёт о смерти приложения **раньше** — соединение рвётся
мгновенно, а `process_poll` обнаружит завершение только на следующем тике,
до 1000 мс позже. Поэтому `control.instance_release` для уже снятого
экземпляра обязан отвечать `CORE_OK`, а не `CORE_NOT_FOUND`: порядок не
определён, обе операции идемпотентны (см.
[`wire-protocol.md`](wire-protocol.md#63-завершение-экземпляра)).

## U4

**`appserv`: экземпляр не привязан к создавшему соединению.** FIX

Файлы: `System/SystemServices/appserv/src/service.c:4` (`struct AppInstance`),
`:68` (`select_instance`).

```c
struct AppInstance {
    MemoryBlock allocation;
    AppInstance *next;
    const NativeAppSpec *app;
    AppInstanceId id;
    ProcessHandle process;
    AppState state;
    ProcessExitStatus status;
};
```

Ссылки на `IpcConnectionHandle` здесь нет: кто запустил экземпляр, нигде не
записано. `select_instance()` ищет экземпляр **только по `id`**, без всякой
проверки, что запрос пришёл от того же клиента. Любой доверенный клиент,
знающий `instance_id`, останавливает любой экземпляр, а `id` предсказуем —
`next_id++` от единицы.

Сейчас это полутеоретическая проблема: угадывать `instance_id` надо
перебором. **Брокер делает её практической**: поле `sender_instance_id` в
каждом `event` раздаёт настоящие идентификаторы всем подписчикам topic'а.
Приложение, получившее событие, сразу знает `instance_id` отправителя и может
его остановить через `appserv`.

Что сделать: добавить в `AppInstance` владельца (соединение, создавшее
экземпляр) и требовать его совпадения в `app.stop`. Для системной оболочки
оставить право останавливать любой экземпляр — по токену, как это сделано для
`ShellToken` в GUI-слое, а не по «доверенности» клиента.

Это изменение контракта `appserv`, поэтому его нужно согласовать с владельцем
сервиса отдельно. Без него брокер технически работает, но открывает
приложениям способ гасить друг друга.

## U5

**`initserv`: токен, регистрация сервиса, порядок и готовность.** BLOCKER

Файлы: `System/SystemServices/initserv/src/main.c`,
`tools/generate_registry.py`, `config/platform_registry.json`.

1. Сгенерировать `MsgControlToken` (32 байта, `random_fill`) один раз на
   загрузочную сессию и передать его в environment и `msgserv`, и `appserv`.
   Шаблон — `PresenterToken` и `ControllerToken` из GUI-слоя: один токен на
   пару взаимодействующих компонентов.
2. Добавить `msgserv` в `PlatformRegistry` и в известный порядок запуска.
   Сейчас порядок — `logserv`, `appserv`, `IOserv`, `locationserv`, `camserv`;
   нужен `logserv`, **`msgserv`**, `appserv`, далее остальные: `appserv` при
   первом же `app.launch` обязан дотянуться до брокера.
3. Готовность определять не успешным `process_start`, а созданным endpoint'ом,
   как это уже сделано для `logserv` (`initserv` подключается к его endpoint'у
   и только потом запускает остальных). Таймаут — по образцу
   `MSG_BIND_TIMEOUT_MS` = 5000 мс, как у `logserv`.
4. Решить, фатален ли отказ `msgserv`. Для `logserv` отказ фатален. Предложение:
   **не фатален** — система должна грузиться без обмена сообщениями,
   приложения получат `CORE_UNSUPPORTED` на `app_msg_*`. Это решение владельца
   `initserv`; оно влияет на контракт 8 (перезапуск только по запросу реестра).

## U6

**`appframework`: модуль `messaging` и приём незапрошенных кадров.** BLOCKER

Файлы: `System/ApplicationLayer/appframework/include/appframework/app.h`,
`src/` (новый `messaging.c`), `src/internal.h`, `Makefile`.

Полное описание API — в
[`appframework-messaging.md`](appframework-messaging.md). Главное, что это не
копия модуля `applications`: существующие клиенты только ждут ответ на свой
запрос, а брокер присылает `event`, `deliver_request` и `deliver_response` в
любой момент. Нужны два разных механизма обратного вызова — callback'и
запросов и handler'ы незапрошенных кадров.

В `Makefile` зависимости не меняются: `SPECIAL_DEPENDENCIES := ipclib` уже
покрывает брокер, потому что `msgserv` — такой же IPC-сервис, как `logserv`.

Готово, когда: приложение получает событие, опубликованное другим
приложением, и отвечает на запрос третьего.

## U7

**`PlatformRegistry` и генераторы в двух репозиториях одновременно.** BLOCKER

Файлы: `System/SystemServices/appserv/tools/generate_catalog.py`,
`System/SystemServices/initserv/tools/generate_registry.py`,
`System/SystemServices/initserv/tools/generate_catalog.py`, схемы
`config/platform_registry.json` в обоих местах, плюс новый генератор
`MessagingSpec` для `msgserv`.

Ловушка здесь неочевидная. По спецификации `initserv` его генератор
«генерирует **тот же** `PlatformRegistryId` и каталог нативных приложений,
что и `appserv`», а сборка «требует точного совпадения `application_id` и
`executable` в обоих представлениях». Значит, добавление секции `messaging` в
реестр обязано быть внесено в **оба** генератора согласованно: если один
начнёт учитывать новое поле при вычислении `PlatformRegistryId`, а второй нет,
идентификаторы разойдутся и **ни один сервис не запустится** — проверка
`PlatformRegistryId` выполняется до создания endpoint'а.

Рекомендация: не включать секцию `messaging` в вычисление
`PlatformRegistryId` до того, как оба генератора обновлены в одном
коммите. Схема поля — в
[`identity-and-permissions.md`](identity-and-permissions.md#5-права-статический-каталог-из-platformregistry).

## U8

**Сборка, образ и загрузочная проверка.** BLOCKER

Файлы: `.github/workflows/ci.yml`, `System/scripts/build-initramfs.sh`,
`System/Boot/ServicesProbe/`, `System/SystemServices/Makefile`.

1. Добавить `msgserv` в сборку `System/SystemServices` и в initramfs. Образ
   обязан остаться **воспроизводимым**: отдельный CI-джоб собирает initramfs
   дважды и сравнивает результат, поэтому в артефакты не должно попадать
   ничего зависящего от времени или порядка файловой системы.
2. Добавить шаг тестов `msgserv` в джоб `Host tests` (рядом с «System
   Services and device integration») и в «Application Layer regression
   mutation checks».
3. Расширить `/init-smoke`: сейчас `ServicesProbe` проверяет доступность
   `appserv` по IPC. Добавить проверку `msgserv`: bind контрольным токеном,
   публикация в тестовый topic, приём события вторым соединением.
4. QEMU-джоб: убедиться, что обычная загрузка печатает готовность `msgserv`
   до запуска `appserv`.

## U9

**`appserv`: список экземпляров не сокращается никогда.** FIX

Файл: `System/SystemServices/appserv/src/service.c:201`.

В `app_service_poll()` завершённый экземпляр получает `state = APP_EXITED` и
остаётся в односвязном списке до остановки сервиса. Освобождается память
только в `app_service_shutdown()`. Контракт 8 действительно требует сохранять
`Exited` и `ProcessExitStatus` «до окончания текущего запуска appserv», чтобы
`app.get_status` мог вернуть результат, — но ограничения на число экземпляров
нет ни в спецификации, ни в коде: каталог ограничен 64 записями, а `instances`
растёт на одну `memory_allocate` за каждый `app.launch`.

Цикл запусков упирается только в память. Брокер добавляет к каждому экземпляру
ещё и запись в `InstanceTable`, то есть удваивает цену утечки.

Что сделать: ввести предел числа хранимых завершённых экземпляров (например,
64 последних, как размер каталога) с вытеснением самых старых, либо явно
освобождать `Exited` после первого успешного `app.get_status`. Первое проще и
не меняет наблюдаемое поведение в типовом сценарии.

## U10

**`SO_PEERCRED` и PID дочернего процесса.** HARDENING, не входит в v1

Файлы: `System/NativeRuntime/SyscallWrappers/include/punos/syscall/constants.h`,
`System/SpecialLibs/ipclib/`, `System/SystemLibs/libprocess/`.

Что уже есть: обёртка `getsockopt()` общая, не захардкожена под один option
(`syscall.h:57`), и `ipclib` её легально использует для `SO_ERROR`
(`src/ipc.c:318`).

Чего нет: константы `SO_PEERCRED` (в `constants.h` только `SOL_SOCKET` = 1,
`SO_REUSEADDR` = 2, `SO_ERROR` = 4), операции `ipclib` для получения
credentials, и — главное — способа узнать PID дочернего процесса:
`libprocess` даёт только `process_current_id()` для своего собственного PID
(`process.h:80`), а `ProcessHandle` непрозрачен.

Зачем: брокер мог бы сверять PID пира с PID, о котором сообщил `appserv`, —
тогда украденный токен не сработает из другого процесса. В v1 не делаем:
цена — правки в трёх компонентах, включая расширение публичного API
`libprocess`, а выигрыш невелик, пока все приложения в каталоге считаются
доверенными.

## U11

**`ipclib`: безусловный `unlink()` чужого endpoint'а.** HARDENING

Файл: `System/SpecialLibs/ipclib/src/ipc.c`, контракт 8 спецификации.

`ipc_server_create()` безусловно удаляет объект по пути endpoint, «не проверяя,
принадлежал ли удалённый endpoint живому серверу» — это осознанное упрощение
v1, нужное, чтобы сервис мог занять свой адрес после аварийного завершения.
Следствие: любой процесс, слинковавший `ipclib`, может занять
`/run/punos/msgserv.sock` и выдавать себя за брокера для тех, кто подключится
позже.

Брокер эту дыру не создаёт и не усугубляет — она общесистемная и одинаково
касается `appserv` и `logserv`. Записана здесь, чтобы не считалась
обнаруженной впервые при ревью брокера. Разумное решение на будущее — блокировка
через отдельный lock-файл либо проверка живости старого endpoint'а перед
`unlink()`.

## U12

**Обновление vendor-снапшотов в отдельных репозиториях сервисов.** BLOCKER

Репозитории `ITMOdefence/appserv`, `ITMOdefence/initserv`,
`ITMOdefence/logserv` содержат подмножество исходников PUNos в `vendor/PUNos`
с закреплённой ревизией в `punos.ref` (в `appserv` сейчас
`b3bfaa53f34688b85fb1b41644f7cf615da4eeff`). Это сделано, чтобы CI собирал
модуль без токена доступа к приватному upstream.

Любая правка `ipclib`, `libprocess`, `librandom` или заголовков из U1–U6
требует обновить снапшот **и** `punos.ref` в каждом затронутом репозитории,
одним коммитом с правкой кода — README всех трёх это требует прямо
(«Updates to dependency snapshots must update punos.ref together»).

`msgserv` разрабатывается **в этом отдельном репозитории**, по той же модели:
`vendor/PUNos` с подмножеством исходников, ревизия в `punos.ref`, свой
`Makefile` и свой CI, а интеграция в PUNos предлагается отдельным PR. Отсюда
дополнительное требование к порядку работ: пока правки U1–U6 не влиты в PUNos,
`punos.ref` этого репозитория указывает на ревизию, где их ещё нет, и
собираться может только сам брокер — сквозные тесты с `appserv` и
`appframework` (L4) появляются лишь после обновления снапшота. Структура
репозитория и порядок — в [`roadmap.md`](roadmap.md#2-структура-репозитория).

---

## Конфликт с PR #9

На 28 октября 2026 года в PUNos открыт PR #9 «Добавлен базовый графический
стек PUNos»: +22 819 / −505 в 118 файлах. Он затрагивает ровно те
компоненты, которые нужны брокеру:

| Компонент | Что меняет PR #9 | Последствие для брокера |
| --- | --- | --- |
| `ipclib` | `send`/`recv` → `sendmsg`/`recvmsg`, `SCM_RIGHTS`, `fcntl(F_DUPFD_CLOEXEC)`, передача FD ancillary-данными | Расширенный API и список syscall'ов; правки U10 нужно делать уже поверх новой версии |
| `appserv` | Handoff `ShellToken` пользовательскому приложению-оболочке | Та же машинерия, которая нужна для `MessagingToken` — U1 надо строить на ней, а не параллельно |
| `initserv` | Startup-токены, readiness по bind, GUI-сервисы в реестре | U5 почти целиком пересекается |
| `appframework` | Используется `uilib` | U6 трогает тот же `app.h` |
| `librandom` | Новый System Lib с `random_fill()` | Генерация токенов существует только в этой ветке |

Вывод: начинать реализацию до мержа PR #9 — почти гарантированный конфликт в
`ipclib`, `appserv` и `initserv`, причём `librandom`, без которого токены не
сгенерировать, в `main` вообще отсутствует. Спецификацию писать можно и нужно
сейчас; код — после мержа либо прямо в ветке GUI. Порядок — в
[`roadmap.md`](roadmap.md).
