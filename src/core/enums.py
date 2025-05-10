import enum


class RoleName(enum.StrEnum):
    ADMIN = enum.auto()
    MANAGER = enum.auto()
    ENGINEER = enum.auto()
    GUEST = enum.auto()


class DeviceTypeName(enum.StrEnum):
    IP = enum.auto()
    TVE = enum.auto()
    ROUTER = enum.auto()


class Strings(enum.StrEnum):
    IP = "IP"
    TVE = "TVE"
    ROUTER = "Роутер"
    ADMIN = "Администратор"
    MANAGER = "РГКС"
    ENGINEER = "СИ"
    GUEST = "Гость"
    INSTALL_DEVICE_BTN = "✅ Установить"
    REMOVE_DEVICE_BTN = "↪️ Забрать"
    EDIT_DEVICE_SN_BTN = "Ввели неверный номер?"
    TICKET_NUMBER_BTN = "Заявка №"
    ADD_DEVICE_BTN = "Добавить устройство"
    # REMOVE_DEVICE_BTN = "Удалить устройство"
    CLOSE_TICKET_BTN = "⚙ Закрыть заявку"
    TICKETS_HISTORY_BTN = "🗓 История"
    WRITEOFF_DEVICES_BTN = "☠ Брак"
    FORM_REPORT_BTN = "🖨 Сформировать отчет"
    QUIT_WITHOUT_SAVING_BTN = "Выйти без сохранения"
    ENABLE_HIRING_BTN = "🙋‍♀️ Открыть найм"
    DISABLE_HIRING_BTN = "🙅‍♀️ Закрыть найм"
    TICKET_DEVICES_LIST = "Возможные действия: изменение номера текущей заявки, изменение/удаление добавленных устройств, закрытие заявки и полный выход без сохранения."
    INCORRECT_TICKET_NUMBER = "Номер заявки должен состоять из цифр. Попробуйте снова."
    ENTER_TICKET_NUMBER = "Введите номер заявки."
    PICK_DEVICE_TYPE = "Выберите тип устройства"
    FROM_THESE_VARIANTS = "из предложенных ниже вариантов"
    DEVICE_TYPE_WAS_NOT_PICKED = "Вы не выбрали тип устройства"
    UNEXPECTED_CALLBACK = "Ваш выбор не распознан"
    DEVICE_TYPE_PICKED = "Выбран тип устройства"
    ENTER_SERIAL_NUMBER = "Введите серийный номер устройства"
    PICK_INSTALL_OR_RETURN = (
        "Серийный номер опознан: устройство с домашнего склада. Выберите действие."
    )
    HELLO = "Здравствуйте"
    THESE_FUNCTIONS_ARE_AVAILABLE = "вам доступны следующие функции."
    HIRING_ENABLED_TIP = "Соискателям необходимо отправить мне любое сообщение со своего Телеграм аккаунта."
    HIRING_ENABLED = f"найм открыт. {HIRING_ENABLED_TIP}"
    HIRING_ALREADY_ENABLED = f"найм уже открыт. {HIRING_ENABLED_TIP}"
    HIRING_DISABLED_TIP = "Если найм закрыт у всех менеджеров, то все незарегистрированные пользователи будут удалены из базы данных."
    HIRING_DISABLED = f"найм закрыт. {HIRING_DISABLED_TIP}"
    HIRING_ALREADY_DISABLED = f"найм уже закрыт. {HIRING_DISABLED_TIP}"


class Action(enum.StrEnum):
    ENABLE_HIRING = enum.auto()
    DISABLE_HIRING = enum.auto()
    # INTRODUCTION_MAINMENU_BUTTONS = enum.auto()
    TICKETS_HISTORY_MENU_BUTTONS = enum.auto()
    WRITEOFF_DEVICES_LIST = enum.auto()
    FORM_REPORT = enum.auto()
    # INITIAL_TICKET_NUMBER_INPUT = enum.auto()
    # INITIAL_DEVICE_TYPE_BUTTONS = enum.auto()
    # INITIAL_SERIAL_NUMBER_INPUT = enum.auto()
    INITIAL_INSTALL_OR_RETURN_BUTTONS = enum.auto()
    TICKET_DEVICES_MENU_BUTTONS = enum.auto()
    TICKET_NUMBER_INPUT = enum.auto()
    DEVICE_N_MENU_BUTTONS = enum.auto()
    DEVICE_TYPE_BUTTONS = enum.auto()
    DEVICE_SERIAL_NUMBER = enum.auto()
    DEVICE_N_INSTALL_OR_RETURN_BUTTONS = enum.auto()
    DEVICE_N_REMOVE_DEVICE_BUTTONS = enum.auto()
    NEXT_DEVICE_TYPE_BUTTONS = enum.auto()
    NEXT_SERIAL_NUMBER_INPUT = enum.auto()
    NEXT_INSTALL_OR_RETURN_BUTTONS = enum.auto()
    CLOSE_TICKET_CONFIRM_BUTTONS = enum.auto()
    QUIT_WITHOUT_SAVING_BUTTONS = enum.auto()


class Script(enum.StrEnum):
    INITIAL_DATA = enum.auto()
    FROM_HISTORY = enum.auto()
    FROM_WRITEOFF = enum.auto()
