"""The two-stage split and the Lambda handlers that drive it.

These matter more than most tests here, because the deployed topology is the one
thing that cannot be exercised locally end-to-end: stage 1 and stage 2 run in
separate Lambdas on opposite sides of a VPC boundary, and the only thing joining
them is `result.json`. So what is tested is exactly that contract —

* stage 1 writes everything stage 2 needs and touches no database;
* stage 2 reads it and needs nothing else;
* a failure survives the crossing as *data*, since an exception cannot;
* the handlers ack a bad file (a user error, not worth a DLQ) and raise on an
  infrastructure failure (worth retrying).

Every one of those is a property of the split rather than of the pipeline, and
each is a way the deployment could be silently broken while the local flow keeps
working perfectly.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.ingest import lambda_handler as handlers
from app.ingest.lambda_handler import (
    UnparseableEvent,
    committer_handler,
    parse_raw_key,
    processor_handler,
)
from app.ingest.storage import (
    get_object_store,
    raw_upload_key,
    upload_result_key,
    upload_status_key,
)
from app.schemas.uploads import UploadPhase
from app.services import notifications as notifications_service
from app.services import uploads as uploads_service
from app.services.ingestion import Stage1Result, commit_stage2, read_status, run_stage1
from app.schemas.uploads import CreateUploadRequest
from tests.test_uploads import CSV

OWNER = "u_ana"


def _staged(filename: str = "ventas.csv", data: bytes = CSV) -> str:
    """An upload whose bytes are in the store but which nothing has processed.

    Registered through the real service so the control-plane row exists, then
    the object written directly — which is precisely the state S3's
    ObjectCreated event describes.
    """
    upload, _url, _headers, _expires = uploads_service.create_upload(
        OWNER, CreateUploadRequest(filename=filename, sizeBytes=len(data))
    )
    get_object_store().put(raw_upload_key(OWNER, upload.id, filename), data)
    return upload.id


class TestStage1:
    def test_writes_everything_stage_2_needs(self) -> None:
        upload_id = _staged()
        result = run_stage1(OWNER, upload_id, "ventas.csv")

        assert result.error is None
        assert len(result.datasets) == 1
        new = result.datasets[0]
        # Every field stage 2 writes to the control plane has to be here — it
        # has no other source of truth on the far side of the VPC.
        assert new.table_name == "ventas"
        assert new.row_count == 3
        assert new.storage_key.endswith("data.parquet")
        assert new.schema.columns
        assert get_object_store().exists(new.storage_key)

    def test_leaves_the_result_where_stage_2_looks(self) -> None:
        upload_id = _staged()
        run_stage1(OWNER, upload_id, "ventas.csv")
        raw = get_object_store().get(upload_result_key(OWNER, upload_id))
        # Round-trips through JSON, because in production it crosses a process
        # boundary as bytes in S3.
        assert Stage1Result.from_json(raw).datasets[0].table_name == "ventas"

    def test_does_not_touch_the_control_plane(self) -> None:
        # The load-bearing property: outside the VPC there is no database to
        # touch, so stage 1 must leave the upload row exactly as it found it.
        upload_id = _staged()
        before = uploads_service.get_upload(OWNER, upload_id)
        run_stage1(OWNER, upload_id, "ventas.csv")
        after = uploads_service.get_upload(OWNER, upload_id)
        assert before is not None and after is not None
        assert after.status == before.status
        assert after.datasets == []

    def test_publishes_progress_where_a_dbless_process_can(self) -> None:
        upload_id = _staged()
        run_stage1(OWNER, upload_id, "ventas.csv")
        assert get_object_store().exists(upload_status_key(OWNER, upload_id))
        assert read_status(OWNER, upload_id) is UploadPhase.done

    def test_the_optional_phase_sink_is_called(self) -> None:
        upload_id = _staged()
        seen: list[UploadPhase] = []
        run_stage1(OWNER, upload_id, "ventas.csv", on_phase=seen.append)
        # The local runner uses this to drive the stepper from the control
        # plane; the deployed one passes nothing and relies on status.json.
        assert UploadPhase.extracting in seen
        assert seen[-1] is UploadPhase.done

    def test_a_bad_file_becomes_data_not_an_exception(self) -> None:
        upload_id = _staged("roto.csv", b"\x00\x01\x02 sin tabla")
        result = run_stage1(OWNER, upload_id, "roto.csv")

        # An exception could not cross into stage 2, which runs in a different
        # process — so the failure has to be recorded, not raised.
        assert result.error is not None
        assert result.datasets == []
        stored = Stage1Result.from_json(
            get_object_store().get(upload_result_key(OWNER, upload_id))
        )
        assert stored.error == result.error

    def test_a_missing_object_is_recorded_not_raised(self) -> None:
        upload, *_ = uploads_service.create_upload(
            OWNER, CreateUploadRequest(filename="fantasma.csv", sizeBytes=10)
        )
        result = run_stage1(OWNER, upload.id, "fantasma.csv")
        assert result.error is not None

    def test_the_raw_document_is_deleted_on_success_only(self) -> None:
        ok = _staged()
        run_stage1(OWNER, ok, "ventas.csv")
        assert not get_object_store().exists(raw_upload_key(OWNER, ok, "ventas.csv"))

        bad = _staged("roto.csv", b"\x00\x01\x02 nada")
        run_stage1(OWNER, bad, "roto.csv")
        # Kept, so `retry` has something to re-read.
        assert get_object_store().exists(raw_upload_key(OWNER, bad, "roto.csv"))

    def test_table_names_avoid_the_ones_it_is_told_about(self) -> None:
        # Stage 1 cannot query the control plane, so uniqueness depends on the
        # caller passing in what is already taken.
        upload_id = _staged()
        result = run_stage1(OWNER, upload_id, "ventas.csv", taken_table_names={"ventas"})
        assert result.datasets[0].table_name == "ventas_2"


class TestStage2:
    def test_commits_what_stage_1_left(self) -> None:
        upload_id = _staged()
        run_stage1(OWNER, upload_id, "ventas.csv")

        assert commit_stage2(OWNER, upload_id) is True
        upload = uploads_service.get_upload(OWNER, upload_id)
        assert upload is not None
        assert upload.status.value == "ready"
        assert upload.datasets[0].tableName == "ventas"

    def test_surfaces_a_stage_1_failure_to_the_user(self) -> None:
        upload_id = _staged("roto.csv", b"\x00\x01\x02 nada")
        run_stage1(OWNER, upload_id, "roto.csv")

        assert commit_stage2(OWNER, upload_id) is False
        upload = uploads_service.get_upload(OWNER, upload_id)
        assert upload is not None
        assert upload.status.value == "error"
        assert upload.error

    def test_tells_the_user_when_the_file_is_ready_to_review(self) -> None:
        upload_id = _staged("ventas_2025.csv")
        run_stage1(OWNER, upload_id, "ventas_2025.csv")
        before = len(notifications_service.list_notifications(OWNER))

        commit_stage2(OWNER, upload_id)

        listed = notifications_service.list_notifications(OWNER)
        assert len(listed) == before + 1
        assert listed[0].title == "ventas_2025.csv está listo para revisar"
        assert listed[0].tone == "ok" and not listed[0].read
        assert listed[0].action is not None and listed[0].action.href == "/ingesta"

    def test_tells_the_user_when_the_file_could_not_be_processed(self) -> None:
        upload_id = _staged("roto.csv", b"\x00\x01\x02 nada")
        run_stage1(OWNER, upload_id, "roto.csv")

        commit_stage2(OWNER, upload_id)

        latest = notifications_service.list_notifications(OWNER)[0]
        assert latest.title == "No pudimos procesar roto.csv"
        assert latest.tone == "err"

    def test_running_twice_does_not_duplicate_datasets(self) -> None:
        # SQS delivers at least once, so this genuinely happens.
        upload_id = _staged()
        run_stage1(OWNER, upload_id, "ventas.csv")
        commit_stage2(OWNER, upload_id)
        commit_stage2(OWNER, upload_id)

        upload = uploads_service.get_upload(OWNER, upload_id)
        assert upload is not None
        assert len(upload.datasets) == 1

    def test_a_missing_result_fails_the_upload_rather_than_hanging(self) -> None:
        upload, *_ = uploads_service.create_upload(
            OWNER, CreateUploadRequest(filename="v.csv", sizeBytes=10)
        )
        assert commit_stage2(OWNER, upload.id) is False
        current = uploads_service.get_upload(OWNER, upload.id)
        assert current is not None and current.status.value == "error"

    def test_a_corrupt_result_fails_the_upload(self) -> None:
        upload, *_ = uploads_service.create_upload(
            OWNER, CreateUploadRequest(filename="v.csv", sizeBytes=10)
        )
        get_object_store().put(upload_result_key(OWNER, upload.id), b"{not json")
        assert commit_stage2(OWNER, upload.id) is False


class TestKeyParsing:
    def test_round_trips_with_the_key_builder(self) -> None:
        key = raw_upload_key("u_ana", "up_1", "ventas 2024.csv")
        assert parse_raw_key(key) == ("u_ana", "up_1", "ventas 2024.csv")

    @pytest.mark.parametrize(
        "key",
        [
            "tenants/u_ana/datasets/ds_1/data.parquet",  # not a raw upload
            "uploads/u_ana",
            "uploads/u_ana/up_1",
            "uploads//up_1/v.csv",
            "",
        ],
    )
    def test_refuses_anything_else(self, key: str) -> None:
        with pytest.raises(UnparseableEvent):
            parse_raw_key(key)


class TestProcessorHandler:
    @staticmethod
    def _s3_event(key: str) -> dict:
        # The real shape: SQS wraps the S3 notification in a JSON string body.
        return {
            "Records": [
                {"body": json.dumps({"Records": [{"s3": {"object": {"key": key}}}]})}
            ]
        }

    def test_processes_an_s3_event(self, monkeypatch) -> None:
        invoked: list[tuple[str, str]] = []
        monkeypatch.setattr(
            handlers, "_invoke_committer", lambda o, u: invoked.append((o, u))
        )
        upload_id = _staged()
        key = raw_upload_key(OWNER, upload_id, "ventas.csv")

        assert processor_handler(self._s3_event(key)) == {"processed": 1}
        # Handing off to stage 2 is the processor's last act; without it the
        # dataset exists in S3 and nothing in the product knows.
        assert invoked == [(OWNER, upload_id)]

    def test_decodes_a_url_encoded_key(self, monkeypatch) -> None:
        monkeypatch.setattr(handlers, "_invoke_committer", lambda o, u: None)
        upload_id = _staged("mi archivo.csv")
        # S3 notifications percent-encode the key AND use `+` for spaces, which
        # is why `unquote_plus` is the right call and `unquote` is not.
        encoded = raw_upload_key(OWNER, upload_id, "mi archivo.csv").replace(" ", "+")

        assert processor_handler(self._s3_event(encoded)) == {"processed": 1}
        result = Stage1Result.from_json(
            get_object_store().get(upload_result_key(OWNER, upload_id))
        )
        assert result.error is None

    def test_handles_a_retry_invoke_without_a_filename(self, monkeypatch) -> None:
        monkeypatch.setattr(handlers, "_invoke_committer", lambda o, u: None)
        upload_id = _staged()
        # The API's retry sends only the ids, so the filename has to be
        # recovered from the object store.
        assert processor_handler({"ownerId": OWNER, "uploadId": upload_id}) == {"processed": 1}

    def test_a_bad_file_is_acknowledged_not_retried(self, monkeypatch) -> None:
        monkeypatch.setattr(handlers, "_invoke_committer", lambda o, u: None)
        upload_id = _staged("roto.csv", b"\x00\x01\x02 nada")
        key = raw_upload_key(OWNER, upload_id, "roto.csv")

        # Returning normally acks the SQS message. A user's broken spreadsheet
        # must not be redelivered three times and then parked in the DLQ.
        assert processor_handler(self._s3_event(key)) == {"processed": 1}

    def test_an_s3_test_event_is_ignored(self) -> None:
        # S3 sends one of these when a notification is first configured.
        event = {"Records": [{"body": json.dumps({"Event": "s3:TestEvent"})}]}
        assert processor_handler(event) == {"processed": 0, "skipped": 1}

    def test_a_malformed_event_is_ignored_rather_than_retried(self) -> None:
        assert processor_handler({"Records": [{"body": "not json"}]})["skipped"] == 1
        assert processor_handler({})["skipped"] == 1

    def test_a_missing_committer_name_raises_so_sqs_retries(self, monkeypatch) -> None:
        # The opposite of the case above: the work succeeded but nothing
        # recorded it, and result.json is sitting there ready. That must retry.
        monkeypatch.delenv("ALIBI_COMMITTER_FUNCTION_NAME", raising=False)
        upload_id = _staged()
        key = raw_upload_key(OWNER, upload_id, "ventas.csv")
        with pytest.raises(RuntimeError, match="COMMITTER_FUNCTION_NAME"):
            processor_handler(self._s3_event(key))


class TestCommitterHandler:
    def test_commits(self) -> None:
        upload_id = _staged()
        run_stage1(OWNER, upload_id, "ventas.csv")
        assert committer_handler({"ownerId": OWNER, "uploadId": upload_id}) == {"committed": True}

    def test_ignores_an_event_without_ids(self) -> None:
        assert committer_handler({}) == {"committed": False}


class TestBothStagesMatchTheLocalPath:
    def test_the_deployed_sequence_produces_the_same_result(
        self, client: TestClient, auth_headers: dict
    ) -> None:
        """The two handlers in sequence must equal what `process_upload` does.

        This is the regression that would otherwise only show up in
        CloudWatch: the local runner and the deployed chain calling the stages
        in a different order, or with different arguments.
        """
        upload_id = _staged()
        run_stage1(OWNER, upload_id, "ventas.csv")
        committer_handler({"ownerId": OWNER, "uploadId": upload_id})

        upload = uploads_service.get_upload(OWNER, upload_id)
        assert upload is not None
        assert upload.status.value == "ready"
        dataset = upload.datasets[0]
        assert dataset.status.value == "awaiting_confirmation"
        assert dataset.tableName == "ventas"
        assert dataset.rows == 3
