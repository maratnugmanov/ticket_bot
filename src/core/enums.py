from __future__ import annotations
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


class CallbackData(enum.StrEnum):
    @staticmethod
    def _generate_next_value_(name, start, count, last_values):
        return "cb_" + name.lower()

    IP = enum.auto()
    TVE = enum.auto()
    ROUTER = enum.auto()

    INSTALL_DEVICE_BTN = enum.auto()
    REMOVE_DEVICE_BTN = enum.auto()
    EDIT_DEVICE_SN_BTN = enum.auto()

    ENABLE_HIRING = enum.auto()
    DISABLE_HIRING = enum.auto()
    ENTER_TICKET_NUMBER = enum.auto()
    EDIT_TICKET_NUMBER = enum.auto()
    ENTER_CONTRACT_NUMBER = enum.auto()
    EDIT_CONTRACT_NUMBER = enum.auto()

    TICKETS_HISTORY_BTN = enum.auto()
    WRITEOFF_DEVICES_BTN = enum.auto()
    FORM_REPORT_BTN = enum.auto()
    DISABLE_HIRING_BTN = enum.auto()
    ENABLE_HIRING_BTN = enum.auto()

    DEVICE_0 = enum.auto()
    DEVICE_1 = enum.auto()
    DEVICE_2 = enum.auto()
    DEVICE_3 = enum.auto()
    DEVICE_4 = enum.auto()
    DEVICE_5 = enum.auto()

    ADD_DEVICE_BTN = enum.auto()
    CLOSE_TICKET_BTN = enum.auto()
    QUIT_WITHOUT_SAVING_BTN = enum.auto()


class String(enum.StrEnum):
    # RoleName
    ADMIN = "Администратор"
    MANAGER = "РГКС"
    ENGINEER = "СИ"
    GUEST = "Гость"
    # DeviceTypeName
    IP = "IP"
    TVE = "TVE"
    ROUTER = "Роутер"

    INSTALL_DEVICE_BTN = "✅ Установить"
    REMOVE_DEVICE_BTN = "↪️ Забрать"
    EDIT_DEVICE_SN_BTN = "Ввели неверный номер?"

    CLOSE_TICKET_BTN = "⚙ Закрыть заявку"
    TICKETS_HISTORY_BTN = "🗓 История"
    WRITEOFF_DEVICES_BTN = "☠ Брак"
    FORM_REPORT_BTN = "🖨 Сформировать отчет"
    ENABLE_HIRING_BTN = "🙋‍♀️ Открыть найм"
    DISABLE_HIRING_BTN = "🙅‍♀️ Закрыть найм"
    ENTER_TICKET_NUMBER = "Введите номер заявки"
    INCORRECT_TICKET_NUMBER = "Номер заявки должен состоять из цифр, попробуйте снова"
    GOT_DATA_NOT_TICKET_NUMBER = "Вы нажали кнопку, а должны были ввести номер заявки"
    EDIT_TICKET_NUMBER = "Изменить номер заявки"
    ENTER_NEW_TICKET_NUMBER = "Введите новый номер заявки"
    ENTER_CONTRACT_NUMBER = "Введите номер договора"
    INCORRECT_CONTRACT_NUMBER = (
        "Номер договора должен состоять из цифр, попробуйте снова"
    )
    GOT_DATA_NOT_CONTRACT_NUMBER = (
        "Вы нажали кнопку, а должны были ввести номер договора"
    )
    EDIT_CONTRACT_NUMBER = "Изменить номер договора"
    ENTER_NEW_CONTRACT_NUMBER = "Введите новый номер договора"

    PICK_DEVICE_TYPE = "Выберите тип устройства"
    FROM_OPTIONS_BELOW = "из предложенных ниже вариантов"
    DEVICE_TYPE_WAS_NOT_PICKED = "Вы не выбрали тип устройства"
    ENTER_SERIAL_NUMBER = "Введите серийный номер"
    INCORRECT_SERIAL_NUM = "Серийный номер должен состоять из букв латинского алфавита и/или цифр, попробуйте снова"
    GOT_DATA_NOT_SERIAL_NUMBER = (
        "Вы нажали кнопку, а должны были ввести серийный номер устройства"
    )
    PICK_INSTALL_OR_RETURN = "Выберите установку или забор устройства"
    # SERIAL_NUMBER_RECOGNIZED = (
    #     "Серийный номер опознан: устройство с домашнего склада. Выберите действие."
    # )
    DEVICE_ACTION_WAS_NOT_PICKED = "Вы не выбрали действие с устройством"
    TICKET_ACTION_WAS_NOT_PICKED = "Вы не выбрали действие с текущей заявкой"
    GOT_UNEXPECTED_DATA = "Ваш выбор не распознан"
    PICK_TICKET_ACTION = "Возможные действия: изменение номера текущей заявки/договора, изменение/добавление/удаление устройств, закрытие заявки и полный выход без сохранения"
    TICKET_NUMBER_BTN = "Заявка №"
    CONTRACT_NUMBER_BTN = "Договор №"
    S_N = "с/н"
    ADD_DEVICE_BTN = "Добавить устройство"
    QUIT_WITHOUT_SAVING_BTN = "Выйти без сохранения"
    PICK_A_FUNCTION = "Выберите функцию"
    NO_FUNCTIONS_ARE_AVAILABLE = "У вас нет доступа к каким-либо функциям"
    HIRING_ENABLED_TIP = (
        "Соискателям необходимо отправить любое сообщение со своего Телеграм аккаунта."
    )
    HIRING_ENABLED = f"Найм открыт. {HIRING_ENABLED_TIP}"
    HIRING_ALREADY_ENABLED = f"Найм уже открыт. {HIRING_ENABLED_TIP}"
    HIRING_DISABLED_TIP = "Если найм закрыт у всех менеджеров, то все незарегистрированные пользователи будут удалены из базы данных."
    HIRING_DISABLED = f"Найм закрыт. {HIRING_DISABLED_TIP}"
    HIRING_ALREADY_DISABLED = f"Найм уже закрыт. {HIRING_DISABLED_TIP}"
    # MESSAGE_HAS_EXPIRED = "Сообщение устарело"
    # YOU_HAVE_PICKED = "Вы выбрали"


class Action(enum.StrEnum):
    @staticmethod
    def _generate_next_value_(name, start, count, last_values):
        return "ac_" + name.lower()

    ENTER_TICKET_NUMBER = enum.auto()
    ENTER_CONTRACT_NUMBER = enum.auto()
    PICK_DEVICE_TYPE = enum.auto()
    ENTER_SERIAL_NUMBER = enum.auto()
    PICK_INSTALL_OR_RETURN = enum.auto()
    TICKET_MENU = enum.auto()
    DEVICE_MENU = enum.auto()
    # ENABLE_HIRING = enum.auto()
    # DISABLE_HIRING = enum.auto()
    # INTRODUCTION_MAINMENU_BUTTONS = enum.auto()
    # TICKETS_HISTORY_MENU_BUTTONS = enum.auto()
    # WRITEOFF_DEVICES_LIST = enum.auto()
    # FORM_REPORT = enum.auto()
    # INITIAL_TICKET_NUMBER_INPUT = enum.auto()
    # INITIAL_DEVICE_TYPE_BUTTONS = enum.auto()
    # INITIAL_SERIAL_NUMBER_INPUT = enum.auto()
    # INITIAL_INSTALL_OR_RETURN_BUTTONS = enum.auto()
    # DEVICE_REMOVE_DEVICE_BUTTONS = enum.auto()
    # NEXT_DEVICE_TYPE_BUTTONS = enum.auto()
    # NEXT_SERIAL_NUMBER_INPUT = enum.auto()
    # NEXT_INSTALL_OR_RETURN_BUTTONS = enum.auto()
    # CLOSE_TICKET_CONFIRM_BUTTONS = enum.auto()
    # QUIT_WITHOUT_SAVING_BUTTONS = enum.auto()


class Script(enum.StrEnum):
    @staticmethod
    def _generate_next_value_(name, start, count, last_values):
        return "sc_" + name.lower()

    INITIAL_DATA = enum.auto()
    FROM_HISTORY = enum.auto()
    FROM_WRITEOFF = enum.auto()
