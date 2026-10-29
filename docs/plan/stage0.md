# Этап 0 — сборка и воспроизводимость рендера Surge XT в NixOS

> Статус: **план предложен; спайк прогнан в облачном Linux-контейнере с Nix (не NixOS): сборка обоих вариантов, `nix flake check` и 31 проверка без FAIL**. Этап считается завершённым только после прогона на вашем NixOS и вашего решения по итогам (раздел 7).
> Связанный документ: [`docs/research/forgesound-research.md`](../research/forgesound-research.md), разделы 2.1, 2.5, 6.3, 6.11, 6.12.

## 1. Цель

Доказать на вашей машине, до любого UI, что:

1. `surgepy` (Python-биндинги Surge XT) **собирается из flake** на NixOS без ручной установки системных библиотек;
2. он **изолирован**: основной процесс ForgeSound его не импортирует, работает только отдельный процесс-воркер;
3. рендер **быстрый** (интерактивный цикл < 50 мс для звука 0,5 с);
4. известно, **какие конфигурации Surge воспроизводимы бит-в-бит**, а какие нет;
5. сэмпл можно **пропустить через Surge**;
6. падение воркера **не роняет ядро**, а опоздавший результат **не заменяет новый**;
7. **все форматы** звуков Trailblazer читаются (мод только читается);
8. **экспорт WAV** в формате Trailblazer детерминирован;
9. **звук выходит** через PipeWire/ALSA вашей системы.

## 2. Что входит в спайк (файлы в репозитории ForgeSound)

| Файл | Назначение |
|---|---|
| `flake.nix` | nixpkgs закреплён на ревизии `nixos-26.05`, исходники Surge main закреплены коммитом с сабмодулями; пакеты `surgepy` (= main) и `surgepy-release` (1.3.4); `devShells.default` (main) и `devShells.release`; `checks` для `nix flake check` |
| `nix/surgepy.nix` | Деривация: исходники Surge → только цель `surgepy` + заводские данные в `share/surge-xt` |
| `spike/stage0/surge_worker.py` | Единственный процесс, который импортирует `surgepy`. Протокол: JSON-строки через stdin/stdout, аудио — через `.npy`-файлы. Каждый рендер — на **нетронутом** экземпляре Surge (заранее подготовленном) |
| `spike/stage0/stage0_check.py` | Проверки S0-1…S0-7, S0-9, S0-10 в основном интерпретаторе (без `surgepy`) |
| `spike/stage0/probe_formats.py` | Проверка S0-8: декодирование всех звуков папки по сигнатуре файла, только чтение |

Это **не код приложения**: после этапа 0 удачные решения переносятся в ядро (этап 1), а спайк остаётся как справочный.

## 3. Требования к системе

- NixOS (x86_64) с включёнными flakes:
  ```nix
  # /etc/nixos/configuration.nix
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
  ```
  или разово: добавлять `--extra-experimental-features 'nix-command flakes'` к командам `nix`.
- Звук (для S0-10): стандартный PipeWire.
  ```nix
  services.pipewire = { enable = true; alsa.enable = true; pulse.enable = true; };
  ```
- Место на диске: ≈ 3 ГБ (зависимости сборки, исходники Surge с сабмодулями, два варианта `surgepy`: замыкание 682 и 583 МиБ, большая часть — заводские данные Surge).
- Время: сборка одного варианта `surgepy` ≈ 6–10 минут на 4 ядрах (в контейнере: release 6 мин 10 с, main 9 мин 05 с).
- Ничего не ставится в систему: всё берётся из Nix-store.

## 4. Команды по шагам

Все команды выполняются из корня клона ForgeSound.

### Шаг 1. Получить код

```sh
git clone https://github.com/Kostr2007/ForgeSound-Engine.git
cd ForgeSound-Engine
git checkout claude/forgesound-engine-research-yvntsq
```

### Шаг 2. Зафиксировать входы flake

```sh
nix flake lock
git add flake.lock        # flake в git-репозитории видит только отслеживаемые файлы
nix eval --raw --impure --expr '(builtins.fromJSON (builtins.readFile ./flake.lock)).nodes.surge-main.locked.narHash'; echo
```

**Ожидается:** создан `flake.lock`; хеш исходников Surge main:

```
sha256-UM3ZMlhzMgYmKGg/7nr8Wd3jlF4lZ9PbRjb5+Ucoe5Y=
```

Совпадение означает, что вы получили **те же исходники**, на которых проверялся спайк. `flake.lock` после этого стоит закоммитить.

### Шаг 3. Собрать оба варианта `surgepy`

```sh
nix build .#surgepy-main -L -o result-main
nix build .#surgepy-release -L -o result-release
ls result-main/lib/python3.13/site-packages/ result-release/lib/python3.13/site-packages/
```

**Ожидается:** в обоих каталогах — `surgepy.cpython-313-x86_64-linux-gnu.so` (≈ 10–12 МБ). В логе конфигурации будут строки `No package 'gtk+-x11-3.0' found`, `No package 'webkit2gtk-4.0' found`, `No package 'libcurl' found` — это нормально: JUCE проверяет необязательные GUI-зависимости, а мы собираем только Python-модуль.

Проверка, что нет зависимостей вне Nix-store:

```sh
ldd result-main/lib/python3.13/site-packages/*.so | grep -v /nix/store
```

**Ожидается:** только `linux-vdso.so.1` и `/lib64/ld-linux-x86-64.so.2` (загрузчик ядра). Если `ldd` в системе нет: `nix shell nixpkgs#glibc.bin -c ldd …`.

### Шаг 4. Проверка в песочнице Nix

```sh
nix flake check -L
```

**Ожидается:** обе проверки (`surgepy-main-smoke`, `surgepy-release-smoke`) проходят. Каждая запускает воркер в песочнице сборки (без сети, без домашнего каталога, без звуковой карты) и рендерит ноту.

### Шаг 5. Основные проверки (вариант main)

```sh
nix develop -c python -I spike/stage0/stage0_check.py --play --out spike/stage0/results-main
```

**Ожидается:** в конце `31 checks, 0 FAIL`; при `--play` — короткий синусоидальный тон (0,5 с) в колонках или наушниках.

### Шаг 6. Те же проверки (релиз 1.3.4)

```sh
nix develop .#release -c python -I spike/stage0/stage0_check.py --out spike/stage0/results-release
```

**Ожидается:** `31 checks, 0 FAIL`.

### Шаг 7. Форматы звуков Trailblazer (только чтение)

```sh
TB=~/путь/к/Trailblazer-Extended          # ваша локальная копия мода
git -C "$TB" status --porcelain > /tmp/tb-before.txt
nix develop -c python -I spike/stage0/probe_formats.py "$TB/src/Sounds"
git -C "$TB" status --porcelain > /tmp/tb-after.txt
diff /tmp/tb-before.txt /tmp/tb-after.txt && echo "Trailblazer не изменён"
```

**Ожидается:** `455 files, 0 failed` (или другое число, если мод изменился, но 0 ошибок) и строка `Trailblazer не изменён`.

### Шаг 8. Отчёт

Пришлите (или закоммитьте в `docs/plan/stage0-results/nixos-main.json` и `nixos-release.json`) файлы `spike/stage0/results-main/stage0-results.json`, `spike/stage0/results-release/stage0-results.json` и вывод шагов 2–7. Для сравнения там же лежат результаты пробного прогона: `container-main.json`, `container-release.json`. По ним будет записан итог этапа 0.

## 5. Проверки, ожидаемые результаты и результаты пробного прогона

Пробный прогон 2026-10-29: облачный контейнер Linux x86_64, 4 ядра, Nix 2.34.6 (**не NixOS**), nixpkgs `nixos-26.05` @ `08d3a97`, без звуковой карты. Команды — те же, что в разделе 4, с одной разницей: в контейнере GitHub API закрыт, поэтому nixpkgs подменялся тем же коммитом через `--override-input nixpkgs "git+https://github.com/NixOS/nixpkgs?ref=nixos-26.05&rev=08d3a97…&shallow=1"`. У вас это не нужно.

### Сборка

| Шаг | main (`dab78c6`) | release 1.3.4 |
|---|---|---|
| `nix flake lock`, хеш `surge-main` | ✅ `sha256-UM3ZMlhzMgYmKGg/7nr8Wd3jlF4lZ9PbRjb5+Ucoe5Y=` (2 мин 23 с) | — |
| `nix build` | ✅ 9 мин 05 с | ✅ 6 мин 10 с |
| Модуль `surgepy…so` | 9,9 МБ | 12 МБ |
| Заводские данные `share/surge-xt` | 486 МБ | 379 МБ |
| Замыкание (всё, что нужно для запуска) | 682 МиБ | 583 МиБ |
| `ldd` вне Nix-store | только `linux-vdso` и `ld-linux` | то же |
| `surgepy.getVersion()` | `1.4.git-no-branch.git-no-commit` | `1.3.git-no-branch.git-no-commit` |
| MSEG/Formula/Extend/Deform API | ✅ есть | ❌ нет |
| `nix flake check` | ✅ `surgepy-main-smoke` | ✅ `surgepy-release-smoke` |

Найдено и исправлено при подготовке (на вашей машине повторяться не должно): старый pybind11 против CMake 4 (`CMAKE_POLICY_VERSION_MINIMUM=3.5`), цели плагинов обязательны для конфигурации, `fontconfig` для JUCE 8 (main), `patchShebangs` для сборки LuaJIT (main).

### Проверки `stage0_check.py`

| ID | Проверка | Критерий PASS | main | release 1.3.4 |
|---|---|---|---|---|
| S0-1 | Изоляция: основной процесс **не** импортирует `surgepy`, воркер — импортирует | Оба условия | ✅ запуск воркера 169 мс | ✅ 135 мс |
| S0-2 | Нота Init-патча рендерится, нет NaN/Inf, не тишина | Пик > −60 дБ | ✅ −12,7 дБ, блок 32 | ✅ −12,9 дБ, блок 32 |
| S0-3 | Детерминизм 11 типов осцилляторов: два рендера в одном воркере и рендер в новом процессе; Retrigger выкл/вкл | С Retrigger вкл бит-в-бит ≥ 1 типа (ожидается 9) | ✅ Retrigger **выкл**: 0 из 11 (расхождения −5…−28 дБ). Retrigger **вкл**: 9 из 11 бит-в-бит — classic, sine, wavetable, FM2, FM3, window, modern, twist, alias. Случайны: **S&H Noise**, **String** | ✅ то же самое |
| S0-4 | Рендер 0,5 с: полный цикл «запрос → ответ» после паузы | Медиана < 50 мс | ✅ **7,7 мс** (макс. 11,6; DSP 2,5 мс); подряд без пауз 84 мс | ✅ **7,4 мс** (DSP 3,2); подряд 62 мс |
| S0-5 | Сэмпл через Surge (Audio Input + фильтр + огибающая) | Выход не тишина | ✅ пик −21,7 дБ | ✅ то же |
| S0-6 | Убитый воркер обнаружен и заменён, ядро живо | Новый воркер отвечает | ✅ | ✅ |
| S0-7 | «Новое побеждает» (модель правила) | Принята только ревизия 3 | ✅ | ✅ |
| S0-9 | WAV 44,1 кГц / 16 бит / моно, дважды из разных процессов | Одинаковый sha256 | ✅ `e35745f3c8734094…` | ✅ `e35745f3c8734094…` — **тот же файл, что и у main** |
| S0-10 | PortAudio видит выходы; `--play` | ≥ 1 выход и слышен тон | ℹ️ в контейнере 0 выходов — проверяется только у вас | ℹ️ то же |
| | **Итого** | 0 FAIL | **31 проверка, 0 FAIL** | **31 проверка, 0 FAIL** |

### Форматы Trailblazer (S0-8, `probe_formats.py`)

✅ 455 файлов, 0 ошибок: 387 Ogg Vorbis, 40 WAV, **12 Doom DMX с расширением `.wav`**, 13 MP3, 3 FLAC. `git status` мода до и после одинаков; мод не изменён.

### Что можно сравнить со своим прогоном

- Хеш `surge-main` (шаг 2) должен совпасть **точно**.
- S0-3 должен дать **тот же список** бит-в-бит типов.
- S0-9: если ваш sha256 начинается с `e35745f3c8734094`, то экспорт бит-в-бит совпадает **между разными машинами**. Несовпадение не считается провалом: numpy и Surge могут выбирать разные SIMD-ветки на разных процессорах. Это будет информацией для этапа 1 (тогда эталоны в golden-тестах сравниваются с допуском −120 дБ, а не по хешу).
- S0-4: ваши миллисекунды будут другими; критерий — < 50 мс.

## 6. Что делать, если что-то не так

| Симптом | Вероятная причина | Что сделать |
|---|---|---|
| `nix flake lock` не может скачать Surge | Сеть, GitHub | Повторить; при длительной проблеме — `nix flake lock --refresh` |
| Хеш на шаге 2 **не совпадает** | Другие исходники по тому же коммиту (крайне маловероятно) или изменились сабмодули | Остановиться и прислать вывод; не продолжать |
| Сборка падает: `Compatibility with CMake < 3.5 has been removed` | Не применился флаг `CMAKE_POLICY_VERSION_MINIMUM` | Проверить `nix/surgepy.nix`, прислать лог |
| Сборка падает: `fontconfig/fontconfig.h: No such file` | Нет `fontconfig` в `buildInputs` | То же |
| Сборка падает: `get_target_property() called with non-existent target "surge-fx"` | Выключены цели плагинов | В `cmakeFlags` не должно быть `SURGE_BUILD_XT/FX=OFF` |
| Сборка main падает: `Failed to build LuaJIT!`, `Build script exit code: no such file or directory` | Скрипт `libs/luajitlib/build-linux-luajit.sh` с `#!/bin/bash` | В `postPatch` варианта main должно быть `patchShebangs libs/luajitlib` |
| Вариант main не собирается, а release собирается | Изменения в main Surge | Этап 0 можно закрыть на релизе 1.3.4 **без MSEG-API**; решение — в разделе 7 |
| S0-4 > 50 мс | Слабый CPU или нагрузка | Прислать числа; варианты: держать несколько «свежих» экземпляров, переиспользовать экземпляр с явным сбросом состояния |
| S0-10: 0 выходов | PortAudio не видит ALSA-устройств PipeWire | Проверить `services.pipewire.alsa.enable = true`; для MVP некритично (звук в браузере) |
| `--play` молчит, но выходы есть | Выбран не тот выход по умолчанию | `pavucontrol` или `wpctl status`; для MVP некритично |

## 7. Критерии завершения этапа 0

Этап 0 **завершён**, когда на вашем NixOS выполнено всё ниже:

| # | Критерий |
|---|---|
| K1 | `nix build .#surgepy-main` и `.#surgepy-release` проходят без установки чего-либо в систему |
| K2 | Хеш `surge-main` в `flake.lock` совпадает с ожидаемым (шаг 2) |
| K3 | `nix flake check` проходит |
| K4 | `stage0_check.py` в обоих окружениях: **0 FAIL** |
| K5 | S0-3: с Retrigger вкл бит-в-бит как минимум 9 типов осцилляторов; S&H Noise и String зафиксированы как недетерминированные |
| K6 | S0-4: медиана интерактивного цикла < 50 мс |
| K7 | S0-8: все звуки вашей копии Trailblazer декодируются; `git status` мода до и после одинаков |
| K8 | S0-10: вы **услышали** ноту с `--play` (если есть звуковая карта; иначе — зафиксировать как «отложено») |
| K9 | Записано решение по итогам (ниже) |

**Решения по итогам этапа 0** (принимаете вы, по моей рекомендации):

1. **main или 1.3.4.** Рекомендация: main, если K1 для него выполнен, — нужен MSEG-API для огибающих Surge-слоя.
2. **S&H Noise и String.** Рекомендация: на этапах 1–3 считать их классом `frozen` (результат замораживается при первом рендере). Патч seed в Surge (класс `seeded`) делать только если эти осцилляторы окажутся важны для звучания.
3. **Retrigger по умолчанию.** Рекомендация: в Surge-узле ForgeSound включать Retrigger у всех осцилляторов по умолчанию (детерминизм и предсказуемая атака — для hitmarker'ов это и звучит лучше); выключение доступно как явный параметр с пометкой «недетерминированно».
4. **Скорость пакетной генерации вариаций.** Подряд один воркер даёт 62–84 мс на рендер: каждый раз создаётся свежий экземпляр Surge (≈ 64 мс). Рекомендация для этапа 4: пул из N воркеров по числу ядер плюс проверка переиспользования экземпляра со сбросом.

## 8. Что этап 0 сознательно не делает

- Не создаёт UI, формат проекта, операции и планировщик — это этап 1–2.
- Не трогает Trailblazer: только чтение файлов в шаге 7.
- Не использует `pedalboard` и VST3 (нет в nixpkgs; раздел 6.11 отчёта).
- Не подключает ИИ.
