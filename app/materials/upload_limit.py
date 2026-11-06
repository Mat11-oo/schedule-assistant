"""Reject oversized multipart streams before the parser spools them to disk."""
import os
from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException


class TooLarge(HTTPException):
    def __init__(self):
        super().__init__(413, detail={"message": "上传内容超过限制，请拆分文件"})


class UploadLimitMiddleware:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/api/arrangements/uploads":
            return await self.app(scope, receive, send)
        limit = max(int(os.getenv("MATERIAL_MAX_AUDIO_MB", "200")), int(os.getenv("MATERIAL_MAX_PDF_MB", "50"))) * 1024 * 1024 + 65536
        size = 0
        async def bounded():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > limit: raise TooLarge()
            return message
        try:
            return await self.app(scope, bounded, send)
        except TooLarge:
            return await JSONResponse({"detail": {"message": "上传内容超过限制，请拆分文件"}}, status_code=413)(scope, receive, send)
