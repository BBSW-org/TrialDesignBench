from __future__ import annotations

from pathlib import Path
from typing import Any

from pypdf import PdfWriter

from trialdesignbench.mathpix import MathpixClient


class FakeTransport:
    def __init__(self) -> None:
        self.submitted_file: Path | None = None
        self.submitted_data: dict[str, str] = {}

    def post_multipart(
        self,
        url: str,
        *,
        headers: dict[str, str],
        file_path: Path,
        data: dict[str, str],
    ) -> dict[str, Any]:
        assert url.endswith("/pdf")
        assert headers == {"app_id": "app-id", "app_key": "app-key"}
        self.submitted_file = file_path
        self.submitted_data = data
        return {"pdf_id": "pdf-123"}

    def get_json(self, url: str, *, headers: dict[str, str]) -> dict[str, Any]:
        assert headers == {"app_id": "app-id", "app_key": "app-key"}
        if url.endswith("/pdf/pdf-123"):
            return {"status": "completed", "percent_done": 100}
        if url.endswith("/converter/pdf-123"):
            return {"conversion_status": {"tex.zip": {"status": "completed"}}}
        raise AssertionError(f"unexpected URL: {url}")

    def get_bytes(self, url: str, *, headers: dict[str, str]) -> bytes:
        assert headers == {"app_id": "app-id", "app_key": "app-key"}
        if url.endswith("/pdf/pdf-123.mmd"):
            return b"# Statistical Analysis Plan\n\nSample size is 120."
        if url.endswith("/pdf/pdf-123.tex.zip"):
            return b"zip-bytes"
        raise AssertionError(f"unexpected URL: {url}")


def test_mathpix_client_converts_pdf_and_saves_artifacts(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sap.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    transport = FakeTransport()
    client = MathpixClient(
        app_id="app-id",
        app_key="app-key",
        transport=transport,
    )

    artifact = client.convert_pdf(
        pdf_path,
        tmp_path / "converted",
        save_tex_zip=True,
        poll_interval_seconds=0,
        timeout_seconds=1,
    )

    assert artifact.pdf_id == "pdf-123"
    assert artifact.text_path.read_text(encoding="utf-8").startswith("# Statistical")
    assert artifact.tex_zip_path is not None
    assert artifact.tex_zip_path.read_bytes() == b"zip-bytes"
    assert artifact.metadata_path.exists()
    assert transport.submitted_file == pdf_path.resolve()
    assert '"tex.zip": true' in transport.submitted_data["options_json"]
