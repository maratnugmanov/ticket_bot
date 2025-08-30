from __future__ import annotations


class _MenuCallback:
    """Callback strings for menu-related actions."""

    def main(self) -> str:
        return "menu:main"


class _UserCallback:
    """Callback strings for user-related actions."""

    def set_hiring(self, enable: bool) -> str:
        return f"user:set:hiring:{int(enable)}"

    def enable_hiring(self) -> str:
        return self.set_hiring(enable=True)

    def disable_hiring(self) -> str:
        return self.set_hiring(enable=False)


class _TicketCallback:
    """Callback strings for ticket-related actions."""

    def list_page(self, page: int = 0) -> str:
        return f"tickets:list:{page}"

    def view(self, id: int) -> str:
        return f"ticket:view:{id}"

    def close(self, id: int) -> str:
        return f"ticket:close:{id}"

    def reopen(self, id: int) -> str:
        return f"ticket:reopen:{id}"

    def edit_number(self, id: int) -> str:
        return f"ticket:edit:number:{id}"

    def edit_contract(self, id: int) -> str:
        return f"ticket:edit:contract:{id}"

    def add_device(self, id: int) -> str:
        return f"ticket:device:add:{id}"

    def create_start(self) -> str:
        return "ticket:create:start"

    def delete_start(self, id: int) -> str:
        return f"ticket:delete:start:{id}"

    def delete_confirm(self, id: int) -> str:
        return f"ticket:delete:confirm:{id}"


class _DeviceCallback:
    """Callback strings for device-related actions within a ticket."""

    def view(self, id: int) -> str:
        return f"device:view:{id}"

    def edit_type(self, id: int) -> str:
        return f"device:edit:type:{id}"

    def edit_action(self, id: int) -> str:  # for install/return
        return f"device:edit:action:{id}"

    def edit_serial_number(self, id: int) -> str:
        return f"device:edit:serial_number:{id}"

    def delete(self, id: int) -> str:
        return f"device:delete:{id}"


class _WriteoffCallback:
    """Callback strings for writeoff-related actions."""

    def view(self, id: int) -> str:
        return f"writeoff:view:{id}"

    def list_page(self, page: int = 0) -> str:
        return f"writeoffs:list:{page}"

    def create_start(self) -> str:
        return "writeoff:create:start"


class _ReportCallback:
    """Callback strings for report-related actions."""

    def create_start(self) -> str:
        return "report:create:start"


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
