# vLLM на CPU в Minikube

Кулаков А.С. — команда исследования технологий машинного обучения.

Для задания я выбрал Qwen2.5-0.5B-Instruct и CPU-сборку vLLM 0.27.0. Модель небольшая, поэтому с неё проще начать локальную проверку. Квантизацию не использую, тип данных — float32. В конфигурации оставил контекст 2048 токена и до четырёх запросов одновременно.

При запуске initContainer проверяет модель на PVC. Если файлы уже скачаны и их контрольные суммы совпадают, он заканчивает работу без обращения к Hugging Face. Основной контейнер читает модель с этого PVC и работает с включённым offline-режимом.

## Что проверено

Проверены три локальных теста: удаление текста и исключений из логов; авторизация и журналирование обычного ответа, потока и ошибки; чтение исправного кэша без импорта Hugging Face и обнаружение повреждённого файла. В тесте кэша на Windows подменён только вызов Linux-блокировки `flock`.

Полный список Python-зависимостей разрешён для Linux x86_64 и Python 3.12. Проверка `uv pip install --dry-run --only-binary :all: --require-hashes` прошла для 152 пакетов. Это проверка зависимостей, а не запуск приложения.

**Docker-образ ещё не собирался, модель в Kubernetes не запускалась.** В среде подготовки нет Docker, Minikube и установленного WSL. Поэтому пока нет измерений скорости и подтверждения запуска на конкретном процессоре. После запуска нужно пройти проверки ниже. Бонус с нагрузочным тестом подготовлен как скрипт; результаты ещё не получены.

## Что понадобится

Linux x86_64 с AVX2 или AVX512, Docker, Minikube, kubectl и Python 3.12. Команды ниже выполняются в bash из корня проекта. На Windows понадобится Docker Desktop с Linux-контейнерами и WSL2; PowerShell для этих команд не подойдёт.

Для Minikube выделяю 4 CPU и 8 ГБ памяти. У компьютера должна оставаться память для самой ОС; ориентир — от 16 ГБ RAM на хосте. В pod выставлен предел 6 ГБ. Фактический расход и совместимость CPU нужно проверить запуском. PVC запрашивает 5 ГБ, Docker-образы потребуют дополнительное место.

## Запуск

```bash
minikube start --driver=docker --cpus=4 --memory=8192
minikube addons enable storage-provisioner
minikube addons enable default-storageclass

# Собираем образы сразу внутри Docker-окружения Minikube.
eval "$(minikube docker-env)"
docker build --platform linux/amd64 -t llm-cpu:0.27.0 -f Dockerfile .
docker build --platform linux/amd64 -t llm-downloader:0.27.0 -f Dockerfile.downloader .

bash scripts/create-secret.sh
kubectl apply -k k8s
kubectl -n llm-cpu rollout status deployment/inference --timeout=20m
kubectl -n llm-cpu port-forward service/inference 8000:8000
```

Скрипт создания Secret запрашивает токены без отображения на экране. Для выбранной открытой модели HF-токен можно оставить пустым. Для gated-модели нужен токен аккаунта, которому выдан доступ. API-токен нужен всегда: минимум 32 символа, либо Enter для генерации. Токены не попадают в Git, Dockerfile или ConfigMap. В кластере создаётся `Secret/inference-secrets` с ключами `hf-token` и `api-token`. Secret сам по себе не шифрует данные в etcd: для общего кластера нужны соответствующие настройки шифрования и RBAC.

В другом терминале получаем токен в переменную, без вывода на экран:

```bash
export API_TOKEN="$(kubectl -n llm-cpu get secret inference-secrets -o jsonpath='{.data.api-token}' | base64 --decode)"
curl -fsS http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"local-instruct","messages":[{"role":"user","content":"Что такое индекс в базе данных?"}],"max_tokens":64}'
```

Не публикуйте API наружу по обычному HTTP. Здесь доступ идёт через локальный port-forward к ClusterIP. Для удалённого доступа понадобится HTTPS.

## Что проверить после запуска

Без токена запрос должен получить 401:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/v1/models
kubectl -n llm-cpu logs deployment/inference -c server --tail=30
kubectl -n llm-cpu exec deployment/inference -c server -- id
kubectl -n llm-cpu top pod
```

Процесс должен работать с UID 10001. В логах запроса ожидается запись `request_completed` с кодом ответа и длительностью. Текст запроса и ответа выводить туда не нужно. `kubectl top` требует metrics-server; при необходимости включить его командой `minikube addons enable metrics-server`.

Проверка кэша:

```bash
kubectl -n llm-cpu logs deployment/inference -c download-model
kubectl -n llm-cpu rollout restart deployment/inference
kubectl -n llm-cpu rollout status deployment/inference --timeout=20m
kubectl -n llm-cpu logs deployment/inference -c download-model
```

При первом скачивании ожидается `model_download_complete`, после перезапуска — `model_cache_hit`. Последний путь дополнительно проверен локальным тестом без доступного модуля Hugging Face. Для проверки всего pod без сети можно после первого скачивания временно запретить исходящий трафик сетевой политикой в кластере с поддержкой NetworkPolicy. Сам локальный тест не заменяет эту проверку.

## Конфигурация и файлы

- `Dockerfile` — CPU-сервер, `Dockerfile.downloader` — отдельный лёгкий загрузчик без torch и vLLM. Оба запускаются от UID/GID 10001. Pod запрещает повышение привилегий, убирает capabilities и использует read-only корневую файловую систему.
- `requirements.txt` и `downloader-requirements.txt` — все прямые и вторичные Python-зависимости с версиями и SHA256. У vLLM зафиксированы URL CPU-wheel и его хеш. Для установки используются только готовые wheels, сборка исходных Python-пакетов запрещена.
- `k8s/config/serve.json` — параметры инференса. `k8s/config/model.env` — модель, точная ревизия и настройки CPU. Чтобы поменять модель, нужно изменить и `MODEL_ID`, и полный commit SHA в `MODEL_REVISION`.
- `k8s/config/logging.json` и `app/privacy.py` — логирование. Formatter не выводит текст служебных сообщений, аргументы и traceback, поскольку они тоже могут содержать входные данные. Из-за этого подробности ошибок в логах намеренно недоступны. Отдельный ASGI middleware пишет только случайный ID, фиксированное имя endpoint, статус и длительность. Он не читает тела запросов и ответов; query string и заголовки не логируются.
- `app/start.py` — запуск с ограниченным набором настроек. Включить логирование промптов или ответов через ConfigMap нельзя. В закреплённой версии vLLM эти функции выключены по умолчанию. Обычный access log Uvicorn также выключен.
- `app/download_model.py` — загрузка точной ревизии во временный каталог, проверка файлов, контрольные суммы и атомарное завершение. При сбое незаконченный каталог не считается готовой моделью. `flock` защищает от параллельной записи.

После изменения конфигов достаточно `kubectl apply -k k8s`: Kustomize меняет хеш ConfigMap, Deployment получает новые ссылки и пересоздаёт pod. Для RWO PVC выбрана стратегия Recreate. После замены Secret нужно вручную выполнить `kubectl -n llm-cpu rollout restart deployment/inference`.

Контекст 2048 включает и вход, и выход. Объём KV-кэша задаётся `VLLM_CPU_KVCACHE_SPACE` в ГиБ; сейчас это 1 ГиБ. GPU-ресурсы не запрашиваются: установлены CPU-wheel vLLM и CPU PyTorch.

## Нагрузочный тест

После успешного запуска:

```bash
mkdir -p results
python3 scripts/benchmark.py --requests 16 --concurrency 2 > results/benchmark.md
```

Скрипт делает отдельный прогрев для короткого и длинного промпта, затем отправляет запросы параллельно. В начало каждого промпта добавляется случайный ID, чтобы повторяющийся текст целиком не попадал в prefix cache. TTFT считается до первого непустого фрагмента текста, latency — до конца потока. Throughput считается как сумма выходных токенов из `usage` за общее время группы запросов. Дополнительно выводятся успешные запросы в секунду и число ошибок. Текст ответов не печатается.

Таблица заполняется только после реального запуска:

| Промпт | TTFT | Latency | Throughput |
|---|---|---|---|
| Короткий | Не измерено | Не измерено | Не измерено |
| Длинный | Не измерено | Не измерено | Не измерено |

Для большого потока коротких запросов я бы сначала попробовал увеличить `max-num-seqs` с 4 до 8 и сравнил скорость вместе с задержками. Если процессор уже полностью занят, дальнейшее увеличение очереди может только увеличить ожидание. Размер `max-num-batched-tokens` тоже стоит подбирать по измерениям.

Для длинных запросов нужно сначала поднять `max-model-len`, если текущего лимита мало, и проверить память. Затем сравнить меньший `max-num-seqs` и несколько значений `max-num-batched-tokens` при включённом chunked prefill. Небольшой бюджет prefill помогает не задерживать уже генерируемые ответы; большой может улучшить обработку входа. Самый быстрый вариант зависит от нагрузки и CPU. Увеличение KV-кэша тоже требует дополнительной RAM. Prefix caching полезен при повторяющихся началах промптов, но не заменяет измерения на разных текстах.

## Локальные проверки и обновление зависимостей

```bash
python3 -m unittest discover -s tests -v
kubectl kustomize k8s > /tmp/inference.yaml
# При доступном кластере — проверка схем и admission без создания ресурсов:
kubectl apply -k k8s --dry-run=server
```

Чтобы намеренно обновить lock-файлы, нужен uv 0.8.22. Без этой команды сборка использует уже зафиксированные версии:

```bash
uv pip compile requirements.in --python-version 3.12 --python-platform x86_64-manylinux_2_34 \
  --generate-hashes --index-strategy unsafe-best-match --output-file requirements.txt
uv pip compile downloader-requirements.in --python-version 3.12 --python-platform x86_64-manylinux_2_34 \
  --generate-hashes --output-file downloader-requirements.txt
```

Python-зависимости закреплены полностью. Теги базового Python-образа и версии Debian-пакетов могут со временем меняться; для воспроизведения всей ОС следует дополнительно закрепить digest образа и использовать snapshot-репозиторий Debian.

## Документация

- [CPU-установка vLLM 0.27.0](https://docs.vllm.ai/en/v0.27.0/getting_started/installation/cpu/)
- [Аргументы API-сервера выбранной версии](https://github.com/vllm-project/vllm/blob/v0.27.0/vllm/entrypoints/openai/cli_args.py)
- [Подключение middleware и логирования в API-сервере](https://github.com/vllm-project/vllm/blob/v0.27.0/vllm/entrypoints/openai/api_server.py)
- [Модель Qwen2.5-0.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct)
- [Kustomize в kubectl](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/kustomization/)

## Остановка

Чтобы остановить сервер и сохранить скачанную модель: `kubectl -n llm-cpu scale deployment/inference --replicas=0`. Для запуска вернуть `--replicas=1`. Удаление namespace или Minikube может удалить PVC вместе с моделью.
