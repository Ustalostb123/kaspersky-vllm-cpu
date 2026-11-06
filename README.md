# vLLM на CPU

Кулаков А.С. Команда исследования технологий машинного обучения.

Взял небольшую SmolLM2-135M-Instruct и vLLM 0.27.0 для CPU. Поставил float32, контекст 2048 токена и до 4 запросов одновременно.

При первом запуске отдельный контейнер скачивает модель на диск PVC. Потом файлы берутся оттуда. Перед загрузкой проверяются контрольные суммы, чтобы не использовать повреждённую модель.

В логах сохраняются номер запроса, код ответа и время обработки. Сам текст запроса и ответа туда не попадает. Оба контейнера работают от пользователя 10001, без root.

## Запуск

Нужны Linux x86_64 с AVX2 или AVX512, Docker, Minikube, kubectl и Python 3.12. На Windows команды можно выполнять в WSL2 с Docker Desktop. Для Minikube выделяю 4 ядра и 8 ГБ памяти. На самом компьютере желательно иметь хотя бы 16 ГБ.

Команды выполнять в bash из папки проекта:

```bash
minikube start --driver=docker --cpus=4 --memory=8192
minikube addons enable storage-provisioner
minikube addons enable default-storageclass
eval "$(minikube docker-env)"
docker build --platform linux/amd64 -t llm-cpu:0.27.0 -f Dockerfile .
docker build --platform linux/amd64 -t llm-downloader:0.27.0 -f Dockerfile.downloader .
bash scripts/create-secret.sh
kubectl apply -k k8s
kubectl -n llm-cpu rollout status deployment/inference --timeout=20m
kubectl -n llm-cpu port-forward service/inference 8000:8000
```

Скрипт спросит два токена. Для открытой модели токен Hugging Face можно пропустить. API-токен нужен от 32 символов, либо можно нажать Enter для генерации. Оба сохраняются в Kubernetes Secret, в файлах проекта их нет.

В другом терминале можно отправить запрос:

```bash
export API_TOKEN="$(kubectl -n llm-cpu get secret inference-secrets -o jsonpath='{.data.api-token}' | base64 --decode)"
curl -fsS http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"local-instruct","messages":[{"role":"user","content":"Что такое индекс в базе данных?"}],"max_tokens":64}'
```

Сервис доступен локально через port-forward. Если открывать его в интернет, нужно настроить HTTPS.

## Настройки

1. В `k8s/config/serve.json` меняются контекст и число запросов.
2. В `k8s/config/model.env` указаны модель, её ревизия и размер CPU-кэша. При смене модели нужно указать и её имя, и полный commit SHA. Сейчас для кэша выделен 1 ГиБ.
3. В `k8s/config/logging.json` и `app/privacy.py` настроены логи. Текст служебных сообщений и ошибок тоже убран, так как в нём могут оказаться данные пользователя.

После изменения настроек выполнить `kubectl apply -k k8s`. Образы пересобирать не нужно. После замены токенов нужно перезапустить pod командой `kubectl -n llm-cpu rollout restart deployment/inference`.

Все Python-зависимости, включая вложенные, записаны в `requirements.txt` и `downloader-requirements.txt` с точными версиями и хешами. Используются только готовые wheels. Версии системных пакетов Debian отдельно не закреплены.

## Проверка

Сборка и запуск проверены на GitHub в Kubernetes через kind. Модель отвечает на запросы, без токена возвращается 401. После перезапуска использовались сохранённые веса при выключенном доступе к Hugging Face. Текст запросов, ответов и токен доступа в логах не обнаружены. Также прошли 3 теста кода. В локальном тесте на Windows вызов Linux-блокировки заменён заглушкой.

[Результат проверки](https://github.com/Ustalostb123/kaspersky-vllm-cpu/actions/runs/42144259238)

```bash
python3 -m unittest discover -s tests -v
kubectl kustomize k8s
kubectl -n llm-cpu logs deployment/inference -c server --tail=20
kubectl -n llm-cpu exec deployment/inference -c server -- id
```

После запуска нужно проверить запрос с токеном и без него. Без токена должен вернуться 401. После перезапуска pod в логах загрузчика должна появиться запись `model_cache_hit`:

```bash
kubectl -n llm-cpu rollout restart deployment/inference
kubectl -n llm-cpu rollout status deployment/inference --timeout=20m
kubectl -n llm-cpu logs deployment/inference -c download-model
```

## Нагрузка

Для коротких и длинных запросов подготовил скрипт:

```bash
mkdir -p results
python3 scripts/benchmark.py --requests 4 --concurrency 1 > results/benchmark.md
```

Он выводит время до первого фрагмента ответа, полное время запроса и число выходных токенов в секунду. У каждого запроса разное начало, чтобы весь промпт не брался из prefix cache. Прогрев в замер не входит. Ошибки считаются отдельно, текст ответов не печатается.

Замер сделан на GitHub, Ubuntu 24.04, 2 vCPU. Контейнеру выделен лимит 6 ГиБ памяти. Для каждого размера отправлены 4 запроса по одному, выход ограничен 64 токенами. Ошибок не было.

| Запрос | Средний вход в токенах | TTFT, с | Latency, с | Throughput, выходных токенов/с |
|---|---|---|---|---|
| Короткий | 66,5 | 0,178 | 3,334 | 19,20 |
| Длинный | 1067,5 | 1,940 | 5,571 | 11,49 |

Для большого потока коротких запросов я бы попробовал поднять `max-num-seqs` с 4 до 8 и сравнил скорость. Если CPU уже занят полностью, больше запросов может просто увеличить ожидание.

Для длинных запросов сначала нужно проверить `max-model-len` и свободную память. Дальше можно уменьшать число одновременных запросов и подбирать `max-num-batched-tokens` с включённым chunked prefill. Меньшее значение помогает не задерживать текущие ответы, большее может ускорить обработку входного текста. Размер KV-кэша тоже можно увеличить, если хватает памяти. Эти изменения настроек отдельно не проверял.

[Документация vLLM для CPU](https://docs.vllm.ai/en/v0.27.0/getting_started/installation/cpu/)

[Выбранная модель](https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct)
