"""Loopback-only, capability-scoped evidence review. No external requests."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import io
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit, parse_qs

from .confirmation import _load_json_object, ConfirmationRequestError
from . import review_assets, workflow


def _highlight_bbox(image, locator: dict, *, source_size: tuple[int, int] | None = None) -> str:
    """Draw only validated evidence coordinates; otherwise leave the page whole."""
    from PIL import ImageDraw

    width, height = image.size
    bbox = locator.get("bbox_1000")
    precision = str(locator.get("position_precision") or "exact")
    coordinates = None
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            values = [float(value) for value in bbox]
        except (TypeError, ValueError):
            values = []
        if len(values) == 4 and 0 <= values[0] < values[2] <= 1000 and 0 <= values[1] < values[3] <= 1000:
            coordinates = [values[0] * width / 1000, values[1] * height / 1000,
                           values[2] * width / 1000, values[3] * height / 1000]
    raw_bbox = locator.get("bbox")
    if coordinates is None and source_size and isinstance(raw_bbox, (list, tuple)) and len(raw_bbox) == 4:
        try:
            values = [float(value) for value in raw_bbox]
        except (TypeError, ValueError):
            values = []
        source_width, source_height = source_size
        if len(values) == 4 and source_width > 0 and source_height > 0 and (
            0 <= values[0] < values[2] <= source_width and 0 <= values[1] < values[3] <= source_height
        ):
            coordinates = [values[0] * width / source_width, values[1] * height / source_height,
                           values[2] * width / source_width, values[3] * height / source_height]
            precision = "approximate"
    if coordinates is None:
        return "unavailable"
    draw = ImageDraw.Draw(image, "RGBA")
    line_width = max(3, round(min(width, height) / 250))
    draw.rectangle(coordinates, fill=(255, 196, 0, 48), outline=(210, 45, 45, 255), width=line_width)
    return "approximate" if precision == "approximate" else "exact"


class ReviewServer:
    def __init__(self, extension) -> None:
        self.extension = extension
        self.sessions: dict[str, Path] = {}
        self.lock = threading.RLock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                pass  # Tokens, filenames and evidence must not enter access logs.

            def _send(self, status, data, mime="application/json; charset=utf-8", extra_headers=None):
                body = json.dumps(data, ensure_ascii=False).encode("utf-8") if isinstance(data, (dict, list)) else data
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                for name, value in (extra_headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def _path(self):
                host = f"127.0.0.1:{owner.http.server_port}"
                if self.headers.get("Host") != host:
                    raise PermissionError("请求地址不匹配。")
                origin = self.headers.get("Origin")
                if origin and origin != "http://" + host:
                    raise PermissionError("只接受当前本地审核页的请求。")
                return urlsplit(self.path)

            def _output(self):
                token = self.headers.get("X-PEG-Token", "")
                with owner.lock:
                    output = owner.sessions.get(token)
                if output is None:
                    raise PermissionError("审核会话无效，请通过 Skill 重新打开审核页。")
                owner.extension.app.state.touch()
                return output

            def do_GET(self):
                try:
                    url = self._path()
                    assets = {"/": (review_assets.HTML, "text/html; charset=utf-8"),
                              "/app.css": (review_assets.CSS, "text/css; charset=utf-8"),
                              "/app.js": (review_assets.JS, "text/javascript; charset=utf-8")}
                    if url.path in assets:
                        content, mime = assets[url.path]
                        return self._send(200, content.encode("utf-8"), mime)
                    output = self._output()
                    query = parse_qs(url.query)
                    if url.path == "/api/state":
                        data = owner.extension.snapshot(output, detailed=True)
                    elif url.path == "/api/preview":
                        with owner.extension.app._operation_lock:
                            data, mime, precision = owner.preview(output, query.get("candidate", [""])[0])
                        return self._send(200, data, mime, {"X-PEG-Evidence-Position": precision})
                    elif url.path == "/api/artifact":
                        with owner.extension.app._operation_lock:
                            _, product, _ = workflow.context(output)
                            manifest = workflow._manifest(output, product["run_summary"]["session_id"])
                            record = manifest["deliverables"].get(query.get("id", [""])[0])
                            if not record:
                                raise ConfirmationRequestError("交付件不存在。")
                            data = {"name": record["name"], "check": record["check"],
                                    "bundle_id": record["bundle_id"]}
                            if Path(record["path"]).suffix.lower() in {".txt", ".md"}:
                                data["text"] = workflow.read_content(Path(record["path"]))
                                if Path(record["path"]).parent == output / "drafts":
                                    data["draft_id"] = Path(record["path"]).stem
                    elif url.path == "/api/table":
                        with owner.extension.app._operation_lock:
                            result = workflow.export_table(output)
                            data = workflow.safe_file(Path(result["path"]))
                        return self._send(200, data, "text/csv; charset=utf-8")
                    else:
                        return self._send(404, {"error": "页面不存在。"})
                    self._send(200, data)
                except PermissionError as exc:
                    self._send(403, {"error": str(exc)})
                except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
                    self._send(400, {"error": str(exc)})

            def do_POST(self):
                try:
                    url = self._path()
                    output = self._output()
                    if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                        raise ValueError("请发送 JSON 请求。")
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= workflow.MAX_CONTENT_BYTES:
                        raise ValueError("请求大小无效。")
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(body, dict):
                        raise ValueError("请求必须为对象。")
                    data = owner.extension.review_action(output, url.path.removeprefix("/api/"), body)
                    self._send(200, data)
                except PermissionError as exc:
                    self._send(403, {"error": str(exc)})
                except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
                    self._send(400, {"error": str(exc)})

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True, name="peg-local-review")
        self.thread.start()

    def open(self, output: Path) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.sessions[token] = output
        return f"http://127.0.0.1:{self.http.server_port}/#token={token}"

    def close(self):
        self.http.shutdown()
        self.http.server_close()
        with self.lock:
            self.sessions.clear()

    @staticmethod
    def preview(output: Path, candidate_id: str):
        _, product, _ = workflow.context(output)
        candidate = next((c for c in product["candidates"] if c["candidate_id"] == candidate_id), None)
        if candidate is None:
            raise ConfirmationRequestError("该证据已经变化，请刷新审核页。")
        state = _load_json_object(output / "analysis-state.json")
        root = Path(state["input_root"]).resolve()
        source = root / candidate["source_file"]
        if not source.resolve().is_relative_to(root):
            raise ConfirmationRequestError("证据文件不在本次资料目录内。")
        raw = workflow.safe_file(source, max_bytes=100 * 1024 * 1024)
        if hashlib.sha256(raw).hexdigest() != candidate["file_hash"]:
            raise ConfirmationRequestError("来源文件已经更新，请重新分析后查看新证据。")
        suffix = source.suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            from PIL import Image
            image = Image.open(io.BytesIO(raw))
            if image.width * image.height > 40_000_000:
                raise ValueError("图片超过预览像素上限。")
            source_size = image.size
            image.thumbnail((1800, 1800))
            precision = _highlight_bbox(image, candidate["locator"], source_size=source_size)
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=88)
            image.close()
            return buffer.getvalue(), "image/jpeg", precision
        if suffix == ".pdf":
            import pypdfium2 as pdfium
            document = pdfium.PdfDocument(raw)
            try:
                index = int(candidate["locator"].get("page", 1)) - 1
                if not 0 <= index < len(document):
                    raise ValueError("证据页码无效。")
                page = document[index]
                try:
                    width, height = page.get_size()
                    scale = min(1.8, 1800 / max(width, height))
                    bitmap = page.render(scale=scale)
                    try:
                        image = bitmap.to_pil()
                        precision = _highlight_bbox(image, candidate["locator"])
                        buffer = io.BytesIO()
                        image.convert("RGB").save(buffer, format="JPEG", quality=88)
                        image.close()
                        return buffer.getvalue(), "image/jpeg", precision
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            finally:
                document.close()
        return {"text": candidate["raw_text"], "locator": candidate["locator"],
                "source": candidate["source_file"]}, "application/json; charset=utf-8", "text"
