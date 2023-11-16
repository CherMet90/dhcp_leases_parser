from color_printer import print_red, print_yellow


class Error(Exception):
    error_messages = []

    def __init__(self, message, ip=None, is_critical=True):
        super().__init__(message)
        if is_critical:
            print_red(f"CriticalError: {message}")
        if ip is not None:
            self.store_error(ip, message)

    @classmethod
    def store_error(cls, ip, message):
        cls.error_messages.append({ip: message})


class NonCriticalError(Error):
    def __init__(self, message, ip=None, calling_function=None):
        if calling_function is not None:
            message = f"{calling_function} failed: {message}"
        print_yellow(f"NonCriticalError: {message}")
        super().__init__(message, ip, is_critical=False)
