from __future__ import annotations
import enum


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

    INSTALL_DEVICE_BTN = enum.auto()
    RETURN_DEVICE_BTN = enum.auto()
    DELETE_DEVICE_BTN = enum.auto()
    DELETE_WRITEOFF_DEVICE_BTN = enum.auto()

    # ENABLE_HIRING = enum.auto()
    # DISABLE_HIRING = enum.auto()
    ENTER_TICKET_NUMBER = enum.auto()
    EDIT_TICKET_NUMBER = enum.auto()
    ENTER_CONTRACT_NUMBER = enum.auto()
    EDIT_CONTRACT_NUMBER = enum.auto()

    EDIT_DEVICE_SERIAL_NUMBER = enum.auto()
    EDIT_DEVICE_TYPE = enum.auto()
    EDIT_TICKET = enum.auto()

    EDIT_WRITEOFF_DEVICE_TYPE = enum.auto()
    EDIT_WRITEOFF_DEVICE_SERIAL_NUMBER = enum.auto()

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

    ADD_DEVICE_BTN = enum.auto()
    CLOSE_TICKET_BTN = enum.auto()
    QUIT_WITHOUT_SAVING_BTN = enum.auto()
    CONFIRM_QUIT_BTN = enum.auto()
    CHANGED_MY_MIND_BTN = enum.auto()
    CONFIRM_CLOSE_TICKET_BTN = enum.auto()
    RETURN_TO_MAIN_MENU = enum.auto()

    ADD_WRITEOFF_DEVICE_BTN = enum.auto()
    PREV_ONES = enum.auto()
    NEXT_ONES = enum.auto()


# icons: ✏️


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

    INSTALL_DEVICE_BTN = "✅ Установка"
    RETURN_DEVICE_BTN = "↪️ Возврат"
    EDIT = "[ Ред ]"
    ERROR = "[ Ошибка ]"

    CLOSE_TICKET_BTN = "⚙ Закрыть заявку"
    TICKETS_HISTORY_BTN = "🗓 История"
    WRITEOFF_DEVICES_BTN = "💩 Брак"
    FORM_REPORT_BTN = "🖨 Сформировать отчет"
    ENABLE_HIRING_BTN = "🙋‍♀️ Открыть найм"
    DISABLE_HIRING_BTN = "🙅‍♀️ Закрыть найм"

    ENTER_TICKET_NUMBER = "Введите номер заявки"
    INCORRECT_TICKET_NUMBER = "Номер заявки должен состоять из цифр и не может быть равен нулю, попробуйте снова"
    GOT_DATA_NOT_TICKET_NUMBER = "Вы нажали кнопку, а должны были ввести номер заявки"
    EDIT_TICKET_NUMBER = "✏️ Изменить номер заявки"
    ENTER_NEW_TICKET_NUMBER = "Введите новый номер заявки"
    TICKET_NUMBER_WAS_EDITED = "Номер заявки был скорректирован"
    TICKET_NUMBER_REMAINS_THE_SAME = "Номер заявки остался прежним"

    ENTER_CONTRACT_NUMBER = "Введите номер договора"
    INCORRECT_CONTRACT_NUMBER = "Номер договора должен состоять из цифр и не может быть равен нулю, попробуйте снова"
    GOT_DATA_NOT_CONTRACT_NUMBER = (
        "Вы нажали кнопку, а должны были ввести номер договора"
    )
    EDIT_CONTRACT_NUMBER = "✏️ Изменить номер договора"
    ENTER_NEW_CONTRACT_NUMBER = "Введите новый номер договора"
    CONTRACT_NUMBER_WAS_EDITED = "Номер договора был скорректирован"
    CONTRACT_NUMBER_REMAINS_THE_SAME = "Номер договора остался прежним"

    EDIT_DEVICE = "✏️ Изменить устройство"
    EDIT_CLOSED_TICKET = "✏️ Изменить закрытую заявку"
    EDIT_WRITEOFF_DEVICE = "✏️ Изменить бракованное устройство"

    RETURNING_TO_TICKET = "<< Возвращаемся в заявку"
    RETURNING_TO_MAIN_MENU = "<< Возвращаемся в главное меню"
    RETURNING_TO_WRITEOFF_DEVICES = "<< Возвращаемся к списку бракованных устройств"
    RETURN_BTN = "<< Назад"

    PICK_DEVICE_TYPE = "Выберите тип устройства"
    PICK_WRITEOFF_DEVICE_TYPE = "Выберите тип бракованного устройства"
    PICK_NEW_WRITEOFF_DEVICE_TYPE = "Выберите новый тип бракованного устройства"
    WRITEOFF_DEVICE_WAS_NOT_FOUND = (
        "Бракованное устройство не найдено в базе данных, возможно оно было удалено"
    )
    WRITEOFF_DEVICE_IS_INCORRECT = (
        "Бракованное устройство содержит ошибку, пожалуйста обратитесь к администратору"
    )
    EDIT_DEVICE_TYPE = "✏️ Изменить тип устройства"
    PICK_NEW_DEVICE_TYPE = "Выберите новый тип устройства"
    FROM_OPTIONS_BELOW = "из предложенных ниже вариантов"
    DEVICE_TYPE_WAS_CHANGED_FOR = "Тип устройства изменен на"
    DEVICE_TYPE_WAS_NOT_PICKED = "Вы не выбрали тип устройства"
    DEVICE_TYPE_IS_DISABLED = "Выбранный тип устройства в данный момент не используется"
    DEVICE_TYPE_IS_DISPOSABLE = "Выбранный тип устройства не нуждается в учете списания"
    DEVICE_TYPE_WAS_EDITED = "Тип устройства был изменен"
    DEVICE_TYPE_REMAINS_THE_SAME = "Тип устройства остался прежним"
    NO_DEVICE_TYPE_AVAILABLE = "Ни один тип устройства в данный момент не доступен, работа с заявками невозможна. обратитесь к администратору"
    NO_WRITEOFF_DEVICE_TYPE_AVAILABLE = "Ни один тип бракованного устройства в данный момент не доступен, добавление брака невозможно. обратитесь к администратору"
    ENTER_SERIAL_NUMBER = "Введите серийный номер"
    INCORRECT_SERIAL_NUMBER = "Серийный номер должен состоять из букв латинского алфавита и/или цифр и не может быть равен нулю, попробуйте снова"
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
    GOT_UNEXPECTED_DATA = "Ваш выбор не распознан"
    MESSAGE_HAS_EXPIRED = "Сообщение устарело"
    DEVICE_ACTION_WAS_CHANGED_FOR = "Тип действия с устройством изменен на"
    PICK_TICKET_ACTION = "Возможные действия: изменение номера текущей заявки/договора, изменение/добавление/удаление устройств, закрытие заявки и выход без сохранения"
    PICK_DEVICE_ACTION = "Возможные действия: изменение серийного номера и типа устройства, смена производимого над ним действия, возврат в предыдущее меню или удаление устройства из данной заявки"
    PICK_TICKETS_HISTORY_ACTION = "Возможные действия: изменение/удаление закрытых заявок или возврат в предыдущее меню"
    PICK_WRITEOFF_DEVICE_ACTION = "Возможные действия: изменение серийного номера и типа бракованного устройства, возврат в предыдущее меню или удаление устройства из брака"
    PICK_WRITEOFF_DEVICES_ACTION = "Возможные действия: добавление/изменение/удаление брака или возврат в предыдущее меню"
    DELETE_DEVICE_FROM_WRITEOFF = "🗑 Удалить устройство из брака"
    DEVICE_WAS_DELETED_FROM_WRITEOFF = "🗑 Устройство удалено из брака"
    TICKET_NUMBER_BTN = "Заявка №"
    TICKET_NUMBER_TIP = "[ Изменить номер заявки ]"
    CONTRACT_NUMBER_BTN = "Договор №"
    CONTRACT_NUMBER_TIP = "[ Изменить номер договора ]"
    ADD_DEVICE_BTN = "➕ Добавить устройство"
    QUIT_WITHOUT_SAVING_BTN = "🗑 Выйти без сохранения"
    ARE_YOU_SURE_YOU_WANT_TO_QUIT_WITHOUT_SAVING = "‼ВНИМАНИЕ‼: Все несохраненные данные будут потеряны, вы уверены что хотите выйти без сохранения"
    QUIT_WITHOUT_SAVING_ACTION_WAS_NOT_PICKED = "Вы не выбрали выйти или остаться"
    CONFIRM_QUIT_BTN = "❌ Да, выйти"
    CHANGED_MY_MIND_BTN = "Я передумал"
    YOU_QUIT_WITHOUT_SAVING = "Вы вышли без сохранения текущей заявки"
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
    # YOU_HAVE_PICKED = "Вы выбрали"

    ADD_WRITEOFF_DEVICE_BTN = "➕ Добавить брак"
    PREV_ONES = "< Предыдущие"
    NEXT_ONES = "Следующие >"
    DONE_BTN = "Готово"


class Action(enum.StrEnum):
    @staticmethod
    def _generate_next_value_(name, start, count, last_values):
        return "ac_" + name.lower()

    ENTER_TICKET_NUMBER = enum.auto()
    EDIT_TICKET_NUMBER = enum.auto()
    ENTER_CONTRACT_NUMBER = enum.auto()
    EDIT_CONTRACT_NUMBER = enum.auto()
    PICK_DEVICE_TYPE = enum.auto()
    EDIT_DEVICE_TYPE = enum.auto()
    ENTER_DEVICE_SERIAL_NUMBER = enum.auto()
    EDIT_DEVICE_SERIAL_NUMBER = enum.auto()
    PICK_INSTALL_OR_RETURN = enum.auto()
    EDIT_INSTALL_OR_RETURN = enum.auto()
    PICK_TICKET_ACTION = enum.auto()
    PICK_DEVICE_ACTION = enum.auto()
    CONFIRM_CLOSE_TICKET = enum.auto()
    CONFIRM_QUIT_WITHOUT_SAVING = enum.auto()
    TICKETS_HISTORY = enum.auto()
    WRITEOFF_DEVICES = enum.auto()
    PICK_WRITEOFF_DEVICE_TYPE = enum.auto()
    ENTER_WRITEOFF_DEVICE_SERIAL_NUMBER = enum.auto()
    PICK_WRITEOFF_DEVICE_ACTION = enum.auto()
    EDIT_WRITEOFF_DEVICE_TYPE = enum.auto()
    EDIT_WRITEOFF_DEVICE_SERIAL_NUMBER = enum.auto()

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
