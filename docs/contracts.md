# Контракты данных

Общие схемы для бэкенда, фронтенда и модуля скоринга. JSON Schema лежат в `schemas/`.

## Conversation (из API организаторов, хранится как есть)

```ts
Conversation {
  conversation_id: string        // "p1-ru-001"
  dataset_group_id: string       // "DEV" или контрольный group_id
  channel: "agent chat" | "voice call"
  language: "RU" | "KZ" | "EN"
  transcript: Message[]
  audio_url: string | null       // только у звонков
}
Message {
  idx: number                    // добавляем при импорте: позиция в transcript, с 0
  timestamp: string              // исходный: "MM:SS" у чатов, ISO 8601 с TZ у звонков
  offset_sec: number | null      // вычисляем: секунды от начала диалога
  speaker: "client" | "operator" | "bot" | "system"
  text: string
}
```

## Checklist

Схема: `schemas/checklist.schema.json`, исходная конфигурация: `config/checklist.default.json`.
Каждое сохранение в UI создаёт новую `version`; оценки ссылаются на `(checklist_id, version)`.

## Evaluation

```ts
Evaluation {
  id: string
  conversation_id: string
  checklist_id: string
  checklist_version: number
  model: string
  cost_usd: number
  created_at: string
  results: CriterionResult[]
  total: number | null           // null — нет применимых критериев с весом
  sections: SectionTotal[]
  critical: { flag: boolean, subtypes: string[] }
}
CriterionResult {
  criterion_id: string
  answer: "yes" | "no" | "na" | number
  checks: { q: string, a: string, msg?: number[] }[]
  evidence: { msg_idx: number, proves: string }[]
  expected_at: { from_msg_idx: number, to_msg_idx: number } | null
  na_reason: string | null
  explanation: string
  weight: number                 // исходный вес
  effective_weight: number       // после перераспределения N/A
  points: number                 // баллы после перераспределения (не округлены)
  needs_review: boolean          // валидатор нашёл проблему
  review_notes: string[]
}
SectionTotal { section_id: string, points: number, max: number }
```

Выход модели (без баллов): `schemas/llm_output.schema.json`.

## Appeal и Decision

```ts
Appeal {
  id: string
  evaluation_id: string
  criterion_id: string
  comment: string
  status: "open" | "confirmed" | "changed"
  created_at: string
}
Decision {
  appeal_id: string
  new_answer: "yes" | "no" | "na" | number   // при confirmed = исходный
  reason: string                              // обязательна
  decided_at: string
}
```

Финальный результат = пересчёт `Evaluation` с подменой ответов по принятым `Decision`.
Исходная `Evaluation` не изменяется.

## Расчёт итога (кратко)

1. `f = 1` для «Да», `0` для «Нет», `value / max(steps)` для шкалы.
2. `A` — критерии без N/A с весом > 0 (флаг C8 не входит).
3. `Σw(A) = 0` → `total = null`.
4. `points_i = w_i × f_i × 100 / Σw(A)`; `total = round_half_up(Σ points_i, 2)`.
5. Секция: `Σ points` и `Σ effective_weight` её критериев.
6. Флаг грубой ошибки хранится отдельно и итог не меняет.
