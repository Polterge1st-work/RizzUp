from aiogram.fsm.state import State, StatesGroup


class UserState(StatesGroup):
    replying = State()          # режим ответа на сообщение
    replying_context = State()  # режим ответа с контекстом
    improving = State()         # режим улучшения сообщения
    starting = State()          # режим генерации первого сообщения
