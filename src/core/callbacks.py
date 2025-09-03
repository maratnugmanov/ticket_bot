from __future__ import annotations


class _MenuCallback:
    """Callback strings for menu-related actions."""

    MAIN = "menu:main"

    def main(self) -> str:
        return self.MAIN


class _UserCallback:
    """Callback strings for user-related actions."""

    SET_HIRING = "user:set:hiring"

    def set_hiring(self, enable: bool) -> str:
        return f"{self.SET_HIRING}:{int(enable)}"

    def enable_hiring(self) -> str:
        return self.set_hiring(enable=True)

    def disable_hiring(self) -> str:
        return self.set_hiring(enable=False)


class _TicketCallback:
    """Callback strings for ticket-related actions."""

    LIST = "tickets:list"
    VIEW = "ticket:view"
    CLOSE = "ticket:close"
    REOPEN = "ticket:reopen"
    EDIT_NUMBER = "ticket:edit:number"
    SET_NUMBER = "ticket:set:number"
    EDIT_CONTRACT = "ticket:edit:contract"
    SET_CONTRACT = "ticket:set:contract"
    ADD_DEVICE = "ticket:device:add"
    CREATE_DEVICE = "ticket:device:create"
    CREATE_START = "ticket:create:start"
    DELETE_START = "ticket:delete:start"
    DELETE_CONFIRM = "ticket:delete:confirm"

    def list_page(self, page: int = 0) -> str:
        return f"{self.LIST}:{page}"

    def view(self, id: int) -> str:
        return f"{self.VIEW}:{id}"

    def close(self, id: int) -> str:
        return f"{self.CLOSE}:{id}"

    def reopen(self, id: int) -> str:
        return f"{self.REOPEN}:{id}"

    def edit_number(self, id: int) -> str:
        return f"{self.EDIT_NUMBER}:{id}"

    def set_number(self, id: int) -> str:
        return f"{self.SET_NUMBER}:{id}"

    def edit_contract(self, id: int) -> str:
        return f"{self.EDIT_CONTRACT}:{id}"

    def set_contract(self, id: int) -> str:
        return f"{self.SET_CONTRACT}:{id}"

    def add_device(self, id: int) -> str:
        return f"{self.ADD_DEVICE}:{id}"

    def create_device(self, ticket_id: int, device_type_id: int) -> str:
        return f"{self.CREATE_DEVICE}:{ticket_id}:{device_type_id}"

    def create_start(self) -> str:
        return self.CREATE_START

    def delete_start(self, id: int) -> str:
        return f"{self.DELETE_START}:{id}"

    def delete_confirm(self, id: int) -> str:
        return f"{self.DELETE_CONFIRM}:{id}"


class _DeviceCallback:
    """Callback strings for device-related actions within a ticket."""

    VIEW = "device:view"
    EDIT_TYPE = "device:edit:type"
    EDIT_ACTION = "device:edit:action"
    SET_ACTION = "device:set:action"
    ADD_SERIAL_NUMBER = "device:add:serial_number"
    SET_SERIAL_NUMBER = "device:set:serial_number"
    EDIT_SERIAL_NUMBER = "device:edit:serial_number"
    DELETE = "device:delete"

    def view(self, id: int) -> str:
        return f"{self.VIEW}:{id}"

    def edit_type(self, id: int) -> str:
        return f"{self.EDIT_TYPE}:{id}"

    def edit_action(self, id: int) -> str:  # for install/return
        return f"{self.EDIT_ACTION}:{id}"

    def set_action(self, id: str, removal: bool) -> str:
        return f"{self.SET_ACTION}:{id}:{int(removal)}"

    def set_action_install(self, id) -> str:
        return self.set_action(id, removal=False)

    def set_action_return(self, id) -> str:
        return self.set_action(id, removal=True)

    def add_serial_number(self, id: int) -> str:
        return f"{self.ADD_SERIAL_NUMBER}:{id}"

    def set_serial_number(self, id: int) -> str:
        return f"{self.SET_SERIAL_NUMBER}:{id}"

    def edit_serial_number(self, id: int) -> str:
        return f"{self.EDIT_SERIAL_NUMBER}:{id}"

    def delete(self, id: int) -> str:
        return f"{self.DELETE}:{id}"


class _WriteoffCallback:
    """Callback strings for writeoff-related actions."""

    VIEW = "writeoff:view"
    LIST = "writeoffs:list"
    CREATE_START = "writeoff:create:start"

    def view(self, id: int) -> str:
        return f"{self.VIEW}:{id}"

    def list_page(self, page: int = 0) -> str:
        return f"{self.LIST}:{page}"

    def create_start(self) -> str:
        return self.CREATE_START


class _ReportCallback:
    """Callback strings for report-related actions."""

    CREATE_START = "report:create:start"

    def create_start(self) -> str:
        return self.CREATE_START


class CallbackDataBuilder:
    """
    A builder class for creating type-safe and consistent callback data strings.
    This avoids manual string formatting and reduces the risk of typos.
    """

    def __init__(self):
        self.menu = _MenuCallback()
        self.user = _UserCallback()
        self.ticket = _TicketCallback()
        self.device = _DeviceCallback()
        self.writeoff = _WriteoffCallback()
        self.report = _ReportCallback()


# A single, global instance of the builder to be used throughout the application.
cb = CallbackDataBuilder()
