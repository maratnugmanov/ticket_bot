from __future__ import annotations
import enum
from src.core.config import settings


class RoleName(enum.StrEnum):
    ADMIN = enum.auto()
    MANAGER = enum.auto()
    ENGINEER = enum.auto()
    GUEST = enum.auto()


class DeviceTypeName(enum.StrEnum):
    ROUTER = enum.auto()
    IP_DEVICE = enum.auto()
    TVE_DEVICE = enum.auto()
    POWER_UNIT = enum.auto()
    NETWORK_HUB = enum.auto()


class ValidationMode(enum.StrEnum):
    OPTIONAL_NEW = enum.auto()
    REQUIRED_EXISTING = enum.auto()


class CallbackData(enum.StrEnum):
    @staticmethod
    def _generate_next_value_(name, start, count, last_values):
        return "cb_" + name.lower()

    # DeviceTypeName
    ROUTER = enum.auto()
    IP_DEVICE = enum.auto()
    TVE_DEVICE = enum.auto()
    POWER_UNIT = enum.auto()
    NETWORK_HUB = enum.auto()

    ADD_DEVICE = enum.auto()
    INSTALL_DEVICE = enum.auto()
    RETURN_DEVICE = enum.auto()
    INSTALL_RETURN = enum.auto()
    DELETE_DEVICE = enum.auto()
    DELETE_WRITEOFF_DEVICE = enum.auto()

    ADD_TICKET = enum.auto()
    EDIT_TICKET = enum.auto()
    EDIT_TICKET_NUMBER = enum.auto()
    ENTER_CONTRACT_NUMBER = enum.auto()
    EDIT_CONTRACT_NUMBER = enum.auto()
    CLOSE_TICKET = enum.auto()
    REOPEN_TICKET = enum.auto()
    DELETE_TICKET = enum.auto()
    CONFIRM_DELETE_TICKET = enum.auto()
    CHANGED_MY_MIND = enum.auto()
    RETURN_TO_TICKETS = enum.auto()
    RETURN_TO_MAIN_MENU = enum.auto()
    EDIT_DEVICE_SERIAL_NUMBER = enum.auto()
    EDIT_DEVICE_TYPE = enum.auto()

    ADD_WRITEOFF_DEVICE = enum.auto()
    EDIT_WRITEOFF_DEVICE_TYPE = enum.auto()
    EDIT_WRITEOFF_DEVICE_SERIAL_NUMBER = enum.auto()

    TICKETS = enum.auto()
    WRITEOFF_DEVICES = enum.auto()
    FORM_REPORT = enum.auto()
    DISABLE_HIRING = enum.auto()
    ENABLE_HIRING = enum.auto()

    DEVICE_0 = enum.auto()
    DEVICE_1 = enum.auto()
    DEVICE_2 = enum.auto()
    DEVICE_3 = enum.auto()
    DEVICE_4 = enum.auto()
    DEVICE_5 = enum.auto()
    DEVICE_6 = enum.auto()
    DEVICE_7 = enum.auto()
    DEVICE_8 = enum.auto()
    DEVICE_9 = enum.auto()
    DEVICE_10 = enum.auto()
    DEVICE_11 = enum.auto()
    DEVICE_12 = enum.auto()
    DEVICE_13 = enum.auto()
    DEVICE_14 = enum.auto()
    DEVICE_15 = enum.auto()

    TICKET_0 = enum.auto()
    TICKET_1 = enum.auto()
    TICKET_2 = enum.auto()
    TICKET_3 = enum.auto()
    TICKET_4 = enum.auto()
    TICKET_5 = enum.auto()
    TICKET_6 = enum.auto()
    TICKET_7 = enum.auto()
    TICKET_8 = enum.auto()
    TICKET_9 = enum.auto()
    TICKET_10 = enum.auto()
    TICKET_11 = enum.auto()
    TICKET_12 = enum.auto()
    TICKET_13 = enum.auto()
    TICKET_14 = enum.auto()
    TICKET_15 = enum.auto()

    # QUIT_WITHOUT_SAVING = enum.auto()
    # CONFIRM_QUIT = enum.auto()
    # CHANGED_MY_MIND_BTN = enum.auto()
    # CONFIRM_CLOSE_TICKET_BTN = enum.auto()

    PREV_ONES = enum.auto()
    NEXT_ONES = enum.auto()


class Action(enum.StrEnum):
    @staticmethod
    def _generate_next_value_(name, start, count, last_values):
        return "ac_" + name.lower()

    TICKETS = enum.auto()
    ENTER_TICKET_NUMBER = enum.auto()
    EDIT_TICKET_NUMBER = enum.auto()
    ENTER_CONTRACT_NUMBER = enum.auto()
    EDIT_CONTRACT_NUMBER = enum.auto()
    PICK_DEVICE_TYPE = enum.auto()
    EDIT_DEVICE_TYPE = enum.auto()
    PICK_INSTALL_OR_RETURN = enum.auto()
    EDIT_INSTALL_OR_RETURN = enum.auto()
    ENTER_DEVICE_SERIAL_NUMBER = enum.auto()
    EDIT_DEVICE_SERIAL_NUMBER = enum.auto()
    PICK_TICKET_ACTION = enum.auto()
    CONFIRM_DELETE_TICKET = enum.auto()
    PICK_DEVICE_ACTION = enum.auto()
    WRITEOFF_DEVICES = enum.auto()
    PICK_WRITEOFF_DEVICE_TYPE = enum.auto()
    EDIT_WRITEOFF_DEVICE_TYPE = enum.auto()
    ENTER_WRITEOFF_DEVICE_SERIAL_NUMBER = enum.auto()
    EDIT_WRITEOFF_DEVICE_SERIAL_NUMBER = enum.auto()
    PICK_WRITEOFF_DEVICE_ACTION = enum.auto()
    # CLOSE_TICKET = enum.auto()
    # CONFIRM_QUIT_WITHOUT_SAVING = enum.auto()
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


class String(enum.StrEnum):
    # RoleName
    ADMIN = "Администратор"
    MANAGER = "РГКС"
    ENGINEER = "СИ"
    GUEST = "Гость"
    # DeviceTypeName
    ROUTER = "Роутер"
    IP_DEVICE = "IP-приставка"
    TVE_DEVICE = "TVE-приставка"
    POWER_UNIT = "Блок питания"
    NETWORK_HUB = "Свитч"

    JAN = "янв"
    FEB = "фев"
    MAR = "мар"
    APR = "апр"
    MAY = "май"
    JUN = "июн"
    JUL = "июл"
    AUG = "авг"
    SEP = "сен"
    OCT = "окт"
    NOV = "ноя"
    DEC = "дек"

    # Main Menu Buttons
    ADD_TICKET_BTN = "➕ Добавить заявку"
    TICKETS_BTN = "🗓 Заявки"
    WRITEOFF_DEVICES_BTN = "💩 Брак"
    FORM_REPORT_BTN = "🖨 Сформировать отчет"
    DISABLE_HIRING_BTN = "🙅‍♀️ Закрыть найм"
    ENABLE_HIRING_BTN = "🙋‍♀️ Открыть найм"
    # Main Menu Texts
    PICK_A_FUNCTION = "Выберите функцию"
    NO_FUNCTIONS_ARE_AVAILABLE = "У вас нет доступа к каким-либо функциям"
    # Error Related
    ERROR_DETECTED = "Обнаружена ошибка"
    CONFIGURATION_ERROR_DETECTED = "Обнаружена ошибка конфигурации"
    INCONSISTENT_STATE_DETECTED = "Обнаружена несогласованность диалога"
    CONTACT_THE_ADMINISTRATOR = "Пожалуйста обратитесь к администратору"
    # Common Phrases
    GOT_UNEXPECTED_DATA = "Ваш выбор не распознан"
    FROM_OPTIONS_BELOW = "из предложенных ниже вариантов"
    # Menus Strings
    PICK_TICKET_ACTION = "Возможные действия: изменение номера текущей заявки/договора, изменение/добавление/удаление устройств, закрытие/открытие заявки и выход без сохранения"
    PICK_DEVICE_ACTION = "Возможные действия: изменение серийного номера и типа устройства, смена производимого над ним действия, возврат в предыдущее меню или удаление устройства из данной заявки"
    PICK_TICKETS_ACTION = "Возможные действия: добавление новой заявки, изменение/удаление старых заявок и возврат в главное меню"
    PICK_WRITEOFF_DEVICE_ACTION = "Возможные действия: изменение серийного номера и типа бракованного устройства, возврат в предыдущее меню или удаление устройства из брака"
    PICK_WRITEOFF_DEVICES_ACTION = "Возможные действия: добавление/изменение/удаление брака или возврат в предыдущее меню"
    # Ticket Strings
    ENTER_TICKET_NUMBER = "Введите номер заявки"  # Ticket creation entry point
    EDIT_TICKET_NUMBER = "Изменить номер заявки"
    ENTER_NEW_TICKET_NUMBER = "Введите новый номер заявки"
    TICKET_NUMBER_WAS_EDITED = "Номер заявки был изменен"
    TICKET_NUMBER_REMAINS_THE_SAME = "Номер заявки остался прежним"
    INCORRECT_TICKET_NUMBER = f"Номер заявки должен состоять из 1-{settings.ticket_number_max_length} цифр и не может быть равен нулю, попробуйте снова"
    ENTER_CONTRACT_NUMBER = "Введите номер договора"
    EDIT_CONTRACT_NUMBER = "Изменить номер договора"
    ENTER_NEW_CONTRACT_NUMBER = "Введите новый номер договора"
    CONTRACT_NUMBER_WAS_EDITED = "Номер договора был изменен"
    CONTRACT_NUMBER_REMAINS_THE_SAME = "Номер договора остался прежним"
    INCORRECT_CONTRACT_NUMBER = f"Номер договора должен состоять из 1-{settings.contract_number_max_length} цифр и не может быть равен нулю, попробуйте снова"
    TICKET_REOPENED = "Заявка возвращена в работу"
    TICKET_CLOSED = "Заявка закрыта"
    CLOSE_TICKET_NUMBER_X = "Удалить заявку №"
    CONFIRM_TICKET_DELETION = "Подтвердите удаление текущей заявки"
    DELETE_TICKET_ACTION_WAS_NOT_PICKED = (
        "Вы не выбрали удаление заявки или возврат к работе с ней"
    )
    TICKET_DELETED = "Заявка удалена"
    TICKET_DELETION_CANCELLED = "Удаление заявки отменено"
    # Ticket Error Strings
    TICKET_WAS_NOT_FOUND = "Заявка не найдена, возможно она была удалена"
    FOREIGN_TICKET = "Заявка принадлежит другому пользователю, доступ ограничен"
    GOT_DATA_NOT_TICKET_NUMBER = "Вы нажали кнопку, а должны были ввести номер заявки"
    GOT_DATA_NOT_CONTRACT_NUMBER = (
        "Вы нажали кнопку, а должны были ввести номер договора"
    )
    # Common Icons
    EDIT_ICON = "✏️"
    ATTENTION_ICON = "❗"
    TRASHCAN_ICON = "🗑"
    # Device Buttons
    INSTALL_DEVICE_ICON = "✅"
    RETURN_DEVICE_ICON = "↪️"
    UNSET_DEVICE_ICON = "❓"
    INSTALL_DEVICE_BTN = "Установка"
    RETURN_DEVICE_BTN = "Возврат"
    INSTALL_RETURN_BTN = "Установка/Возврат"
    # Common Tags
    EDIT = "[ Ред ]"
    ERROR = "[ Ошибка ]"
    DISPOSABLE = "[ Расходник ]"
    # Ticket Buttons
    CLOSED_TICKET_ICON = "✔"  # Former icon ⚙
    OPEN_TICKET_ICON = "🧰"
    TICKET_NUMBER_BTN = "Заявка №"
    CONTRACT_NUMBER_BTN = "Договор №"
    ADD_DEVICE_BTN = "➕ Добавить устройство"
    CLOSE_TICKET_BTN = "Закрыть заявку"
    REOPEN_TICKET_BTN = "Вернуть заявку в работу"
    DELETE_TICKET_BTN = "Удалить заявку (необратимо)"
    THE_LIMIT_OF = "Максимальный лимит в"
    DEVICES_REACHED = "устройств был достигнут"
    TICKETS = "Заявки"
    MAIN_MENU = "Главное меню"
    # Common Buttons
    PREV_ONES = "< Предыдущие"
    NEXT_ONES = "Следующие >"
    DONE_BTN = "Готово"

    EDIT_DEVICE = "Изменить устройство"
    EDIT_TICKET = "Изменить имеющуюся заявку"
    EDIT_WRITEOFF_DEVICE = "Изменить брак"

    RETURN_BTN = "<< Назад"
    RETURNING_TO_TICKET = "Возвращаемся в заявку >>"
    TO_MAIN_MENU = "В главное меню"
    RETURNING_TO_WRITEOFF_DEVICES = "<< Возвращаемся к списку бракованных устройств"

    PICK_DEVICE_TYPE = "Выберите тип устройства"
    PICK_WRITEOFF_DEVICE_TYPE = "Выберите тип бракованного устройства"
    PICK_NEW_WRITEOFF_DEVICE_TYPE = "Выберите новый тип бракованного устройства"
    WRITEOFF_DEVICE_NOT_FOUND = (
        "Бракованное устройство не найдено, возможно оно было удалено"
    )
    WRITEOFF_DEVICE_IS_INCORRECT = "Бракованное устройство содержит ошибку"
    EDIT_DEVICE_TYPE = "✏️ Изменить тип устройства"
    PICK_NEW_DEVICE_TYPE = "Выберите новый тип устройства"
    DEVICE_ADDED = "Устройство добавлено"
    DEVICE_NOT_FOUND = "Устройство не найдено, возможно оно было удалено"
    DEVICE_TYPE_NOT_FOUND = "Тип устройства не найден"
    DEVICE_TYPE_HAS_NO_SERIAL_NUMBER = (
        "Серийный номер для этого типа устройства не предусмотрен"
    )
    DEVICE_TYPE_WAS_CHANGED_FOR = "Тип устройства изменен на"
    DEVICE_TYPE_WAS_NOT_PICKED = "Вы не выбрали тип устройства"
    DEVICE_TYPE_IS_DISABLED = "Выбранный тип устройства в данный момент не используется"
    DEVICE_TYPE_IS_DISPOSABLE = "Выбранный тип устройства не нуждается в учете списания"
    DEVICE_TYPE_WAS_EDITED = "Тип устройства был изменен"
    DEVICE_TYPE_REMAINS_THE_SAME = "Тип устройства остался прежним"
    NO_ACTIVE_DEVICE_TYPE_AVAILABLE = "Ни один тип устройства в данный момент не активен, работа с новыми заявками невозможна"
    NO_WRITEOFF_DEVICE_TYPE_AVAILABLE = "Ни один тип бракованного устройства в данный момент не доступен, добавление брака невозможно"
    ENTER_SERIAL_NUMBER = "Введите серийный номер"
    INCORRECT_SERIAL_NUMBER = f"Серийный номер должен состоять из 1-{settings.serial_number_max_length} букв латинского алфавита и/или цифр и не может быть равен нулю, попробуйте снова"
    GOT_DATA_NOT_SERIAL_NUMBER = (
        "Вы нажали кнопку, а должны были ввести серийный номер устройства"
    )
    EDIT_SERIAL_NUMBER = "✏️ Изменить серийный номер"
    ENTER_NEW_SERIAL_NUMBER = "Введите новый серийный номер"
    SERIAL_NUMBER_WAS_EDITED = "Серийный номер был изменен"
    SERIAL_NUMBER_REMAINS_THE_SAME = "Серийный номер остался прежним"
    PICK_INSTALL_OR_RETURN = "Выберите установку или возврат устройства"
    # SERIAL_NUMBER_RECOGNIZED = (
    #     "Серийный номер опознан: устройство с домашнего склада. Выберите действие."
    # )
    EDIT_INSTALL_OR_RETURN = "✏️ Изменить действие с устройством"
    INSTALL_OR_RETURN_WAS_EDITED = "Действие с устройством было изменено"
    INSTALL_OR_RETURN_REMAINS_THE_SAME = "Действие с устройством осталось прежним"
    DEVICE_ACTION_WAS_NOT_PICKED = "Вы не выбрали действие с устройством"
    WRITEOFF_DEVICE_ACTION_WAS_NOT_PICKED = (
        "Вы не выбрали действие с бракованным устройством"
    )
    TICKET_ACTION_WAS_NOT_PICKED = "Вы не выбрали действие с текущей заявкой"
    ACTION_WAS_NOT_PICKED = "Вы не выбрали действие"
    MESSAGE_HAS_EXPIRED = "Сообщение устарело"
    DEVICE_ACTION_WAS_CHANGED_FOR = "Тип действия с устройством изменен на"
    DELETE_DEVICE_FROM_WRITEOFF = "🗑 Удалить устройство из брака"
    DEVICE_WAS_DELETED_FROM_WRITEOFF = "🗑 Устройство удалено из брака"
    TICKET_NUMBER_TIP = "[ Изменить номер заявки ]"
    CONTRACT_NUMBER_TIP = "[ Изменить номер договора ]"
    QUIT_WITHOUT_SAVING_BTN = "🗑 Выйти без сохранения"
    ARE_YOU_SURE_YOU_WANT_TO_QUIT_WITHOUT_SAVING = "‼ВНИМАНИЕ‼: Все несохраненные данные будут потеряны, вы уверены что хотите выйти без сохранения"
    QUIT_WITHOUT_SAVING_ACTION_WAS_NOT_PICKED = "Вы не выбрали выйти или остаться"
    CONFIRM_DELETE_TICKET = "❌ Да, удалить заявку"
    CHANGED_MY_MIND = "Я передумал"
    YOU_QUIT_WITHOUT_SAVING = "Вы вышли без сохранения текущей заявки"
    YOU_LEFT_TICKET = "Вы закончили работу с заявкой"
    YOU_LEFT_TICKETS = "Вы закончили работу с заявками"
    YOU_LEFT_WRITEOFF_DEVICES = "Вы закончили работу с бракованными устройствами"
    YOU_LEFT_WRITEOFF_DEVICE = "Вы закончили работу с бракованным устройством"
    CONFIRM_YOU_WANT_TO_CLOSE_TICKET = "Подтвердите что вы произвели все необходимые действия по заявке и уверены что готовы ее закрыть"
    CLOSE_TICKET_ACTION_WAS_NOT_PICKED = (
        "Вы не выбрали закрыть заявку или вернуться к работе с ней"
    )
    CONFIRM_CLOSE_TICKET_BTN = "⚙ Да, закрыть заявку"
    TICKET_CLOSE_FAILED = "🚫 Ошибка: не удалось сохранить и закрыть заявку, проверьте введенные данные или попробуйте позже"
    YOU_CLOSED_TICKET = "Вы успешно закрыли заявку"
    YOU_ADDED_WRITEOFF_DEVICE = "Вы успешно добавили бракованное устройство"
    NUMBER_SYMBOL = "№"
    WITH_X = "с"
    FROM_X = "от"
    X_DEVICE = "устройством"
    X_DEVICES = "устройствами"
    DELETE_DEVICE_FROM_TICKET = "🗑 Удалить устройство из заявки"
    DEVICE_WAS_DELETED_FROM_TICKET = "🗑 Устройство удалено из заявки"
    HIRING_ENABLED_TIP = (
        "Соискателям необходимо отправить любое сообщение со своего Телеграм аккаунта"
    )
    HIRING_ENABLED = f"Найм открыт. {HIRING_ENABLED_TIP}"
    HIRING_ALREADY_ENABLED = f"Найм уже открыт. {HIRING_ENABLED_TIP}"
    HIRING_DISABLED_TIP = "Если найм закрыт у всех менеджеров, то все незарегистрированные соискатели будут удалены из базы данных"
    HIRING_DISABLED = f"Найм закрыт. {HIRING_DISABLED_TIP}"
    HIRING_ALREADY_DISABLED = f"Найм уже закрыт. {HIRING_DISABLED_TIP}"
    # YOU_HAVE_PICKED = "Вы выбрали"

    ADD_WRITEOFF_DEVICE_BTN = "➕ Добавить брак"


class Script(enum.StrEnum):
    @staticmethod
    def _generate_next_value_(name, start, count, last_values):
        return "sc_" + name.lower()

    INITIAL_DATA = enum.auto()
    FROM_HISTORY = enum.auto()
    FROM_WRITEOFF = enum.auto()
