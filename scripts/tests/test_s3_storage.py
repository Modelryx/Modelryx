"""Object operation tests without network or AWS credentials."""
import io
import sys
from pathlib import Path
from unittest.mock import Mock

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server/src/server"))
from modelryx.services.s3_storage import S3ObjectStorage


@pytest.fixture
def client():
    return boto3.client("s3", region_name="us-east-1", aws_access_key_id="test",
                        aws_secret_access_key="test")


def test_download_exists_delete_and_missing(client, tmp_path):
    storage = S3ObjectStorage("bucket", prefix="project", client=client)
    arguments = {"Bucket": "bucket", "Key": "project/part.step"}
    body = io.BytesIO(b"cad-data")
    with Stubber(client) as stub:
        stub.add_response("head_object", {}, arguments)
        stub.add_response("get_object", {"Body": body}, arguments)
        stub.add_response("delete_object", {}, arguments)
        stub.add_client_error("head_object", "404", http_status_code=404,
                              expected_params=arguments)
        stub.add_client_error("get_object", "NoSuchKey", http_status_code=404,
                              expected_params=arguments)
        assert storage.exists("part.step")
        destination = tmp_path / "part.step"
        storage.download_file("part.step", destination)
        assert destination.read_bytes() == b"cad-data"
        assert body.closed
        storage.delete("part.step")
        assert not storage.exists("part.step")
        with pytest.raises(FileNotFoundError):
            storage.open_download("part.step")
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("operation", ["exists", "open_download", "delete"])
def test_permission_errors_propagate(client, operation):
    storage = S3ObjectStorage("bucket", client=client)
    method = {"exists": "head_object", "open_download": "get_object", "delete": "delete_object"}
    with Stubber(client) as stub:
        stub.add_client_error(method[operation], "AccessDenied", http_status_code=403,
                              expected_params={"Bucket": "bucket", "Key": "part.step"})
        with pytest.raises(ClientError):
            getattr(storage, operation)("part.step")


def test_upload_uses_managed_transfer(tmp_path):
    client = Mock()
    storage = S3ObjectStorage("bucket", prefix="project/", client=client)
    source = tmp_path / "part.step"
    source.write_bytes(b"cad-data")
    client.upload_fileobj.side_effect = lambda stream, bucket, key: (
        stream.read() == b"cad-data" or pytest.fail("wrong upload content")
    )
    storage.upload_file("part.step", source)
    assert client.upload_fileobj.call_args.args[1:] == ("bucket", "project/part.step")


def test_failed_download_preserves_existing_file(tmp_path):
    client = Mock()
    body = Mock()
    body.__enter__ = Mock(return_value=body)
    body.__exit__ = Mock(return_value=False)
    body.read.side_effect = [b"partial", OSError("connection lost")]
    client.get_object.return_value = {"Body": body}
    destination = tmp_path / "part.step"
    destination.write_bytes(b"original")
    with pytest.raises(OSError):
        S3ObjectStorage("bucket", client=client).download_file("part.step", destination)
    assert destination.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [destination]
    body.__exit__.assert_called_once()


@pytest.mark.parametrize("key", ["", "/absolute", "a/../b", "a//b", "a\\b", "x" * 1025])
def test_rejects_invalid_keys(key):
    client = Mock()
    with pytest.raises(ValueError):
        S3ObjectStorage("bucket", client=client).exists(key)
    client.head_object.assert_not_called()
