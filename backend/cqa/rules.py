"""Детерминированные признаки грубых ошибок в репликах оператора — второй сигнал, независимый от модели.

Правила не выставляют оценку: если модель ответила «Нет» по грубым ошибкам, а правило нашло признак,
критерий помечается «требует проверки» (валидатор `critical_phrases` в validation.py). Грубые ошибки —
самое дорогое для бизнеса, поэтому пропуск модели должен увидеть человек.

Шаблоны описывают общие формулировки (RU / KZ / EN), а не фразы конкретных диалогов; ложное
срабатывание стоит лишь лишнего взгляда проверяющего.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cqa.models import Conversation
from cqa.transcript import ConversationFacts, EventType

SUBTYPE_TITLES = {
    "insult": "оскорбление клиента",
    "bypass": "услуга в обход компании",
    "disclosure": "данные счёта до подтверждения личности",
}


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


INSULT = _rx(
    r"читать (не )?умеете", r"\bдура?к", r"\bидиот", r"\bтуп(ой|ая|ые|ица|ость)\b", r"бестолк", r"отстаньте",
    r"заткн", r"не морочьте", r"\bstupid\b", r"\bidiot", r"can'?t you read", r"\bdumb\b", r"shut up",
    r"ақымақ", r"миың", r"оқи (алмайсыз|білмейсіз)",
)

BYPASS = _rx(
    # деньги лично оператору
    r"(мне|лично)\b.{0,40}(на карту|переве|перечисл|скин)", r"(на карту|переведите|скиньте).{0,30}\bмне\b",
    r"\bmy (card|account)\b.{0,40}(send|transfer)", r"send (me|it to me)\b.{0,40}money", r"money.{0,30}\bto me\b",
    r"маған.{0,30}(аудар|салы)", r"картама",
    # частная платная консультация, мимо компании
    r"частн\w* консультац", r"через компанию.{0,20}не (надо|нужно)", r"private (advice|consult)",
    r"outside (of )?the company", r"личн\w* номер.{0,120}(плат|за деньги)", r"(плат|за деньги).{0,120}личн\w* номер",
    r"personal (phone )?number.{0,120}(charge|fee|pay)", r"(charge|fee|pay).{0,120}personal (phone )?number",
    r"жеке нөмір.{0,120}ақылы", r"ақылы.{0,120}жеке нөмір",
)

# оператор называет данные конкретного счёта — признак раскрытия, если личность ещё не подтверждена;
# [^.!?] — в пределах одного предложения, (?<!не ) — «не вижу» не считается
ACCOUNT_DATA = _rx(
    r"(?<!не )(вижу|посмотрел\w*|видно|проверил\w*)[^.!?]{0,40}ваш\w* сч[её]т",
    r"на вашем сч[её]те (есть|стоит|установлен|висит)",
    r"\bI (can )?see\b[^.!?]{0,40}\b(on|in) your account", r"\bon your account (there is|there's|is)\b",
    r"шотыңызда[^.!?]{0,60}(көріп тұрмын|бар|қойыл)",
)
SKIP_VERIFICATION = _rx(
    r"подтверждени\w* (пока |сейчас |тут )?не нуж", r"идентификаци\w* (пока |сейчас )?не нуж",
    r"без (подтверждения|идентификации)", r"\bskip (that|it|the verification|verification)\b",
    r"no need (for|to) (verif|confirm)", r"растаудың (қазір )?қажеті жоқ", r"растаудың керегі жоқ",
)


@dataclass(frozen=True)
class Hit:
    subtype: str
    msg_idx: int
    text: str

    def describe(self) -> str:
        quote = self.text if len(self.text) <= 80 else self.text[:77] + "…"
        return f"{SUBTYPE_TITLES[self.subtype]} — реплика {self.msg_idx + 1} («{quote}»)"


def find_critical(conv: Conversation, facts: ConversationFacts) -> list[Hit]:
    """Признаки грубых ошибок в репликах оператора, по одному на подтип (первое срабатывание)."""
    verified = [e.idx for e in facts.events_of(EventType.IDENTITY_VERIFIED)]
    first_verified = min(verified) if verified else None
    hits: dict[str, Hit] = {}
    for m in conv.transcript:
        if m.speaker != "operator":
            continue
        before_verification = first_verified is None or m.idx < first_verified
        checks = [("insult", INSULT.search(m.text)), ("bypass", BYPASS.search(m.text)),
                  ("disclosure", before_verification and (ACCOUNT_DATA.search(m.text)
                                                          or SKIP_VERIFICATION.search(m.text)))]
        for subtype, found in checks:
            if found and subtype not in hits:
                hits[subtype] = Hit(subtype, m.idx, " ".join(m.text.split()))
    return list(hits.values())
