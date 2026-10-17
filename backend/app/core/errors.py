class AppError(Exception):
    def __init__(self, status_code: int, message: str, code: str = "REQUEST_FAILED"):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code

