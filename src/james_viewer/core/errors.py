"""User-facing error types. Every message here is safe to show in a dialog."""


class ViewerError(Exception):
    """An expected failure with a message written for the user."""


class UnsupportedFile(ViewerError):
    pass


class CorruptFile(ViewerError):
    pass


class PasswordRequired(ViewerError):
    pass


class WrongPassword(ViewerError):
    pass


class PermissionDenied(ViewerError):
    pass


class OfficeEngineError(ViewerError):
    pass
