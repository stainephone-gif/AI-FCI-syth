from aiogram.fsm.state import State, StatesGroup


class EditFlow(StatesGroup):
    remark = State()  # ждём текст замечания к черновику
    time = State()  # ждём время публикации
