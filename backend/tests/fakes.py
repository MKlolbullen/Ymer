"""In-memory stand-in for the boto3 S3 client, enough for the files API."""


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, dict]] = {}

    def upload_fileobj(self, fileobj, bucket, key, ExtraArgs=None):  # noqa: N803
        self.objects[(bucket, key)] = (fileobj.read(), ExtraArgs or {})

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.objects.pop((Bucket, Key), None)

    def delete_objects(self, Bucket, Delete):  # noqa: N803
        for obj in Delete["Objects"]:
            self.objects.pop((Bucket, obj["Key"]), None)

    def generate_presigned_url(self, op, Params, ExpiresIn):  # noqa: N803
        return (
            f"https://s3.test/{Params['Bucket']}/{Params['Key']}"
            f"?disposition={Params['ResponseContentDisposition']}&expires={ExpiresIn}"
        )
