import pytest

from cqa.transcript import EventType, classify_system_event, extract_facts, format_facts, format_transcript


@pytest.mark.parametrize(("text", "expected"), [
    ("Эскалация на оператора.", EventType.ESCALATION),
    ("Чат операторға жіберілді.", EventType.ESCALATION),
    ("Chat transferred to an agent.", EventType.ESCALATION),
    ("Личность клиента подтверждена.", EventType.IDENTITY_VERIFIED),
    ("Клиенттің жеке басы расталды.", EventType.IDENTITY_VERIFIED),
    ("Customer identity verified.", EventType.IDENTITY_VERIFIED),
    ("Оператор закрыл чат.", EventType.OPERATOR_CLOSED),
    ("Оператор чатты жапты.", EventType.OPERATOR_CLOSED),
    ("Agent closed the chat.", EventType.OPERATOR_CLOSED),
    ("Клиент покинул чат.", EventType.CLIENT_LEFT),
    ("Клиент завершил разговор.", EventType.CLIENT_LEFT),
    ("Соединение с клиентом прервано.", EventType.CONNECTION_LOST),
    ("Клиентпен байланыс үзілді.", EventType.CONNECTION_LOST),
    ("Connection to customer lost.", EventType.CONNECTION_LOST),
    ("Клиент находится на удержании.", EventType.HOLD_START),
    ("Клиент больше не находится на удержании.", EventType.HOLD_END),
    ("Запись и транскрибирование приостановлены.", EventType.RECORDING),
    ("Что-то совсем другое.", None),
])
def test_classify_system_event(text, expected):
    assert classify_system_event(text) == expected


def test_facts(conversation):
    facts = extract_facts(conversation, wait_threshold_sec=120)
    assert facts.first_operator_idx == 5
    assert facts.last_operator_idx == 12
    assert facts.client_messages_before_operator == [0, 3]
    assert [e.type for e in facts.events] == [
        EventType.ESCALATION, EventType.IDENTITY_VERIFIED, EventType.OPERATOR_CLOSED,
    ]
    assert facts.ended_by == EventType.OPERATOR_CLOSED


def test_pauses_skip_first_reaction_and_measure_from_first_unanswered(conversation):
    facts = extract_facts(conversation, wait_threshold_sec=120)
    # Первая реакция (00:54 → 02:38) не считается; 03:18 → 04:07 короче порога;
    # 04:43 → 09:57 — ожидание от первой неотвеченной реплики клиента.
    assert [(p.after_idx, p.reply_idx, round(p.seconds)) for p in facts.pauses] == [(8, 11, 314)]


def test_format_transcript(conversation):
    lines = format_transcript(conversation).splitlines()
    assert lines[0] == "[1] 00:00 КЛИЕНТ: В портфеле не вижу бумагу. Сделок не делал."
    assert lines[5].startswith("[6] 02:38 ОПЕРАТОР: Здравствуйте, я Соня.")


def test_format_facts_mentions_key_events(conversation):
    text = format_facts(extract_facts(conversation))
    assert "Первая реплика оператора: [6]" in text   # номера с 1
    assert "[10]: личность клиента подтверждена" in text
    assert "Долгое ожидание: клиент [9] → оператор [12], 05:14" in text
