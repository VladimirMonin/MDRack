# Аудит embedding-ролей и модельных маршрутов — 2026-10-26

Исследование baseline `master`, `9435effd5c45a7f0a1d440130baa0549908ef034`.
Код продукта не изменялся. [Обзор](modern-embedding-models-2026.md) прочитан полностью
и первоначально импортирован побайтно. Перед публикацией из копии в репозитории
удалён только один завершающий LF — пустая строка EOF; основной текст, метаданные,
ссылки и смысл не изменены. SHA-256 исходника (**ORIGINAL**):
`9d80dc67a1fb555a5a0d9044dd7a7a1fea8106e5eaf6872cf7efc2f26829eb5f`.
SHA-256 публикуемой копии (**REPOSITORY**):
`604bbcd5084eb26c65f749b77d39022004ce98f503afefc7b0494ce968714b22`.
Это исследовательская ссылка, а не спецификация MDRack.

## Ответ

**Общей гарантии правильного document/query-формата для выбранной модели и транспорта
сейчас нет. Правила «новая модель требует явного mapping обеих ролей» тоже нет.**

- Обычный LM Studio HTTP: документы передаются без модельной оболочки;
  query по умолчанию становится `Represent the query for retrieval: {text}`.
  Это не официальный retrieval-шаблон ни Qwen3, ни EmbeddingGemma 2.
- Отдельный opt-in `mdrack-cuda-retrieval-v2` действительно добавляет Gemma
  `title: none | text: ` для текстового DOCUMENT и
  `task: search result | query: ` для QUERY, сохраняя тело. Это фиксированный
  квалифицированный рецепт, **не общий реестр моделей**.
- Старый `mdrack-cuda-abi12-v1` не поддерживает текстовый DOCUMENT: только image
  DOCUMENT и text QUERY для image-кандидатов. Его нельзя считать тем же v2.
- Настройка `query_instruction` позволяет вручную приблизить query-рецепт, но не
  задаёт document-правило и не проверяет модель/маршрут. Пустая строка запрещена;
  внешние пробелы удаляются, затем безусловно добавляется `: `.

Из этого не следует, что все исторические неудачи качества вызваны префиксами.
[Прежняя HTTP-квалификация](../evaluation/text-provider-functional-2026-10-24.md)
доказывала plumbing, не правильность модельной семантики.
[Native v2](../evaluation/native-text-image-product-2026-10-24.md) остаётся отдельной
функциональной границей, не результатом нынешнего HTTP-опыта.

## Реальные цепочки вызовов и offline-проверка

| Путь | Что действительно происходит |
|---|---|
| `config/models.py::EmbeddingConfig` → `embeddings/runtime.py::create_embedding_provider` | Модель — произвольная строка; family/runtime/recipe заданы независимо. Нет lookup модели и транспорта. |
| `application/indexing.py::_prepare_structural_file` → `provider.embed` | Отправляется `chunk.embedding_text`; `application/chunking.py::_embedding_text` добавляет heading/kind, не модельную роль. |
| Legacy `_prepare_legacy_file` → `markdown/embedding_text.py::build_embedding_text` → `embed` | `[title] relative_path > heading\n\nbody` — контекст содержимого, не Gemma document-template. |
| `MDRackEngine.search_semantic` → `application/retrieval.py` → `embed_query` | Используется настроенная общая инструкция; unified search, изображения с текстовыми проекциями и transcript-пути также вызывают `embed_query`. |
| `embeddings/prepared.py::LegacyTextEmbeddingAdapter.encode` | Явный QUERY вызывает `embed_query`, DOCUMENT — `embed([text])`. Роль не выводится из размера пакета. |
| `application/media_search.py::_PreparedTextAdapter` → prepared provider | DOCUMENT/QUERY передаются явно; native `_prepare` добавляет свою оболочку перед записью UTF-8 и запуском процесса. |

В новом синтетическом каталоге offline выполнены настоящий `run_indexer` и
`MDRackEngine.search_semantic` с `httpx.MockTransport`. Перехваченный JSON:

```json
{"model":"unregistered-embedding-model","input":["Heading\n\nНейтральный текст документа."]}
{"model":"unregistered-embedding-model","input":["Represent the query for retrieval: Перефразированный вопрос"]}
```

Неизвестная модель принята обычной цепочкой. Отдельный wire-контроль установил:
`query_instruction="  Instruct: Task\nQuery: "` превращается в
`Instruct: Task\nQuery:: Тело`. Можно вручную задать строку без последнего двоеточия,
но это не контракт точного prefix. `embed([query1, query2])` остаётся нейтральным:
название метода и batch size не обозначают роль. Native `_prepare` сохраняет
CRLF, Unicode и пробелы тела; тестировался offline, без нового native forward.

## Первичные модельные правила

Официальные страницы ниже получены HTTP200 во время исследования; снимки и SHA-256
сохранены в частных артефактах. Проверены опорные правила, не все численные таблицы
обзора и не его аудио/видеобенчмарки.

| Модель и маршрут | Правило и граница проверки |
|---|---|
| [Qwen3 0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), [8B](https://huggingface.co/Qwen/Qwen3-Embedding-8B), raw text | Документ без инструкции. В Transformers-примере query: `Instruct: {task}\nQuery:{text}`; last-token pooling. TEI-пример содержит пробел после `Query:`: выбранная точная версия должна быть зафиксирована, а не «очищена». |
| [EmbeddingGemma 2](https://ai.google.dev/gemma/docs/embeddinggemma/model_card_2), raw text | Search-query и title/text document различны. Для отсутствующего заголовка `title: none`. `prompt_name="Document"` уже добавляет оболочку; дублировать её нельзя. Mean pooling + projection. |
| [Gemma inference](https://ai.google.dev/gemma/docs/embeddinggemma/inference-embeddinggemma-with-sentence-transformers) | BF16/FP32; float16 activations несовместимы. Q8_0 GGUF сам по себе не доказывает dtype всех операций LM Studio. |
| [OpenAI embedding-3](https://developers.openai.com/api/docs/guides/embeddings) | Обычный input без отдельной retrieval-инструкции с обеих сторон. Full small=1536; dimensions — отдельная возможность. |
| [Nemotron 3 raw](https://huggingface.co/nvidia/Nemotron-3-Embed-1B-BF16) / [NIM](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-embed-1b-infer) | Raw `query: `/`passage: `; hosted NIM требует `input_type=query/passage`. NIM limit 4096 не равен окну весов 32768. |
| [Nemotron 3.5](https://huggingface.co/nvidia/Nemotron-3.5-Embed-1B-BF16) | Официальный adapter владеет prefixes; `/v2/embed` выбирает их через `input_type=query/document`. Это не NIM-схема и не image-query гарантия. |
| [Gemini Embedding 2](https://ai.google.dev/gemini-api/docs/embeddings) | Текстовые prefixes; `task_type` относится к 001, не к 2. Список частей одного Content агрегируется; нужны отдельные документы/Content. Reduced output авто-нормируется. |

Существенное расхождение метаданных: LM Studio inventory показывает Gemma
`maxContextLength=262144`, официальная модель — 8192; в опыте явно загружено **2048**.
Ни одно число из inventory не использовано как доказательство безопасного длинного
контекста. В облаке served aliases отличаются от канонических имён весов.
Внутренние серверные шаблоны и pooling HTTP-маршрутов не инструментированы:
«model-correct» ниже означает **официальный клиентский формат**, а не полную
квалификацию токенов, pooling или единственного владельца внутри сервера.

## Небольшие реальные опыты

Корпус заранее зафиксирован: 12 нейтральных документов RU/EN, 8 перефразированных
запросов (4 RU, 4 EN), один релевантный документ на запрос; есть cross-language и
близкие отрицательные документы. SHA-256 исходного JSON:
`12a2345a5fb11261a815648b83e79db2359639b970718b53e181779cd9c9e876`.
Ни пользовательские данные, ни старые gold/stores не использовались.

Условия: полный вектор, проверка каждого значения/индекса ответа, L2 в float64,
точный cosine/dot, без MRL/ANN/FTS. Размерность постоянна внутри модели.

- **current**: plain documents; общий MDRack query-prefix.
- **raw**: обе стороны без префиксов.
- **correct**: Gemma — официальная пара; Qwen — plain documents и
  `Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:{text}`;
  OpenAI — raw с обеих сторон.

Одинаковые document vectors переиспользованы; для OpenAI raw=correct без повторного
вызова. Локальный current выполнен настоящим `LMStudioProvider`; corrected/raw —
его нейтральным `embed`. Облачный current воспроизводит строку MDRack отдельным
probe: это **не облачная интеграция продукта**. Опыт не включает влияние Markdown
chunking: входы — заранее подготовленные короткие passage bodies.

| Модель / маршрут / полный размер | current Hit@1 / MRR@10 | raw | correct |
|---|---:|---:|---:|
| LM Studio `text-embedding-embeddinggemma-2`, unsloth Q8_0, 768 | 7/8 / 0.9375 | 7/8 / 0.9375 | 7/8 / 0.9375 |
| LM Studio `text-embedding-qwen3-embedding-0.6b`, Qwen Q8_0, 1024 | 7/8 / 0.9375 | 8/8 / 1.0000 | 8/8 / 1.0000 |
| Polza `qwen/qwen3-embedding-8b`, 4096 | 8/8 / 1.0000 | 8/8 / 1.0000 | **NOT_RUN_COMPLETE: HTTP503** |
| Polza `openai/text-embedding-3-small`, 1536 | 8/8 / 1.0000 | 8/8 / 1.0000 | raw, тот же результат |

nDCG@10 равен 0.953866 для строк с 7/8 и 1.0 для 8/8.
Top-1 ниже указан тройкой **current / raw / correct**; полные top-3, scores и ranks
сохранены в артефактах, без округления исходных векторов.

| Запрос: смысл → релевантный ID | Gemma | Qwen 0.6B | Qwen 8B | OpenAI small |
|---|---|---|---|---|
| q01 RU: возврат соединения после ошибки → d01 | d01/d01/d01 | d01/d01/d01 | d01/d01/— | d01/d01/d01 |
| q02 RU: проверка восстановления backup → d03 | d04/d04/d03 | d03/d03/d03 | d03/d03/— | d03/d03/d03 |
| q03 RU: точный код изделия → d05 | d05/d05/d05 | d05/d05/d05 | d05/d05/— | d05/d05/d05 |
| q04 RU: вздутый аккумулятор → d07 EN | d07/d07/d07 | d07/d07/d07 | d07/d07/— | d07/d07/d07 |
| q05 EN: connection cleanup → d01 RU | d01/d01/d01 | d01/d01/d01 | d01/d01/— | d01/d01/d01 |
| q06 EN: paraphrased product description → d06 RU | d06/d06/d05 | d05/d06/d06 | d06/d06/— | d06/d06/d06 |
| q07 EN: missing delivered parcel → d09 | d09/d09/d09 | d09/d09/d09 | d09/d09/— | d09/d09/d09 |
| q08 EN: pale stretched seedlings → d11 | d11/d11/d11 | d11/d11/d11 | d11/d11/— | d11/d11/d11 |

**Отрицательный/неоднозначный результат сохранён:** у Gemma correct улучшил q02,
но ухудшил q06, оставив среднее тем же. У Qwen0.6 raw не хуже correct на этих восьми
вопросах. Это не основание отказаться от модельного контракта, не общий рейтинг
и не доказательство, что 8B лучше 0.6B при прочих равных: различаются веса,
квантование и транспорт. Корпус мал и во многом прост; повторов и доверительных
интервалов для обобщения не было.

### Задержки и доступность

Измерено время HTTP dispatch → полностью прочитанный ответ, без загрузки модели.
Current local queries — восемь одиночных `embed_query`; остальные queries — batch
из восьми. Это разные схемы batching, **не парное сравнение скорости префиксов**.

| Маршрут | Documents raw / correct, с | Queries current / raw / correct, с |
|---|---:|---:|
| Gemma | 0.436 / 0.159 | 0.0193–0.0219 на одиночный запрос / 0.105 batch / 0.125 batch |
| Qwen0.6 | 0.184 / reuse | 0.0168–0.0212 на одиночный запрос / 0.096 batch / 0.099 batch |
| Polza Qwen8 | 5.915 / reuse | 4.879 batch после retry / 6.156 batch / отказ за 10.202 |
| Polza OpenAI | 3.141 / reuse | 3.979 batch / 7.897 batch / reuse |

OpenRouter: настроенный API-key маршрут обнаружен, точная модель и тариф получены
через `/api/v1/embeddings/models` и model endpoints. Первый Qwen8 POST: **HTTP402
Insufficient credits**, без повторения/пополнения. Codex OAuth не использовался.
Далее использован уже настроенный Polza `/api/v1/embeddings`: Qwen8 documents
успешны; current queries дали HTTP503, единственный инфраструктурный retry успешен;
raw успешен; correct дал новый HTTP503 `SERVICE_UNAVAILABLE`. Второй retry запрещён
лимитом, поэтому сравнение correct незавершено, а не «плохое качество».
OpenAI-small через Polza завершён полностью. Ответ Qwen назвал модель
`Qwen/Qwen3-Embedding-8B`; фактическая ревизия/квантование облачных весов неизвестны.
Каталог Polza указывает top provider `deepinfra/us` и `openai`, но ответ не
подтверждает исполнителя. Полученная общая docs-страница Polza не доказывает
поддержку server-role; специализированный embeddings-models GET вернул 404.

Перед локальными загрузками `lms ps --json` был пуст, GPU свободно >10 GiB,
utilization 2–3%. Использованы только собственные named instances, `--gpu max`,
контекст 2048, уже выбранный CUDA12 runtime 2.63.0; downloads/upgrades/restarts и
CPU fallback не применялись. **Устройство выполненных HTTP kernels — UNKNOWN**:
`deviceIdentifier=null`, HTTP200 и GPU-offload request недостаточны. Ожидание ресурсов
не понадобилось. Оба экземпляра адресно выгружены; исходный сервер оставлен включённым,
финальный roster пуст. Повторная собственная Gemma-загрузка потребовалась после
ошибки source-import probe до первого inference; исходный failure не скрыт.

Всего **32 POST / 168 submitted items**, включая ошибки и retry; 29 HTTP200,
1 HTTP402, 2 HTTP503; native forwards=0. Local: 23/84; OpenRouter: 1/12;
Polza: 8/72. Тарифы опубликованного каталога Polza: Qwen8 1.157868 RUB/миллион
input tokens, OpenAI-small 2.364660 RUB/миллион. Успешные ответы сообщают
2091 prompt tokens, **0.00377873 RUB** суммарно. USD billed и тарификация ошибок
не подтверждены. Консервативный ceiling всех попыток — **$0.025971**, считая каждый
UTF-8 byte плюс 32/item токеном и намеренно завышая 1 RUB до $1; это не валютный курс.
Лимиты 64 POST / 256 items / $2 соблюдены.

## Статья → фактический механизм → пробел

Пути ниже относительно `src/mdrack/`, если не указано иное.

| Механизм статьи | Состояние, реализация и доказательство | Пробел / следствие |
|---|---|---|
| Model+route registry, обе роли, explicit empty | **Нет общего registry.** `config/models.py`, `embeddings/runtime.py`; unknown-model normal-path mock принят. `cli/commands/model.py::_build_switched_config` меняет model/dims, не выбирает новую role recipe. | Generic query может перейти на другую модель; document mapping отсутствует; `""` неотличимо от запрещённой настройки. |
| Exact whitespace, один владелец | **Частично.** LMStudioProvider strips instruction и добавляет `: `; native v2 `_prepare` форматирует один раз, не меняя body. | HTTP server template неизвестен; нельзя обещать single-owner end-to-end. Готовый prefix нельзя бездумно вставить в query_instruction. |
| Batch/role semantics | **Есть на prepared port.** `EncodingRole`, `LegacyTextEmbeddingAdapter`, `_PreparedTextAdapter`; neutral embed разбивает >32, не назначает роль. | У legacy API нет типизированного query batch; единственный документ также остаётся document. |
| Pooling, dtype, runtime qualification | **Делегировано.** HTTP не усредняет hidden states сам; native pins/recipe и `_result` проверяют producer evidence. | Делегирование не равно новой квалификации LM Studio pooling/device/dtype. Native historical evidence не переносится на HTTP. |
| Dimensions / MRL / L2 | **Частично.** `LMStudioProvider.embed` проверяет полную длину, не обрезает; requested dimensions только при `tested`. `domain/profiles.py` различает capability states. Native проверяет raw→double-L2→f32. | HTTP `normalization_mode=l2` — identity label, не клиентская нормализация. Factory не передаёт native_dimensions, поэтому обычный путь не даёт полного MRL evidence. В этом опыте MRL не проверялся. Cosine делит на нормы: truncation не обязательно требует повторного L2 именно для такого scorer, но для unit-dot требует. |
| Все значения, counts, response indices | **Частично.** Provider отвергает неверную длину, но mock подтвердил принятие NaN/zero, missing/duplicate/permuted indices. Indexing проверяет count; prepared/core/canonical float32 guard отвергает нечисловое/неfinite/zero-cosine. | Downstream защита не исправляет перестановку корректных векторов: текст может получить чужой embedding. Нельзя объявить provider полностью валидирующим по одному dimension-check. |
| Model/recipe/space identity | **Есть, неполно для общей модели.** `domain/profiles.py::fingerprint`, `application/textual_embedding_space.py`; native identity дополнительно pins weights/source/recipe. | HTTP нет обязательной ревизии/документного recipe; строковые family/normalization можно объявить без доказательства. Query hash берётся до provider strip: одинаковый wire может иметь разные fingerprints. |
| Index invalidation | **Fail-closed по fingerprint.** Проверки catalog/profile и существующие truthfulness tests. | Query-only изменение сейчас меняет пространство и блокирует старое. Математически документные vectors не изменились: нужна явная совместимость, а не безусловный rebuild. Новый document prefix требует новых embeddings. |
| Structural chunking, context | **Есть.** `application/chunking.py`: spans, headings, code/table policies, UTF8-based token estimate; `overlap_chars` не используется. Native v2 token bound 2048 квалифицирован отдельно. | Общая модель+route tokenizer/context policy отсутствует; byte/estimated-token bound не гарантирует fit после model prefix/special tokens. Длинный контекст не тестировался. |
| Hybrid / reranker / semantic-only | **RRF есть, reranker намеренно отсутствует.** Core `application/fusion.py`, `application/retrieval.py`; non-null reranker fail-closed. | Запрет FTS fallback не универсален: offline `UnifiedTextSearchService._search_prepared` при vector failure в semantic режиме вызвал lexical; `degraded=true`, mode остаётся semantic. Это отдельный узкий regression target, не свойство всех retrieval callers. |
| Русский FTS | **Unicode есть; русская морфология не обещана.** SQLite FTS/tokenizer; это не neural reranker. | Результат prefix-probe не измеряет hybrid, stemming или lexical quality. |
| Native modality/role limits | **Типизированы, ограничены.** `domain/embedding_inputs.py`, native v1/v2, [контракт](../contracts/native-image-v1.md). | HTTP text-only; image QUERY/SIMILARITY/audio/video inference у данного native host не добавлены. Preparation/транскрипты не доказывают acoustic/visual retrieval. |
| ANN, compact storage, master vectors | **Exact scan и canonical f32 есть**, отдельные packages SQLite/core. | Не проверялись MRL quality curves, master-vector restoration, ANN или vector quantization; их нельзя вывести из маленького префиксного опыта. |

## Минимальный следующий implementation slice — предложение, не реализация

1. Небольшой immutable mapping по **transport contract + точному model ID / явному
   alias + qualified revision/recipe**. Не substring detection. Неизвестная пара
   отвергается до HTTP. Каждая запись обязана содержать document и query, даже если
   это `""`; отсутствие поля/`null` — ошибка. Поддержанные modality/role фиксируются.
2. Два взаимоисключающих режима: `client-template` с точными UTF-8 шаблонами либо
   `server-role` с переводом document/query в конкретные параметры API. Exactly one
   owner; batching не назначает роль. Title policy отдельна от chunk heading.
   Для первого среза достаточно LM Studio Qwen0.6/Gemma2; не добавлять production
   cloud, NVIDIA или Gemini лишь потому, что исследовательский probe ими интересуется.
3. Хранить document recipe/weights/dimensions/pooling/normalization в identity
   корпуса; query recipe — версионированно с явной разрешённой совместимостью.
   Не переписывать старые fingerprints молча. Новый document-format или модель
   требуют нового вычисления документов; query-only change — оценки и совместимого
   переключения, **не обязательно** повторного inference корпуса. Dimension change
   требует нового index representation; inference нужен, если нет совместимого full vector.
4. Перед возвратом HTTP batch проверить полный набор уникальных indices 0..N-1,
   восстановить порядок, count, expected dimensions, finite numeric coordinates и
   ненулевую cosine-норму. Не угадывать pooling на клиенте.

Фокусные regressions: unknown route/model и отсутствующая роль → zero dispatch;
explicit empty сохраняется; Unicode/CRLF/trailing spaces точны; один и 33 документа,
один и несколько queries используют свою роль один раз; обычные index→search и
prepared callers перехватываются на HTTP/bytes, а не только проверяют config.
Дополнительно shuffled/duplicate/missing indices, NaN/Inf/bool/zero/wrong dimension;
native v1 неизменён и отказывает text DOCUMENT; native v2 identity меняется при
смене document recipe. Отдельный тест semantic-only vector-error → **никакого FTS**.
Не ослаблять существующую space-защиту ради query-only миграции.

## Проверки и артефакты

- Workspace wrapper `scripts/offline_checks.py`, выполненный через
  `uv run --no-sync` с выбранным checkout: **88 passed**, существующие `test_lmstudio_provider.py`,
  `test_embedding_profiles.py`, `test_embedding_text.py`, `test_native_text_document.py`,
  `test_embedding_truthfulness_paths.py`. Это source/offline, не installed qualification.
- Workspace `wire_checks.py`, `semantic_fallback.py`: mock/local-components evidence,
  zero real embedding requests. `probe.py`: ограниченные live external calls.
- `lms ls --embedding --json`, `lms ps --json`, API model GETs, `lms runtime ls`,
  `nvidia-smi`; адресные load/unload, без вмешательства в чужие экземпляры.
- Исходные проверки исследования: `uv run --no-sync python scripts/check_documentation.py --check`:
  exit 0, 31 выбранный документ; scope не расширен, 109 внешних ссылок не проверены этим checker.
  Отдельная проверка новых материалов: 15 локальных ссылок, восемь навигационных anchors
  статьи, совпадение байтов первоначальной копии с источником и отсутствие trailing whitespace.
  Тогдашний `git diff --check` был unstaged-проверкой: ещё неотслеживаемый импорт
  в неё не входил. Позднее `git diff --cached --check --` обнаружил одну пустую
  строку EOF копии; перед публикацией удалён ровно один конечный LF, без иных
  изменений обзора. Финальная Git-проверка охватывает cached diff всех четырёх
  публикуемых путей отдельно от сохранённых исследовательских результатов.
  README отличается ровно одной добавленной ссылкой. Все 29 успешных реальных ответов
  независимо проверены на count, indices, dimensions, конечность и ненулевую норму.
  Новых диаграмм не создавалось; Mermaid импортированной статьи сохранён без rendering
  qualification. Полный suite сознательно не запускался для docs-only изменения.

Частный handoff содержит `results.json`, `release.json`, `LOG.md`, frozen corpus,
`evidence/rankings.md`, `wire-checks.json`, `semantic-fallback.json`, `usage.jsonl`,
`usage-summary.json`, `wire-*.json`, `response-*.json`, `receipt-*.json`, снимки первичных
refs и исходные vectors. В Git входят только этот отчёт, обзор и навигация.
Это завершённое ограниченное исследование, **не приёмка продукта/релиза**.
