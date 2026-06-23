import json
import re
from pathlib import Path
from urllib import request

from pydantic import BaseModel

from qagent.config import get_settings
from qagent.storage.repository import DeliveryOutboxRecord, QagentRepository


class DeliverySendItem(BaseModel):
    delivery_id: str
    channel: str
    status: str
    destination: str | None = None
    error: str | None = None


class DeliverySendResult(BaseModel):
    scanned: int
    sent: int
    failed: int
    dry_run: int
    items: list[DeliverySendItem]


def send_pending_deliveries(
    repo: QagentRepository,
    output_dir: Path | str | None = None,
    channel: str | None = None,
    webhook_url: str | None = None,
    dry_run: bool = False,
    limit: int = 20,
) -> DeliverySendResult:
    deliveries = repo.list_delivery_outbox(status="queued", limit=limit)
    if channel:
        deliveries = [delivery for delivery in deliveries if delivery.channel == channel]

    items: list[DeliverySendItem] = []
    sent = 0
    failed = 0
    dry_run_count = 0
    for delivery in deliveries:
        if dry_run:
            dry_run_count += 1
            items.append(
                DeliverySendItem(
                    delivery_id=delivery.delivery_id,
                    channel=delivery.channel,
                    status="dry_run",
                )
            )
            continue
        try:
            destination = _send_delivery(delivery, output_dir=output_dir, webhook_url=webhook_url)
        except Exception as exc:
            failed += 1
            items.append(
                DeliverySendItem(
                    delivery_id=delivery.delivery_id,
                    channel=delivery.channel,
                    status="failed",
                    error=str(exc),
                )
            )
            continue
        marked = repo.mark_delivery_sent(delivery.delivery_id)
        if marked is None:
            failed += 1
            items.append(
                DeliverySendItem(
                    delivery_id=delivery.delivery_id,
                    channel=delivery.channel,
                    status="failed",
                    error="delivery disappeared before it could be marked sent",
                )
            )
            continue
        sent += 1
        items.append(
            DeliverySendItem(
                delivery_id=delivery.delivery_id,
                channel=delivery.channel,
                status="sent",
                destination=destination,
            )
        )

    return DeliverySendResult(
        scanned=len(deliveries),
        sent=sent,
        failed=failed,
        dry_run=dry_run_count,
        items=items,
    )


def _send_delivery(
    delivery: DeliveryOutboxRecord,
    output_dir: Path | str | None,
    webhook_url: str | None,
) -> str:
    if delivery.channel in {"markdown", "file"}:
        return _write_markdown_file(delivery, output_dir)
    if delivery.channel == "webhook":
        if not webhook_url:
            raise ValueError("webhook_url is required for webhook deliveries")
        return _post_webhook(delivery, webhook_url)
    raise ValueError(f"unsupported delivery channel: {delivery.channel}")


def _write_markdown_file(delivery: DeliveryOutboxRecord, output_dir: Path | str | None) -> str:
    directory = Path(output_dir) if output_dir is not None else get_settings().data_dir / "outbox"
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{delivery.created_at.strftime('%Y%m%d%H%M%S')}-{_safe_filename(delivery.subject)}-{delivery.delivery_id}.md"
    path = directory / filename
    path.write_text(delivery.markdown, encoding="utf-8")
    return str(path)


def _post_webhook(delivery: DeliveryOutboxRecord, webhook_url: str) -> str:
    payload = json.dumps(
        {
            "delivery_id": delivery.delivery_id,
            "subject": delivery.subject,
            "markdown": delivery.markdown,
            "payload": delivery.payload,
        }
    ).encode("utf-8")
    req = request.Request(
        webhook_url,
        data=payload,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=10) as response:
        status = getattr(response, "status", 200)
        if status < 200 or status >= 300:
            raise RuntimeError(f"webhook returned HTTP {status}")
    return webhook_url


def _safe_filename(value: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return compact[:80] or "delivery"
