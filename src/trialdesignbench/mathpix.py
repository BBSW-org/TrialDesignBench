"""Mathpix PDF conversion client for workflow step 1."""

from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from trialdesignbench.models import ConversionArtifact

MATHPIX_API_BASE_URL = "https://api.mathpix.com/v3"
DEFAULT_HTTP_TIMEOUT_SECONDS = 30.0


class MathpixError(RuntimeError):
    """Raised when Mathpix rejects a request or returns an unusable response."""


class MathpixTransport(Protocol):
    """Minimal transport surface used by `MathpixClient`."""

    def post_multipart(
        self,
        url: str,
        *,
        headers: dict[str, str],
        file_path: Path,
        data: dict[str, str],
    ) -> dict[str, Any]:
        """POST a multipart form-data request and return a JSON object."""

    def get_json(self, url: str, *, headers: dict[str, str]) -> dict[str, Any]:
        """GET a JSON object."""

    def get_bytes(self, url: str, *, headers: dict[str, str]) -> bytes:
        """GET raw bytes."""


@dataclass(frozen=True, slots=True)
class UrllibMathpixTransport:
    """`urllib` implementation of the Mathpix HTTP transport."""

    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS

    def post_multipart(
        self,
        url: str,
        *,
        headers: dict[str, str],
        file_path: Path,
        data: dict[str, str],
    ) -> dict[str, Any]:
        boundary = f"tdb-{uuid.uuid4().hex}"
        body = _encode_multipart(boundary=boundary, file_path=file_path, data=data)
        request_headers = {
            **headers,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        request = urllib.request.Request(
            url, data=body, headers=request_headers, method="POST"
        )
        return self._read_json(request)

    def get_json(self, url: str, *, headers: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(url, headers=headers, method="GET")
        return self._read_json(request)

    def get_bytes(self, url: str, *, headers: dict[str, str]) -> bytes:
        request = urllib.request.Request(url, headers=headers, method="GET")
        return self._request_bytes(request)

    def _request_bytes(self, request: urllib.request.Request) -> bytes:
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise MathpixError(_format_http_error(exc)) from exc
        except urllib.error.URLError as exc:
            raise MathpixError(f"Mathpix request failed: {exc.reason}") from exc

    def _read_json(self, request: urllib.request.Request) -> dict[str, Any]:
        payload = self._request_bytes(request)
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise MathpixError("Mathpix returned a non-JSON response") from exc
        if not isinstance(decoded, dict):
            raise MathpixError("Mathpix returned a JSON response that is not an object")
        if "error" in decoded:
            raise MathpixError(f"Mathpix returned an error: {decoded['error']}")
        return decoded


@dataclass(frozen=True, slots=True)
class MathpixClient:
    """Client for uploading PDFs to Mathpix and downloading converted text."""

    app_id: str
    app_key: str
    base_url: str = MATHPIX_API_BASE_URL
    transport: MathpixTransport | None = None
    http_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS

    def convert_pdf(
        self,
        pdf_path: Path,
        output_dir: Path,
        *,
        save_tex_zip: bool = False,
        poll_interval_seconds: float = 5.0,
        timeout_seconds: float = 600.0,
    ) -> ConversionArtifact:
        """Convert a PDF to Mathpix Markdown and optionally save a LaTeX ZIP."""
        source_pdf = pdf_path.expanduser().resolve()
        if not source_pdf.exists():
            raise FileNotFoundError(source_pdf)
        if source_pdf.suffix.lower() != ".pdf":
            msg = f"Expected a PDF file, got: {source_pdf}"
            raise ValueError(msg)

        destination = output_dir.expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)

        pdf_id = self.submit_pdf(source_pdf, save_tex_zip=save_tex_zip)
        status = self.wait_for_pdf(
            pdf_id,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=timeout_seconds,
        )

        stem = source_pdf.stem
        text_path = destination / f"{stem}.mmd"
        text_path.write_text(self.download_text(pdf_id), encoding="utf-8")

        tex_zip_path: Path | None = None
        if save_tex_zip:
            self.wait_for_conversion(
                pdf_id,
                "tex.zip",
                poll_interval_seconds=poll_interval_seconds,
                timeout_seconds=timeout_seconds,
            )
            tex_zip_path = destination / f"{stem}.tex.zip"
            tex_zip_path.write_bytes(self.download_bytes(pdf_id, "tex.zip"))

        metadata_path = destination / f"{stem}.mathpix.json"
        metadata = {
            "pdf_path": str(source_pdf),
            "pdf_id": pdf_id,
            "status": status,
            "text_path": str(text_path),
            "tex_zip_path": str(tex_zip_path) if tex_zip_path else None,
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        return ConversionArtifact(
            pdf_path=source_pdf,
            pdf_id=pdf_id,
            text_path=text_path,
            tex_zip_path=tex_zip_path,
            metadata_path=metadata_path,
            status=status,
        )

    def submit_pdf(self, pdf_path: Path, *, save_tex_zip: bool = False) -> str:
        """Submit a local PDF file to Mathpix for asynchronous OCR."""
        options: dict[str, Any] = {}
        if save_tex_zip:
            options["conversion_formats"] = {"tex.zip": True}
        data = {"options_json": json.dumps(options)} if options else {}
        response = self._transport().post_multipart(
            f"{self.base_url}/pdf",
            headers=self._headers(),
            file_path=pdf_path,
            data=data,
        )
        pdf_id = response.get("pdf_id")
        if not isinstance(pdf_id, str) or not pdf_id:
            raise MathpixError("Mathpix response did not include a pdf_id")
        return pdf_id

    def wait_for_pdf(
        self,
        pdf_id: str,
        *,
        poll_interval_seconds: float,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Poll Mathpix until PDF OCR completes or fails."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            status = self._transport().get_json(
                f"{self.base_url}/pdf/{pdf_id}",
                headers=self._headers(),
            )
            status_value = status.get("status")
            if status_value == "completed":
                return status
            if status_value == "error":
                raise MathpixError(f"Mathpix PDF processing failed: {status}")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for Mathpix PDF job {pdf_id}")
            time.sleep(poll_interval_seconds)

    def wait_for_conversion(
        self,
        pdf_id: str,
        conversion_format: str,
        *,
        poll_interval_seconds: float,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Poll Mathpix until a requested conversion format is ready."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            status = self._transport().get_json(
                f"{self.base_url}/converter/{pdf_id}",
                headers=self._headers(),
            )
            conversion_status = status.get("conversion_status")
            if isinstance(conversion_status, dict):
                format_status = conversion_status.get(conversion_format)
                if isinstance(format_status, dict):
                    state = format_status.get("status")
                    if state == "completed":
                        return status
                    if state == "error":
                        raise MathpixError(f"Mathpix conversion failed: {status}")
            if status.get("status") == "error":
                raise MathpixError(f"Mathpix conversion failed: {status}")
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for Mathpix {conversion_format} conversion {pdf_id}"
                )
            time.sleep(poll_interval_seconds)

    def download_text(self, pdf_id: str) -> str:
        """Download Mathpix Markdown for a completed PDF job."""
        return self.download_bytes(pdf_id, "mmd").decode("utf-8")

    def download_bytes(self, pdf_id: str, extension: str) -> bytes:
        """Download a completed Mathpix PDF result by extension."""
        return self._transport().get_bytes(
            f"{self.base_url}/pdf/{pdf_id}.{extension}",
            headers=self._headers(),
        )

    def _headers(self) -> dict[str, str]:
        return {"app_id": self.app_id, "app_key": self.app_key}

    def _transport(self) -> MathpixTransport:
        if self.transport is not None:
            return self.transport
        return UrllibMathpixTransport(timeout_seconds=self.http_timeout_seconds)


def _encode_multipart(*, boundary: str, file_path: Path, data: dict[str, str]) -> bytes:
    lines: list[bytes] = []
    for key, value in data.items():
        lines.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )

    content_type = mimetypes.guess_type(file_path.name)[0] or "application/pdf"
    lines.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="file"; '
                f'filename="{file_path.name}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return b"".join(lines)


def _format_http_error(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read().decode("utf-8")
    except UnicodeDecodeError:
        body = "<binary response>"
    return f"Mathpix HTTP {error.code}: {body}"
