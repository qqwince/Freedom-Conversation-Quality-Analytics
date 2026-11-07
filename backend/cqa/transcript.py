"""Подготовка диалога для модели: нумерованный транскрипт и факты, найденные кодом.

Всё, что надёжно определяется без модели (системные события, первая реплика оператора,
паузы), вычисляется здесь и передаётся в промпт готовыми фактами.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from cqa.models import Conversation, Message


class EventType(StrEnum):
    ESCALATION = "escalation"                # чат/звонок передан оператору
    IDENTITY_VERIFIED = "identity_verified"  # личность клиента подтверждена
    OPERATOR_CLOSED = "operator_closed"      # оператор закрыл чат
    CLIENT_LEFT = "client_left"              # клиент ушёл или завершил разговор
    CONNECTION_LOST = "connection_lost"      # связь с клиентом прервана
    HOLD_START = "hold_start"                # клиент поставлен на удержание
    HOLD_END = "hold_end"
    RECORDING = "recording"                  # служебные события записи звонка


EVENT_LABELS: dict[EventType, str] = {
    EventType.ESCALATION: "передача оператору",
    EventType.IDENTITY_VERIFIED: "личность клиента подтверждена",
    EventType.OPERATOR_CLOSED: "оператор закрыл чат",
    EventType.CLIENT_LEFT: "клиент покинул чат / завершил разговор",
    EventType.CONNECTION_LOST: "соединение с клиентом прервано",
    EventType.HOLD_START: "клиент на удержании",
    EventType.HOLD_END: "удержание снято",
    EventType.RECORDING: "служебное событие записи",
}

# Порядок важен: первое совпадение побеждает («больше не находится на удержании» раньше «на удержании»).
_EVENT_PATTERNS: list[tuple[EventType, re.Pattern[str]]] = [
    (EventType.IDENTITY_VERIFIED, re.compile(r"личность .*подтвержден|жеке басы расталды|identity verified", re.I)),
    (EventType.OPERATOR_CLOSED, re.compile(r"оператор закрыл|оператор чатты жапты|agent closed", re.I)),
    (EventType.CONNECTION_LOST, re.compile(r"соединение .*прерван|байланыс үзілді|connection .*lost", re.I)),
    (EventType.CLIENT_LEFT, re.compile(
        r"клиент (покинул|завершил)|клиент .*шықты|(customer|client) (left|ended)", re.I)),
    (EventType.HOLD_END, re.compile(r"больше не находится на удержании|no longer on hold", re.I)),
    (EventType.HOLD_START, re.compile(r"на удержании|on hold", re.I)),
    (EventType.ESCALATION, re.compile(
        r"эскалация|операторға жіберілді|transferred to an agent|агент присоединился|переведен .*представител"
        r"|консультаци[яю] с агентом", re.I)),
    (EventType.RECORDING, re.compile(r"запись и транскрибирование|жазу және транскрипция|transcription", re.I)),
]


def classify_system_event(text: str) -> EventType | None:
    for event_type, pattern in _EVENT_PATTERNS:
        if pattern.search(text):
            return event_type
    return None


@dataclass(frozen=True)
class SystemEvent:
    idx: int
    type: EventType
    text: str


@dataclass(frozen=True)
class Pause:
    after_idx: int      # реплика клиента, после которой ждали
    reply_idx: int      # ответ оператора
    seconds: float


@dataclass
class ConversationFacts:
    first_operator_idx: int | None
    last_operator_idx: int | None
    client_messages_before_operator: list[int]
    events: list[SystemEvent] = field(default_factory=list)
    pauses: list[Pause] = field(default_factory=list)
    ended_by: EventType | None = None   # событие после последней реплики оператора, если есть

    def events_of(self, event_type: EventType) -> list[SystemEvent]:
        return [e for e in self.events if e.type == event_type]


def extract_facts(conv: Conversation, wait_threshold_sec: float = 120) -> ConversationFacts:
    messages = conv.transcript
    operator_idx = [m.idx for m in messages if m.speaker == "operator"]
    first_op = operator_idx[0] if operator_idx else None
    last_op = operator_idx[-1] if operator_idx else None

    events = [
        SystemEvent(m.idx, event_type, m.text)
        for m in messages
        if m.speaker == "system" and (event_type := classify_system_event(m.text)) is not None
    ]

    ended_by = None
    if last_op is not None:
        terminal = {EventType.OPERATOR_CLOSED, EventType.CLIENT_LEFT, EventType.CONNECTION_LOST}
        after_last = [e for e in events if e.idx > last_op and e.type in terminal]
        ended_by = after_last[0].type if after_last else None

    return ConversationFacts(
        first_operator_idx=first_op,
        last_operator_idx=last_op,
        client_messages_before_operator=[
            m.idx for m in messages if m.speaker == "client" and (first_op is None or m.idx < first_op)
        ],
        events=events,
        pauses=_find_pauses(messages, first_op, wait_threshold_sec),
        ended_by=ended_by,
    )


def _find_pauses(messages: list[Message], first_op: int | None, threshold: float) -> list[Pause]:
    """Долгие ожидания ответа оператора после его первого ответа (первая реакция не оценивается).

    Пауза — время от первой неотвеченной реплики клиента до следующей реплики оператора.
    """
    if first_op is None:
        return []
    pauses: list[Pause] = []
    waiting_since: Message | None = None
    for m in messages:
        if m.idx <= first_op or m.offset_sec is None:
            continue
        if m.speaker == "client" and waiting_since is None:
            waiting_since = m
        elif m.speaker == "operator":
            if waiting_since is not None and waiting_since.offset_sec is not None:
                gap = m.offset_sec - waiting_since.offset_sec
                if gap > threshold:
                    pauses.append(Pause(waiting_since.idx, m.idx, gap))
            waiting_since = None
    return pauses


def format_offset(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


_SPEAKER_LABEL = {"client": "КЛИЕНТ", "operator": "ОПЕРАТОР", "bot": "БОТ", "system": "СИСТЕМА"}


def turn(idx: int) -> int:
    """Номер реплики для людей и модели: с 1 по исходному массиву (как в формате сдачи организаторов).
    Внутри системы реплики индексируются с 0."""
    return idx + 1


def format_transcript(conv: Conversation) -> str:
    """Транскрипт для промпта: `[номер] время РОЛЬ: текст`. Номер — с 1, время — от начала диалога."""
    lines = []
    for m in conv.transcript:
        text = " ".join(m.text.split())
        lines.append(f"[{turn(m.idx)}] {format_offset(m.offset_sec)} {_SPEAKER_LABEL[m.speaker]}: {text}")
    return "\n".join(lines)


def format_facts(facts: ConversationFacts) -> str:
    """Факты для промпта. Формулировки нейтральные: факты подсказывают, но не решают за модель."""
    lines = []
    if facts.first_operator_idx is None:
        lines.append("- Оператор в диалоге не отвечал.")
    else:
        lines.append(f"- Первая реплика оператора: [{turn(facts.first_operator_idx)}]. "
                     f"Последняя реплика оператора: [{turn(facts.last_operator_idx)}].")
    if facts.client_messages_before_operator:
        refs = ", ".join(f"[{turn(i)}]" for i in facts.client_messages_before_operator)
        lines.append(f"- Сообщения клиента до подключения оператора (оператору они известны): {refs}.")
    for e in facts.events:
        if e.type != EventType.RECORDING:
            lines.append(f"- Системное событие [{turn(e.idx)}]: {EVENT_LABELS[e.type]}.")
    if facts.ended_by is not None:
        lines.append(f"- После последней реплики оператора: {EVENT_LABELS[facts.ended_by]}.")
    if facts.pauses:
        for p in facts.pauses:
            lines.append(f"- Долгое ожидание: клиент [{turn(p.after_idx)}] → оператор [{turn(p.reply_idx)}], "
                         f"{format_offset(p.seconds)}.")
    else:
        lines.append("- Долгих ожиданий ответа оператора после его первого ответа нет.")
    return "\n".join(lines)
