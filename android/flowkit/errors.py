class JobError(RuntimeError):
    """Lỗi khi xử lý job, kèm mã lỗi gửi về server (chữ hoa, số, '_', tối đa 50 ký tự)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
