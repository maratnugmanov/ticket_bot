import enum


class RoleName(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    ENGINEER = "engineer"
    GUEST = "guest"


class DeviceTypeName(str, enum.Enum):
    IP = "IP"
    TVE = "TVE"
    ROUTER = "Router"  # Russian?


class DialogueStrings(str, enum.Enum):
    INSTALL_BTN = "✅ Установить"
    REMOVE_BTN = "↪️ Забрать"
    EDIT_SN_BTN = "Ввели неверный номер?"
    TICKET_NUMBER_BTN = "Заявка № "
    ADD_DEVICE_BTN = "Добавить устройство"
    # REMOVE_DEVICE_BTN = "Удалить устройство"
    CLOSE_TICKET_BTN = "Закрыть заявку"
    # QUIT_WITHOUT_SAVING = "Выйти без сохранения"
    TICKET_DEVICES_LIST = "Возможные действия: изменение номера текущей заявки, изменение/удаление добавленных устройств, закрытие заявки и полный выход без сохранения."
    INCORRECT_TICKET_NUMBER = "Номер заявки должен состоять из цифр. Попробуйте снова. "
    TICKET_NUMBER_INPUT = "Введите номер заявки."
    DEVICE_TYPE_INPUT = "Выберите тип устройства."
    SERIAL_NUMBER_INPUT = "Введите серийный номер устройства."
    INSTALL_RETURN_INPUT = (
        "Серийный номер опознан: устройство с домашнего склада. Выберите действие."
    )
    HELLO_TO = "Здравствуйте, "
    THESE_FUNCTIONS_ARE_AVAILABLE = ", вам доступны следующие функции."
    HIRING_ENABLED_TIP = "Соискателям необходимо отправить мне любое сообщение со своего Телеграм аккаунта."
    HIRING_ENABLED = ", найм открыт. " + HIRING_ENABLED_TIP
    HIRING_ALREADY_ENABLED = ", найм уже открыт. " + HIRING_ENABLED_TIP
    HIRING_DISABLED_TIP = "Если найм закрыт у всех менеджеров, то все незарегистрированные пользователи будут удалены из базы данных."
    HIRING_DISABLED = ", найм закрыт. " + HIRING_DISABLED_TIP
    HIRING_ALREADY_DISABLED = ", найм уже закрыт. " + HIRING_DISABLED_TIP


class Scenario(str, enum.Enum):
    def _generate_next_value_(name, start, count, last_values):
        return name.lower()

    ENABLE_HIRING = enum.auto()
    DISABLE_HIRING = enum.auto()
    # INTRODUCTION_MAINMENU_BUTTONS = enum.auto()
    TICKETS_HISTORY_MENU_BUTTONS = enum.auto()
    WRITEOFF_DEVICES_LIST = enum.auto()
    INITIAL_TICKET_NUMBER_INPUT = enum.auto()
    INITIAL_DEVICE_TYPE_BUTTONS = enum.auto()
    INITIAL_SERIAL_NUMBER_INPUT = enum.auto()
    INITIAL_INSTALL_OR_RETURN_BUTTONS = enum.auto()
    TICKET_DEVICES_MENU_BUTTONS = enum.auto()
    EDIT_TICKET_NUMBER_INPUT = enum.auto()
    DEVICE_N_MENU_BUTTONS = enum.auto()
    DEVICE_N_DEVICE_TYPE_BUTTONS = enum.auto()
    DEVICE_N_SERIAL_NUMBER_INPUT = enum.auto()
    DEVICE_N_INSTALL_OR_RETURN_BUTTONS = enum.auto()
    DEVICE_N_REMOVE_DEVICE_BUTTONS = enum.auto()
    NEXT_DEVICE_TYPE_BUTTONS = enum.auto()
    NEXT_SERIAL_NUMBER_INPUT = enum.auto()
    NEXT_INSTALL_OR_RETURN_BUTTONS = enum.auto()
    CLOSE_TICKET_CONFIRM_BUTTONS = enum.auto()
    QUIT_WITHOUT_SAVING_BUTTONS = enum.auto()


class Modifier(str, enum.Enum):
    def _generate_next_value_(name, start, count, last_values):
        return name.lower()

    INITIAL_DATA = enum.auto()
    FROM_HISTORY = enum.auto()
    FROM_WRITEOFF = enum.auto()
